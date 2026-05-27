from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from fastapi.testclient import TestClient

from db.connection import init_db
from db.insights import PeriodicInsightRow, upsert_periodic_insight
from db.messages import ParsedMessage, insert_messages, sync_rooms
from openchat import webapp
from openchat.config import ProjectConfig, load_settings
from openchat.projects_store import ProjectsStore

TZ = "Asia/Seoul"


def test_webapp_project_stats_api(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "projects.yaml"
    ui = tmp_path / "ui_settings.yaml"
    db = tmp_path / "t.db"
    cfg.write_text(
        yaml.safe_dump(
            {"projects": [{"id": "p1", "label": "P1", "enabled": True, "titles": ["방"]}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    ui.write_text(
        yaml.safe_dump(
            {"data_scope": {"mode": "last_days", "last_days": 7, "time_field": "message_at"}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROJECTS_CONFIG", str(cfg))
    monkeypatch.setenv("UI_SETTINGS_CONFIG", str(ui))
    monkeypatch.setenv("DATABASE_PATH", str(db))
    webapp._store = ProjectsStore(cfg)
    webapp._ui_store = None

    settings = load_settings()
    conn = init_db(settings.database_path)
    sync_rooms(conn, [ProjectConfig(id="p1", titles=["방"], label="P1", enabled=True)])
    now = datetime(2026, 5, 26, 12, 0, tzinfo=ZoneInfo(TZ))
    insert_messages(
        conn,
        "p1",
        [
            ParsedMessage(
                nick="u1",
                message_at=datetime(2026, 5, 25, 9, 0, tzinfo=ZoneInfo(TZ)),
                body="msg",
                content_hash="x1",
            ),
        ],
        collected_at=now,
    )
    upsert_periodic_insight(
        conn,
        PeriodicInsightRow(
            room_id="p1",
            period_key="2026-05-25",
            period_start=datetime(2026, 5, 25, tzinfo=ZoneInfo(TZ)),
            period_end=datetime(2026, 5, 26, tzinfo=ZoneInfo(TZ)),
            period_type="1d",
            message_count=1,
            coverage="low",
            topics=[],
            patch_reactions=[],
            analyzer_model="t",
            analyzer_version="v1",
            prompt_hash="h",
            created_at=now,
        ),
    )
    conn.close()

    client = TestClient(webapp.app)
    api = client.get("/api/projects/p1/stats")
    assert api.status_code == 200
    body = api.json()
    assert body["project_id"] == "p1"
    assert body["message_count"] == 1
    assert body["scope"]["last_days"] == 7

    page = client.get("/stats/projects/p1")
    assert page.status_code == 200
    assert "상위 주제" in page.text
