"""Room registry sync and message persistence."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from openchat.config import RoomConfig


@dataclass(frozen=True)
class ParsedMessage:
    nick: str
    message_at: datetime
    body: str
    content_hash: str


def compute_content_hash(
    room_id: str,
    nick: str,
    message_at: datetime,
    body: str,
) -> str:
    """Hash(room_id + nick + message_at + normalized_body)."""
    normalized_body = body.strip()
    message_at_key = message_at.isoformat(timespec="seconds")
    payload = f"{room_id}\0{nick}\0{message_at_key}\0{normalized_body}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sync_rooms(conn: sqlite3.Connection, rooms: list[RoomConfig]) -> None:
    for room in rooms:
        conn.execute(
            """
            INSERT INTO rooms (room_id, canonical_title, label, enabled)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(room_id) DO UPDATE SET
                canonical_title = excluded.canonical_title,
                label = excluded.label,
                enabled = 1
            """,
            (room.id, room.title, room.label),
        )
    conn.commit()


def insert_messages(
    conn: sqlite3.Connection,
    room_id: str,
    messages: list[ParsedMessage],
    *,
    collected_at: datetime,
) -> int:
    """Insert parsed messages; ignore duplicates by content_hash. Returns new row count."""
    if not messages:
        return 0

    collected_key = collected_at.isoformat(timespec="seconds")
    rows = [
        (
            room_id,
            collected_key,
            msg.message_at.isoformat(timespec="seconds"),
            msg.nick,
            msg.body,
            msg.content_hash,
        )
        for msg in messages
    ]
    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO messages
            (room_id, collected_at, message_at, nick, body, content_hash)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def purge_messages_older_than(
    conn: sqlite3.Connection,
    *,
    cutoff: datetime,
) -> int:
    """Delete messages with collected_at before cutoff. Returns deleted count."""
    cutoff_key = cutoff.isoformat(timespec="seconds")
    cur = conn.execute(
        "DELETE FROM messages WHERE collected_at < ?",
        (cutoff_key,),
    )
    conn.commit()
    return cur.rowcount
