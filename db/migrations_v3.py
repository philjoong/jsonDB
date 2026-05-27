"""Add email_snapshot_json to report_runs."""

from __future__ import annotations

import sqlite3


def apply_v3(conn: sqlite3.Connection) -> None:
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(report_runs)").fetchall()
    }
    if "email_snapshot_json" not in cols:
        conn.execute("ALTER TABLE report_runs ADD COLUMN email_snapshot_json TEXT")
    conn.commit()
