"""Load settings from .env and rooms.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid integer for {name}: {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class RoomConfig:
    id: str
    title: str
    label: str = ""
    enabled: bool = True


@dataclass
class AppSettings:
    tz: str = "Asia/Seoul"
    database_path: Path = Path("data/openchat.db")
    retention_raw_days: int = 7
    collect_interval_minutes: int = 10
    min_distinct_nicks: int = 3
    rooms_config: Path = Path("config/rooms.yaml")
    captures_dir: Path = Path("captures")
    state_dir: Path = Path("data/state")
    output_dir: Path = Path("reports")
    patchnotes_path: Path = Path("docs/patchnotes.md")
    roadmap_path: Path = Path("docs/roadmap.md")
    rooms: list[RoomConfig] = field(default_factory=list)
    exclude_nicks: list[str] = field(default_factory=list)
    exclude_body_patterns: list[str] = field(default_factory=list)
    restore_clipboard: bool = True
    room_timeout_seconds: float = 30.0


def load_settings(env_path: Path | None = None) -> AppSettings:
    """Load .env (if present) and rooms YAML into AppSettings."""
    if env_path:
        if not env_path.is_file():
            raise SystemExit(f".env file not found: {env_path}")
        load_dotenv(env_path)
    else:
        load_dotenv()

    rooms_path = Path(os.getenv("ROOMS_CONFIG", "config/rooms.yaml"))
    if not rooms_path.is_file():
        raise SystemExit(
            f"Rooms config not found: {rooms_path}. "
            "Create config/rooms.yaml (see development-plan.md)."
        )

    with rooms_path.open(encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    rooms: list[RoomConfig] = []
    for item in raw.get("rooms") or []:
        if not item.get("enabled", True):
            continue
        room_id = item.get("id")
        title = item.get("title")
        if not room_id or not title:
            raise SystemExit(f"Each room needs id and title: {item!r}")
        rooms.append(
            RoomConfig(
                id=str(room_id),
                title=str(title),
                label=str(item.get("label") or title),
                enabled=True,
            )
        )

    if not rooms:
        raise SystemExit(f"No enabled rooms in {rooms_path}")

    return AppSettings(
        tz=os.getenv("TZ", "Asia/Seoul"),
        database_path=Path(os.getenv("DATABASE_PATH", "data/openchat.db")),
        retention_raw_days=_env_int("RETENTION_RAW_DAYS", 7),
        collect_interval_minutes=_env_int("COLLECT_INTERVAL_MINUTES", 10),
        min_distinct_nicks=_env_int("MIN_DISTINCT_NICKS", 3),
        rooms_config=rooms_path,
        captures_dir=Path(os.getenv("CAPTURES_DIR", "captures")),
        state_dir=Path(os.getenv("STATE_DIR", "data/state")),
        output_dir=Path(os.getenv("OUTPUT_DIR", "reports")),
        patchnotes_path=Path(os.getenv("PATCHNOTES_PATH", "docs/patchnotes.md")),
        roadmap_path=Path(os.getenv("ROADMAP_PATH", "docs/roadmap.md")),
        rooms=rooms,
        exclude_nicks=[str(x) for x in raw.get("exclude_nicks") or []],
        exclude_body_patterns=[str(x) for x in raw.get("exclude_body_patterns") or []],
        restore_clipboard=not _env_bool("NO_RESTORE_CLIPBOARD", False),
        room_timeout_seconds=float(os.getenv("ROOM_TIMEOUT_SECONDS", "30")),
    )
