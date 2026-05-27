"""OpenAI-compatible client for Periodic Analyzer (EXAONE via Ollama, etc.)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import APIConnectionError, APIStatusError, OpenAI

logger = logging.getLogger(__name__)


class AnalyzerLLMError(RuntimeError):
    """LLM call or response parsing failed."""


_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*([\s\S]*?)\s*```",
    re.IGNORECASE,
)
_PIPE_UNION_RE = re.compile(
    r'("(?:[^"\\]|\\.)*")\s*\|\s*"(?:[^"\\]|\\.)*"'
)


def create_analyzer_client(
    *,
    api_base: str,
    api_key: str,
) -> OpenAI:
    return OpenAI(
        base_url=api_base.rstrip("/"),
        api_key=api_key or "ollama",
    )


def _extract_json_text(content: str) -> str:
    text = (content or "").strip().lstrip("\ufeff")
    if not text:
        raise AnalyzerLLMError("Empty LLM response")
    if text.startswith("{"):
        return text
    m = _JSON_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    raise AnalyzerLLMError("Response does not contain JSON object")


def _strip_pipe_unions(s: str) -> str:
    """Turn `"a" | "b" | "c"` into `"a"` (schema copy mistake)."""
    prev = None
    while prev != s:
        prev = s
        s = _PIPE_UNION_RE.sub(r"\1", s)
    return s


def _repair_json_text(raw: str) -> str:
    s = raw.strip()
    s = _strip_pipe_unions(s)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
    s = re.sub(r"//[^\n]*", "", s)
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    return s


def _balance_brackets(raw: str) -> str:
    """Close truncated JSON when the model stops mid-object."""
    s = raw.rstrip()
    if s.count("{") > s.count("}"):
        s += "}" * (s.count("{") - s.count("}"))
    if s.count("[") > s.count("]"):
        s += "]" * (s.count("[") - s.count("]"))
    return s


def _iter_json_candidates(raw: str) -> list[str]:
    repaired = _repair_json_text(raw)
    candidates: list[str] = []
    if repaired != raw:
        candidates.append(repaired)
    candidates.append(raw)
    balanced = _balance_brackets(repaired)
    if balanced not in candidates:
        candidates.append(balanced)
    balanced_raw = _balance_brackets(raw)
    if balanced_raw not in candidates:
        candidates.append(balanced_raw)
    return candidates


def parse_json_object(content: str) -> dict[str, Any]:
    raw = _extract_json_text(content)
    last_err: str | None = None
    for candidate in _iter_json_candidates(raw):
        try:
            val = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_err = str(exc)
            continue
        if not isinstance(val, dict):
            last_err = "root must be a JSON object"
            continue
        return val

    snippet = raw[:500].replace("\n", "\\n")
    logger.warning("LLM JSON parse failed (%s). Snippet: %s", last_err, snippet)
    raise AnalyzerLLMError(f"Invalid JSON from LLM: {last_err or 'unknown'}")


_JSON_RETRY_USER = (
    "Your previous reply was not valid JSON (RFC 8259). "
    "Reply with ONLY one corrected JSON object. "
    "Use double quotes for strings. No markdown. No comments. "
    "Do not use | union syntax inside values. No trailing commas. "
    "Keep at most 12 topics; omit quote_refs if needed to finish the JSON."
)


def chat_completion_json(
    client: OpenAI,
    *,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.2,
    timeout_seconds: float = 600.0,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """Call chat/completions and parse a JSON object from the assistant message."""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_content = ""
    last_error: AnalyzerLLMError | None = None

    for attempt in range(max(1, int(max_attempts))):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature if attempt == 0 else min(temperature, 0.1),
                response_format={"type": "json_object"},
                timeout=timeout_seconds,
            )
        except APIConnectionError as exc:
            raise AnalyzerLLMError(
                f"Cannot reach analyzer API at {client.base_url!s}: {exc}"
            ) from exc
        except APIStatusError as exc:
            raise AnalyzerLLMError(
                f"Analyzer API error ({exc.status_code}): {exc}"
            ) from exc
        except Exception as exc:
            raise AnalyzerLLMError(f"Analyzer API call failed: {exc}") from exc

        if not resp.choices:
            raise AnalyzerLLMError("Analyzer API returned no choices")
        message = resp.choices[0].message
        last_content = (message.content if message else None) or ""
        if not last_content.strip():
            last_error = AnalyzerLLMError("Analyzer API returned empty content")
            continue

        try:
            return parse_json_object(last_content)
        except AnalyzerLLMError as exc:
            last_error = exc
            if attempt + 1 >= max_attempts:
                break
            logger.info(
                "JSON parse failed (attempt %s/%s), retrying with correction prompt",
                attempt + 1,
                max_attempts,
            )
            messages = [
                *messages,
                {"role": "assistant", "content": last_content},
                {"role": "user", "content": _JSON_RETRY_USER},
            ]

    raise last_error or AnalyzerLLMError("Failed to obtain valid JSON from LLM")
