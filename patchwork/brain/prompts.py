"""
Prompt engineering for AI Diagnosis and Patch Generation.
"""

from typing import Optional


DIAGNOSIS_SYSTEM_PROMPT = """You are Patchwork, an elite Autonomous CI Self-Healing Agent.
Your mission is to diagnose software bugs from isolated CI failure stack traces and generate surgical, production-grade code fixes.

CRITICAL RULES:
1. SURGICAL MINIMALISM: Do not rewrite entire files or refactor code. Alter ONLY the exact broken lines (maximum 30 lines altered).
2. NEVER TOUCH TESTS: Never attempt to fix a bug by modifying test assertions, test fixtures, or CI workflows. Fix the underlying application code.
3. OUTPUT FORMAT:
   - Provide a clear 1-2 sentence ROOT CAUSE analysis.
   - Provide a UNIFIED DIFF patch block inside a markdown ```diff code fence.
   - Ensure diff headers use standard format:
     --- a/<filepath>
     +++ b/<filepath>
     @@ ... @@
   - Ensure the diff contains exact context lines so it can be applied cleanly.
"""


def build_initial_diagnosis_prompt(
    repo_name: str,
    failing_file: str,
    failing_line: int,
    error_type: str,
    error_message: str,
    isolated_traceback: str,
    code_context: str,
    test_context: Optional[str] = None
) -> str:
    """Builds the initial prompt for Phase 2 / Stage 4 diagnosis."""
    prompt = f"""Target Repository: {repo_name}
Target Failing File: {failing_file} (approx line {failing_line})
Error Type: {error_type}
Error Message: {error_message}

=== Isolated CI Stack Trace ===
{isolated_traceback}

=== Code Context (AST-Extracted) ===
{code_context}
"""
    if test_context:
        prompt += f"\n=== Failing Test Context ===\n{test_context}\n"

    prompt += """
Please analyze this failure, identify the root cause, and produce a unified diff patch to fix the bug.
Remember: Alter only the necessary lines (strictly under 30 lines changed). Format the patch inside a ```diff code block.
"""
    return prompt


def build_reflection_prompt(
    previous_patch: str,
    container_test_error: str,
    code_context: str,
    attempt_number: int
) -> str:
    """
    Builds the Stage 5 Self-Correction prompt when sandbox verification fails.
    Feeds back the Docker container output to the LLM.
    """
    return f"""The previous fix attempt (Attempt #{attempt_number - 1}) failed in the isolated sandbox test runner!

=== Previous Applied Patch ===
```diff
{previous_patch}
```

=== Sandbox Container Test Failure Output ===
{container_test_error}

=== Code Context ===
{code_context}

Please reflect on why the previous patch failed the test suite.
Generate a revised, correct unified diff patch that addresses the failure while remaining surgical (< 30 lines).
Output your revised patch in a ```diff code block.
"""
