"""
Local Isolated Sandbox Runner.
Fallback when Docker daemon is not active. Executes tests in an isolated
subprocess with strict timeout enforcement.
"""

import sys
import asyncio
import logging
from typing import Optional
from pydantic import BaseModel

from patchwork.config import settings

logger = logging.getLogger("patchwork.sandbox.local")


class LocalExecutionResult(BaseModel):
    success: bool
    exit_code: int
    output: str
    timed_out: bool = False
    error: Optional[str] = None


async def run_local_test(
    project_dir: str,
    test_command: str = "pytest -v",
    timeout_seconds: int = settings.SANDBOX_TIMEOUT_SECONDS
) -> LocalExecutionResult:
    """
    Executes tests locally using current python interpreter within the project directory.
    Enforces strict timeout.
    """
    # Replace generic 'pytest' with python -m pytest using the current venv interpreter if available
    cmd_parts = test_command.split()
    if cmd_parts and cmd_parts[0] == "pytest":
        executable = sys.executable
        full_command = [executable, "-m", "pytest"] + cmd_parts[1:]
    else:
        full_command = cmd_parts

    logger.info(f"Running isolated local tests in {project_dir}: {' '.join(full_command)}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *full_command,
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )

        try:
            stdout_data, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=float(timeout_seconds)
            )
            output = stdout_data.decode("utf-8", errors="replace") if stdout_data else ""
            exit_code = proc.returncode or 0

            return LocalExecutionResult(
                success=(exit_code == 0),
                exit_code=exit_code,
                output=output,
                timed_out=False
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return LocalExecutionResult(
                success=False,
                exit_code=-1,
                output="Test execution exceeded 2-minute safety timeout (Infinite Loop Prevention).",
                timed_out=True
            )

    except Exception as e:
        return LocalExecutionResult(
            success=False,
            exit_code=1,
            output="",
            error=f"Local execution failed: {e}"
        )
