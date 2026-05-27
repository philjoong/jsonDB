"""Periodic analyzer (phase 3b / 3d).

Primary path: EXAONE 3.5 7.8B Instruct via OpenAI-compatible API (Ollama).
Fallback: deterministic heuristic when LLM is disabled or fails.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from analyzer.bucketizer import Bucket
from analyzer.llm import AnalyzerLLMError, chat_completion_json, create_analyzer_client
from analyzer.prompts import (
    build_system_prompt,
    build_user_prompt,
    format_message_block,
    prompt_fingerprint,
)
from context.loader import ContextBundle
from openchat.config import AppSettings

logger = logging.getLogger(__name__)

_VALID_TAGS = frozenset({"bug", "balance", "event", "ops", "meta", "general"})
_VALID_STANCES = frozenset({"negative", "neutral", "positive", "mixed"})
_VALID_COVERAGE = frozenset({"high", "partial", "low"})

_WORD_RE = re.compile(r"[0-9A-Za-z가-힣_]{2,}")

_STOPWORDS = {
    "그리고",
    "그런데",
    "그래서",
    "진짜",
    "그냥",
    "오늘",
    "내일",
    "어제",
    "ㅋㅋ",
    "ㅎㅎ",
}

@dataclass(frozen=True)
class AnalyzedInsight:
    message_count: int
    coverage: str | None
    topics: list[dict]
    patch_reactions: list[dict]
    prompt_hash: str
    analyzer_backend: str = "llm"


def _prompt_fingerprint_heuristic(bucket: Bucket, model: str, version: str) -> str:
    payload = f"{bucket.period_type}\0{bucket.period_key}\0{model}\0{version}\0heuristic-v1"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fetch_bucket_messages(
    conn: sqlite3.Connection,
    bucket: Bucket,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT message_id, nick, message_at, body
        FROM messages
        WHERE room_id = ?
          AND message_at >= ?
          AND message_at < ?
        ORDER BY message_at ASC
        """,
        (
            bucket.room_id,
            bucket.period_start.isoformat(timespec="seconds"),
            bucket.period_end.isoformat(timespec="seconds"),
        ),
    ).fetchall()
    return [
        {
            "message_id": int(r["message_id"]),
            "nick": str(r["nick"]),
            "message_at": str(r["message_at"]),
            "body": str(r["body"]),
        }
        for r in rows
    ]


def _truncate_transcript(
    rows: list[dict[str, Any]],
    *,
    max_chars: int,
) -> tuple[list[dict[str, Any]], str | None]:
    block = format_message_block(rows)
    if len(block) <= max_chars:
        return rows, None

    head: list[dict[str, Any]] = []
    tail: list[dict[str, Any]] = []
    head_budget = max_chars // 4
    tail_budget = max_chars - head_budget - 80

    size = 0
    for r in rows:
        line = f"{r['message_id']}\t{r['nick']}\t{r['message_at']}\t{r['body']}\n"
        if size + len(line) > head_budget:
            break
        head.append(r)
        size += len(line)

    size = 0
    for r in reversed(rows):
        line = f"{r['message_id']}\t{r['nick']}\t{r['message_at']}\t{r['body']}\n"
        if size + len(line) > tail_budget:
            break
        tail.insert(0, r)
        size += len(line)

    if not head and not tail:
        return rows[:1], "transcript truncated to fit context window"

    merged_ids = {r["message_id"] for r in head} | {r["message_id"] for r in tail}
    merged = [r for r in rows if r["message_id"] in merged_ids]
    omitted = len(rows) - len(merged)
    note = (
        f"transcript truncated ({omitted} middle messages omitted, "
        f"{len(merged)} of {len(rows)} kept) for context limit"
    )
    return merged, note


def _normalize_quote_refs(raw: Any, *, room_id: str) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("message_id") is not None:
            try:
                out.append({"message_id": int(item["message_id"])})
            except (TypeError, ValueError):
                continue
        elif item.get("search_phrase"):
            ref: dict[str, Any] = {"search_phrase": str(item["search_phrase"])}
            ref["room_id"] = str(item.get("room_id") or room_id)
            out.append(ref)
    return out


def _normalize_topics(
    raw: Any,
    *,
    room_id: str,
    min_distinct_nicks: int,
) -> list[dict]:
    if not isinstance(raw, list):
        return []
    topics: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        tag = str(item.get("tag") or "general").strip().lower()
        if tag not in _VALID_TAGS:
            tag = "general"
        mentions = max(0, int(item.get("mentions") or 0))
        distinct = max(0, int(item.get("distinct_nicks") or 0))
        under = item.get("underrepresented")
        if under is None:
            under = distinct < min_distinct_nicks
        summary = str(item.get("summary") or "").strip()
        topic: dict[str, Any] = {
            "tag": tag,
            "title": title,
            "topic_key": str(item.get("topic_key") or title)[:120],
            "mentions": mentions,
            "distinct_nicks": distinct,
            "underrepresented": bool(under),
        }
        if summary:
            topic["summary"] = summary[:500]
        if item.get("first_seen"):
            topic["first_seen"] = str(item["first_seen"])
        refs = _normalize_quote_refs(item.get("quote_refs"), room_id=room_id)
        if refs:
            topic["quote_refs"] = refs
        topics.append(topic)
    return topics


def _normalize_patch_reactions(raw: Any, *, room_id: str) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        patch_item = str(item.get("patch_item") or "").strip()
        if not patch_item:
            continue
        stance = str(item.get("stance") or "neutral").strip().lower()
        if stance not in _VALID_STANCES:
            stance = "neutral"
        pr: dict[str, Any] = {
            "patch_item": patch_item,
            "stance": stance,
            "mentions": max(0, int(item.get("mentions") or 0)),
            "distinct_nicks": max(0, int(item.get("distinct_nicks") or 0)),
            "summary": str(item.get("summary") or "").strip(),
        }
        refs = _normalize_quote_refs(item.get("quote_refs"), room_id=room_id)
        if refs:
            pr["quote_refs"] = refs
        out.append(pr)
    return out


def normalize_llm_payload(
    data: dict[str, Any],
    *,
    bucket: Bucket,
    message_count: int,
    min_distinct_nicks: int,
) -> tuple[str | None, list[dict], list[dict]]:
    coverage = data.get("coverage")
    if coverage is not None:
        cov = str(coverage).strip().lower()
        coverage_out: str | None = cov if cov in _VALID_COVERAGE else "partial"
    else:
        coverage_out = None

    topics = _normalize_topics(
        data.get("topics"),
        room_id=bucket.room_id,
        min_distinct_nicks=min_distinct_nicks,
    )
    patches = _normalize_patch_reactions(
        data.get("patch_reactions"),
        room_id=bucket.room_id,
    )
    return coverage_out, topics, patches


def analyze_bucket_llm(
    conn: sqlite3.Connection,
    bucket: Bucket,
    settings: AppSettings,
    *,
    context: ContextBundle | None = None,
    room_label: str | None = None,
    client: Any | None = None,
) -> AnalyzedInsight:
    """Analyze a bucket with EXAONE (OpenAI-compatible chat/completions)."""
    rows = fetch_bucket_messages(conn, bucket)
    message_count = len(rows)
    model_label = settings.analyzer_model_label

    if message_count == 0:
        ph = prompt_fingerprint(
            bucket=bucket,
            model_label=model_label,
            prompt_version=settings.analyzer_prompt_version,
            min_distinct_nicks=settings.min_distinct_nicks,
            message_count=0,
            truncated=False,
        )
        return AnalyzedInsight(
            message_count=0,
            coverage="no_messages",
            topics=[],
            patch_reactions=[],
            prompt_hash=ph,
            analyzer_backend="llm",
        )

    ctx = context or ContextBundle(patchnotes="", roadmap="")
    label = room_label or bucket.room_id
    trimmed, trunc_note = _truncate_transcript(
        rows,
        max_chars=settings.analyzer_max_transcript_chars,
    )
    system = build_system_prompt(
        min_distinct_nicks=settings.min_distinct_nicks,
        prompt_version=settings.analyzer_prompt_version,
    )
    user = build_user_prompt(
        bucket,
        room_label=label,
        messages_block=format_message_block(trimmed),
        message_count=message_count,
        patchnotes=ctx.patchnotes,
        roadmap=ctx.roadmap,
        truncated_note=trunc_note,
    )

    own_client = client is None
    if own_client:
        client = create_analyzer_client(
            api_base=settings.openai_api_base,
            api_key=settings.analyzer_api_key,
        )

    assert client is not None
    data = chat_completion_json(
        client,
        model=settings.analyzer_model,
        system=system,
        user=user,
        temperature=settings.analyzer_temperature,
        timeout_seconds=settings.analyzer_timeout_seconds,
    )

    coverage, topics, patches = normalize_llm_payload(
        data,
        bucket=bucket,
        message_count=message_count,
        min_distinct_nicks=settings.min_distinct_nicks,
    )

    ph = prompt_fingerprint(
        bucket=bucket,
        model_label=model_label,
        prompt_version=settings.analyzer_prompt_version,
        min_distinct_nicks=settings.min_distinct_nicks,
        message_count=message_count,
        truncated=trunc_note is not None,
    )
    return AnalyzedInsight(
        message_count=message_count,
        coverage=coverage,
        topics=topics,
        patch_reactions=patches,
        prompt_hash=ph,
        analyzer_backend="llm",
    )


def analyze_bucket(
    conn: sqlite3.Connection,
    bucket: Bucket,
    settings: AppSettings,
    *,
    context: ContextBundle | None = None,
    room_label: str | None = None,
    force_heuristic: bool = False,
    top_n: int = 12,
) -> AnalyzedInsight:
    """
    Run LLM analysis when enabled; on failure optionally fall back to heuristic.
    """
    use_llm = (
        settings.analyzer_use_llm
        and not force_heuristic
        and (settings.analyzer_provider or "").lower() not in ("heuristic", "none", "off")
    )
    if not use_llm:
        return analyze_bucket_heuristic(
            conn,
            bucket,
            tz=settings.tz,
            top_n=top_n,
            analyzer_model=settings.analyzer_model_label,
            analyzer_version=settings.analyzer_prompt_version,
        )

    try:
        return analyze_bucket_llm(
            conn,
            bucket,
            settings,
            context=context,
            room_label=room_label,
        )
    except AnalyzerLLMError as exc:
        if not settings.analyzer_fallback_heuristic:
            raise
        logger.warning(
            "LLM analyze failed for %s %s: %s — using heuristic fallback",
            bucket.room_id,
            bucket.period_key,
            exc,
        )
        insight = analyze_bucket_heuristic(
            conn,
            bucket,
            tz=settings.tz,
            top_n=top_n,
            analyzer_model=settings.analyzer_model_label,
            analyzer_version=settings.analyzer_prompt_version,
        )
        return AnalyzedInsight(
            message_count=insight.message_count,
            coverage=insight.coverage or "llm_fallback",
            topics=insight.topics,
            patch_reactions=insight.patch_reactions,
            prompt_hash=insight.prompt_hash,
            analyzer_backend="heuristic_fallback",
        )


def _tokenize(text: str) -> list[str]:
    tokens = [m.group(0) for m in _WORD_RE.finditer(text)]
    cleaned: list[str] = []
    for t in tokens:
        tt = t.strip().lower()
        if not tt:
            continue
        if tt in _STOPWORDS:
            continue
        cleaned.append(tt)
    return cleaned


def analyze_bucket_heuristic(
    conn: sqlite3.Connection,
    bucket: Bucket,
    *,
    tz: ZoneInfo | str = "Asia/Seoul",
    top_n: int = 12,
    analyzer_model: str = "heuristic",
    analyzer_version: str = "v1",
) -> AnalyzedInsight:
    """
    Produce `topics` and `patch_reactions` for a bucket (no external LLM).
    topics[] fields: tag, title, mentions, distinct_nicks (plus topic_key).
    """
    if isinstance(tz, str):
        tz = ZoneInfo(tz)

    rows = conn.execute(
        """
        SELECT nick, body
        FROM messages
        WHERE room_id = ?
          AND message_at >= ?
          AND message_at < ?
        ORDER BY message_at ASC
        """,
        (
            bucket.room_id,
            bucket.period_start.isoformat(timespec="seconds"),
            bucket.period_end.isoformat(timespec="seconds"),
        ),
    ).fetchall()

    message_count = len(rows)
    if message_count == 0:
        prompt_hash = _prompt_fingerprint_heuristic(bucket, analyzer_model, analyzer_version)
        return AnalyzedInsight(
            message_count=0,
            coverage="no_messages",
            topics=[],
            patch_reactions=[],
            prompt_hash=prompt_hash,
            analyzer_backend="heuristic",
        )

    term_mentions: dict[str, int] = {}
    term_nicks: dict[str, set[str]] = {}

    for r in rows:
        nick = str(r["nick"])
        body = str(r["body"])
        tokens = _tokenize(body)
        seen_in_message: set[str] = set()
        for t in tokens:
            term_mentions[t] = term_mentions.get(t, 0) + 1
            seen_in_message.add(t)
        for t in seen_in_message:
            s = term_nicks.get(t)
            if s is None:
                s = set()
                term_nicks[t] = s
            s.add(nick)

    ranked = sorted(
        term_mentions.items(),
        key=lambda kv: (kv[1], len(term_nicks.get(kv[0], set())), kv[0]),
        reverse=True,
    )[: max(0, int(top_n))]

    topics: list[dict] = []
    for term, mentions in ranked:
        nicks = term_nicks.get(term, set())
        topics.append(
            {
                "tag": "general",
                "topic_key": term,
                "title": term,
                "mentions": int(mentions),
                "distinct_nicks": int(len(nicks)),
            }
        )

    prompt_hash = _prompt_fingerprint_heuristic(bucket, analyzer_model, analyzer_version)
    return AnalyzedInsight(
        message_count=message_count,
        coverage=None,
        topics=topics,
        patch_reactions=[],
        prompt_hash=prompt_hash,
        analyzer_backend="heuristic",
    )
