"""Read/write operational UI settings (config/ui_settings.yaml)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from openchat.config import DataScopeConfig
from openchat.projects_store import atomic_write_yaml


def resolve_ui_settings_path() -> Path:
    raw = (os.getenv("UI_SETTINGS_CONFIG") or "").strip()
    if raw:
        return Path(raw)
    return Path("config/ui_settings.yaml")


@dataclass
class UiSettings:
    """Operational settings editable from the web UI (non-secret)."""

    collect_interval_minutes: int = 10
    analyzer_period: str = "1d"
    reporter_window: str = "7d"
    data_scope: DataScopeConfig = field(default_factory=DataScopeConfig)

    def to_dict(self) -> dict[str, Any]:
        scope = self.data_scope
        tz = (scope.tz or "").strip()
        data_scope: dict[str, Any] = {
            "mode": scope.mode,
            "last_days": scope.last_days,
            "time_field": scope.time_field,
        }
        if tz:
            data_scope["tz"] = tz
        return {
            "collect_interval_minutes": self.collect_interval_minutes,
            "analyzer_period": self.analyzer_period,
            "reporter_window": self.reporter_window,
            "data_scope": data_scope,
        }


def _parse_data_scope(raw: dict[str, Any] | None, *, default_tz: str) -> DataScopeConfig:
    block = raw or {}
    mode = str(block.get("mode") or "last_days").strip() or "last_days"
    try:
        last_days = int(block.get("last_days", 7))
    except (TypeError, ValueError):
        last_days = 7
    last_days = max(1, last_days)
    time_field = str(block.get("time_field") or "message_at").strip() or "message_at"
    tz = str(block.get("tz") or default_tz).strip() or default_tz
    return DataScopeConfig(
        mode=mode,
        last_days=last_days,
        time_field=time_field,
        tz=tz,
    )


def load_ui_settings(
    path: Path | None = None,
    *,
    default_tz: str = "Asia/Seoul",
) -> UiSettings:
    """Load UI settings; missing file yields defaults."""
    config_path = path or resolve_ui_settings_path()
    if not config_path.is_file():
        return UiSettings(data_scope=DataScopeConfig(tz=default_tz))

    with config_path.open(encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    try:
        collect_interval = int(raw.get("collect_interval_minutes", 10))
    except (TypeError, ValueError):
        collect_interval = 10
    collect_interval = max(1, collect_interval)

    analyzer_period = str(raw.get("analyzer_period") or "1d").strip() or "1d"
    reporter_window = str(raw.get("reporter_window") or "7d").strip() or "7d"
    scope = _parse_data_scope(raw.get("data_scope"), default_tz=default_tz)

    return UiSettings(
        collect_interval_minutes=collect_interval,
        analyzer_period=analyzer_period,
        reporter_window=reporter_window,
        data_scope=scope,
    )


class UiSettingsStore:
    """In-memory UI settings backed by YAML."""

    def __init__(self, config_path: Path | None = None, *, default_tz: str = "Asia/Seoul") -> None:
        self._config_path = config_path or resolve_ui_settings_path()
        self._default_tz = default_tz
        self._settings = load_ui_settings(self._config_path, default_tz=default_tz)

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def settings(self) -> UiSettings:
        return self._settings

    def reload(self, *, default_tz: str | None = None) -> UiSettings:
        if default_tz is not None:
            self._default_tz = default_tz
        self._settings = load_ui_settings(self._config_path, default_tz=self._default_tz)
        return self._settings

    def update(
        self,
        *,
        collect_interval_minutes: int | None = None,
        analyzer_period: str | None = None,
        reporter_window: str | None = None,
        data_scope: DataScopeConfig | None = None,
    ) -> UiSettings:
        if collect_interval_minutes is not None:
            self._settings.collect_interval_minutes = max(1, int(collect_interval_minutes))
        if analyzer_period is not None:
            p = analyzer_period.strip()
            if not p:
                raise ValueError("analyzer_period cannot be empty")
            self._settings.analyzer_period = p
        if reporter_window is not None:
            w = reporter_window.strip()
            if not w:
                raise ValueError("reporter_window cannot be empty")
            self._settings.reporter_window = w
        if data_scope is not None:
            self._settings.data_scope = data_scope
        self._persist()
        return self._settings

    def update_scope(
        self,
        *,
        last_days: int,
        mode: str = "last_days",
        time_field: str = "message_at",
        tz: str | None = None,
        sync_reporter_window: bool = True,
    ) -> UiSettings:
        last_days = max(1, int(last_days))
        mode = (mode or "last_days").strip() or "last_days"
        time_field = (time_field or "message_at").strip() or "message_at"
        scope_tz = (tz or self._settings.data_scope.tz or self._default_tz).strip()
        self._settings.data_scope = DataScopeConfig(
            mode=mode,
            last_days=last_days,
            time_field=time_field,
            tz=scope_tz,
        )
        if sync_reporter_window and mode == "last_days":
            self._settings.reporter_window = f"{last_days}d"
        self._persist()
        return self._settings

    def _persist(self) -> None:
        atomic_write_yaml(self._config_path, self._settings.to_dict())
