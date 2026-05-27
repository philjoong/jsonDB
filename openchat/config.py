"""Load settings from .env and rooms/projects YAML."""

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
class ProjectConfig:
    """
    Project configuration.

    Backward compatibility:
    - Old schema uses a single `title`.
    - New schema supports multiple `titles` for one project id.
    """

    id: str
    # Legacy schema uses a single title. Keep `title` as an init arg for tests/old code.
    title: str = ""
    # New schema supports multiple titles.
    titles: list[str] = field(default_factory=list)
    label: str = ""
    enabled: bool = True
    update_notes_url: str = ""
    email_sender: str = ""
    email_receivers: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Normalize: ensure `titles` is always populated and `title` mirrors first.
        norm_titles = [str(t).strip() for t in (self.titles or []) if str(t).strip()]
        if not norm_titles and str(self.title or "").strip():
            norm_titles = [str(self.title).strip()]
        if not norm_titles:
            raise ValueError("ProjectConfig requires title or titles")
        self.titles = norm_titles
        self.title = norm_titles[0]


# Backward-compatible alias (existing code imports RoomConfig)
RoomConfig = ProjectConfig


@dataclass
class DataScopeConfig:
    """Shared message/report/stats window (persisted in ui_settings.yaml)."""

    mode: str = "last_days"
    last_days: int = 7
    time_field: str = "message_at"
    tz: str = "Asia/Seoul"


def parse_project_email(item: dict[str, Any]) -> tuple[str, list[str]]:
    """Parse sender/receivers from project YAML (nested ``email`` or flat keys)."""
    block = item.get("email") if isinstance(item.get("email"), dict) else {}
    sender = str(block.get("sender") or item.get("email_sender") or "").strip()
    raw_receivers = block.get("receivers") or item.get("email_receivers") or []
    if isinstance(raw_receivers, str):
        raw_receivers = [line.strip() for line in raw_receivers.replace(",", "\n").splitlines()]
    receivers = [str(r).strip() for r in raw_receivers if str(r).strip()]
    return sender, receivers


def parse_window_days(window: str) -> int:
    """Parse reporter window strings like ``7d`` or plain day counts."""
    w = (window or "").strip().lower()
    if not w:
        return 7
    if w.endswith("d"):
        try:
            return max(1, int(w[:-1]))
        except ValueError:
            return 7
    try:
        return max(1, int(w))
    except ValueError:
        return 7


def effective_scope_days(settings: AppSettings) -> int:
    """Days used for report/stats when data_scope mode is last_days."""
    if settings.data_scope.mode == "last_days":
        return max(1, settings.data_scope.last_days)
    return parse_window_days(settings.reporter_window)


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
    ui_settings_config: Path = Path("config/ui_settings.yaml")
    data_scope: DataScopeConfig = field(default_factory=DataScopeConfig)
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
    email_api_base_url: str = ""

    @property
    def analyzer_model_label(self) -> str:
        """Model id stored in periodic_insights (base + quantization)."""
        return f"{self.analyzer_model}@{self.analyzer_quantization}"


def load_settings(env_path: Path | None = None) -> AppSettings:
    """Load .env (if present) and rooms/projects YAML into AppSettings."""
    if env_path:
        if not env_path.is_file():
            raise SystemExit(f".env file not found: {env_path}")
        load_dotenv(env_path)
    else:
        load_dotenv()

    # Config path priority:
    # 1) explicit PROJECTS_CONFIG
    # 2) explicit ROOMS_CONFIG
    # 3) auto-detect: prefer config/projects.yaml if present, else config/rooms.yaml
    projects_env = (os.getenv("PROJECTS_CONFIG") or "").strip()
    rooms_env = (os.getenv("ROOMS_CONFIG") or "").strip()
    if projects_env:
        rooms_path = Path(projects_env)
    elif rooms_env:
        rooms_path = Path(rooms_env)
    else:
        projects_path = Path("config/projects.yaml")
        rooms_path = projects_path if projects_path.is_file() else Path("config/rooms.yaml")
    if not rooms_path.is_file():
        raise SystemExit(
            f"Rooms/projects config not found: {rooms_path}. "
            "Create config/projects.yaml (preferred) or config/rooms.yaml."
        )

    with rooms_path.open(encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    rooms: list[RoomConfig] = []
    items = raw.get("projects") or raw.get("rooms") or []
    for item in items:
        if not item.get("enabled", True):
            continue
        project_id = item.get("id")
        titles = item.get("titles")
        legacy_title = item.get("title")
        if titles is None:
            titles = [legacy_title] if legacy_title else []
        if not project_id or not titles:
            raise SystemExit(
                "Each project needs id and titles (or legacy title): "
                f"{item!r}"
            )
        norm_titles = [str(t).strip() for t in titles if str(t).strip()]
        if not norm_titles:
            raise SystemExit(f"Project titles cannot be empty: {item!r}")
        email_sender, email_receivers = parse_project_email(item)
        rooms.append(
            RoomConfig(
                id=str(project_id).strip(),
                title=norm_titles[0],
                titles=norm_titles,
                label=str(item.get("label") or norm_titles[0]),
                enabled=True,
                update_notes_url=str(item.get("update_notes_url") or "").strip(),
                email_sender=email_sender,
                email_receivers=email_receivers,
            )
        )

    if not rooms:
        raise SystemExit(f"No enabled rooms in {rooms_path}")

    tz = os.getenv("TZ", "Asia/Seoul")
    settings = AppSettings(
        tz=tz,
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
        ui_settings_config=Path(
            (os.getenv("UI_SETTINGS_CONFIG") or "config/ui_settings.yaml").strip()
            or "config/ui_settings.yaml"
        ),
        data_scope=DataScopeConfig(tz=tz),
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
        email_api_base_url=str(os.getenv("EMAIL_API_BASE_URL", "")).strip().rstrip("/"),
    )
    return _merge_ui_settings(settings)


def _merge_ui_settings(settings: AppSettings) -> AppSettings:
    """Apply config/ui_settings.yaml overrides (non-secret operational values)."""
    from openchat.ui_settings_store import load_ui_settings

    ui_path = settings.ui_settings_config
    if not ui_path.is_file():
        return settings

    ui = load_ui_settings(ui_path, default_tz=settings.tz)
    settings.collect_interval_minutes = ui.collect_interval_minutes
    settings.analyzer_period = ui.analyzer_period
    settings.reporter_window = ui.reporter_window
    settings.data_scope = ui.data_scope
    if not (settings.data_scope.tz or "").strip():
        settings.data_scope.tz = settings.tz
    return settings
