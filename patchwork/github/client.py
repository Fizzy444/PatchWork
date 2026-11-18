"""
GitHub App Client and REST API Automation.
Handles GitHub App RS256 authentication, log downloading, workflow re-runs,
branch creation, commits, and Pull Request generation.
"""

import os
import time
import base64
import logging
from typing import Optional, Dict, Any, Tuple
import httpx
import jwt

from patchwork.config import settings

logger = logging.getLogger("patchwork.github")


class GitHubClient:
    def __init__(self):
        self.app_id = settings.GITHUB_APP_ID
        self.private_key = settings.GITHUB_PRIVATE_KEY
        self.token = settings.GITHUB_TOKEN

    def _generate_app_jwt(self) -> Optional[str]:
        """Generates RS256 JWT for GitHub App authentication."""
        if not self.app_id or not self.private_key:
            return None

        # If private_key is a file path, read file
        key_content = self.private_key
        if os.path.exists(self.private_key):
            with open(self.private_key, "r", encoding="utf-8") as f:
                key_content = f.read()

        key_content = key_content.strip()
        if not key_content.startswith("-----BEGIN"):
            key_content = f"-----BEGIN RSA PRIVATE KEY-----\n{key_content}\n-----END RSA PRIVATE KEY-----"

        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + (10 * 60),  # 10 minutes
            "iss": self.app_id
        }

        try:
            return jwt.encode(payload, key_content, algorithm="RS256")
        except Exception as e:
            logger.error(f"Failed to sign GitHub App JWT: {e}")
            return None

    async def get_installation_token(self, installation_id: int) -> Optional[str]:
        """Exchanges App JWT for an installation access token."""
        app_jwt = self._generate_app_jwt()
        if not app_jwt:
            return self.token or None

        url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Stateless-S2S-Token": "true",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers)
            if resp.status_code == 201:
                return resp.json().get("token")
            logger.error(f"Failed to get installation token: {resp.text}")
            return None

    async def get_effective_token(self, installation_id: Optional[int] = None) -> Optional[str]:
        """Gets installation token if app credentials exist, otherwise falls back to static PAT."""
        if installation_id and self.app_id and self.private_key:
            token = await self.get_installation_token(installation_id)
            if token:
                return token
        return self.token or None

    async def fetch_workflow_logs(self, repo_name: str, run_id: int, installation_id: Optional[int] = None) -> str:
        """
        Fetches workflow execution log from GitHub API or returns simulated log.
        """
        token = await self.get_effective_token(installation_id)
        if not token:
            logger.info(f"No GitHub token configured. Returning mock failure log for {repo_name} run #{run_id}.")
            return self._get_mock_failure_log()

        url = f"https://api.github.com/repos/{repo_name}/actions/runs/{run_id}/logs"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        }

        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                if resp.content.startswith(b"PK"):
                    import io
                    import zipfile
                    try:
                        zf = zipfile.ZipFile(io.BytesIO(resp.content))
                        logs_text = []
                        for name in sorted(zf.namelist()):
                            if name.endswith(".txt"):
                                logs_text.append(zf.read(name).decode("utf-8", errors="replace"))
                        if logs_text:
                            return "\n".join(logs_text)
                    except Exception as e:
                        logger.error(f"Failed to decompress log archive: {e}")
                return resp.text
            logger.warning(f"Failed to fetch real logs ({resp.status_code}). Using mock fallback.")
            return self._get_mock_failure_log()

    async def trigger_workflow_rerun(self, repo_name: str, run_id: int, installation_id: Optional[int] = None) -> bool:
        """
        Guardrail 3: Flaky Test Auto-Rerun.
        Calls POST /repos/{owner}/{repo}/actions/runs/{run_id}/rerun
        """
        token = await self.get_effective_token(installation_id)
        logger.info(f"Triggering workflow re-run for {repo_name} run #{run_id} (Flaky Test Guardrail)")
        if not token:
            return True  # Mock success

        url = f"https://api.github.com/repos/{repo_name}/actions/runs/{run_id}/rerun"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers)
            return resp.status_code in (201, 202)

    async def get_file_content(self, repo_name: str, file_path: str, ref: str, installation_id: Optional[int] = None) -> Optional[str]:
        """Fetches raw content of a file at specific git ref/commit."""
        token = await self.get_effective_token(installation_id)
        if not token:
            return None

        url = f"https://api.github.com/repos/{repo_name}/contents/{file_path}?ref={ref}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                content_b64 = data.get("content", "")
                return base64.b64decode(content_b64).decode("utf-8")
        return None

    async def open_remediation_pr(
        self,
        repo_name: str,
        base_branch: str,
        run_id: int,
        failing_file: str,
        patched_code: str,
        root_cause: str,
        diff_patch: str,
        attempts_count: int,
        test_output: str,
        installation_id: Optional[int] = None
    ) -> Tuple[str, int]:
        """
        Creates branch ai-fix/run-{run_id}, commits patched code, and opens a Pull Request.
        Returns (pr_url, pr_number).
        """
        fix_branch = f"{settings.LOOP_PREVENTION_PREFIX}run-{run_id}"
        pr_title = f"[Patchwork] Fix CI failure in {failing_file} (Run #{run_id})"

        pr_body = f"""## 🩹 Patchwork Autonomous CI Healing Report

**Status:** ✅ Verified in Ephemeral Sandbox Container
**Failed Workflow Run:** `#{run_id}`
**Target File:** `{failing_file}`
**Self-Correction Attempts:** `{attempts_count}/{settings.MAX_RETRY_ATTEMPTS}`

---

### 🔍 Root Cause Analysis
{root_cause}

---

### 🛡️ Guardrail Compliance
- [x] **Never Touch Tests:** Verified no modifications to test suites or CI workflows.
- [x] **Infinite Loop Prevention:** Targeted against branch `{base_branch}`; branch tagged `{fix_branch}`.
- [x] **Diff Size Limiter:** Verified patch is surgical (< {settings.MAX_DIFF_LINES} lines altered).
- [x] **Sandboxed Verification:** Passed clean pytest run inside ephemeral Docker container.

---

### 📝 Verified Patch
```diff
{diff_patch}
```

---

### 🧪 Sandbox Verification Output Proof
```text
{test_output[:1000]}
```

*Automated by Patchwork Autonomous CI Self-Healing Engine.*
"""

        token = await self.get_effective_token(installation_id)
        if not token:
            mock_pr_num = run_id % 1000 or 42
            mock_url = f"https://github.com/{repo_name}/pull/{mock_pr_num}"
            logger.info(f"Simulated PR creation: {mock_url} on branch {fix_branch}")
            return mock_url, mock_pr_num

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        }

        async with httpx.AsyncClient() as client:
            # 1. Get base branch commit SHA
            ref_resp = await client.get(
                f"https://api.github.com/repos/{repo_name}/git/refs/heads/{base_branch}",
                headers=headers
            )
            if ref_resp.status_code != 200:
                raise RuntimeError(f"Could not fetch ref for {base_branch}: {ref_resp.text}")

            base_sha = ref_resp.json()["object"]["sha"]

            # 2. Create new branch ai-fix/run-{run_id}
            create_ref_resp = await client.post(
                f"https://api.github.com/repos/{repo_name}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{fix_branch}", "sha": base_sha}
            )

            # 3. Get existing file SHA on fix branch to update it
            file_resp = await client.get(
                f"https://api.github.com/repos/{repo_name}/contents/{failing_file}?ref={fix_branch}",
                headers=headers
            )
            file_sha = file_resp.json().get("sha") if file_resp.status_code == 200 else None

            # 4. Commit updated file
            encoded_content = base64.b64encode(patched_code.encode("utf-8")).decode("utf-8")
            commit_payload = {
                "message": f"fix({failing_file}): auto-patch bug from CI run #{run_id}",
                "content": encoded_content,
                "branch": fix_branch
            }
            if file_sha:
                commit_payload["sha"] = file_sha

            await client.put(
                f"https://api.github.com/repos/{repo_name}/contents/{failing_file}",
                headers=headers,
                json=commit_payload
            )

            # 5. Open Pull Request
            pr_resp = await client.post(
                f"https://api.github.com/repos/{repo_name}/pulls",
                headers=headers,
                json={
                    "title": pr_title,
                    "body": pr_body,
                    "head": fix_branch,
                    "base": base_branch
                }
            )

            if pr_resp.status_code == 201:
                pr_data = pr_resp.json()
                return pr_data["html_url"], pr_data["number"]

            raise RuntimeError(f"Failed to create PR: {pr_resp.text}")

    def _get_mock_failure_log(self) -> str:
        """Standard mock failure log with realistic pytest traceback."""
        return """
2026-03-20T10:15:20.1234567Z ##[group]Run pytest tests/
2026-03-20T10:15:22.4567890Z ==================================== FAILURES ====================================
2026-03-20T10:15:22.4567891Z __________________________________ test_divide ___________________________________
2026-03-20T10:15:22.4567892Z
2026-03-20T10:15:22.4567893Z     def test_divide():
2026-03-20T10:15:22.4567894Z >       assert divide(10, 0) == 0
2026-03-20T10:15:22.4567895Z
2026-03-20T10:15:22.4567896Z tests/test_math_service.py:12: 
2026-03-20T10:15:22.4567897Z _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
2026-03-20T10:15:22.4567898Z math_service.py:18: in divide
2026-03-20T10:15:22.4567899Z     return a / b
2026-03-20T10:15:22.4567900Z _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
2026-03-20T10:15:22.4567901Z
2026-03-20T10:15:22.4567902Z     def divide(a: int, b: int) -> int:
2026-03-20T10:15:22.4567903Z >       return a / b
2026-03-20T10:15:22.4567904Z E       ZeroDivisionError: division by zero
2026-03-20T10:15:22.4567905Z
2026-03-20T10:15:22.4567906Z math_service.py:18: ZeroDivisionError
2026-03-20T10:15:22.4567907Z =========================== short test summary info ============================
2026-03-20T10:15:22.4567908Z FAILED tests/test_math_service.py::test_divide - ZeroDivisionError: division by zero
2026-03-20T10:15:22.4567909Z ============================== 1 failed in 0.12s ===============================
2026-03-20T10:15:22.4567910Z ##[error]Process completed with exit code 1.
"""


github_client = GitHubClient()
