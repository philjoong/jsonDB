"""Resolve quote references to raw message bodies (phase 4b).

Security / correctness constraint (development-plan.md §5.3):
- The reporter must only embed quotes retrieved from the DB `messages.body`.
  Never embed LLM-generated quote text directly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class ResolvedQuote:
    message_id: int
    room_id: str
    message_at: str
    nick: str
    body: str


def _parse_iso_date_prefix(s: str) -> str | None:
    """Return YYYY-MM-DD if parseable, else None."""
    ss = (s or "").strip()
    if not ss:
        return None
    try:
        # Allow full datetime as well.
        dt = datetime.fromisoformat(ss.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except Exception:
        try:
            d = date.fromisoformat(ss[:10])
            return d.isoformat()
        except Exception:
            return None


def resolve_quote(
    conn: sqlite3.Connection,
    ref: dict[str, Any],
    *,
    like_limit: int = 50,
) -> ResolvedQuote | None:
    """Resolve one quote ref.

    Supported ref shapes:
    - {"message_id": 123}
    - {"search_phrase": "...", "room_id": "...", "around": "2026-05-12"}
    """
    if not isinstance(ref, dict):
        return None

    mid = ref.get("message_id")
    if mid is not None:
        try:
            mid_i = int(mid)
        except (TypeError, ValueError):
            mid_i = -1
        if mid_i <= 0:
            return None
        row = conn.execute(
            """
            SELECT message_id, room_id, message_at, nick, body
            FROM messages
            WHERE message_id = ?
            """,
            (mid_i,),
        ).fetchone()
        if row is None:
            return None
        return ResolvedQuote(
            message_id=int(row["message_id"]),
            room_id=str(row["room_id"]),
            message_at=str(row["message_at"]),
            nick=str(row["nick"]),
            body=str(row["body"]),
        )

    phrase = ref.get("search_phrase") or ref.get("phrase") or ref.get("q")
    if not phrase or not str(phrase).strip():
        return None
    phrase_s = str(phrase).strip()
    room_id = ref.get("room_id")
    around = _parse_iso_date_prefix(str(ref.get("around") or ""))

    where = ["body LIKE ?"]
    params: list[Any] = [f"%{phrase_s}%"]
    if room_id:
        where.append("room_id = ?")
        params.append(str(room_id))
    if around:
        # day-bounded to keep LIKE search cheap and relevant
        where.append("message_at >= ? AND message_at < ?")
        params.append(f"{around}T00:00:00")
        params.append(f"{around}T23:59:59")

    sql = (
        "SELECT message_id, room_id, message_at, nick, body "
        "FROM messages "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY message_at DESC "
        "LIMIT ?"
    )
    params.append(int(max(1, like_limit)))
    row = conn.execute(sql, tuple(params)).fetchone()
    if row is None:
        return None
    return ResolvedQuote(
        message_id=int(row["message_id"]),
        room_id=str(row["room_id"]),
        message_at=str(row["message_at"]),
        nick=str(row["nick"]),
        body=str(row["body"]),
    )


def resolve_quotes(
    conn: sqlite3.Connection,
    refs: list[dict[str, Any]] | None,
    *,
    like_limit: int = 50,
    max_quotes: int = 20,
) -> tuple[list[ResolvedQuote], int]:
    """Resolve multiple refs. Returns (resolved_quotes, miss_count)."""
    if not refs:
        return ([], 0)
    resolved: list[ResolvedQuote] = []
    miss = 0
    for ref in refs[: max(0, int(max_quotes))]:
        q = resolve_quote(conn, ref, like_limit=like_limit)
        if q is None:
            miss += 1
            continue
        resolved.append(q)
    return (resolved, miss)

