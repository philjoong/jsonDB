from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from analyzer.periodic import AnalyzedInsight
from db.connection import init_db
from db.messages import ParsedMessage, compute_content_hash, insert_messages, sync_rooms
from openchat.config import ProjectConfig, load_settings
from openchat.pipeline import run_analyze_project

TZ = ZoneInfo("Asia/Seoul")


def _write_projects(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "projects": [
                    {"id": "p1", "label": "P1", "enabled": True, "titles": ["Room A"]},
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def _msg(room_id: str, body: str, when: datetime) -> ParsedMessage:
    return ParsedMessage(
        nick="u",
        message_at=when,
        body=body,
        content_hash=compute_content_hash(room_id, "u", when, body),
    )


def test_run_analyze_project_continues_after_bucket_failure(
    tmp_path: Path,
    monkeypatch,
):
    db = tmp_path / "openchat.db"
    projects = tmp_path / "projects.yaml"
    ui = tmp_path / "ui_settings.yaml"
    _write_projects(projects)
    ui.write_text(
        yaml.safe_dump({"analyzer_period": "1d"}, allow_unicode=True),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROJECTS_CONFIG", str(projects))
    monkeypatch.setenv("UI_SETTINGS_CONFIG", str(ui))
    monkeypatch.setenv("DATABASE_PATH", str(db))

    settings = load_settings()
    conn = init_db(settings.database_path)
    sync_rooms(conn, [ProjectConfig(id="p1", titles=["Room A"], label="P1")])
    d1 = datetime(2026, 5, 20, 10, 0, tzinfo=TZ)
    d2 = datetime(2026, 5, 21, 10, 0, tzinfo=TZ)
    insert_messages(conn, "p1", [_msg("p1", "first", d1)], collected_at=d1)
    insert_messages(conn, "p1", [_msg("p1", "second", d2)], collected_at=d2)

    def fake_analyze_bucket(_conn, bucket, *_args, **_kwargs):
        if bucket.period_key == "2026-05-20":
            raise RuntimeError("LLM returned no topics")
        return AnalyzedInsight(
            message_count=1,
            coverage="partial",
            topics=[
                {
                    "tag": "general",
                    "title": "second topic",
                    "topic_key": "second_topic",
                    "mentions": 1,
                    "distinct_nicks": 1,
                }
            ],
            patch_reactions=[],
            prompt_hash="hash",
        )

    monkeypatch.setattr("openchat.pipeline.analyze_bucket", fake_analyze_bucket)

    result = run_analyze_project(conn, settings, "p1", include_current=True)

    assert result.processed == 1
    assert result.processed_buckets == [("p1", "2026-05-21")]
    assert result.failed_buckets[0]["period_key"] == "2026-05-20"
    rows = conn.execute("SELECT period_key, topics_json FROM periodic_insights").fetchall()
    assert [r["period_key"] for r in rows] == ["2026-05-21"]
    conn.close()
