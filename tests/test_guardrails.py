"""
Unit tests for the 4 Industry-Grade Guardrails (Section 4).
"""

from patchwork.guardrails.rules import (
    is_protected_file,
    is_loop_branch,
    check_diff_size_limit,
    validate_proposed_patch
)
from patchwork.guardrails.flaky import is_flaky_or_transient_error


def test_guardrail_1_never_touch_tests():
    # Forbidden files
    assert is_protected_file("tests/test_service.py") is True
    assert is_protected_file("/tests/unit/test_app.py") is True
    assert is_protected_file(".github/workflows/ci.yml") is True
    assert is_protected_file("src/calculator_test.py") is True
    assert is_protected_file("frontend/src/app.test.ts") is True

    # Allowed application files
    assert is_protected_file("src/math_service.py") is False
    assert is_protected_file("app/controllers/checkout.py") is False
    assert is_protected_file("main.py") is False

    # Validation rejection
    res = validate_proposed_patch("tests/test_foo.py", "+ assert True")
    assert res.passed is False
    assert res.violation_code == "NEVER_TOUCH_TESTS"


def test_guardrail_2_infinite_loop_prevention():
    # AI Fix branches must be ignored
    assert is_loop_branch("ai-fix/run-1234") is True
    assert is_loop_branch("patchwork-fix/bug-99") is True
    assert is_loop_branch("autofix/main") is True
    assert is_loop_branch("feature/checkout", sender_login="github-actions[bot]") is True

    # Legitimate developer branches
    assert is_loop_branch("main") is False
    assert is_loop_branch("feature/new-billing") is False
    assert is_loop_branch("hotfix/auth-leak") is False


def test_guardrail_3_flaky_test_detection():
    network_log = "Fatal: ETIMEDOUT connect to remote database 10.0.0.1:5432"
    is_flaky, reason = is_flaky_or_transient_error(network_log)
    assert is_flaky is True
    assert "ETIMEDOUT" in reason

    gateway_log = "HTTP 502 Bad Gateway while pulling dependencies from internal registry"
    is_flaky2, _ = is_flaky_or_transient_error(gateway_log)
    assert is_flaky2 is True

    code_bug_log = "ZeroDivisionError: division by zero in divide()"
    is_flaky3, _ = is_flaky_or_transient_error(code_bug_log)
    assert is_flaky3 is False


def test_guardrail_4_diff_size_limiter():
    # Small surgical patch (under 30 lines) -> PASSED
    small_diff = """--- a/math_service.py
+++ b/math_service.py
@@ -14,2 +14,4 @@
 def divide(a, b):
+    if b == 0:
+        return 0
     return a / b
"""
    passed, total, _ = check_diff_size_limit(small_diff, max_allowed_lines=30)
    assert passed is True
    assert total <= 30

    # Huge refactor (> 30 lines altered) -> REJECTED
    huge_diff_lines = ["+ some new line" for _ in range(40)]
    huge_diff = "\n".join(huge_diff_lines)
    passed_huge, total_huge, reason = check_diff_size_limit(huge_diff, max_allowed_lines=30)
    assert passed_huge is False
    assert total_huge == 40
    assert "exceeds the safety limit" in reason
