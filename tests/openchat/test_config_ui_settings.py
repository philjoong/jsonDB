from __future__ import annotations

from pathlib import Path

import yaml

from openchat.config import effective_scope_days, load_settings


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")


def test_load_settings_merges_ui_settings(monkeypatch, tmp_path: Path):
    projects = tmp_path / "projects.yaml"
    ui = tmp_path / "ui_settings.yaml"
    _write_yaml(
        projects,
        {
            "projects": [
                {"id": "p1", "label": "P1", "enabled": True, "titles": ["방"]},
            ]
        },
    )
    _write_yaml(
        ui,
        {
            "collect_interval_minutes": 20,
            "analyzer_period": "12h",
            "reporter_window": "5d",
            "data_scope": {
                "mode": "last_days",
                "last_days": 5,
                "time_field": "message_at",
                "tz": "Asia/Seoul",
            },
        },
    )
    monkeypatch.setenv("PROJECTS_CONFIG", str(projects))
    monkeypatch.setenv("UI_SETTINGS_CONFIG", str(ui))
    monkeypatch.delenv("ROOMS_CONFIG", raising=False)

    settings = load_settings(env_path=None)
    assert settings.collect_interval_minutes == 20
    assert settings.analyzer_period == "12h"
    assert settings.reporter_window == "5d"
    assert settings.data_scope.last_days == 5
    assert effective_scope_days(settings) == 5
