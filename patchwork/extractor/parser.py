"""
Log Parser: Extracts isolated error stack traces, failing files, line numbers, and root errors.
"""

import re
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from patchwork.extractor.cleaner import clean_ci_log


class CrashLocation(BaseModel):
    file_path: str
    line_number: int
    is_test_file: bool = False
    context_line: Optional[str] = None


class ExtractedErrorContext(BaseModel):
    raw_stack_trace: str
    isolated_traceback: str
    error_type: str
    error_message: str
    failing_file: str
    failing_line: int
    test_file: Optional[str] = None
    test_name: Optional[str] = None
    stack_frames: List[CrashLocation] = []


# Python Traceback Pattern: File "foo.py", line 123, in func
PY_TRACEBACK_FRAME_REGEX = re.compile(
    r'File\s+["\'](?P<file>[^"\']+)["\'],\s+line\s+(?P<line>\d+)(?:,\s+in\s+(?P<func>[^\n]+))?(?:\n\s+(?P<code>[^\n]+))?'
)

# Pytest short location pattern: path/to/file.py:123: ErrorType
PYTEST_LOC_REGEX = re.compile(
    r'(?P<file>[a-zA-Z0-9_\-./\\]+\.py):(?P<line>\d+):\s+(?:in\s+\w+|\w+Error|AssertionError|Exception)'
)

# Generic Exception Line: ZeroDivisionError: division by zero
EXCEPTION_LINE_REGEX = re.compile(
    r'^(?:E\s+)?(?P<type>[A-Za-z0-9_.]*(?:Error|Exception|Failure|Panic|Fault)):\s*(?P<msg>.*)$',
    re.MULTILINE
)

# Pytest failure summary: FAILED tests/test_foo.py::test_bar - ZeroDivisionError: ...
PYTEST_FAILED_SUMMARY_REGEX = re.compile(
    r'FAILED\s+(?P<file>[^\s:]+)::(?P<test>[^\s\-]+)(?:\s+-\s+(?P<summary>.*))?'
)


def _is_test_path(path: str) -> bool:
    norm = path.replace("\\", "/").lower()
    return "test" in norm or "spec" in norm


def _is_third_party_or_std(path: str) -> bool:
    norm = path.replace("\\", "/").lower()
    return any(p in norm for p in ["site-packages", "dist-packages", "/lib/python", "anaconda", ".venv", "/usr/"])


def extract_stack_trace(raw_log: str) -> Optional[ExtractedErrorContext]:
    """
    Slices raw CI log to isolate the exact crash traceback,
    identifying the target application file and failing line.
    """
    cleaned = clean_ci_log(raw_log)

    # 1. Look for pytest failure summary if available
    test_file: Optional[str] = None
    test_name: Optional[str] = None
    summary_match = PYTEST_FAILED_SUMMARY_REGEX.search(cleaned)
    if summary_match:
        test_file = summary_match.group("file")
        test_name = summary_match.group("test")

    # 2. Locate Traceback block or Pytest FAILURES section
    traceback_slice = ""
    tb_start = cleaned.find("Traceback (most recent call last):")
    if tb_start != -1:
        # Find end of traceback (first unindented line after frames or summary)
        tb_end = cleaned.find("\n\n", tb_start + 35)
        if tb_end == -1:
            tb_end = tb_start + 4000
        traceback_slice = cleaned[tb_start:tb_end].strip()
    elif "=== FAILURES ===" in cleaned or "=== FAILURES" in cleaned:
        fail_start = cleaned.find("=== FAILURES")
        fail_end = cleaned.find("=== short test summary", fail_start)
        if fail_end == -1:
            fail_end = cleaned.find("=== 1 failed", fail_start)
        if fail_end == -1:
            fail_end = fail_start + 5000
        traceback_slice = cleaned[fail_start:fail_end].strip()
    else:
        # Fallback: find any line containing 'Error:' or 'Exception:' and grab surrounding context
        exc_match = EXCEPTION_LINE_REGEX.search(cleaned)
        if exc_match:
            idx = exc_match.start()
            start_window = max(0, idx - 1500)
            end_window = min(len(cleaned), idx + 500)
            traceback_slice = cleaned[start_window:end_window].strip()
        else:
            # If no obvious exception, return None
            return None

    # 3. Find error type and message
    error_type = "UnknownError"
    error_message = "Failure detected in build output"
    exc_matches = list(EXCEPTION_LINE_REGEX.finditer(traceback_slice))
    if exc_matches:
        last_exc = exc_matches[-1]
        error_type = last_exc.group("type")
        error_message = last_exc.group("msg").strip()

    # 4. Extract all stack frames
    frames: List[CrashLocation] = []
    
    # Check File "...", line 123
    for match in PY_TRACEBACK_FRAME_REGEX.finditer(traceback_slice):
        fpath = match.group("file")
        if _is_third_party_or_std(fpath):
            continue
        line_num = int(match.group("line"))
        code_line = match.group("code")
        frames.append(CrashLocation(
            file_path=fpath,
            line_number=line_num,
            is_test_file=_is_test_path(fpath),
            context_line=code_line
        ))

    # Also check pytest location format (file.py:123:)
    if not frames:
        for match in PYTEST_LOC_REGEX.finditer(traceback_slice):
            fpath = match.group("file")
            if _is_third_party_or_std(fpath):
                continue
            line_num = int(match.group("line"))
            frames.append(CrashLocation(
                file_path=fpath,
                line_number=line_num,
                is_test_file=_is_test_path(fpath)
            ))

    if not frames:
        # If no frame matched inside slice, check whole log
        for match in PY_TRACEBACK_FRAME_REGEX.finditer(cleaned):
            fpath = match.group("file")
            if not _is_third_party_or_std(fpath):
                frames.append(CrashLocation(
                    file_path=fpath,
                    line_number=int(match.group("line")),
                    is_test_file=_is_test_path(fpath),
                    context_line=match.group("code")
                ))

    # 5. Determine the source file that actually contains the bug:
    # We prefer non-test application files in the repository (e.g. math_service.py over test_math_service.py)
    target_frame: Optional[CrashLocation] = None
    app_frames = [f for f in frames if not f.is_test_file]
    if app_frames:
        # The crash in application code is usually the deepest non-test frame
        target_frame = app_frames[-1]
    elif frames:
        # Fallback to the deepest frame
        target_frame = frames[-1]
    else:
        # Cannot isolate a frame
        return None

    return ExtractedErrorContext(
        raw_stack_trace=traceback_slice,
        isolated_traceback=traceback_slice,
        error_type=error_type,
        error_message=error_message,
        failing_file=target_frame.file_path,
        failing_line=target_frame.line_number,
        test_file=test_file,
        test_name=test_name,
        stack_frames=frames
    )
