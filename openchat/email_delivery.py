"""Send open-chat report summary emails for a project."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from db.connection import init_db
from db.web_jobs import get_report_run, list_report_runs
from email_sender import send_html_email
from openchat.config import load_settings
from openchat.pipeline import find_project
from report.openchat_email import build_openchat_report_email_html

logger = logging.getLogger("openchat.email")


def public_report_url(run_id: int) -> str:
    """Absolute URL for the report HTML if SERVE_PUBLIC_BASE_URL is set."""
    base = (os.getenv("SERVE_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    path = f"/reports/{run_id}/file"
    return f"{base}{path}" if base else path


def _project_email_config(settings, project_id: str) -> tuple[str, list[str]]:
    project = find_project(settings, project_id)
    sender = (project.email_sender or "").strip()
    receivers = [r.strip() for r in project.email_receivers if r.strip()]
    if not sender or not receivers:
        raise ValueError(
            f"프로젝트 {project_id}에 이메일 sender/receivers가 설정되지 않았습니다."
        )
    return sender, receivers


def send_report_run_email(
    project_id: str,
    run_id: int,
    *,
    api_base_url: str | None = None,
) -> dict[str, Any]:
    """Send email for an existing report run."""
    settings = load_settings()
    project = find_project(settings, project_id)
    sender, receivers = _project_email_config(settings, project_id)

    conn = init_db(settings.database_path)
    try:
        run = get_report_run(conn, run_id)
        if run is None:
            raise ValueError(f"report run not found: {run_id}")
        if run.project_id != project_id:
            raise ValueError("report run does not belong to this project")
        raw = run.email_snapshot_json or "{}"
        snapshot = json.loads(raw) if raw.strip() else {}
    finally:
        conn.close()

    if not snapshot:
        snapshot = {
            "scope_label": run.window_label if run else "",
            "bucket_count": run.bucket_count if run else 0,
            "executive_summary": "",
            "highlights": [],
            "topics": [],
        }

    view_url = public_report_url(run_id)
    html_body = build_openchat_report_email_html(
        project_label=project.label,
        snapshot=snapshot,
        report_view_url=view_url,
    )
    window = (run.window_label if run else "") or snapshot.get("scope_label", "")
    subject = f"[오픈채팅 리포트] {project.label} — {window}"

    ok = send_html_email(
        sender=sender,
        recipients=receivers,
        subject=subject,
        html_body=html_body,
        api_base_url=api_base_url or settings.email_api_base_url or None,
        log_fn=lambda msg, lvl: logger.log(
            logging.INFO if lvl == "INFO" else logging.WARNING if lvl == "WARN" else logging.ERROR,
            msg,
        ),
    )
    if not ok:
        raise RuntimeError("이메일 API 발송에 실패했습니다.")
    return {
        "ok": True,
        "run_id": run_id,
        "project_id": project_id,
        "sender": sender,
        "recipients": receivers,
        "view_url": view_url,
    }


def latest_report_run_id(project_id: str) -> int | None:
    settings = load_settings()
    conn = init_db(settings.database_path)
    try:
        runs = list_report_runs(conn, project_id=project_id, limit=1)
    finally:
        conn.close()
    if not runs:
        return None
    return runs[0].run_id
