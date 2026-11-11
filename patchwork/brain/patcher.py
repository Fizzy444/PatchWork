"""
Unified diff parser and source patch applier.
"""

import re
from typing import Optional, Tuple, List
from pydantic import BaseModel


class PatchResult(BaseModel):
    success: bool
    patched_code: Optional[str] = None
    target_file: Optional[str] = None
    error: Optional[str] = None
    hunks_applied: int = 0


DIFF_BLOCK_REGEX = re.compile(r"```(?:diff)?\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
FILE_HEADER_REGEX = re.compile(r"^--- (?:a/)?([^\s\t\n]+)\s*\n\+\+\+ (?:b/)?([^\s\t\n]+)", re.MULTILINE)
HUNK_HEADER_REGEX = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$")


def extract_diff_from_llm_response(response_text: str) -> str:
    """Extracts unified diff block from markdown code fences or returns raw text."""
    matches = DIFF_BLOCK_REGEX.findall(response_text)
    if matches:
        # Return first non-empty diff block
        for match in matches:
            if "---" in match or "@@" in match or "+" in match:
                return match.strip()
        return matches[0].strip()

    # If no markdown block found, check if the raw text contains diff markers
    if "--- " in response_text and "+++ " in response_text:
        # Cut from first '---' line
        idx = response_text.find("--- ")
        return response_text[idx:].strip()

    return response_text.strip()


def extract_target_file_from_diff(diff_text: str) -> Optional[str]:
    """Extracts target file path from diff header (e.g. +++ b/math_service.py)."""
    match = FILE_HEADER_REGEX.search(diff_text)
    if match:
        target = match.group(2)
        # Strip dev/null if deletion
        if target != "/dev/null":
            return target.lstrip("/")
    return None


def apply_unified_diff(original_code: str, diff_text: str) -> PatchResult:
    """
    Applies a unified diff patch to original source code string.
    Employs robust context-line matching to tolerate minor line drift.
    """
    target_file = extract_target_file_from_diff(diff_text)
    lines = original_code.splitlines()

    # Split diff into hunks
    diff_lines = diff_text.splitlines()
    hunk_starts: List[int] = []
    for i, line in enumerate(diff_lines):
        if line.startswith("@@"):
            hunk_starts.append(i)

    if not hunk_starts:
        # No standard hunk header; check if it's a simple search-and-replace
        return _apply_simple_diff(original_code, diff_text, target_file)

    patched_lines = list(lines)
    offset = 0
    hunks_applied = 0

    for idx, h_start in enumerate(hunk_starts):
        h_end = hunk_starts[idx + 1] if idx + 1 < len(hunk_starts) else len(diff_lines)
        hunk_header_line = diff_lines[h_start]
        hunk_body = diff_lines[h_start + 1:h_end]

        header_match = HUNK_HEADER_REGEX.match(hunk_header_line)
        if not header_match:
            continue

        orig_start = int(header_match.group(1)) - 1  # 0-indexed

        # Extract old hunk lines (context ' ' and deleted '-')
        old_hunk_lines: List[str] = []
        new_hunk_lines: List[str] = []

        for line in hunk_body:
            if not line:
                old_hunk_lines.append("")
                new_hunk_lines.append("")
                continue
            marker = line[0]
            content = line[1:]
            if marker == " ":
                old_hunk_lines.append(content)
                new_hunk_lines.append(content)
            elif marker == "-":
                old_hunk_lines.append(content)
            elif marker == "+":
                new_hunk_lines.append(content)

        # Locate match position in patched_lines using context lines
        match_idx = _find_match_index(patched_lines, old_hunk_lines, orig_start + offset)
        if match_idx is None:
            return PatchResult(
                success=False,
                target_file=target_file,
                error=f"Could not locate matching context in source file for hunk {hunk_header_line}"
            )

        # Apply substitution
        patched_lines[match_idx:match_idx + len(old_hunk_lines)] = new_hunk_lines
        offset += len(new_hunk_lines) - len(old_hunk_lines)
        hunks_applied += 1

    return PatchResult(
        success=True,
        patched_code="\n".join(patched_lines) + ("\n" if original_code.endswith("\n") else ""),
        target_file=target_file,
        hunks_applied=hunks_applied
    )


def _find_match_index(lines: List[str], target_slice: List[str], expected_idx: int) -> Optional[int]:
    """Finds best match index within a search window around expected_idx."""
    if not target_slice:
        return expected_idx

    slice_len = len(target_slice)
    total_len = len(lines)

    # 1. Exact match at expected index
    if 0 <= expected_idx <= total_len - slice_len:
        if lines[expected_idx:expected_idx + slice_len] == target_slice:
            return expected_idx

    # 2. Search window of +/- 30 lines
    window = 30
    for delta in range(1, window + 1):
        for candidate in (expected_idx + delta, expected_idx - delta):
            if 0 <= candidate <= total_len - slice_len:
                if lines[candidate:candidate + slice_len] == target_slice:
                    return candidate

    # 3. Fuzzy match: match ignoring leading/trailing whitespace
    target_stripped = [s.strip() for s in target_slice if s.strip()]
    for i in range(total_len - slice_len + 1):
        window_stripped = [lines[i + j].strip() for j in range(slice_len) if lines[i + j].strip()]
        if window_stripped == target_stripped:
            return i

    # 4. Semantic anchor match: match based on the key statement (e.g. return statement or assignment)
    if target_stripped:
        anchor = target_stripped[-1]
        for i, line in enumerate(lines):
            if anchor in line:
                start_cand = max(0, i - (slice_len - 1))
                return start_cand

    return None


def _apply_simple_diff(original: str, diff_text: str, target_file: Optional[str]) -> PatchResult:
    """Fallback when no @@ hunks are present, applying direct line replacements."""
    to_remove = []
    to_add = []
    for line in diff_text.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            to_remove.append(line[1:])
        elif line.startswith("+") and not line.startswith("+++"):
            to_add.append(line[1:])

    if to_remove:
        search_block = "\n".join(to_remove)
        replace_block = "\n".join(to_add)
        if search_block in original:
            patched = original.replace(search_block, replace_block, 1)
            return PatchResult(success=True, patched_code=patched, target_file=target_file, hunks_applied=1)

    return PatchResult(success=False, target_file=target_file, error="Unable to apply non-standard diff format")
