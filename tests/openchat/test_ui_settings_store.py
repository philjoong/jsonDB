from __future__ import annotations

from pathlib import Path

import yaml

from openchat.ui_settings_store import UiSettingsStore, load_ui_settings


def _write_ui(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")


def test_load_ui_settings_defaults(tmp_path: Path):
    path = tmp_path / "ui_settings.yaml"
    ui = load_ui_settings(path, default_tz="Asia/Seoul")
    assert ui.collect_interval_minutes == 10
    assert ui.data_scope.last_days == 7
    assert ui.data_scope.time_field == "message_at"


def test_ui_settings_store_atomic_write(tmp_path: Path):
    path = tmp_path / "ui_settings.yaml"
    store = UiSettingsStore(path, default_tz="Asia/Seoul")
    store.update(collect_interval_minutes=15, analyzer_period="12h")
    store.update_scope(last_days=14, sync_reporter_window=True)

    reloaded = load_ui_settings(path, default_tz="Asia/Seoul")
    assert reloaded.collect_interval_minutes == 15
    assert reloaded.analyzer_period == "12h"
    assert reloaded.data_scope.last_days == 14
    assert reloaded.reporter_window == "14d"

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["collect_interval_minutes"] == 15
    assert raw["data_scope"]["last_days"] == 14


def test_ui_settings_store_reload_picks_disk_changes(tmp_path: Path):
    path = tmp_path / "ui_settings.yaml"
    store = UiSettingsStore(path, default_tz="Asia/Seoul")
    _write_ui(
        path,
        {
            "collect_interval_minutes": 5,
            "analyzer_period": "1d",
            "reporter_window": "3d",
            "data_scope": {"mode": "last_days", "last_days": 3, "time_field": "message_at"},
        },
    )
    store.reload()
    assert store.settings.collect_interval_minutes == 5
    assert store.settings.data_scope.last_days == 3
