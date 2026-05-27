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
