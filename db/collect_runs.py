"""Collect run audit records."""

from __future__ import annotations

import sqlite3
from datetime import datetime


def record_collect_run(
    conn: sqlite3.Connection,
    *,
    started_at: datetime,
    finished_at: datetime,
    room_id: str,
    status: str,
    new_message_count: int,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO collect_runs
            (started_at, finished_at, room_id, status, new_message_count, error)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            started_at.isoformat(timespec="seconds"),
            finished_at.isoformat(timespec="seconds"),
            room_id,
            status,
            new_message_count,
            error,
        ),
    )
    conn.commit()
