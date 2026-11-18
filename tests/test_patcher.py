"""
Unit tests for unified diff extraction and patch application.
"""

from patchwork.brain.patcher import (
    extract_diff_from_llm_response,
    extract_target_file_from_diff,
    apply_unified_diff
)

ORIGINAL_CODE = """def divide(a: int, b: int) -> int:
    return a / b
"""

SAMPLE_DIFF = """--- a/math_service.py
+++ b/math_service.py
@@ -1,2 +1,4 @@
 def divide(a: int, b: int) -> int:
+    if b == 0:
+        return 0
     return a / b
"""

LLM_RESPONSE = f"""Here is my diagnosis of the bug.

Root Cause: The function does not guard against zero denominator.

```diff
{SAMPLE_DIFF}
```
"""


def test_extract_diff_from_markdown():
    extracted = extract_diff_from_llm_response(LLM_RESPONSE)
    assert extracted.startswith("--- a/math_service.py")
    assert "return 0" in extracted


def test_extract_target_file():
    target = extract_target_file_from_diff(SAMPLE_DIFF)
    assert target == "math_service.py"


def test_apply_unified_diff():
    res = apply_unified_diff(ORIGINAL_CODE, SAMPLE_DIFF)
    assert res.success is True
    assert "if b == 0:" in res.patched_code
    assert "return 0" in res.patched_code
    assert res.hunks_applied == 1


import pytest
from unittest.mock import patch
from patchwork.brain.llm import LLMClient
from patchwork.config import settings
from patchwork.dashboard.templating import system_status


@pytest.mark.asyncio
async def test_llm_claude_provider_call():
    client = LLMClient()
    with patch.object(settings, "LLM_PROVIDER", "anthropic"), \
         patch.object(settings, "ANTHROPIC_API_KEY", "sk-ant-test-key"), \
         patch.object(settings, "LLM_MODEL", "claude-3-5-sonnet-20241022"):

        mock_response = {
            "content": [{"type": "text", "text": "Root Cause: Bug found.\n\n```diff\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n```"}],
            "usage": {"input_tokens": 100, "output_tokens": 50}
        }

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.raise_for_status = lambda: None
            mock_post.return_value.json = lambda: mock_response

            result = await client.generate_fix("Diagnose this error")
            assert result.root_cause == "Bug found."
            assert "--- a/file.py" in result.patch_diff
            assert result.tokens_used == 150

        status = system_status()
        assert "Anthropic" in status["llm"]["value"]
        assert status["llm"]["state"] == "ok"
