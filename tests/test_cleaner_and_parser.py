"""
Unit tests for Log Cleaner & Parser (Component C).
"""

import pytest
from patchwork.extractor.cleaner import strip_ansi_codes, clean_ci_log
from patchwork.extractor.parser import extract_stack_trace


SAMPLE_LOG_WITH_ANSI = "\x1b[31mERROR:\x1b[0m division by \x1b[33mzero\x1b[0m"

SAMPLE_RAW_PYTEST_LOG = """
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
"""


def test_strip_ansi_codes():
    cleaned = strip_ansi_codes(SAMPLE_LOG_WITH_ANSI)
    assert cleaned == "ERROR: division by zero"
    assert "\x1b" not in cleaned


def test_clean_ci_log_removes_timestamps_and_annotations():
    cleaned = clean_ci_log(SAMPLE_RAW_PYTEST_LOG)
    assert "2026-03-20T10:15:20" not in cleaned
    assert "##[group]" not in cleaned
    assert "ZeroDivisionError: division by zero" in cleaned


def test_extract_stack_trace_locates_broken_file_and_line():
    context = extract_stack_trace(SAMPLE_RAW_PYTEST_LOG)
    assert context is not None
    assert context.error_type == "ZeroDivisionError"
    assert "division by zero" in context.error_message
    assert context.failing_file == "math_service.py"
    assert context.failing_line == 18
    assert context.test_file == "tests/test_math_service.py"
    assert context.test_name == "test_divide"
