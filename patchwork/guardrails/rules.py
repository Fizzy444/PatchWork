"""
Core Industry-Grade Guardrails.
1. The "Never Touch Tests" Rule
2. Infinite Loop Prevention
3. Diff Size Limiter
"""

import re
from typing import Tuple, List, Optional
from pydantic import BaseModel

from patchwork.config import settings


class GuardrailCheckResult(BaseModel):
    passed: bool
    violation_code: Optional[str] = None
    reason: Optional[str] = None
    details: Optional[dict] = None


def is_loop_branch(branch_name: str, sender_login: Optional[str] = None) -> bool:
    """
    Guardrail 2: Infinite Loop Prevention
    Checks whether the failure happened on an AI-created branch (e.g. ai-fix/*)
    or was triggered by the bot itself.
    """
    if not branch_name:
        return False

    clean_branch = branch_name.lower().strip()
    if clean_branch.startswith(settings.LOOP_PREVENTION_PREFIX.lower()):
        return True

    # Also detect common bot branches
    if clean_branch.startswith("patchwork-fix/") or clean_branch.startswith("autofix/"):
        return True

    # Check bot sender
    if sender_login and "[bot]" in sender_login.lower():
        return True

    return False


def is_protected_file(file_path: str, protected_paths: Optional[List[str]] = None) -> bool:
    """
    Guardrail 1: The "Never Touch Tests" Rule
    Forbids the agent from modifying any file inside /tests, test_*.py, *_test.py,
    or .github/workflows directory.
    """
    if not file_path:
        return False

    normalized = file_path.replace("\\", "/").strip().lower()
    rules = protected_paths or settings.PROTECTED_PATHS

    for rule in rules:
        rule_norm = rule.replace("\\", "/").lower()
        if rule_norm in normalized:
            return True

    # Explicit filename checks
    filename = normalized.split("/")[-1]
    if filename.startswith("test_") or filename.endswith("_test.py") or filename.endswith(".spec.ts") or filename.endswith(".test.ts"):
        return True

    return False


def count_diff_lines(diff_content: str) -> Tuple[int, int]:
    """
    Counts additions and deletions in a unified diff.
    Returns (added_lines, deleted_lines).
    """
    added = 0
    deleted = 0
    for line in diff_content.splitlines():
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            deleted += 1
    return added, deleted


def check_diff_size_limit(diff_content: str, max_allowed_lines: int = settings.MAX_DIFF_LINES) -> Tuple[bool, int, str]:
    """
    Guardrail 4: Diff Size Limiter
    Reject any AI patch that alters more than ~30 lines of code.
    """
    added, deleted = count_diff_lines(diff_content)
    total_altered = added + deleted

    if total_altered > max_allowed_lines:
        return False, total_altered, (
            f"Diff alters {total_altered} lines ({added} additions, {deleted} deletions), "
            f"which exceeds the safety limit of {max_allowed_lines} lines."
        )

    return True, total_altered, f"Diff alters {total_altered} lines (limit: {max_allowed_lines})."


def validate_proposed_patch(target_file: str, diff_content: str) -> GuardrailCheckResult:
    """
    Comprehensive guardrail evaluation of proposed patch before sandboxing/committing.
    """
    # 1. Never Touch Tests Check
    if is_protected_file(target_file):
        return GuardrailCheckResult(
            passed=False,
            violation_code="NEVER_TOUCH_TESTS",
            reason=f"Patch targets protected file '{target_file}'. Modifications to test suites or CI workflows are strictly prohibited.",
            details={"file": target_file}
        )

    # 2. Diff Size Check
    passed_diff, total_lines, diff_msg = check_diff_size_limit(diff_content)
    if not passed_diff:
        return GuardrailCheckResult(
            passed=False,
            violation_code="DIFF_SIZE_EXCEEDED",
            reason=diff_msg,
            details={"lines_altered": total_lines, "max_allowed": settings.MAX_DIFF_LINES}
        )

    return GuardrailCheckResult(
        passed=True,
        details={"lines_altered": total_lines, "file": target_file}
    )
