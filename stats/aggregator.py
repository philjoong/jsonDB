"""Aggregate periodic_insights into cached stats tables (phase 4a).

This module intentionally keeps aggregation deterministic and idempotent:
- For each (room_id, period_key, period_type) we rebuild `topic_stats`.
- For each period_key we rebuild `patch_reaction_stats` as an all-rooms aggregate.
- Additionally, we compute an all-rooms aggregate for topics with room_id = NULL.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AggregateResult:
    topic_rows_inserted: int
    patch_rows_inserted: int
    periods_processed: int


def _as_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_json_list(raw: Any) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            val = json.loads(s)
        except json.JSONDecodeError:
            return []
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    return []


def aggregate_stats(conn: sqlite3.Connection) -> AggregateResult:
    """Rebuild topic_stats and patch_reaction_stats from periodic_insights."""
    rows = conn.execute(
        """
        SELECT room_id, period_key, period_type, topics_json, patch_reactions_json
        FROM periodic_insights
        ORDER BY period_key ASC, room_id ASC
        """
    ).fetchall()

    topic_inserted = 0
    patch_inserted = 0
    periods_processed = 0

    # Accumulate per-period all-room topic aggregates.
    overall_topics: dict[tuple[str, str, str | None, str | None, str | None], dict[str, Any]] = {}
    # Accumulate per-period all-room patch aggregates.
    overall_patches: dict[tuple[str, str, str], dict[str, Any]] = {}
    seen_period_keys: set[str] = set()

    for r in rows:
        room_id = str(r["room_id"])
        period_key = str(r["period_key"])
        period_type = str(r["period_type"])
        seen_period_keys.add(period_key)
        periods_processed += 1

        topics = _safe_json_list(r["topics_json"])
        patches = _safe_json_list(r["patch_reactions_json"])

        # Idempotent rebuild: delete then insert for this bucket.
        conn.execute(
            """
            DELETE FROM topic_stats
            WHERE room_id = ? AND period_key = ? AND period_type = ?
            """,
            (room_id, period_key, period_type),
        )

        for t in topics:
            tag = t.get("tag")
            topic_key = t.get("topic_key")
            title = t.get("title")
            mentions = _as_int(t.get("mentions"), 0)
            distinct_nicks = _as_int(t.get("distinct_nicks"), 0)

            # Optional fields that LLM analyzers may emit later.
            messages_referenced = _as_int(
                t.get("messages_referenced"),
                _as_int(len(t.get("quote_refs") or []), 0),
            )

            conn.execute(
                """
                INSERT INTO topic_stats
                    (period_key, period_type, room_id, tag, topic_key, title,
                     mentions, distinct_nicks, messages_referenced)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    period_key,
                    period_type,
                    room_id,
                    str(tag) if tag is not None else None,
                    str(topic_key) if topic_key is not None else None,
                    str(title) if title is not None else None,
                    int(mentions),
                    int(distinct_nicks),
                    int(messages_referenced),
                ),
            )
            topic_inserted += 1

            # overall aggregate key: (period_key, period_type, tag, topic_key, title)
            ok = (
                period_key,
                period_type,
                str(tag) if tag is not None else None,
                str(topic_key) if topic_key is not None else None,
                str(title) if title is not None else None,
            )
            agg = overall_topics.get(ok)
            if agg is None:
                agg = {
                    "mentions": 0,
                    "distinct_nicks": 0,
                    "messages_referenced": 0,
                }
                overall_topics[ok] = agg
            agg["mentions"] += int(mentions)
            agg["distinct_nicks"] += int(distinct_nicks)
            agg["messages_referenced"] += int(messages_referenced)

        for p in patches:
            patch_item = p.get("patch_item") or p.get("item") or p.get("key")
            stance = p.get("stance") or "neutral"
            mentions = _as_int(p.get("mentions"), 0)
            distinct_nicks = _as_int(p.get("distinct_nicks"), 0)
            summary = p.get("summary")
            if not patch_item:
                continue

            pk = (period_key, str(patch_item), str(stance))
            agg = overall_patches.get(pk)
            if agg is None:
                agg = {
                    "mentions": 0,
                    "distinct_nicks": 0,
                    "summary": None,
                }
                overall_patches[pk] = agg
            agg["mentions"] += int(mentions)
            agg["distinct_nicks"] += int(distinct_nicks)
            if summary and not agg["summary"]:
                agg["summary"] = str(summary)

    # Rebuild overall (room_id IS NULL) topic aggregates.
    if overall_topics:
        conn.execute("DELETE FROM topic_stats WHERE room_id IS NULL")
        for (period_key, period_type, tag, topic_key, title), agg in overall_topics.items():
            conn.execute(
                """
                INSERT INTO topic_stats
                    (period_key, period_type, room_id, tag, topic_key, title,
                     mentions, distinct_nicks, messages_referenced)
                VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    period_key,
                    period_type,
                    tag,
                    topic_key,
                    title,
                    int(agg["mentions"]),
                    int(agg["distinct_nicks"]),
                    int(agg["messages_referenced"]),
                ),
            )
            topic_inserted += 1

    # Rebuild patch aggregates (all rooms) per period_key.
    if seen_period_keys:
        conn.executemany(
            "DELETE FROM patch_reaction_stats WHERE period_key = ?",
            [(k,) for k in sorted(seen_period_keys)],
        )
    for (period_key, patch_item, stance), agg in overall_patches.items():
        conn.execute(
            """
            INSERT INTO patch_reaction_stats
                (period_key, patch_item, stance, mentions, distinct_nicks, summary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                period_key,
                patch_item,
                stance,
                int(agg["mentions"]),
                int(agg["distinct_nicks"]),
                agg["summary"],
            ),
        )
        patch_inserted += 1

    conn.commit()
    return AggregateResult(
        topic_rows_inserted=int(topic_inserted),
        patch_rows_inserted=int(patch_inserted),
        periods_processed=int(periods_processed),
    )

