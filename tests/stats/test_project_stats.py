from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from db.connection import init_db
from db.insights import PeriodicInsightRow, upsert_periodic_insight
from db.messages import ParsedMessage, insert_messages, sync_rooms
from openchat.config import ProjectConfig, load_settings
from stats.project_stats import query_project_stats, resolve_data_window

TZ = "Asia/Seoul"


def _setup_env(monkeypatch, tmp_path: Path) -> None:
    projects = tmp_path / "projects.yaml"
    ui = tmp_path / "ui_settings.yaml"
    db = tmp_path / "db.sqlite"
    projects.write_text(
        yaml.safe_dump(
            {
                "projects": [
                    {"id": "p1", "label": "P1", "enabled": True, "titles": ["방"]},
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    ui.write_text(
        yaml.safe_dump(
            {
                "data_scope": {
                    "mode": "last_days",
                    "last_days": 7,
                    "time_field": "message_at",
                    "tz": TZ,
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROJECTS_CONFIG", str(projects))
    monkeypatch.setenv("UI_SETTINGS_CONFIG", str(ui))
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setenv("TZ", TZ)


def test_query_project_stats_scoped(monkeypatch, tmp_path: Path):
    _setup_env(monkeypatch, tmp_path)
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
                message_at=datetime(2026, 5, 25, 10, 0, tzinfo=ZoneInfo(TZ)),
                body="hello",
                content_hash="h1",
            ),
            ParsedMessage(
                nick="u2",
                message_at=datetime(2026, 5, 24, 10, 0, tzinfo=ZoneInfo(TZ)),
                body="hi",
                content_hash="h2",
            ),
        ],
        collected_at=now,
    )

    upsert_periodic_insight(
        conn,
        PeriodicInsightRow(
            room_id="p1",
            period_key="2026-05-25",
            period_start=datetime(2026, 5, 25, 0, 0, tzinfo=ZoneInfo(TZ)),
            period_end=datetime(2026, 5, 26, 0, 0, tzinfo=ZoneInfo(TZ)),
            period_type="1d",
            message_count=2,
            coverage="low",
            topics=[
                {
                    "tag": "general",
                    "topic_key": "t1",
                    "title": "주제A",
                    "mentions": 5,
                    "distinct_nicks": 3,
                }
            ],
            patch_reactions=[
                {
                    "patch_item": "패치1",
                    "stance": "negative",
                    "mentions": 2,
                    "distinct_nicks": 2,
                }
            ],
            analyzer_model="test",
            analyzer_version=settings.analyzer_prompt_version,
            prompt_hash="x",
            created_at=now,
        ),
    )

    stats = query_project_stats(conn, settings, "p1", project_label="P1", now=now)
    assert stats.message_count == 2
    assert stats.distinct_nicks == 2
    assert stats.insight_bucket_count == 1
    assert len(stats.top_topics) == 1
    assert stats.top_topics[0]["title"] == "주제A"
    assert len(stats.top_patches) == 1
    assert stats.window.last_days == 7
    conn.close()


def test_resolve_data_window_uses_ui_settings(monkeypatch, tmp_path: Path):
    _setup_env(monkeypatch, tmp_path)
    settings = load_settings()
    window = resolve_data_window(
        settings, now=datetime(2026, 5, 26, 12, 0, tzinfo=ZoneInfo(TZ))
    )
    assert window.last_days == 7
    assert window.time_field == "message_at"
