from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db.connection import init_db
from db.web_jobs import create_job, fail_active_jobs, get_job, set_job_status


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


def test_fail_active_jobs_marks_interrupted_jobs_failed(tmp_path: Path):
    now = datetime(2026, 5, 27, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    conn = init_db(tmp_path / "t.db")
    try:
        running = create_job(conn, project_id="p1", kind="analyze", created_at=now)
        pending = create_job(conn, project_id="p1", kind="report", created_at=now)
        done = create_job(conn, project_id="p1", kind="collect", created_at=now)
        set_job_status(conn, running, status="running", started_at=now)
        set_job_status(conn, done, status="succeeded", finished_at=now)

        changed = fail_active_jobs(
            conn,
            finished_at=now,
            reason="web process restarted before the job finished",
        )

        assert changed == 2
        assert get_job(conn, running).status == "failed"  # type: ignore[union-attr]
        assert get_job(conn, pending).status == "failed"  # type: ignore[union-attr]
        assert get_job(conn, done).status == "succeeded"  # type: ignore[union-attr]
    finally:
        conn.close()
