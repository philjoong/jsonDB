"""KakaoTalk window title normalization and matching."""

from __future__ import annotations

import re

# Trailing unread count: "방 제목 (3)"
_UNREAD_SUFFIX = re.compile(r"\s*\(\d+\)\s*$")


def normalize_title(title: str) -> str:
    """Remove trailing (N) unread suffix for exact match against rooms config."""
    return _UNREAD_SUFFIX.sub("", title).strip()


def titles_match(canonical_title: str, window_title: str) -> bool:
    """True when window title equals canonical after normalization."""
    return normalize_title(window_title) == canonical_title.strip()
