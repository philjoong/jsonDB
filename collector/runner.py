"""Multi-room sequential collection with per-room snapshot diff."""

from __future__ import annotations

import logging
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from db.collect_runs import record_collect_run
from db.connection import init_db
from db.messages import insert_messages, sync_rooms
from openchat.config import AppSettings, RoomConfig
from parser.kakao_clipboard import parse_kakao_clipboard_text

from collector.clipboard import capture_visible_messages_from_hwnd, find_room_windows
from collector.diff import extract_new_content
from collector.title import normalize_title

logger = logging.getLogger("openchat.collect")


@dataclass
class RoomCollectResult:
    room_id: str
    label: str
    status: str  # ok | skipped | error
    canonical_title: str | None = None
    room_title: str | None = None
    new_line_count: int = 0
    new_message_count: int = 0
    total_line_count: int = 0
    capture_path: Path | None = None
    diff_path: Path | None = None
    error: str | None = None


@dataclass
class CollectCycleResult:
    started_at: datetime
    finished_at: datetime | None = None
    rooms: list[RoomCollectResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.rooms if r.status == "ok")

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.rooms if r.status == "skipped")

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.rooms if r.status == "error")


def _snapshot_path(state_dir: Path, room_id: str) -> Path:
    return state_dir / f"{room_id}_last.txt"


def _read_snapshot(state_dir: Path, room_id: str) -> str:
    path = _snapshot_path(state_dir, room_id)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _write_snapshot(state_dir: Path, room_id: str, text: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    _snapshot_path(state_dir, room_id).write_text(text, encoding="utf-8")


def _slugify_for_path(text: str) -> str:
    text = normalize_title(text)
    text = re.sub(r"[^0-9A-Za-z가-힣\-_ ]+", "", text)
    text = re.sub(r"\s+", "_", text).strip("_")
    return text or "room"


def _snapshot_key(project_id: str, canonical_title: str) -> str:
    slug = _slugify_for_path(canonical_title)
    return f"{project_id}__{slug}"


def _write_capture_file(
    captures_dir: Path,
    room_id: str,
    header: dict[str, Any],
    body: str,
    *,
    suffix: str,
) -> Path:
    room_dir = captures_dir / room_id
    room_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = room_dir / f"capture_{stamp}_{suffix}.txt"
    lines = [f"{key}: {value}" for key, value in header.items()]
    lines.append("")
    path.write_text("\n".join(lines) + body + ("\n" if body else ""), encoding="utf-8")
    return path


def _persist_messages(
    conn: sqlite3.Connection,
    room: RoomConfig,
    settings: AppSettings,
    full_text: str,
    *,
    collected_at: datetime,
) -> int:
    parsed = parse_kakao_clipboard_text(
        full_text,
        room.id,
        tz=settings.tz,
        exclude_nicks=settings.exclude_nicks,
        exclude_body_patterns=settings.exclude_body_patterns,
    )
    return insert_messages(conn, room.id, parsed, collected_at=collected_at)


def collect_room(
    room: RoomConfig,
    settings: AppSettings,
    conn: sqlite3.Connection | None = None,
    *,
    canonical_title: str | None = None,
    save_full_capture: bool = True,
    save_diff: bool = True,
) -> RoomCollectResult:
    """Collect one room; update last snapshot; optionally write capture files."""
    room_started = datetime.now()
    canonical = (canonical_title or room.title or "").strip()
    matches = find_room_windows(canonical)
    if not matches:
        logger.warning(
            "Room skipped (window not found): id=%s title=%r",
            room.id,
            canonical,
        )
        result = RoomCollectResult(
            room_id=room.id,
            label=room.label,
            status="skipped",
            canonical_title=canonical,
            error=f"window not found: {canonical!r}",
        )
        if conn is not None:
            record_collect_run(
                conn,
                started_at=room_started,
                finished_at=datetime.now(),
                room_id=room.id,
                status=result.status,
                new_message_count=0,
                error=result.error,
            )
        return result

    if len(matches) > 1:
        logger.warning(
            "Multiple windows for room id=%s title=%r; using first of %d",
            room.id,
            canonical,
            len(matches),
        )

    hwnd_main, window_title = matches[0]
    try:
        captured = capture_visible_messages_from_hwnd(
            hwnd_main,
            window_title,
            restore_clipboard=settings.restore_clipboard,
        )
    except Exception as exc:
        logger.error(
            "Room capture failed: id=%s title=%r error=%s",
            room.id,
            canonical,
            exc,
        )
        result = RoomCollectResult(
            room_id=room.id,
            label=room.label,
            status="error",
            canonical_title=canonical,
            room_title=window_title,
            error=str(exc),
        )
        if conn is not None:
            record_collect_run(
                conn,
                started_at=room_started,
                finished_at=datetime.now(),
                room_id=room.id,
                status=result.status,
                new_message_count=0,
                error=result.error,
            )
        return result

    full_text: str = captured["text"]
    collected_at = datetime.now(ZoneInfo(settings.tz))
    new_message_count = 0
    if conn is not None and full_text.strip():
        new_message_count = _persist_messages(
            conn,
            room,
            settings,
            full_text,
            collected_at=collected_at,
        )
    snapshot_id = room.id
    if canonical and len(getattr(room, "titles", []) or []) > 1:
        snapshot_id = _snapshot_key(room.id, canonical)
    previous = _read_snapshot(settings.state_dir, snapshot_id)
    new_text, new_line_count = extract_new_content(previous, full_text)
    total_line_count = len(full_text.splitlines()) if full_text else 0

    header = {
        "room_id": room.id,
        "canonical_title": canonical,
        "room_title": captured["room_title"],
        "room_hwnd": captured["room_hwnd"],
        "list_hwnd": captured["list_hwnd"],
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "new_line_count": new_line_count,
        "total_line_count": total_line_count,
    }

    capture_path = None
    diff_path = None
    if save_full_capture and full_text:
        capture_path = _write_capture_file(
            settings.captures_dir,
            snapshot_id,
            header,
            full_text,
            suffix="full",
        )
    if save_diff and new_text:
        diff_path = _write_capture_file(
            settings.captures_dir,
            snapshot_id,
            header,
            new_text,
            suffix="diff",
        )

    if full_text:
        _write_snapshot(settings.state_dir, snapshot_id, full_text)

    logger.info(
        "Room ok: id=%s title=%r new_lines=%d new_messages=%d total_lines=%d",
        room.id,
        captured["room_title"],
        new_line_count,
        new_message_count,
        total_line_count,
    )
    result = RoomCollectResult(
        room_id=room.id,
        label=room.label,
        status="ok",
        canonical_title=canonical,
        room_title=captured["room_title"],
        new_line_count=new_line_count,
        new_message_count=new_message_count,
        total_line_count=total_line_count,
        capture_path=capture_path,
        diff_path=diff_path,
    )
    if conn is not None:
        record_collect_run(
            conn,
            started_at=room_started,
            finished_at=datetime.now(),
            room_id=room.id,
            status=result.status,
            new_message_count=new_message_count,
        )
    return result


def run_collect_cycle(
    settings: AppSettings,
    *,
    rooms: list[RoomConfig] | None = None,
    save_captures: bool = True,
) -> CollectCycleResult:
    """Iterate all enabled rooms sequentially (clipboard is global)."""
    target_rooms = rooms if rooms is not None else settings.rooms
    cycle = CollectCycleResult(started_at=datetime.now())
    logger.info(
        "Collect cycle started: rooms=%d interval_minutes=%d",
        len(target_rooms),
        settings.collect_interval_minutes,
    )

    conn = init_db(settings.database_path)
    sync_rooms(conn, target_rooms)
    try:
        for room in target_rooms:
            titles = list(getattr(room, "titles", []) or [])
            if not titles:
                titles = [room.title]
            for canonical_title in titles:
                result = collect_room(
                    room,
                    settings,
                    conn,
                    canonical_title=canonical_title,
                    save_full_capture=save_captures,
                    save_diff=save_captures,
                )
                if len(titles) > 1:
                    result.label = f"{room.label} / {canonical_title}"
                cycle.rooms.append(result)
    finally:
        conn.close()

    cycle.finished_at = datetime.now()
    elapsed = (cycle.finished_at - cycle.started_at).total_seconds()
    logger.info(
        "Collect cycle finished: ok=%d skipped=%d error=%d elapsed=%.1fs",
        cycle.ok_count,
        cycle.skipped_count,
        cycle.error_count,
        elapsed,
    )
    return cycle


def run_watch(
    settings: AppSettings,
    *,
    once: bool = False,
) -> None:
    """Run collect cycles every COLLECT_INTERVAL_MINUTES until interrupted."""
    interval_sec = settings.collect_interval_minutes * 60
    cycle_no = 0

    while True:
        cycle_no += 1
        logger.info("=== Watch cycle %d ===", cycle_no)
        cycle = run_collect_cycle(settings)
        print_cycle_summary(cycle)

        if once:
            break

        logger.info(
            "Sleeping %d minutes until next cycle (Ctrl+C to stop)",
            settings.collect_interval_minutes,
        )
        try:
            time.sleep(interval_sec)
        except KeyboardInterrupt:
            logger.info("Watch stopped by user")
            break


def print_cycle_summary(cycle: CollectCycleResult, file: Any = None) -> None:
    out = file or sys.stdout
    print(f"\n--- Collect cycle @ {cycle.started_at.isoformat(timespec='seconds')} ---", file=out)
    for room in cycle.rooms:
        if room.status == "ok":
            print(
                f"  [OK] {room.room_id} ({room.label}): "
                f"+{room.new_message_count} msgs / "
                f"+{room.new_line_count} lines / {room.total_line_count} total",
                file=out,
            )
        elif room.status == "skipped":
            print(f"  [SKIP] {room.room_id}: {room.error}", file=out)
        else:
            print(f"  [ERR] {room.room_id}: {room.error}", file=out)
    print(
        f"Summary: ok={cycle.ok_count} skipped={cycle.skipped_count} "
        f"error={cycle.error_count}",
        file=out,
    )
