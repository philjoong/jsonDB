"""Resolve which periodic_insights buckets appear in a report."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class InsightBucket:
    room_id: str
    period_key: str


@dataclass(frozen=True)
class ReportScope:
    """Resolved filter for report queries."""

    buckets: tuple[InsightBucket, ...]
    mode: str  # "window" | "latest" | "period_keys" | "buckets"


def resolve_report_scope(
    conn: sqlite3.Connection,
    *,
    tz: str,
    window_days: int,
    now_dt: datetime,
    buckets: list[tuple[str, str]] | None = None,
    period_keys: list[str] | None = None,
    room_ids: list[str] | None = None,
    latest: int | None = None,
) -> ReportScope:
    """Pick (room_id, period_key) pairs for the report."""
    if buckets:
        pairs = [InsightBucket(room_id=r, period_key=p) for r, p in buckets]
        return ReportScope(buckets=tuple(pairs), mode="buckets")

    if latest is not None and latest > 0:
        rows = conn.execute(
            """
            SELECT room_id, period_key
            FROM periodic_insights
            ORDER BY created_at DESC, insight_id DESC
            LIMIT ?
            """,
            (int(latest),),
        ).fetchall()
        pairs = [InsightBucket(str(r["room_id"]), str(r["period_key"])) for r in rows]
        return ReportScope(buckets=tuple(pairs), mode="latest")

    if period_keys:
        placeholders = ",".join(["?"] * len(period_keys))
        params: list[object] = list(period_keys)
        sql = f"""
            SELECT DISTINCT room_id, period_key
            FROM periodic_insights
            WHERE period_key IN ({placeholders})
        """
        if room_ids:
            rph = ",".join(["?"] * len(room_ids))
            sql += f" AND room_id IN ({rph})"
            params.extend(room_ids)
        sql += " ORDER BY period_key ASC, room_id ASC"
        rows = conn.execute(sql, tuple(params)).fetchall()
        pairs = [InsightBucket(str(r["room_id"]), str(r["period_key"])) for r in rows]
        return ReportScope(buckets=tuple(pairs), mode="period_keys")

    cutoff = now_dt.astimezone(ZoneInfo(tz)) - timedelta(days=window_days)
    params: list[object] = [cutoff.isoformat(timespec="seconds")]
    sql = """
        SELECT DISTINCT room_id, period_key
        FROM periodic_insights
        WHERE period_end >= ?
    """
    if room_ids:
        rph = ",".join(["?"] * len(room_ids))
        sql += f" AND room_id IN ({rph})"
        params.extend(room_ids)
    sql += " ORDER BY period_key ASC, room_id ASC"
    rows = conn.execute(sql, tuple(params)).fetchall()
    pairs = [InsightBucket(str(r["room_id"]), str(r["period_key"])) for r in rows]
    return ReportScope(buckets=tuple(pairs), mode="window")


def bucket_sql_in_clause(buckets: tuple[InsightBucket, ...]) -> tuple[str, tuple[object, ...]]:
    if not buckets:
        return "(?, ?)", ("", "")
    parts = ",".join(["(?, ?)"] * len(buckets))
    params: list[object] = []
    for b in buckets:
        params.extend([b.room_id, b.period_key])
    return f"({parts})", tuple(params)
