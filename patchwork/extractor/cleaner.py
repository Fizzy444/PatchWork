"""
Log cleaning: Strips ANSI escape codes, CI runner headers, and noise.
"""

import re

# Regex for ANSI escape codes (terminal colors, cursor movement, etc.)
ANSI_ESCAPE_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Regex for ISO-8601 timestamps common in CI logs: 2026-03-12T14:23:45.1234567Z
CI_TIMESTAMP_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s+", re.MULTILINE)

# GitHub Actions log annotations
GITHUB_ANNOTATION_REGEX = re.compile(r"^##\[(?:group|endgroup|notice|debug|command)\][^\r\n]*$", re.MULTILINE)


def strip_ansi_codes(text: str) -> str:
    """Removes terminal color codes and escape sequences."""
    return ANSI_ESCAPE_REGEX.sub("", text)


def strip_ci_timestamps(text: str) -> str:
    """Removes CI runner timestamps at the start of each line."""
    return CI_TIMESTAMP_REGEX.sub("", text)


def clean_ci_log(raw_log: str) -> str:
    """
    Cleans raw CI log:
    1. Strips ANSI escapes.
    2. Strips timestamps.
    3. Normalizes line endings to \n.
    4. Removes noisy runner group annotations.
    """
    text = strip_ansi_codes(raw_log)
    text = strip_ci_timestamps(text)
    text = GITHUB_ANNOTATION_REGEX.sub("", text)
    # Normalize Windows CRLF
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()
