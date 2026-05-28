from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from threading import Event

from db.connection import init_db
from db.web_jobs import get_job
from openchat import job_service


def _wait_until(predicate, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _job_statuses(db: Path, job_ids: tuple[str, ...]) -> list[str]:
    conn = init_db(db)
    try:
        return [get_job(conn, jid).status for jid in job_ids]
    finally:
        conn.close()


def test_background_jobs_run_through_single_fifo_worker(
    tmp_path: Path,
    monkeypatch,
):
    db = tmp_path / "openchat.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    init_db(db).close()

    first_started = Event()
    release_first = Event()
    second_started = Event()
    run_order: list[str] = []

    def fake_run_analyze_project(_conn, _settings, project_id, **_kwargs):
        run_order.append(project_id)
        if project_id == "p1":
            first_started.set()
            assert release_first.wait(timeout=5.0)
        if project_id == "p2":
            second_started.set()
        return SimpleNamespace(
            processed=0,
            processed_buckets=[],
            failed_buckets=[],
        )

    monkeypatch.setattr(job_service, "run_analyze_project", fake_run_analyze_project)

    first = job_service.submit_analyze("p1")
    second = job_service.submit_analyze("p2")

    assert first_started.wait(timeout=5.0)

    conn = init_db(db)
    try:
        assert get_job(conn, first.job_id).status == "running"
        assert get_job(conn, second.job_id).status == "pending"
    finally:
        conn.close()

    assert not second_started.is_set()
    release_first.set()

    assert second_started.wait(timeout=5.0)
    assert _wait_until(
        lambda: _job_statuses(db, (first.job_id, second.job_id))
        == ["succeeded", "succeeded"]
    )
    assert run_order == ["p1", "p2"]
