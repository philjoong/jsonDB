"""Persistence helpers for periodic_insights (phase 3b+)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PeriodicInsightRow:
    room_id: str
    period_key: str
    period_start: datetime
    period_end: datetime
    period_type: str
    message_count: int
    coverage: str | None
    topics: list[dict]
    patch_reactions: list[dict]
    analyzer_model: str | None
    analyzer_version: str
    prompt_hash: str | None
    created_at: datetime


def upsert_periodic_insight(conn: sqlite3.Connection, row: PeriodicInsightRow) -> None:
    """Upsert periodic_insights by (room_id, period_key, analyzer_version)."""
    conn.execute(
        """
        INSERT INTO periodic_insights
            (room_id, period_key, period_start, period_end, period_type,
             message_count, coverage, topics_json, patch_reactions_json,
             analyzer_model, analyzer_version, prompt_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(room_id, period_key, analyzer_version) DO UPDATE SET
            period_start = excluded.period_start,
            period_end = excluded.period_end,
            period_type = excluded.period_type,
            message_count = excluded.message_count,
            coverage = excluded.coverage,
            topics_json = excluded.topics_json,
            patch_reactions_json = excluded.patch_reactions_json,
            analyzer_model = excluded.analyzer_model,
            prompt_hash = excluded.prompt_hash,
            created_at = excluded.created_at
        """,
        (
            row.room_id,
            row.period_key,
            row.period_start.isoformat(timespec="seconds"),
            row.period_end.isoformat(timespec="seconds"),
            row.period_type,
            int(row.message_count),
            row.coverage,
            json.dumps(row.topics, ensure_ascii=False),
            json.dumps(row.patch_reactions, ensure_ascii=False),
            row.analyzer_model,
            row.analyzer_version,
            row.prompt_hash,
            row.created_at.isoformat(timespec="seconds"),
        ),
    )
    conn.commit()

