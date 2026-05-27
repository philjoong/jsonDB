from __future__ import annotations

from pathlib import Path

from db.connection import init_db


def test_init_db_applies_v2_tables(tmp_path: Path):
    conn = init_db(tmp_path / "t.db")
    try:
        jobs = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='background_jobs'"
        ).fetchone()
        reports = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='report_runs'"
        ).fetchone()
        versions = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    finally:
        conn.close()
    assert jobs is not None
    assert reports is not None
    assert any(int(v[0]) == 2 for v in versions)
