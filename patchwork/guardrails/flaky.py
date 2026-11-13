"""
Flaky Test and Transient Network/Timeout Error Detection.
"""

import re
from typing import Tuple, List

FLAKY_PATTERNS = [
    r"ETIMEDOUT",
    r"ECONNRESET",
    r"ECONNREFUSED",
    r"Connection reset by peer",
    r"Operation timed out",
    r"Gateway Timeout",
    r"502 Bad Gateway",
    r"503 Service Unavailable",
    r"504 Gateway Time-out",
    r"429 Too Many Requests",
    r"ReadTimeoutError",
    r"ConnectTimeoutError",
    r"socket\.timeout",
    r"httpx\.ConnectTimeout",
    r"httpx\.ReadTimeout",
    r"requests\.exceptions\.ConnectionError",
    r"requests\.exceptions\.Timeout",
    r"pip._vendor.urllib3.exceptions.ReadTimeoutError",
    r"Could not resolve host",
    r"Temporary failure in name resolution",
    r"SSL: CERTIFICATE_VERIFY_FAILED",
    r"Docker daemon not responding",
    r"network timeout",
]

_COMPILED_FLAKY = [re.compile(p, re.IGNORECASE) for p in FLAKY_PATTERNS]


def is_flaky_or_transient_error(log_content: str) -> Tuple[bool, str]:
    """
    Scans the failure log for network timeouts, socket errors, or transient gateway failures.
    Returns (is_flaky, matched_reason).
    """
    for regex in _COMPILED_FLAKY:
        match = regex.search(log_content)
        if match:
            return True, f"Detected transient/network timeout: '{match.group(0)}'"

    return False, ""
