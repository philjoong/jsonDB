"""Parse KakaoTalk clipboard export text into logical messages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from db.messages import ParsedMessage, compute_content_hash

DATE_LINE = re.compile(
    r"^(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일"
)
MESSAGE_LINE = re.compile(
    r"^\[(.+?)\]\s*\[(오전|오후)\s*(\d{1,2}):(\d{2})\]\s*(.*)$"
)

DEFAULT_EXCLUDE_BODY_PATTERNS = (
    "이모티콘을 보냈습니다",
    "삭제된 메시지",
    "메시지를 삭제했습니다",
)


@dataclass
class _DraftMessage:
    nick: str
    message_at: datetime
    body_parts: list[str]


def _korean_ampm_to_hour(ampm: str, hour12: int) -> int:
    if ampm == "오전":
        return 0 if hour12 == 12 else hour12
    return hour12 if hour12 == 12 else hour12 + 12


def _should_exclude_body(body: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern and pattern in body:
            return True
    return False


def parse_kakao_clipboard_text(
    text: str,
    room_id: str,
    *,
    tz: ZoneInfo | str = "Asia/Seoul",
    exclude_nicks: list[str] | None = None,
    exclude_body_patterns: list[str] | None = None,
) -> list[ParsedMessage]:
    """
    Parse clipboard capture into deduplicated logical messages.

    Rules: development-plan.md §2.1
    """
    if isinstance(tz, str):
        tz = ZoneInfo(tz)

    exclude_nicks = exclude_nicks or []
    exclude_nick_set = {n.strip() for n in exclude_nicks if n.strip()}
    body_patterns = list(DEFAULT_EXCLUDE_BODY_PATTERNS)
    if exclude_body_patterns:
        body_patterns.extend(exclude_body_patterns)

    current_date: tuple[int, int, int] | None = None
    draft: _DraftMessage | None = None
    results: list[ParsedMessage] = []

    def flush_draft() -> None:
        nonlocal draft
        if draft is None:
            return
        body = "\n".join(draft.body_parts).strip()
        if not body or _should_exclude_body(body, body_patterns):
            draft = None
            return
        content_hash = compute_content_hash(
            room_id,
            draft.nick,
            draft.message_at,
            body,
        )
        results.append(
            ParsedMessage(
                nick=draft.nick,
                message_at=draft.message_at,
                body=body,
                content_hash=content_hash,
            )
        )
        draft = None

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        date_match = DATE_LINE.match(line)
        if date_match:
            flush_draft()
            year, month, day = (int(x) for x in date_match.groups())
            current_date = (year, month, day)
            continue

        msg_match = MESSAGE_LINE.match(line)
        if msg_match:
            flush_draft()
            nick, ampm, hour_s, minute_s, body = msg_match.groups()
            if nick in exclude_nick_set:
                continue
            if current_date is None:
                continue
            if _should_exclude_body(body, body_patterns):
                continue

            hour = _korean_ampm_to_hour(ampm, int(hour_s))
            minute = int(minute_s)
            y, m, d = current_date
            message_at = datetime(y, m, d, hour, minute, tzinfo=tz)
            draft = _DraftMessage(nick=nick, message_at=message_at, body_parts=[body])
            continue

        if draft is not None:
            if _should_exclude_body(line, body_patterns):
                flush_draft()
                continue
            draft.body_parts.append(line)

    flush_draft()
    return results
