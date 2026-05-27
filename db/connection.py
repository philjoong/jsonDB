"""SQLite access and migrations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 3
_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> sqlite3.Connection:
    """Apply schema if needed and return an open connection."""
    conn = connect(db_path)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        _apply_schema(conn)
    _migrate(conn)
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply incremental migrations for existing databases."""
    from db.migrations_v2 import apply_v2

    current = int(
        conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
    )
    from db.migrations_v3 import apply_v3

    if current < 2 or not _table_exists(conn, "background_jobs"):
        apply_v2(conn)
        if current < 2:
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (2, ?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
            )
            conn.commit()
            current = 2

    if current < 3:
        apply_v3(conn)
        if current < 3:
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            conn.commit()


def _apply_schema(conn: sqlite3.Connection) -> None:
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    # Record v1 first; _migrate() upgrades to SCHEMA_VERSION for older scripts.
    conn.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (1, ?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
    )
    conn.commit()
