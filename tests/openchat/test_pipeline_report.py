from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from db.connection import init_db
from db.insights import PeriodicInsightRow, upsert_periodic_insight
from db.messages import sync_rooms
from db.web_jobs import list_report_runs
from openchat.config import ProjectConfig, load_settings
from openchat.pipeline import run_report_project

TZ = "Asia/Seoul"


def _write_projects(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "projects": [
                    {
                        "id": "p1",
                        "label": "P1",
                        "enabled": True,
                        "titles": ["방"],
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def test_run_report_project_records_run(tmp_path: Path, monkeypatch):
    db = tmp_path / "openchat.db"
    projects = tmp_path / "projects.yaml"
    ui = tmp_path / "ui_settings.yaml"
    out = tmp_path / "reports"
    _write_projects(projects)
    ui.write_text(
        yaml.safe_dump(
            {
                "collect_interval_minutes": 10,
                "analyzer_period": "1d",
                "reporter_window": "7d",
                "data_scope": {
                    "mode": "last_days",
                    "last_days": 7,
                    "time_field": "message_at",
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROJECTS_CONFIG", str(projects))
    monkeypatch.setenv("UI_SETTINGS_CONFIG", str(ui))
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setenv("OUTPUT_DIR", str(out))
    monkeypatch.setenv("REPORTER_USE_LLM", "false")

    settings = load_settings()
    conn = init_db(settings.database_path)
    sync_rooms(
        conn,
        [ProjectConfig(id="p1", titles=["방"], label="P1", enabled=True)],
    )
    upsert_periodic_insight(
        conn,
        PeriodicInsightRow(
            room_id="p1",
            period_key="2026-05-20",
            period_start=datetime(2026, 5, 20, tzinfo=ZoneInfo(TZ)),
            period_end=datetime(2026, 5, 21, tzinfo=ZoneInfo(TZ)),
            period_type="1d",
            message_count=2,
            coverage="low",
            topics=[{"tag": "general", "topic_key": "t1", "title": "주제", "mentions": 2}],
            patch_reactions=[],
            analyzer_model="test",
            analyzer_version=settings.analyzer_prompt_version,
            prompt_hash="x",
            created_at=datetime(2026, 5, 21, 10, 0, tzinfo=ZoneInfo(TZ)),
        ),
    )

    result = run_report_project(conn, settings, "p1")
    assert result.run_id is not None
    assert result.report.output_path.is_file()

    runs = list_report_runs(conn, project_id="p1")
    assert len(runs) == 1
    assert runs[0].run_id == result.run_id
    conn.close()
