"""Load external context documents (phase 5b)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ContextBundle:
    patchnotes: str
    roadmap: str


def _read_text_optional(path: Path) -> str:
    try:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")
    except Exception:
        # Context is optional; keep pipeline running.
        return ""


def load_context(*, patchnotes_path: Path, roadmap_path: Path) -> ContextBundle:
    return ContextBundle(
        patchnotes=_read_text_optional(patchnotes_path),
        roadmap=_read_text_optional(roadmap_path),
    )


def excerpt_for_llm(text: str, *, max_chars: int = 6000) -> str:
    """Trim context files for Reporter LLM input (head + tail hint if truncated)."""
    raw = (text or "").strip()
    if not raw:
        return ""
    limit = max(500, int(max_chars))
    if len(raw) <= limit:
        return raw
    head = limit - 120
    return (
        raw[:head]
        + f"\n\n… (중략, 원문 {len(raw):,}자 중 앞 {head:,}자만 포함) …\n"
    )
