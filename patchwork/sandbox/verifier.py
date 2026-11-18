"""
Sandboxed Verification & The 3-Strike Self-Correction Loop.
"""

import os
import shutil
import tempfile
import logging
from typing import Optional, List, Dict
from pydantic import BaseModel

from patchwork.config import settings
from patchwork.guardrails.rules import validate_proposed_patch
from patchwork.brain.patcher import apply_unified_diff, extract_diff_from_llm_response, extract_target_file_from_diff
from patchwork.brain.prompts import build_reflection_prompt
from patchwork.brain.llm import llm_client
from patchwork.sandbox.docker_manager import DockerSandboxManager
from patchwork.sandbox.local_runner import run_local_test
from patchwork.database import record_patch_attempt, record_job_event

logger = logging.getLogger("patchwork.sandbox.verifier")


class VerificationResult(BaseModel):
    success: bool
    verified_diff: Optional[str] = None
    target_file: Optional[str] = None
    total_attempts: int = 0
    test_output: str = ""
    guardrail_violated: Optional[str] = None
    error_reason: Optional[str] = None


class SandboxVerifier:
    def __init__(self):
        self.docker_manager = DockerSandboxManager()

    async def verify_and_heal(
        self,
        job_id: str,
        repo_files: Dict[str, str],  # relative_path -> content
        initial_diff: str,
        target_file: str,
        code_context: str,
        test_command: str = "pytest -v",
        max_attempts: int = settings.MAX_RETRY_ATTEMPTS
    ) -> VerificationResult:
        """
        Coordinates the 3-strike self-correction loop.
        Applies fix in an ephemeral sandbox, executes tests, and feeds errors
        back to LLM if tests fail.
        """
        current_diff = initial_diff
        current_target_file = target_file
        last_test_output = ""

        # Create temporary working directory for the sandbox
        temp_sandbox_dir = tempfile.mkdtemp(prefix="patchwork_sandbox_")

        try:
            # 1. Populate initial sandbox workspace with repository files
            self._write_repo_files(temp_sandbox_dir, repo_files)

            for attempt in range(1, max_attempts + 1):
                logger.info(f"[{job_id}] Starting Sandbox Verification - Attempt {attempt}/{max_attempts}")
                await record_job_event(
                    job_id=job_id,
                    stage="verifying",
                    message=f"Sandbox Attempt {attempt}/{max_attempts}: Validating guardrails and applying patch to {current_target_file}...",
                    level="info"
                )

                # 2. Check Guardrails for proposed patch
                guardrail_res = validate_proposed_patch(current_target_file, current_diff)
                if not guardrail_res.passed:
                    logger.warning(f"[{job_id}] Patch rejected by guardrail: {guardrail_res.reason}")
                    await record_job_event(
                        job_id=job_id,
                        stage="guardrail_rejected",
                        message=f"Attempt {attempt}: Patch violated guardrail ({guardrail_res.reason})",
                        level="error"
                    )
                    await record_patch_attempt(
                        job_id=job_id,
                        attempt_number=attempt,
                        patch_diff=current_diff,
                        test_output=f"GUARDRAIL REJECTED: {guardrail_res.reason}",
                        passed=False
                    )
                    return VerificationResult(
                        success=False,
                        total_attempts=attempt,
                        guardrail_violated=guardrail_res.violation_code,
                        error_reason=guardrail_res.reason
                    )

                # 3. Apply patch to file in sandbox workspace
                file_disk_path = os.path.join(temp_sandbox_dir, current_target_file)
                if not os.path.exists(file_disk_path):
                    # Maybe relative path needs normalization
                    norm_path = current_target_file.replace("/", os.sep)
                    file_disk_path = os.path.join(temp_sandbox_dir, norm_path)

                if not os.path.exists(file_disk_path):
                    error_msg = f"Target file '{current_target_file}' not found in sandbox."
                    logger.error(error_msg)
                    return VerificationResult(
                        success=False,
                        total_attempts=attempt,
                        error_reason=error_msg
                    )

                with open(file_disk_path, "r", encoding="utf-8") as f:
                    orig_content = f.read()

                patch_res = apply_unified_diff(orig_content, current_diff)
                if not patch_res.success:
                    logger.warning(f"[{job_id}] Failed to apply diff: {patch_res.error}")
                    last_test_output = f"Diff application error: {patch_res.error}"
                else:
                    with open(file_disk_path, "w", encoding="utf-8") as f:
                        f.write(patch_res.patched_code)

                    # 4. Run tests inside isolated container or local runner
                    if settings.USE_DOCKER_SANDBOX and self.docker_manager.is_available():
                        exec_res = self.docker_manager.run_in_container(
                            project_dir=temp_sandbox_dir,
                            test_command=test_command
                        )
                        tests_passed = exec_res.success
                        last_test_output = exec_res.output or exec_res.error or ""
                    else:
                        exec_res = await run_local_test(
                            project_dir=temp_sandbox_dir,
                            test_command=test_command
                        )
                        tests_passed = exec_res.success
                        last_test_output = exec_res.output or exec_res.error or ""

                # 5. Record attempt outcome in database
                await record_patch_attempt(
                    job_id=job_id,
                    attempt_number=attempt,
                    patch_diff=current_diff,
                    test_output=last_test_output,
                    passed=tests_passed if patch_res.success else False
                )

                # 6. Branch on test outcome
                if patch_res.success and tests_passed:
                    logger.info(f"[{job_id}] Fix VERIFIED successfully on attempt {attempt}!")
                    await record_job_event(
                        job_id=job_id,
                        stage="verifying",
                        message=f"Sandbox Attempt {attempt}/{max_attempts}: ALL TESTS PASSED! Fix verified.",
                        level="success"
                    )
                    return VerificationResult(
                        success=True,
                        verified_diff=current_diff,
                        target_file=current_target_file,
                        total_attempts=attempt,
                        test_output=last_test_output
                    )

                # Tests failed: if we have attempts remaining, feed back output to LLM for reflection
                if attempt < max_attempts:
                    logger.info(f"[{job_id}] Attempt {attempt} failed. Triggering Self-Correction reflection loop.")
                    await record_job_event(
                        job_id=job_id,
                        stage="verifying",
                        message=f"Sandbox Attempt {attempt}/{max_attempts}: Tests failed. Feeding test feedback to AI for reflection...",
                        level="warning"
                    )
                    reflection_prompt = build_reflection_prompt(
                        previous_patch=current_diff,
                        container_test_error=last_test_output[:2000],
                        code_context=code_context,
                        attempt_number=attempt + 1
                    )
                    diag_res = await llm_client.generate_fix(reflection_prompt)
                    current_diff = diag_res.patch_diff
                    # Reset sandbox file back to original before next attempt
                    with open(file_disk_path, "w", encoding="utf-8") as f:
                        f.write(orig_content)

            # All attempts exhausted
            logger.warning(f"[{job_id}] All {max_attempts} attempts failed sandbox verification.")
            await record_job_event(
                job_id=job_id,
                stage="verifying",
                message=f"Sandbox verification failed after {max_attempts} attempts.",
                level="error"
            )
            return VerificationResult(
                success=False,
                total_attempts=max_attempts,
                test_output=last_test_output,
                error_reason="Exceeded maximum self-correction retry attempts (3 strikes)."
            )

        finally:
            # Ephemeral destruction: Clean up temporary sandbox directory
            try:
                shutil.rmtree(temp_sandbox_dir, ignore_errors=True)
                logger.info(f"[{job_id}] Sandbox directory cleanly destroyed.")
            except Exception:
                pass

    def _write_repo_files(self, target_dir: str, files: Dict[str, str]) -> None:
        """Writes repo file dictionary into destination sandbox directory."""
        for rel_path, content in files.items():
            full_path = os.path.join(target_dir, rel_path.replace("/", os.sep))
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
