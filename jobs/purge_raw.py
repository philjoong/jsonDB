"""Retention jobs for raw message data."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from db.connection import init_db
from db.messages import purge_messages_older_than
from openchat.config import load_settings

logger = logging.getLogger("openchat.purge")


def purge_raw_messages(
    *,
    env_path: Path | None = None,
    retention_days: int | None = None,
    now: datetime | None = None,
) -> int:
    """Delete messages older than retention.raw_days (default from settings)."""
    settings = load_settings(env_path)
    days = retention_days if retention_days is not None else settings.retention_raw_days
    tz = ZoneInfo(settings.tz)
    reference = now.astimezone(tz) if now else datetime.now(tz)
    cutoff = reference - timedelta(days=days)

    conn = init_db(settings.database_path)
    try:
        deleted = purge_messages_older_than(conn, cutoff=cutoff)
    finally:
        conn.close()

    logger.info(
        "Purged %d messages with collected_at before %s (retention=%d days)",
        deleted,
        cutoff.isoformat(timespec="seconds"),
        days,
    )
    return deleted
