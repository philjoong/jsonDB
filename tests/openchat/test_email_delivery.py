from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from db.connection import init_db
from db.web_jobs import insert_report_run
from openchat.config import ProjectConfig, load_settings
from openchat.email_delivery import send_report_run_email


def test_send_report_run_email(monkeypatch, tmp_path: Path):
    db = tmp_path / "db.sqlite"
    projects = tmp_path / "projects.yaml"
    projects.write_text(
        yaml.safe_dump(
            {
                "projects": [
                    {
                        "id": "p1",
                        "label": "P1",
                        "enabled": True,
                        "titles": ["방"],
                        "email": {
                            "sender": "from@test.com",
                            "receivers": ["to@test.com"],
                        },
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROJECTS_CONFIG", str(projects))
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setenv("EMAIL_API_BASE_URL", "http://mail.test")

    settings = load_settings()
    conn = init_db(settings.database_path)
    sync = ProjectConfig(id="p1", titles=["방"], label="P1", enabled=True)
    from db.messages import sync_rooms

    sync_rooms(conn, [sync])
    run_id = insert_report_run(
        conn,
        project_id="p1",
        job_id=None,
        created_at=datetime.now(ZoneInfo("Asia/Seoul")),
        output_path=str(tmp_path / "r.html"),
        window_label="최근 7일",
        scope_json="{}",
        period_keys=["2026-05-20"],
        bucket_count=1,
        reporter_backend="static",
        scope_mode="window",
        email_snapshot_json=json.dumps(
            {
                "executive_summary": "요약",
                "highlights": [],
                "topics": [],
                "scope_label": "최근 7일",
                "bucket_count": 1,
            },
            ensure_ascii=False,
        ),
    )
    conn.close()

    sent: list[dict] = []

    def fake_send(**kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr("openchat.email_delivery.send_html_email", fake_send)
    result = send_report_run_email("p1", run_id)
    assert result["ok"] is True
    assert len(sent) == 1
    assert sent[0]["sender"] == "from@test.com"
    assert sent[0]["recipients"] == ["to@test.com"]
    assert "오픈채팅 리포트" in sent[0]["subject"]
