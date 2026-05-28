"""Assemble structured input for Reporter LLM (no raw chat)."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from context.loader import ContextBundle, excerpt_for_llm
from openchat.config import AppSettings
from report.scope import ReportScope, bucket_sql_in_clause
from report.update_notes_web import (
    gather_update_notes_for_rooms,
    room_notes_to_dict,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReporterPayload:
    """JSON-serializable bundle passed to the Reporter LLM."""

    meta: dict[str, Any]
    topic_stats: list[dict[str, Any]]
    patch_stats: list[dict[str, Any]]
    insights: list[dict[str, Any]]
    update_notes_by_room: list[dict[str, Any]]
    roadmap_excerpt: str


def _sort_key_topic(row: dict[str, Any]) -> tuple:
    return (
        -int(row.get("mentions") or 0),
        -int(row.get("distinct_nicks") or 0),
        str(row.get("period_key") or ""),
    )


def _sort_key_patch(row: dict[str, Any]) -> tuple:
    return (
        -int(row.get("mentions") or 0),
        -int(row.get("distinct_nicks") or 0),
        str(row.get("period_key") or ""),
    )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _compact_topics(topics: list[Any], *, limit: int = 5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(topics, list):
        return out
    for t in topics[:limit]:
        if not isinstance(t, dict):
            continue
        entry: dict[str, Any] = {
            "tag": t.get("tag"),
            "title": t.get("title"),
            "topic_key": t.get("topic_key"),
            "mentions": t.get("mentions"),
            "distinct_nicks": t.get("distinct_nicks"),
            "underrepresented": t.get("underrepresented"),
        }
        summary = t.get("summary")
        if summary:
            entry["summary"] = summary
        refs = t.get("quote_refs")
        if isinstance(refs, list) and refs:
            entry["quote_refs"] = refs[:3]
        context_ids = t.get("context_ids")
        if isinstance(context_ids, list) and context_ids:
            entry["context_ids"] = [str(c) for c in context_ids[:5]]
        contexts = t.get("contexts")
        if isinstance(contexts, list) and contexts:
            compact_contexts: list[dict[str, Any]] = []
            for ctx in contexts[:5]:
                if not isinstance(ctx, dict):
                    continue
                compact_ctx: dict[str, Any] = {
                    "context_id": ctx.get("context_id"),
                    "label": ctx.get("label"),
                    "summary": ctx.get("summary"),
                    "message_ids": ctx.get("message_ids"),
                    "first_message_id": ctx.get("first_message_id"),
                    "last_message_id": ctx.get("last_message_id"),
                }
                nicks = ctx.get("nicks")
                if isinstance(nicks, list) and nicks:
                    compact_ctx["nicks"] = nicks[:8]
                compact_contexts.append(
                    {k: v for k, v in compact_ctx.items() if v not in (None, "", [])}
                )
            if compact_contexts:
                entry["contexts"] = compact_contexts
        out.append(entry)
    return out


def _room_ids_for_report(
    scope: ReportScope,
    topics: list[dict[str, Any]],
    insights: list[dict[str, Any]],
    settings: AppSettings,
) -> set[str]:
    ids: set[str] = set()
    for b in scope.buckets:
        ids.add(b.room_id)
    for t in topics:
        rid = t.get("room_id")
        if rid:
            ids.add(str(rid))
    for ins in insights:
        rid = ins.get("room_id")
        if rid:
            ids.add(str(rid))
    if not ids:
        ids = {r.id for r in settings.rooms if r.update_notes_url}
    return ids


def _load_insights(
    conn: sqlite3.Connection,
    scope: ReportScope,
    *,
    cutoff_iso: str | None,
) -> list[dict[str, Any]]:
    if scope.buckets:
        in_clause, params = bucket_sql_in_clause(scope.buckets)
        rows = conn.execute(
            f"""
            SELECT room_id, period_key, period_start, period_end, message_count,
                   coverage, topics_json, patch_reactions_json, analyzer_model
            FROM periodic_insights
            WHERE (room_id, period_key) IN {in_clause}
            ORDER BY period_key ASC, room_id ASC
            """,
            params,
        ).fetchall()
    elif cutoff_iso:
        rows = conn.execute(
            """
            SELECT room_id, period_key, period_start, period_end, message_count,
                   coverage, topics_json, patch_reactions_json, analyzer_model
            FROM periodic_insights
            WHERE period_end >= ?
            ORDER BY period_end DESC, room_id ASC
            """,
            (cutoff_iso,),
        ).fetchall()
    else:
        return []

    compact: list[dict[str, Any]] = []
    for r in rows:
        topics_raw = r["topics_json"]
        patches_raw = r["patch_reactions_json"]
        try:
            topics = json.loads(topics_raw) if isinstance(topics_raw, str) else []
        except Exception:
            topics = []
        try:
            patches = (
                json.loads(patches_raw) if isinstance(patches_raw, str) else []
            )
        except Exception:
            patches = []
        compact.append(
            {
                "room_id": r["room_id"],
                "period_key": r["period_key"],
                "period_start": r["period_start"],
                "period_end": r["period_end"],
                "message_count": r["message_count"],
                "coverage": r["coverage"],
                "analyzer_model": r["analyzer_model"],
                "topics": _compact_topics(topics, limit=5),
                "patch_reactions": _compact_topics(patches, limit=3),
            }
        )
    return compact


def build_reporter_payload(
    conn: sqlite3.Connection,
    *,
    scope: ReportScope,
    settings: AppSettings,
    topic_rows: list[sqlite3.Row],
    patch_rows: list[sqlite3.Row],
    ctx: ContextBundle,
    now_dt: datetime,
    cutoff_iso: str | None = None,
) -> ReporterPayload:
    max_topics = int(settings.reporter_max_topics)
    max_patches = int(settings.reporter_max_patch_reactions)

    topics = [_row_to_dict(r) for r in topic_rows]
    topics.sort(key=_sort_key_topic)
    topics = topics[:max_topics]

    patches = [_row_to_dict(r) for r in patch_rows]
    patches.sort(key=_sort_key_patch)
    patches = patches[:max_patches]

    insights = _load_insights(conn, scope, cutoff_iso=cutoff_iso)

    room_ids = _room_ids_for_report(scope, topics, insights, settings)

    logger.info(
        "Fetching update notes for %s room(s) (web_search=%s)",
        len(room_ids),
        settings.reporter_web_search,
    )
    notes_by_room = [
        room_notes_to_dict(n)
        for n in gather_update_notes_for_rooms(settings, room_ids)
    ]

    meta = {
        "generated_at": now_dt.isoformat(timespec="seconds"),
        "scope_mode": scope.mode,
        "reporter_window": settings.reporter_window,
        "min_distinct_nicks": settings.min_distinct_nicks,
        "bucket_count": len(scope.buckets),
        "period_keys": sorted({str(t.get("period_key")) for t in topics}),
        "room_ids": sorted(room_ids),
    }

    return ReporterPayload(
        meta=meta,
        topic_stats=topics,
        patch_stats=patches,
        insights=insights,
        update_notes_by_room=notes_by_room,
        roadmap_excerpt=excerpt_for_llm(
            ctx.roadmap, max_chars=settings.reporter_context_chars
        ),
    )


def payload_to_user_dict(payload: ReporterPayload) -> dict[str, Any]:
    return {
        "meta": payload.meta,
        "topic_stats": payload.topic_stats,
        "patch_stats": payload.patch_stats,
        "periodic_insights": payload.insights,
        "update_notes_by_room": payload.update_notes_by_room,
    }
