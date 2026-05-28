"""Background job execution (thread pool) for analyze/report tasks."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from db.connection import init_db
from db.web_jobs import (
    append_job_log,
    create_job,
    fail_active_jobs,
    get_job,
    has_active_job,
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

_JobItem = tuple[str, str, str, int | None]
_job_queue: queue.Queue[_JobItem] = queue.Queue()
_worker_lock = threading.Lock()
_worker_thread: threading.Thread | None = None

@dataclass
class JobSubmitResult:
    job_id: str
    kind: str
    project_id: str
    async_mode: bool


def _now_iso(settings_tz: str) -> datetime:
    return datetime.now(ZoneInfo(settings_tz))


def _emit_job(event: str, *, project_id: str, kind: str, job_id: str) -> None:
    # stamp = datetime.now().isoformat(timespec="seconds")
    # print(
    #     f"[openchat.jobs] {stamp} {event} project={project_id} kind={kind} job={job_id}",
    #     flush=True,
    # )
    pass


def submit_collect(project_id: str) -> JobSubmitResult:
    return _submit_background(project_id, "collect")


def submit_analyze(project_id: str) -> JobSubmitResult:
    return _submit_background(project_id, "analyze")

def submit_analyze_retry(project_id: str) -> JobSubmitResult:
    return _submit_background(project_id, "analyze_retry")


def submit_report(project_id: str) -> JobSubmitResult:
    return _submit_background(project_id, "report")


def submit_report_email(project_id: str, *, run_id: int | None = None) -> JobSubmitResult:
    return _submit_background(project_id, "report_email", run_id=run_id)


def submit_report_and_email(project_id: str) -> JobSubmitResult:
    return _submit_background(project_id, "report_and_email")

def submit_collect_analyze_report_email(project_id: str) -> JobSubmitResult:
    return _submit_background(project_id, "collect_analyze_report_email")


def _submit_background(
    project_id: str,
    kind: str,
    *,
    run_id: int | None = None,
) -> JobSubmitResult:
    settings = load_settings()
    _ensure_project_idle(settings, project_id, kind)
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

    _ensure_queue_worker()
    _job_queue.put((job_id, project_id, kind, run_id))
    # _emit_job("submitted", project_id=project_id, kind=kind, job_id=job_id)
    return JobSubmitResult(
        job_id=job_id, kind=kind, project_id=project_id, async_mode=True
    )


def _ensure_queue_worker() -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _worker_thread = threading.Thread(
            target=_queue_worker,
            name="openchat-background-job-worker",
            daemon=True,
        )
        _worker_thread.start()


def _queue_worker() -> None:
    while True:
        job_id, project_id, kind, run_id = _job_queue.get()
        try:
            _run_background_job(job_id, project_id, kind, run_id)
        finally:
            _job_queue.task_done()


def _ensure_project_idle(settings, project_id: str, kind: str) -> None:
    conn = init_db(settings.database_path)
    try:
        _recover_stale_collect_jobs(conn, project_id, now=_now_iso(settings.tz))
        if has_active_job(conn, project_id, kind):
            raise ValueError(
                f"project '{project_id}' already has a pending/running '{kind}' job"
            )
    finally:
        conn.close()


def recover_interrupted_jobs() -> int:
    """Fail jobs left active by a previous web process."""
    settings = load_settings()
    conn = init_db(settings.database_path)
    try:
        return fail_active_jobs(
            conn,
            finished_at=_now_iso(settings.tz),
            reason="web process restarted before the job finished",
        )
    finally:
        conn.close()


def _recover_stale_collect_jobs(conn, project_id: str, *, now: datetime) -> None:
    """Mark abandoned collect jobs as failed so they stop blocking new jobs."""
    rows = conn.execute(
        """
        SELECT job_id, started_at
        FROM background_jobs
        WHERE project_id = ?
          AND kind = 'collect'
          AND status = 'running'
        """,
        (project_id,),
    ).fetchall()
    if not rows:
        return

    stale_cutoff = now - timedelta(minutes=5)
    for r in rows:
        started_at = r["started_at"]
        if not started_at:
            continue
        try:
            started = datetime.fromisoformat(str(started_at))
        except ValueError:
            continue
        if started > stale_cutoff:
            continue
        set_job_status(
            conn,
            str(r["job_id"]),
            status="failed",
            finished_at=now,
            error="stale collect job auto-recovered",
        )
        append_job_log(
            conn,
            str(r["job_id"]),
            "collect auto-failed: detected as stale (>5m) before new submission",
        )


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
        # _emit_job("started", project_id=project_id, kind=kind, job_id=job_id)
        if kind == "collect":
            payload = _run_collect(settings, project_id, conn, job_id)
        elif kind == "analyze":
            result = run_analyze_project(conn, settings, project_id, job_id=job_id)
            if result.failed_buckets and result.processed == 0:
                first = result.failed_buckets[0]
                raise RuntimeError(
                    "analyze failed for all queued buckets; "
                    f"first failure {first['period_key']}: {first['error']}"
                )
            if result.failed_buckets:
                append_job_log(
                    conn,
                    job_id,
                    f"analyze completed with {len(result.failed_buckets)} failed bucket(s)",
                )
            payload = {
                "processed": result.processed,
                "processed_buckets": result.processed_buckets,
                "failed_buckets": result.failed_buckets,
            }
        elif kind == "analyze_retry":
            result = run_analyze_project(
                conn,
                settings,
                project_id,
                job_id=job_id,
                force=True,
                include_current=True,
            )
            if result.failed_buckets and result.processed == 0:
                first = result.failed_buckets[0]
                raise RuntimeError(
                    "analyze failed for all queued buckets; "
                    f"first failure {first['period_key']}: {first['error']}"
                )
            if result.failed_buckets:
                append_job_log(
                    conn,
                    job_id,
                    f"analyze completed with {len(result.failed_buckets)} failed bucket(s)",
                )
            payload = {
                "processed": result.processed,
                "processed_buckets": result.processed_buckets,
                "failed_buckets": result.failed_buckets,
                "forced": True,
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
        elif kind == "collect_analyze_report_email":
            append_job_log(conn, job_id, "collect started")
            collect_result = run_collect_project(settings, project_id)
            append_job_log(
                conn,
                job_id,
                "collect finished "
                f"ok={collect_result.cycle.ok_count} "
                f"skipped={collect_result.cycle.skipped_count} "
                f"error={collect_result.cycle.error_count}",
            )
            report_result = run_analyze_and_report_project(
                settings,
                project_id,
                job_id=job_id,
            )
            append_job_log(conn, job_id, "send email")
            email_result = send_report_run_email(
                project_id, int(report_result.run_id or 0)
            )
            payload = {
                "collect": {
                    "ok_count": collect_result.cycle.ok_count,
                    "skipped_count": collect_result.cycle.skipped_count,
                    "error_count": collect_result.cycle.error_count,
                },
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
        # _emit_job("succeeded", project_id=project_id, kind=kind, job_id=job_id)
    except Exception as exc:
        logger.exception("Background job %s failed", job_id)
        # _emit_job("failed", project_id=project_id, kind=kind, job_id=job_id)
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
