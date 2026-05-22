"""Overlap diff between consecutive clipboard snapshots."""

from __future__ import annotations


def extract_new_content(previous: str, current: str) -> tuple[str, int]:
    """
    Return (new_text, new_line_count) by finding the longest suffix/prefix overlap.

    When there is no previous snapshot, the full current text is treated as new.
    """
    if not previous.strip():
        lines = current.splitlines()
        return current, len(lines)

    prev_lines = previous.splitlines()
    curr_lines = current.splitlines()
    if not curr_lines:
        return "", 0

    max_overlap = 0
    limit = min(len(prev_lines), len(curr_lines))
    for size in range(limit, 0, -1):
        if prev_lines[-size:] == curr_lines[:size]:
            max_overlap = size
            break

    new_lines = curr_lines[max_overlap:]
    return "\n".join(new_lines), len(new_lines)
