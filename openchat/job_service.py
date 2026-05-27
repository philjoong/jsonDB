"""Background job execution (thread pool) for analyze/report tasks."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from db.connection import init_db
from db.web_jobs import (
    append_job_log,
    create_job,
    get_job,
    set_job_status,
)
from openchat.config import load_settings
from openchat.email_delivery import send_report_run_email
from openchat.pipeline import (
    run_analyze_and_report_project,
    run_analyze_project,
    run_collect_project,
    run_report_project,
)

logger = logging.getLogger("openchat.jobs")

@dataclass
class JobSubmitResult:
    job_id: str
    kind: str
    project_id: str
    async_mode: bool


def _now_iso(settings_tz: str) -> datetime:
    return datetime.now(ZoneInfo(settings_tz))


def submit_collect(project_id: str) -> JobSubmitResult:
    """Run collect synchronously in the caller thread (short)."""
    settings = load_settings()
    job_id = _create_and_run_sync(
        project_id,
        kind="collect",
        settings_tz=settings.tz,
        runner=lambda conn, jid: _run_collect(settings, project_id, conn, jid),
    )
    return JobSubmitResult(
        job_id=job_id, kind="collect", project_id=project_id, async_mode=False
    )


def submit_analyze(project_id: str) -> JobSubmitResult:
    return _submit_background(project_id, "analyze")


def submit_report(project_id: str) -> JobSubmitResult:
    return _submit_background(project_id, "report")


def submit_report_email(project_id: str, *, run_id: int | None = None) -> JobSubmitResult:
    return _submit_background(project_id, "report_email", run_id=run_id)


def submit_report_and_email(project_id: str) -> JobSubmitResult:
    return _submit_background(project_id, "report_and_email")


def _submit_background(
    project_id: str,
    kind: str,
    *,
    run_id: int | None = None,
) -> JobSubmitResult:
    settings = load_settings()
    conn = init_db(settings.database_path)
    try:
        job_id = create_job(
            conn,
            project_id=project_id,
            kind=kind,
            created_at=_now_iso(settings.tz),
        )
    finally:
        conn.close()

    thread = threading.Thread(
        target=_run_background_job,
        args=(job_id, project_id, kind, run_id),
        name=f"openchat-{kind}-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return JobSubmitResult(
        job_id=job_id, kind=kind, project_id=project_id, async_mode=True
    )


def _create_and_run_sync(
    project_id: str,
    *,
    kind: str,
    settings_tz: str,
    runner: Callable,
) -> str:
    settings = load_settings()
    conn = init_db(settings.database_path)
    try:
        job_id = create_job(
            conn,
            project_id=project_id,
            kind=kind,
            created_at=_now_iso(settings_tz),
        )
        set_job_status(
            conn,
            job_id,
            status="running",
            started_at=_now_iso(settings_tz),
        )
        try:
            result = runner(conn, job_id)
            set_job_status(
                conn,
                job_id,
                status="succeeded",
                finished_at=_now_iso(settings_tz),
                result=result,
            )
        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            set_job_status(
                conn,
                job_id,
                status="failed",
                finished_at=_now_iso(settings_tz),
                error=str(exc),
            )
            raise
    finally:
        conn.close()
    return job_id


def _run_collect(settings, project_id: str, conn, job_id: str) -> dict:
    append_job_log(conn, job_id, "collect started")
    result = run_collect_project(settings, project_id)
    append_job_log(
        conn,
        job_id,
        f"collect finished ok={result.cycle.ok_count} skipped={result.cycle.skipped_count}",
    )
    return {
        "ok_count": result.cycle.ok_count,
        "skipped_count": result.cycle.skipped_count,
        "error_count": result.cycle.error_count,
        "rooms": [
            {
                "room_id": r.room_id,
                "status": r.status,
                "new_message_count": r.new_message_count,
                "error": r.error,
            }
            for r in result.cycle.rooms
        ],
    }


def _run_background_job(
    job_id: str,
    project_id: str,
    kind: str,
    run_id: int | None = None,
) -> None:
    settings = load_settings()
    conn = init_db(settings.database_path)
    try:
        set_job_status(
            conn,
            job_id,
            status="running",
            started_at=_now_iso(settings.tz),
        )
        append_job_log(conn, job_id, f"{kind} started")
        if kind == "analyze":
            result = run_analyze_project(conn, settings, project_id, job_id=job_id)
            payload = {
                "processed": result.processed,
                "processed_buckets": result.processed_buckets,
            }
        elif kind == "report":
            report_result = run_report_project(
                conn, settings, project_id, job_id=job_id
            )
            payload = {
                "run_id": report_result.run_id,
                "output_path": str(report_result.report.output_path),
                "bucket_count": report_result.report.bucket_count,
            }
        elif kind == "report_and_email":
            report_result = run_report_project(
                conn, settings, project_id, job_id=job_id
            )
            append_job_log(conn, job_id, "send email")
            email_result = send_report_run_email(
                project_id, int(report_result.run_id or 0)
            )
            payload = {
                "run_id": report_result.run_id,
                "output_path": str(report_result.report.output_path),
                "email": email_result,
            }
        elif kind == "report_email":
            if run_id is None:
                from openchat.email_delivery import latest_report_run_id

                run_id = latest_report_run_id(project_id)
                if run_id is None:
                    raise ValueError("발송할 리포트가 없습니다. 먼저 리포트를 생성하세요.")
            append_job_log(conn, job_id, f"email for run {run_id}")
            email_result = send_report_run_email(project_id, int(run_id))
            payload = {"run_id": run_id, "email": email_result}
        else:
            raise ValueError(f"unknown job kind: {kind}")

        set_job_status(
            conn,
            job_id,
            status="succeeded",
            finished_at=_now_iso(settings.tz),
            result=payload,
        )
        append_job_log(conn, job_id, f"{kind} succeeded")
    except Exception as exc:
        logger.exception("Background job %s failed", job_id)
        if conn is not None:
            set_job_status(
                conn,
                job_id,
                status="failed",
                finished_at=_now_iso(settings.tz),
                error=str(exc),
            )
            append_job_log(conn, job_id, f"{kind} failed: {exc}")
    finally:
        if conn is not None:
            conn.close()


def job_to_dict(job) -> dict:
    return {
        "job_id": job.job_id,
        "project_id": job.project_id,
        "kind": job.kind,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "result": job.result,
        "error": job.error,
        "log_text": job.log_text,
    }


def fetch_job(job_id: str):
    settings = load_settings()
    conn = init_db(settings.database_path)
    try:
        return get_job(conn, job_id)
    finally:
        conn.close()
