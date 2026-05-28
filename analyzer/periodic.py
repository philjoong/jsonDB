"""Periodic analyzer (phase 3b / 3d).

Primary path: EXAONE 3.5 7.8B Instruct via OpenAI-compatible API (Ollama).
Fallback: deterministic heuristic when LLM is disabled or fails.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from analyzer.bucketizer import Bucket
from analyzer.llm import AnalyzerLLMError, chat_completion_json, create_analyzer_client
from analyzer.prompts import (
    build_system_prompt,
    build_user_prompt,
    format_message_block,
    prompt_fingerprint,
)
from openchat.config import AppSettings

logger = logging.getLogger(__name__)

_VALID_TAGS = frozenset({"bug", "balance", "event", "ops", "meta", "general"})
_VALID_COVERAGE = frozenset({"high", "partial", "low"})

# Override legacy token/stopword definitions with readable Korean-safe rules.
_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_STOPWORDS = {
    "지금",
    "근데",
    "일단",
    "그럼",
    "그리고",
    "그런데",
    "그래서",
    "진짜",
    "그냥",
    "오늘",
    "내일",
    "어제",
    "이번",
    "저번",
}
_DISCOURSE_ONLY = {
    "지금",
    "근데",
    "일단",
    "그럼",
    "그리고",
    "그래서",
    "그러면",
    "아니",
    "음",
    "어",
}
_SUBJECTLESS_VERB_ENDINGS = (
    "합니다",
    "했습니다",
    "됩니다",
    "됐습니다",
    "나갔습니다",
    "갑니다",
    "왔습니다",
    "감사합니다",
)

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


def _looks_like_noise_topic(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    if t in _DISCOURSE_ONLY:
        return True
    if t.endswith(_SUBJECTLESS_VERB_ENDINGS):
        return True
    if len(t) <= 2 and t in {"네", "음", "어"}:
        return True
    return False

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


def _split_transcript_chunks(
    rows: list[dict[str, Any]],
    *,
    max_chars: int,
) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for r in rows:
        line = f"{r['message_id']}\t{r['nick']}\t{r['message_at']}\t{r['body']}\n"
        line_len = len(line)
        if current and size + line_len > max_chars:
            chunks.append(current)
            current = []
            size = 0
        current.append(r)
        size += line_len
    if current:
        chunks.append(current)
    return chunks


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


def _normalize_message_ids(raw: Any, *, limit: int = 30) -> list[int]:
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    seen: set[int] = set()
    for value in raw:
        try:
            mid = int(value)
        except (TypeError, ValueError):
            continue
        if mid in seen:
            continue
        out.append(mid)
        seen.add(mid)
        if len(out) >= limit:
            break
    return out


def _normalize_contexts(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    contexts: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        message_ids = _normalize_message_ids(item.get("message_ids"))
        if not message_ids:
            for key in ("first_message_id", "last_message_id"):
                if item.get(key) is not None:
                    message_ids.extend(_normalize_message_ids([item.get(key)], limit=1))
        if not message_ids:
            continue
        context_id = str(item.get("context_id") or f"ctx_{idx}").strip()[:80]
        if not context_id:
            context_id = f"ctx_{idx}"
        base_id = context_id
        suffix = 2
        while context_id in used_ids:
            context_id = f"{base_id}_{suffix}"
            suffix += 1
        used_ids.add(context_id)

        first_ids = _normalize_message_ids([item.get("first_message_id")], limit=1)
        last_ids = _normalize_message_ids([item.get("last_message_id")], limit=1)
        context: dict[str, Any] = {
            "context_id": context_id,
            "message_ids": message_ids,
            "first_message_id": first_ids[0] if first_ids else message_ids[0],
            "last_message_id": last_ids[0] if last_ids else message_ids[-1],
        }
        label = str(item.get("label") or "").strip()
        if label:
            context["label"] = label[:120]
        summary = str(item.get("summary") or "").strip()
        if summary:
            context["summary"] = summary[:500]
        nicks = item.get("nicks")
        if isinstance(nicks, list):
            clean_nicks = [str(n).strip() for n in nicks if str(n).strip()]
            if clean_nicks:
                context["nicks"] = clean_nicks[:12]
        contexts.append(context)
    return contexts[:20]


def _normalize_context_ids(raw: Any, known_ids: set[str]) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in raw:
        cid = str(value).strip()
        if not cid or cid in seen:
            continue
        if known_ids and cid not in known_ids:
            continue
        out.append(cid)
        seen.add(cid)
        if len(out) >= 5:
            break
    return out


def _normalize_topics(
    raw: Any,
    *,
    room_id: str,
    min_distinct_nicks: int,
    contexts_by_id: dict[str, dict] | None = None,
) -> list[dict]:
    if not isinstance(raw, list):
        return []
    contexts_by_id = contexts_by_id or {}
    known_context_ids = set(contexts_by_id)
    topics: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        if _looks_like_noise_topic(title):
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
        context_ids = _normalize_context_ids(item.get("context_ids"), known_context_ids)
        if context_ids:
            topic["context_ids"] = context_ids
            topic["contexts"] = [contexts_by_id[cid] for cid in context_ids]
        topics.append(topic)
    return topics


def normalize_llm_payload(
    data: dict[str, Any],
    *,
    bucket: Bucket,
    message_count: int,
    min_distinct_nicks: int,
) -> tuple[str | None, list[dict]]:
    coverage = data.get("coverage")
    if coverage is not None:
        cov = str(coverage).strip().lower()
        coverage_out: str | None = cov if cov in _VALID_COVERAGE else "partial"
    else:
        coverage_out = None

    contexts = _normalize_contexts(data.get("conversation_contexts"))
    contexts_by_id = {
        str(ctx["context_id"]): ctx
        for ctx in contexts
        if isinstance(ctx, dict) and ctx.get("context_id")
    }
    topics = _normalize_topics(
        data.get("topics"),
        room_id=bucket.room_id,
        min_distinct_nicks=min_distinct_nicks,
        contexts_by_id=contexts_by_id,
    )
    return coverage_out, topics


def _topic_merge_key(topic: dict[str, Any]) -> tuple[str, str]:
    tag = str(topic.get("tag") or "general").strip().lower()
    text = str(topic.get("topic_key") or topic.get("title") or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return tag, text


def _merge_llm_topics(
    topic_lists: list[list[dict]],
    *,
    min_distinct_nicks: int,
    top_n: int = 12,
) -> list[dict]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for topics in topic_lists:
        for t in topics:
            key = _topic_merge_key(t)
            if not key[1]:
                continue
            agg = merged.get(key)
            if agg is None:
                agg = dict(t)
                agg["mentions"] = 0
                agg["distinct_nicks"] = 0
                agg["quote_refs"] = []
                merged[key] = agg
            agg["mentions"] += max(0, int(t.get("mentions") or 0))
            agg["distinct_nicks"] += max(0, int(t.get("distinct_nicks") or 0))
            if not agg.get("summary") and t.get("summary"):
                agg["summary"] = str(t["summary"])
            context_ids = t.get("context_ids")
            if isinstance(context_ids, list):
                agg_ids = agg.setdefault("context_ids", [])
                for cid in context_ids:
                    cid = str(cid)
                    if cid and cid not in agg_ids:
                        agg_ids.append(cid)
                agg["context_ids"] = agg_ids[:5]
            contexts = t.get("contexts")
            if isinstance(contexts, list):
                agg_contexts = agg.setdefault("contexts", [])
                seen_contexts = {
                    str(ctx.get("context_id"))
                    for ctx in agg_contexts
                    if isinstance(ctx, dict) and ctx.get("context_id")
                }
                for ctx in contexts:
                    if not isinstance(ctx, dict):
                        continue
                    cid = str(ctx.get("context_id") or "")
                    if not cid or cid in seen_contexts:
                        continue
                    agg_contexts.append(ctx)
                    seen_contexts.add(cid)
                agg["contexts"] = agg_contexts[:5]
            refs = t.get("quote_refs")
            if isinstance(refs, list):
                seen = {
                    tuple(sorted(ref.items()))
                    for ref in agg.get("quote_refs", [])
                    if isinstance(ref, dict)
                }
                for ref in refs:
                    if not isinstance(ref, dict):
                        continue
                    marker = tuple(sorted(ref.items()))
                    if marker not in seen:
                        agg["quote_refs"].append(ref)
                        seen.add(marker)
            agg["quote_refs"] = agg["quote_refs"][:3]

    out = sorted(
        merged.values(),
        key=lambda t: (-int(t.get("distinct_nicks") or 0), -int(t.get("mentions") or 0)),
    )[:top_n]
    for t in out:
        t["underrepresented"] = int(t.get("distinct_nicks") or 0) < min_distinct_nicks
        if not t.get("quote_refs"):
            t.pop("quote_refs", None)
    return out


def _call_analyzer_llm(
    client: Any,
    *,
    bucket: Bucket,
    settings: AppSettings,
    room_label: str,
    rows: list[dict[str, Any]],
    note: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    system = build_system_prompt(
        min_distinct_nicks=settings.min_distinct_nicks,
        prompt_version=settings.analyzer_prompt_version,
    )
    block = format_message_block(rows)
    user = build_user_prompt(
        bucket,
        room_label=room_label,
        messages_block=block,
        message_count=len(rows),
        truncated_note=note,
    )
    started = time.perf_counter()
    data = chat_completion_json(
        client,
        model=settings.analyzer_model,
        system=system,
        user=user,
        temperature=settings.analyzer_temperature,
        timeout_seconds=min(settings.analyzer_timeout_seconds, 180.0),
    )
    elapsed = time.perf_counter() - started
    return data, {
        "sent_messages": len(rows),
        "sent_chars": len(block),
        "prompt_chars": len(system) + len(user),
        "elapsed": elapsed,
    }


def analyze_bucket_llm(
    conn: sqlite3.Connection,
    bucket: Bucket,
    settings: AppSettings,
    *,
    room_label: str | None = None,
    client: Any | None = None,
    progress: Callable[[str], None] | None = None,
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

    label = room_label or bucket.room_id
    max_transcript_chars = min(settings.analyzer_max_transcript_chars, 8_000)

    own_client = client is None
    if own_client:
        client = create_analyzer_client(
            api_base=settings.openai_api_base,
            api_key=settings.analyzer_api_key,
        )

    assert client is not None
    raw_chars = len(format_message_block(rows))
    chunk_topic_lists: list[list[dict]] = []
    metrics: list[dict[str, Any]] = []
    coverage_values: list[str] = []
    empty_chunks: list[int] = []
    try:
        if raw_chars > max_transcript_chars:
            chunks = _split_transcript_chunks(rows, max_chars=max_transcript_chars)
            for idx, chunk in enumerate(chunks, start=1):
                note = (
                    f"large bucket chunk {idx}/{len(chunks)}; "
                    f"analyze only the provided chat lines in this chunk"
                )
                chunk_chars = len(format_message_block(chunk))
                if progress:
                    progress(
                        f"analyze bucket {bucket.period_key} chunk {idx}/{len(chunks)} "
                        f"messages={len(chunk)} chars={chunk_chars}"
                    )
                data, metric = _call_analyzer_llm(
                    client,
                    bucket=bucket,
                    settings=settings,
                    room_label=label,
                    rows=chunk,
                    note=note,
                )
                coverage, chunk_topics = normalize_llm_payload(
                    data,
                    bucket=bucket,
                    message_count=len(chunk),
                    min_distinct_nicks=settings.min_distinct_nicks,
                )
                if coverage:
                    coverage_values.append(coverage)
                chunk_topic_lists.append(chunk_topics)
                metrics.append(metric)
                if not chunk_topics:
                    empty_chunks.append(idx)
                if progress:
                    progress(
                        f"analyze bucket {bucket.period_key} chunk {idx}/{len(chunks)} "
                        f"finished topics={len(chunk_topics)} elapsed={metric['elapsed']:.1f}s"
                    )
            topics = _merge_llm_topics(
                chunk_topic_lists,
                min_distinct_nicks=settings.min_distinct_nicks,
                top_n=12,
            )
            coverage = "partial" if "partial" in coverage_values else (
                coverage_values[0] if coverage_values else None
            )
        else:
            if progress:
                progress(
                    f"analyze bucket {bucket.period_key} single chunk "
                    f"messages={len(rows)} chars={raw_chars}"
                )
            data, metric = _call_analyzer_llm(
                client,
                bucket=bucket,
                settings=settings,
                room_label=label,
                rows=rows,
                note=None,
            )
            metrics.append(metric)
            coverage, topics = normalize_llm_payload(
                data,
                bucket=bucket,
                message_count=message_count,
                min_distinct_nicks=settings.min_distinct_nicks,
            )
            if progress:
                progress(
                    f"analyze bucket {bucket.period_key} single chunk "
                    f"finished topics={len(topics)} elapsed={metric['elapsed']:.1f}s"
                )
    except AnalyzerLLMError as exc:
        sent_messages = sum(int(m["sent_messages"]) for m in metrics)
        sent_chars = sum(int(m["sent_chars"]) for m in metrics)
        elapsed = sum(float(m["elapsed"]) for m in metrics)
        empty_note = f", empty_chunks={empty_chunks}" if empty_chunks else ""
        raise AnalyzerLLMError(
            f"{exc} (bucket={bucket.room_id} {bucket.period_key}, "
            f"messages={message_count}, sent_messages={sent_messages}, "
            f"sent_chars={sent_chars}, chunks={len(metrics)}{empty_note}, "
            f"elapsed={elapsed:.1f}s)"
        ) from exc

    if message_count > 0 and not topics:
        sent_messages = sum(int(m["sent_messages"]) for m in metrics)
        sent_chars = sum(int(m["sent_chars"]) for m in metrics)
        elapsed = sum(float(m["elapsed"]) for m in metrics)
        empty_note = f", empty_chunks={empty_chunks}" if empty_chunks else ""
        raise AnalyzerLLMError(
            f"LLM returned no topics for {bucket.room_id} {bucket.period_key} "
            f"with {message_count} messages "
            f"(sent_messages={sent_messages}, sent_chars={sent_chars}, "
            f"chunks={len(metrics)}{empty_note}, elapsed={elapsed:.1f}s)"
        )

    ph = prompt_fingerprint(
        bucket=bucket,
        model_label=model_label,
        prompt_version=settings.analyzer_prompt_version,
        min_distinct_nicks=settings.min_distinct_nicks,
        message_count=message_count,
        truncated=raw_chars > max_transcript_chars,
    )
    return AnalyzedInsight(
        message_count=message_count,
        coverage=coverage,
        topics=topics,
        patch_reactions=[],
        prompt_hash=ph,
        analyzer_backend="llm",
    )


def analyze_bucket(
    conn: sqlite3.Connection,
    bucket: Bucket,
    settings: AppSettings,
    *,
    room_label: str | None = None,
    force_heuristic: bool = False,
    top_n: int = 12,
    progress: Callable[[str], None] | None = None,
) -> AnalyzedInsight:
    """Run LLM analysis when enabled; on failure optionally fall back to heuristic."""
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
            room_label=room_label,
            progress=progress,
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
    """Produce `topics` for a bucket (no external LLM)."""
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
        if _looks_like_noise_topic(term):
            continue
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
