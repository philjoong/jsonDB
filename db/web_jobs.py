"""Persist background jobs and report run metadata for the web UI."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class BackgroundJob:
    job_id: str
    project_id: str | None
    kind: str
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    log_text: str = ""


@dataclass
class ReportRunRow:
    run_id: int
    project_id: str
    job_id: str | None
    created_at: str
    output_path: str
    window_label: str | None
    scope_json: str | None
    period_keys_json: str | None
    bucket_count: int
    reporter_backend: str | None
    scope_mode: str | None
    email_snapshot_json: str | None = None


@dataclass
class ProjectLastRuns:
    project_id: str
    last_collect_at: str | None
    last_analyze_at: str | None
    last_report_at: str | None


def _row_to_job(row: sqlite3.Row) -> BackgroundJob:
    raw = row["result_json"]
    result = None
    if isinstance(raw, str) and raw.strip():
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {"raw": raw}
    return BackgroundJob(
        job_id=str(row["job_id"]),
        project_id=row["project_id"],
        kind=str(row["kind"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        result=result,
        error=row["error"],
        log_text=str(row["log_text"] or ""),
    )


def create_job(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    kind: str,
    created_at: datetime,
) -> str:
    job_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO background_jobs
            (job_id, project_id, kind, status, created_at, log_text)
        VALUES (?, ?, ?, 'pending', ?, '')
        """,
        (job_id, project_id, kind, created_at.isoformat(timespec="seconds")),
    )
    conn.commit()
    return job_id


def append_job_log(conn: sqlite3.Connection, job_id: str, line: str) -> None:
    row = conn.execute(
        "SELECT log_text FROM background_jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return
    prev = str(row["log_text"] or "")
    text = prev + line + ("\n" if not line.endswith("\n") else "")
    conn.execute(
        "UPDATE background_jobs SET log_text = ? WHERE job_id = ?",
        (text, job_id),
    )
    conn.commit()


def set_job_status(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    status: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE background_jobs
        SET status = ?,
            started_at = COALESCE(?, started_at),
            finished_at = COALESCE(?, finished_at),
            result_json = COALESCE(?, result_json),
            error = COALESCE(?, error)
        WHERE job_id = ?
        """,
        (
            status,
            started_at.isoformat(timespec="seconds") if started_at else None,
            finished_at.isoformat(timespec="seconds") if finished_at else None,
            json.dumps(result, ensure_ascii=False) if result is not None else None,
            error,
            job_id,
        ),
    )
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: str) -> BackgroundJob | None:
    row = conn.execute(
        "SELECT * FROM background_jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_job(row)


def list_jobs_for_project(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    limit: int = 20,
) -> list[BackgroundJob]:
    rows = conn.execute(
        """
        SELECT * FROM background_jobs
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (project_id, int(limit)),
    ).fetchall()
    return [_row_to_job(r) for r in rows]


def list_jobs(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[BackgroundJob]:
    where_parts: list[str] = []
    params: list[Any] = []
    if project_id:
        where_parts.append("project_id = ?")
        params.append(project_id)
    if status:
        where_parts.append("status = ?")
        params.append(status)

    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    rows = conn.execute(
        f"""
        SELECT * FROM background_jobs
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()
    return [_row_to_job(r) for r in rows]


def insert_report_run(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    job_id: str | None,
    created_at: datetime,
    output_path: str,
    window_label: str | None,
    scope_json: str | None,
    period_keys: list[str],
    bucket_count: int,
    reporter_backend: str | None,
    scope_mode: str | None,
    email_snapshot_json: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO report_runs
            (project_id, job_id, created_at, output_path, window_label,
             scope_json, period_keys_json, bucket_count, reporter_backend, scope_mode,
             email_snapshot_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            job_id,
            created_at.isoformat(timespec="seconds"),
            output_path,
            window_label,
            scope_json,
            json.dumps(period_keys, ensure_ascii=False),
            int(bucket_count),
            reporter_backend,
            scope_mode,
            email_snapshot_json,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_report_runs(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
    limit: int = 50,
) -> list[ReportRunRow]:
    if project_id:
        rows = conn.execute(
            """
            SELECT * FROM report_runs
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (project_id, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM report_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [
        ReportRunRow(
            run_id=int(r["run_id"]),
            project_id=str(r["project_id"]),
            job_id=r["job_id"],
            created_at=str(r["created_at"]),
            output_path=str(r["output_path"]),
            window_label=r["window_label"],
            scope_json=r["scope_json"],
            period_keys_json=r["period_keys_json"],
            bucket_count=int(r["bucket_count"] or 0),
            reporter_backend=r["reporter_backend"],
            scope_mode=r["scope_mode"],
            email_snapshot_json=r["email_snapshot_json"]
            if "email_snapshot_json" in r.keys()
            else None,
        )
        for r in rows
    ]


def get_report_run(conn: sqlite3.Connection, run_id: int) -> ReportRunRow | None:
    row = conn.execute(
        "SELECT * FROM report_runs WHERE run_id = ?",
        (int(run_id),),
    ).fetchone()
    if row is None:
        return None
    return ReportRunRow(
        run_id=int(row["run_id"]),
        project_id=str(row["project_id"]),
        job_id=row["job_id"],
        created_at=str(row["created_at"]),
        output_path=str(row["output_path"]),
        window_label=row["window_label"],
        scope_json=row["scope_json"],
        period_keys_json=row["period_keys_json"],
        bucket_count=int(row["bucket_count"] or 0),
        reporter_backend=row["reporter_backend"],
        scope_mode=row["scope_mode"],
        email_snapshot_json=row["email_snapshot_json"]
        if "email_snapshot_json" in row.keys()
        else None,
    )


def has_active_job(conn: sqlite3.Connection, project_id: str, kind: str | None = None) -> bool:
    if kind is not None:
        row = conn.execute(
            """
            SELECT 1
            FROM background_jobs
            WHERE project_id = ?
              AND kind = ?
              AND status IN ('pending', 'running')
            LIMIT 1
            """,
            (project_id, kind),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT 1
            FROM background_jobs
            WHERE project_id = ?
              AND status IN ('pending', 'running')
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    return row is not None


def fail_active_jobs(
    conn: sqlite3.Connection,
    *,
    finished_at: datetime,
    reason: str,
    project_id: str | None = None,
) -> int:
    """Mark pending/running jobs as failed after the worker process was interrupted."""
    params: list[Any] = [
        finished_at.isoformat(timespec="seconds"),
        reason,
        f"auto-failed: {reason}\n",
    ]
    project_sql = ""
    if project_id:
        project_sql = " AND project_id = ?"
        params.append(project_id)
    cur = conn.execute(
        f"""
        UPDATE background_jobs
        SET status = 'failed',
            finished_at = COALESCE(finished_at, ?),
            error = COALESCE(error, ?),
            log_text = COALESCE(log_text, '') || ?
        WHERE status IN ('pending', 'running')
        {project_sql}
        """,
        tuple(params),
    )
    conn.commit()
    return int(cur.rowcount or 0)


def get_project_last_runs(conn: sqlite3.Connection, project_id: str) -> ProjectLastRuns:
    collect_row = conn.execute(
        """
        SELECT MAX(finished_at) AS ts
        FROM collect_runs
        WHERE room_id = ?
          AND status = 'ok'
        """,
        (project_id,),
    ).fetchone()
    analyze_row = conn.execute(
        """
        SELECT MAX(finished_at) AS ts
        FROM background_jobs
        WHERE project_id = ?
          AND kind IN ('analyze', 'collect_analyze_report_email')
          AND status = 'succeeded'
        """,
        (project_id,),
    ).fetchone()
    report_row = conn.execute(
        """
        SELECT MAX(created_at) AS ts
        FROM report_runs
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    return ProjectLastRuns(
        project_id=project_id,
        last_collect_at=collect_row["ts"] if collect_row else None,
        last_analyze_at=analyze_row["ts"] if analyze_row else None,
        last_report_at=report_row["ts"] if report_row else None,
    )


def list_project_last_runs(
    conn: sqlite3.Connection,
    project_ids: list[str],
) -> dict[str, ProjectLastRuns]:
    out: dict[str, ProjectLastRuns] = {}
    for pid in project_ids:
        out[pid] = get_project_last_runs(conn, pid)
    return out


def has_report_email_sent_since(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    since_at: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM background_jobs
        WHERE project_id = ?
          AND kind IN ('report_and_email', 'report_email')
          AND status = 'succeeded'
          AND finished_at >= ?
        LIMIT 1
        """,
        (project_id, since_at),
    ).fetchone()
    return row is not None
