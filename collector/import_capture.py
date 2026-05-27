"""Import Kakao clipboard capture files into the messages DB (no GUI)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db.messages import insert_messages, sync_rooms
from openchat.config import AppSettings, RoomConfig
from parser.kakao_clipboard import parse_kakao_clipboard_text


def split_capture_file(text: str) -> tuple[dict[str, str], str]:
    """Split a capture file into header key/value lines and chat body."""
    lines = text.splitlines()
    header: dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            break
        if ":" in line and not line.lstrip().startswith("["):
            key, _, val = line.partition(":")
            key = key.strip()
            if key:
                header[key] = val.strip()
                i += 1
                continue
        break
    body = "\n".join(lines[i:]).lstrip("\n")
    return header, body


def import_capture_file(
    conn,
    path: Path,
    settings: AppSettings,
    *,
    room_id: str | None = None,
    collected_at: datetime | None = None,
) -> tuple[str, int]:
    """Parse a capture file and insert messages. Returns (room_id, new_count)."""
    text = path.read_text(encoding="utf-8")
    header, body = split_capture_file(text)
    rid = room_id or header.get("room_id")
    if not rid:
        raise ValueError(
            f"room_id not found in {path}; pass --room-id or use a capture with room_id header"
        )
    if not body.strip():
        return rid, 0

    room_cfg = next((r for r in settings.rooms if r.id == rid), None)
    if room_cfg is None:
        room_cfg = RoomConfig(id=rid, title=rid, label=rid, enabled=True)
    sync_rooms(conn, [room_cfg])

    tz = ZoneInfo(settings.tz)
    when = collected_at
    if when is None:
        raw_ts = header.get("captured_at")
        if raw_ts:
            try:
                when = datetime.fromisoformat(raw_ts)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=tz)
            except ValueError:
                when = None
    if when is None:
        when = datetime.now(tz)

    parsed = parse_kakao_clipboard_text(
        body,
        rid,
        tz=settings.tz,
        exclude_nicks=settings.exclude_nicks,
        exclude_body_patterns=settings.exclude_body_patterns,
    )
    new_count = insert_messages(conn, rid, parsed, collected_at=when)
    return rid, new_count
