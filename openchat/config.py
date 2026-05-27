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
    update_notes_url: str = ""


@dataclass
class AppSettings:
    tz: str = "Asia/Seoul"
    database_path: Path = Path("data/openchat.db")
    retention_raw_days: int = 7
    collect_interval_minutes: int = 10
    min_distinct_nicks: int = 3
    analyzer_provider: str = "openai"
    openai_api_base: str = "http://localhost:11434/v1"
    analyzer_model: str = "EXAONE-3.5-7.8B-Instruct"
    analyzer_quantization: str = "Q4_K_M"
    analyzer_gguf_repo: str = "LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct-GGUF"
    analyzer_api_key: str = "ollama"
    analyzer_period: str = "1d"
    analyzer_prompt_version: str = "v1"
    analyzer_use_llm: bool = True
    analyzer_fallback_heuristic: bool = True
    analyzer_max_transcript_chars: int = 90_000
    analyzer_temperature: float = 0.2
    analyzer_timeout_seconds: float = 600.0
    reporter_provider: str = "openai"
    reporter_model: str = "gpt-5.2"
    reporter_api_key: str = ""
    reporter_use_llm: bool = True
    reporter_temperature: float = 0.3
    reporter_timeout_seconds: float = 300.0
    reporter_max_topics: int = 20
    reporter_max_patch_reactions: int = 15
    reporter_context_chars: int = 6000
    reporter_fallback_static: bool = True
    reporter_web_search: bool = True
    reporter_openai_api_base: str = "https://api.openai.com/v1"
    reporter_update_notes_max_pages: int = 8
    reporter_fetch_timeout_seconds: float = 45.0
    reporter_fetch_user_agent: str = (
        "OpenChatInsightBot/1.0 (+https://github.com/open-chat)"
    )
    reporter_window: str = "7d"
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

    @property
    def analyzer_model_label(self) -> str:
        """Model id stored in periodic_insights (base + quantization)."""
        return f"{self.analyzer_model}@{self.analyzer_quantization}"


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
                update_notes_url=str(item.get("update_notes_url") or "").strip(),
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
        analyzer_provider=str(os.getenv("ANALYZER_PROVIDER", "openai")).strip() or "openai",
        openai_api_base=str(
            os.getenv("OPENAI_API_BASE", "http://localhost:11434/v1")
        ).strip()
        or "http://localhost:11434/v1",
        analyzer_model=str(
            os.getenv("ANALYZER_MODEL", "EXAONE-3.5-7.8B-Instruct")
        ).strip()
        or "EXAONE-3.5-7.8B-Instruct",
        analyzer_quantization=str(os.getenv("ANALYZER_QUANTIZATION", "Q4_K_M")).strip()
        or "Q4_K_M",
        analyzer_gguf_repo=str(
            os.getenv(
                "ANALYZER_GGUF_REPO",
                "LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct-GGUF",
            )
        ).strip()
        or "LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct-GGUF",
        analyzer_api_key=str(os.getenv("ANALYZER_API_KEY", "ollama")).strip(),
        analyzer_period=str(os.getenv("ANALYZER_PERIOD", "1d")).strip() or "1d",
        analyzer_prompt_version=str(os.getenv("ANALYZER_PROMPT_VERSION", "v1")).strip()
        or "v1",
        analyzer_use_llm=_env_bool("ANALYZER_USE_LLM", True),
        analyzer_fallback_heuristic=_env_bool("ANALYZER_FALLBACK_HEURISTIC", True),
        analyzer_max_transcript_chars=_env_int("ANALYZER_MAX_TRANSCRIPT_CHARS", 90_000),
        analyzer_temperature=float(os.getenv("ANALYZER_TEMPERATURE", "0.2")),
        analyzer_timeout_seconds=float(os.getenv("ANALYZER_TIMEOUT_SECONDS", "600")),
        reporter_provider=str(os.getenv("REPORTER_PROVIDER", "openai")).strip()
        or "openai",
        reporter_model=str(os.getenv("REPORTER_MODEL", "gpt-5.2")).strip()
        or "gpt-5.2",
        reporter_api_key=str(os.getenv("REPORTER_API_KEY", "")).strip(),
        reporter_use_llm=_env_bool("REPORTER_USE_LLM", True),
        reporter_temperature=float(os.getenv("REPORTER_TEMPERATURE", "0.3")),
        reporter_timeout_seconds=float(os.getenv("REPORTER_TIMEOUT_SECONDS", "300")),
        reporter_max_topics=_env_int("REPORTER_MAX_TOPICS", 20),
        reporter_max_patch_reactions=_env_int("REPORTER_MAX_PATCH_REACTIONS", 15),
        reporter_context_chars=_env_int("REPORTER_CONTEXT_CHARS", 6000),
        reporter_fallback_static=_env_bool("REPORTER_FALLBACK_STATIC", True),
        reporter_web_search=_env_bool("REPORTER_WEB_SEARCH", True),
        # Reporter always uses OpenAI cloud by default (not ANALYZER's OPENAI_API_BASE / Ollama).
        reporter_openai_api_base=str(
            os.getenv("REPORTER_OPENAI_API_BASE", "https://api.openai.com/v1")
        ).strip()
        or "https://api.openai.com/v1",
        reporter_update_notes_max_pages=_env_int("REPORTER_UPDATE_NOTES_MAX_PAGES", 8),
        reporter_fetch_timeout_seconds=float(
            os.getenv("REPORTER_FETCH_TIMEOUT_SECONDS", "45")
        ),
        reporter_fetch_user_agent=str(
            os.getenv(
                "REPORTER_FETCH_USER_AGENT",
                "OpenChatInsightBot/1.0 (+https://github.com/open-chat)",
            )
        ).strip(),
        reporter_window=str(os.getenv("REPORTER_WINDOW", "7d")).strip() or "7d",
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
