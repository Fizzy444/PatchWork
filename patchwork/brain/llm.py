"""
LLM Client: Provides unified interface for Gemini, OpenAI, and Mock diagnosis.
"""

import os
import re
import logging
from typing import Tuple, Optional
import httpx

from patchwork.config import settings
from patchwork.brain.prompts import DIAGNOSIS_SYSTEM_PROMPT
from patchwork.brain.patcher import extract_diff_from_llm_response

logger = logging.getLogger("patchwork.brain")


class LLMDiagnosisResult:
    def __init__(self, root_cause: str, patch_diff: str, raw_response: str, tokens_used: int = 0):
        self.root_cause = root_cause
        self.patch_diff = patch_diff
        self.raw_response = raw_response
        self.tokens_used = tokens_used


class LLMClient:
    async def generate_fix(
        self,
        prompt: str,
        system_prompt: str = DIAGNOSIS_SYSTEM_PROMPT
    ) -> LLMDiagnosisResult:
        """Generates diagnosis and patch from LLM."""
        provider = (settings.LLM_PROVIDER or "mock").lower()

        # Check if actual API key exists
        if provider == "gemini" and (settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")):
            return await self._call_gemini(prompt, system_prompt)
        elif provider in ("anthropic", "claude") and (settings.ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY")):
            return await self._call_claude(prompt, system_prompt)
        elif provider == "openai" and (settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")):
            return await self._call_openai(prompt, system_prompt)
        else:
            logger.info("Using built-in Mock LLM engine for deterministic diagnosis & patching.")
            return self._call_mock(prompt)

    async def _call_gemini(self, prompt: str, system_prompt: str) -> LLMDiagnosisResult:
        """Calls Google Gemini REST API with automatic model fallback."""
        api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
        model = settings.LLM_MODEL or "gemini-2.5-flash"
        payload = {
            "system_instruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 2048,
            }
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                resp = await client.post(url, json=payload)
                if resp.status_code != 200 and model != "gemini-2.5-flash":
                    logger.warning(
                        f"Gemini model '{model}' returned HTTP {resp.status_code}. Falling back to 'gemini-2.5-flash'..."
                    )
                    model = "gemini-2.5-flash"
                    fallback_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                    resp = await client.post(fallback_url, json=payload)

                resp.raise_for_status()
                data = resp.json()

                candidates = data.get("candidates", [])
                if not candidates:
                    raise RuntimeError("No generation candidates returned from Gemini.")

                text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                usage = data.get("usageMetadata", {})
                tokens = usage.get("totalTokenCount", 350)

                diff = extract_diff_from_llm_response(text)
                root_cause = self._extract_root_cause(text)

                return LLMDiagnosisResult(root_cause=root_cause, patch_diff=diff, raw_response=text, tokens_used=tokens)
        except Exception as e:
            logger.error(f"Gemini API call failed ({e}). Falling back to local diagnostic engine to prevent pipeline deadlock.")
            mock_res = self._call_mock(prompt)
            mock_res.root_cause = f"[Fallback: Gemini failed ({type(e).__name__})] {mock_res.root_cause}"
            return mock_res

    async def _call_openai(self, prompt: str, system_prompt: str) -> LLMDiagnosisResult:
        """Calls OpenAI Chat Completions API."""
        api_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")
        model = settings.LLM_MODEL if "gpt" in (settings.LLM_MODEL or "") else "gpt-4o"
        url = "https://api.openai.com/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()

                text = data["choices"][0]["message"]["content"]
                tokens = data.get("usage", {}).get("total_tokens", 350)

                diff = extract_diff_from_llm_response(text)
                root_cause = self._extract_root_cause(text)

                return LLMDiagnosisResult(root_cause=root_cause, patch_diff=diff, raw_response=text, tokens_used=tokens)
        except Exception as e:
            logger.error(f"OpenAI API call failed ({e}). Falling back to local diagnostic engine.")
            mock_res = self._call_mock(prompt)
            mock_res.root_cause = f"[Fallback: OpenAI failed ({type(e).__name__})] {mock_res.root_cause}"
            return mock_res

    async def _call_claude(self, prompt: str, system_prompt: str) -> LLMDiagnosisResult:
        """Calls Anthropic Claude Messages API."""
        api_key = settings.ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY")
        model = settings.LLM_MODEL if "claude" in (settings.LLM_MODEL or "") else "claude-3-5-sonnet-20241022"
        url = "https://api.anthropic.com/v1/messages"

        headers = {
            "x-api-key": str(api_key),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 2048,
            "temperature": 0.2,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()

                text = ""
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        text += block.get("text", "")

                usage = data.get("usage", {})
                tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

                diff = extract_diff_from_llm_response(text)
                root_cause = self._extract_root_cause(text)

                return LLMDiagnosisResult(root_cause=root_cause, patch_diff=diff, raw_response=text, tokens_used=tokens or 350)
        except Exception as e:
            logger.error(f"Claude API call failed ({e}). Falling back to local diagnostic engine.")
            mock_res = self._call_mock(prompt)
            mock_res.root_cause = f"[Fallback: Claude failed ({type(e).__name__})] {mock_res.root_cause}"
            return mock_res

    def _call_mock(self, prompt: str) -> LLMDiagnosisResult:
        """
        Returns a deterministic patch for the built-in divide-by-zero test case.
        """
        prompt_lower = prompt.lower()

        # Target file extraction from prompt
        file_match = re.search(r"Target Failing File:\s*([^\s\n]+)", prompt)
        target_file = file_match.group(1) if file_match else "math_service.py"

        # Extract signature and line if present in prompt
        sig_match = re.search(r"(def\s+divide\s*\([^)]*\)[^:]*:)", prompt)
        sig = sig_match.group(1) if sig_match else "def divide(a, b):"
        line_match = re.search(r"\[Lines\s+(\d+)-(\d+)\]", prompt)
        start_line = int(line_match.group(1)) if line_match else 1

        # Case 1: ZeroDivisionError in divide
        if "zerodivisionerror" in prompt_lower or "division by zero" in prompt_lower:
            root_cause = "Division by zero occurred when denominator b is 0 without a guard check."
            diff = f"""--- a/{target_file}
+++ b/{target_file}
@@ -{start_line},2 +{start_line},4 @@
 {sig}
+    if b == 0:
+        return 0
     return a / b
"""
        # Case 2: KeyError
        elif "keyerror" in prompt_lower:
            root_cause = "Missing dictionary key access without default or key presence validation."
            diff = f"""--- a/{target_file}
+++ b/{target_file}
@@ -10,2 +10,2 @@
-    return data['user_id']
+    return data.get('user_id', None)
"""
        # Case 3: Off-by-one or IndexError
        elif "indexerror" in prompt_lower or "list index out of range" in prompt_lower:
            root_cause = "Index out of range due to off-by-one loop boundary."
            diff = f"""--- a/{target_file}
+++ b/{target_file}
@@ -8,2 +8,2 @@
-    for i in range(len(items) + 1):
+    for i in range(len(items)):
"""
        # Case 4: Default generic surgical fix
        else:
            root_cause = "Unhandled edge-case in arithmetic or input validation."
            diff = f"""--- a/{target_file}
+++ b/{target_file}
@@ -14,2 +14,4 @@
 def divide(a, b):
+    if b == 0:
+        return 0
     return a / b
"""

        raw = f"Root Cause: {root_cause}\n\n```diff\n{diff}\n```"
        return LLMDiagnosisResult(root_cause=root_cause, patch_diff=diff.strip(), raw_response=raw, tokens_used=240)

    def _extract_root_cause(self, response_text: str) -> str:
        """Extracts 1-2 sentence root cause from response."""
        for line in response_text.splitlines():
            line_clean = line.strip()
            if line_clean.lower().startswith("root cause:"):
                return line_clean[11:].strip()
            if line_clean.lower().startswith("**root cause**:"):
                return line_clean[15:].strip()

        # Fallback to first non-empty line
        for line in response_text.splitlines():
            clean = line.strip()
            if clean and not clean.startswith("```") and not clean.startswith("#"):
                return clean[:200]

        return "Bug diagnosed and patched based on CI error trace."


# Global client instance
llm_client = LLMClient()
