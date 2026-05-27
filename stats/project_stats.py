"""Project-scoped statistics using shared UI data_scope (message_at / insight windows)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from openchat.config import AppSettings, effective_scope_days


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


def _as_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


@dataclass
class DataWindow:
    tz: str
    mode: str
    last_days: int
    time_field: str
    window_start: datetime
    window_end: datetime

    @property
    def window_start_iso(self) -> str:
        return self.window_start.isoformat(timespec="seconds")

    @property
    def window_end_iso(self) -> str:
        return self.window_end.isoformat(timespec="seconds")

    @property
    def label(self) -> str:
        if self.mode == "last_days":
            return f"최근 {self.last_days}일 ({self.time_field})"
        return f"최근 {self.last_days}일"


def resolve_data_window(
    settings: AppSettings,
    *,
    now: datetime | None = None,
) -> DataWindow:
    """Resolve the shared stats/report scope window from UI settings."""
    tz_name = (settings.data_scope.tz or settings.tz or "Asia/Seoul").strip()
    tz = ZoneInfo(tz_name)
    end = now.astimezone(tz) if now else datetime.now(tz)
    days = effective_scope_days(settings)
    start = end - timedelta(days=days)
    return DataWindow(
        tz=tz_name,
        mode=settings.data_scope.mode,
        last_days=days,
        time_field=settings.data_scope.time_field or "message_at",
        window_start=start,
        window_end=end,
    )


@dataclass
class ProjectStats:
    project_id: str
    project_label: str
    window: DataWindow
    message_count: int = 0
    distinct_nicks: int = 0
    insight_bucket_count: int = 0
    period_keys: list[str] = field(default_factory=list)
    top_topics: list[dict[str, Any]] = field(default_factory=list)
    top_patches: list[dict[str, Any]] = field(default_factory=list)
    messages_by_day: list[dict[str, Any]] = field(default_factory=list)
    insight_message_by_period: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_label": self.project_label,
            "scope": {
                "mode": self.window.mode,
                "last_days": self.window.last_days,
                "time_field": self.window.time_field,
                "tz": self.window.tz,
                "window_start": self.window.window_start_iso,
                "window_end": self.window.window_end_iso,
                "label": self.window.label,
            },
            "message_count": self.message_count,
            "distinct_nicks": self.distinct_nicks,
            "insight_bucket_count": self.insight_bucket_count,
            "period_keys": self.period_keys,
            "top_topics": self.top_topics,
            "top_patches": self.top_patches,
            "messages_by_day": self.messages_by_day,
            "insight_message_by_period": self.insight_message_by_period,
        }


def _aggregate_topics(insight_rows: list[sqlite3.Row], *, top_n: int = 15) -> list[dict[str, Any]]:
    merged: dict[tuple[str | None, str | None, str | None], dict[str, Any]] = {}
    for r in insight_rows:
        for t in _safe_json_list(r["topics_json"]):
            tag = str(t.get("tag") or "") or None
            topic_key = str(t.get("topic_key") or "") or None
            title = str(t.get("title") or topic_key or "") or None
            key = (tag, topic_key, title)
            agg = merged.get(key)
            if agg is None:
                agg = {
                    "tag": tag,
                    "topic_key": topic_key,
                    "title": title,
                    "mentions": 0,
                    "distinct_nicks": 0,
                    "messages_referenced": 0,
                }
                merged[key] = agg
            agg["mentions"] += _as_int(t.get("mentions"))
            agg["distinct_nicks"] += _as_int(t.get("distinct_nicks"))
            agg["messages_referenced"] += _as_int(
                t.get("messages_referenced"), _as_int(len(t.get("quote_refs") or []))
            )
    ranked = sorted(
        merged.values(),
        key=lambda x: (-int(x["mentions"]), -int(x["distinct_nicks"])),
    )
    return ranked[:top_n]


def _aggregate_patches(insight_rows: list[sqlite3.Row], *, top_n: int = 12) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for r in insight_rows:
        for p in _safe_json_list(r["patch_reactions_json"]):
            patch_item = p.get("patch_item") or p.get("item") or p.get("key")
            if not patch_item:
                continue
            stance = str(p.get("stance") or "neutral")
            key = (str(patch_item), stance)
            agg = merged.get(key)
            if agg is None:
                agg = {
                    "patch_item": str(patch_item),
                    "stance": stance,
                    "mentions": 0,
                    "distinct_nicks": 0,
                    "summary": None,
                }
                merged[key] = agg
            agg["mentions"] += _as_int(p.get("mentions"))
            agg["distinct_nicks"] += _as_int(p.get("distinct_nicks"))
            summary = p.get("summary")
            if summary and not agg["summary"]:
                agg["summary"] = str(summary)
    ranked = sorted(
        merged.values(),
        key=lambda x: (-int(x["mentions"]), -int(x["distinct_nicks"])),
    )
    return ranked[:top_n]


def query_project_stats(
    conn: sqlite3.Connection,
    settings: AppSettings,
    project_id: str,
    *,
    project_label: str | None = None,
    now: datetime | None = None,
    top_topics: int = 15,
    top_patches: int = 12,
) -> ProjectStats:
    """Compute on-demand stats for one project within the shared data scope."""
    window = resolve_data_window(settings, now=now)
    label = project_label or project_id

    if window.time_field != "message_at":
        raise ValueError(f"unsupported time_field: {window.time_field}")

    msg_row = conn.execute(
        """
        SELECT COUNT(*) AS c,
               COUNT(DISTINCT nick) AS distinct_nicks
        FROM messages
        WHERE room_id = ?
          AND message_at >= ?
          AND message_at < ?
        """,
        (project_id, window.window_start_iso, window.window_end_iso),
    ).fetchone()
    message_count = int(msg_row["c"] or 0)
    distinct_nicks = int(msg_row["distinct_nicks"] or 0)

    day_rows = conn.execute(
        """
        SELECT substr(message_at, 1, 10) AS day,
               COUNT(*) AS messages,
               COUNT(DISTINCT nick) AS distinct_nicks
        FROM messages
        WHERE room_id = ?
          AND message_at >= ?
          AND message_at < ?
        GROUP BY substr(message_at, 1, 10)
        ORDER BY day ASC
        """,
        (project_id, window.window_start_iso, window.window_end_iso),
    ).fetchall()
    messages_by_day = [
        {
            "day": str(r["day"]),
            "messages": int(r["messages"] or 0),
            "distinct_nicks": int(r["distinct_nicks"] or 0),
        }
        for r in day_rows
    ]

    insight_rows = conn.execute(
        """
        SELECT room_id, period_key, period_type, period_start, period_end,
               message_count, topics_json, patch_reactions_json
        FROM periodic_insights
        WHERE room_id = ?
          AND period_end >= ?
          AND period_start < ?
        ORDER BY period_key ASC
        """,
        (project_id, window.window_start_iso, window.window_end_iso),
    ).fetchall()

    period_keys = sorted({str(r["period_key"]) for r in insight_rows})
    insight_message_by_period = [
        {
            "period_key": str(r["period_key"]),
            "message_count": int(r["message_count"] or 0),
        }
        for r in insight_rows
    ]

    return ProjectStats(
        project_id=project_id,
        project_label=label,
        window=window,
        message_count=message_count,
        distinct_nicks=distinct_nicks,
        insight_bucket_count=len(insight_rows),
        period_keys=period_keys,
        top_topics=_aggregate_topics(insight_rows, top_n=top_topics),
        top_patches=_aggregate_patches(insight_rows, top_n=top_patches),
        messages_by_day=messages_by_day,
        insight_message_by_period=insight_message_by_period,
    )
