"""Schema additions for web jobs and report run history."""

from __future__ import annotations

import sqlite3

_V2_SQL = """
CREATE TABLE IF NOT EXISTS background_jobs (
    job_id TEXT NOT NULL PRIMARY KEY,
    project_id TEXT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    result_json TEXT,
    error TEXT,
    log_text TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_background_jobs_project_created
    ON background_jobs (project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS report_runs (
    run_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    job_id TEXT,
    created_at TEXT NOT NULL,
    output_path TEXT NOT NULL,
    window_label TEXT,
    scope_json TEXT,
    period_keys_json TEXT,
    bucket_count INTEGER NOT NULL DEFAULT 0,
    reporter_backend TEXT,
    scope_mode TEXT
);

CREATE INDEX IF NOT EXISTS idx_report_runs_project_created
    ON report_runs (project_id, created_at DESC);
"""


def apply_v2(conn: sqlite3.Connection) -> None:
    conn.executescript(_V2_SQL)
    conn.commit()
