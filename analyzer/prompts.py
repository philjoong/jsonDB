"""Prompt templates for Periodic Analyzer (EXAONE 3.5 7.8B Instruct)."""

from __future__ import annotations

import hashlib
from typing import Any

from analyzer.bucketizer import Bucket

# Valid JSON example only (no | unions — models copy them and break JSON).
OUTPUT_SCHEMA_HINT = """
{
  "room_id": "room-slug",
  "period_key": "2026-05-26",
  "period_start": "2026-05-26T00:00:00+09:00",
  "period_end": "2026-05-27T00:00:00+09:00",
  "period_type": "1d",
  "message_count": 0,
  "coverage": "partial",
  "conversation_contexts": [
    {
      "context_id": "ctx_1",
      "label": "short Korean context label",
      "summary": "1 sentence: the connected exchange before topic extraction",
      "message_ids": [1, 2],
      "nicks": ["nick1", "nick2"],
      "first_message_id": 1,
      "last_message_id": 2
    }
  ],
  "topics": [
    {
      "tag": "balance",
      "title": "short Korean title",
      "topic_key": "snake_case_id",
      "summary": "1 sentence: what specifically was discussed",
      "context_ids": ["ctx_1"],
      "mentions": 0,
      "distinct_nicks": 0,
      "underrepresented": false,
      "quote_refs": [{ "message_id": 1 }]
    }
  ]
}
""".strip()

_ENUM_NOTES = """
Enums (pick ONE string value each, never use | in output):
- coverage: high, partial, low
- topic.tag: bug, balance, event, ops, meta, general
""".strip()


def build_system_prompt(*, min_distinct_nicks: int, prompt_version: str) -> str:
    return f"""You analyze KakaoTalk open-chat logs for a game community (Periodic Analyzer).
Prompt version: {prompt_version}.

Rules:
- Output ONLY one JSON object. No markdown, no commentary.
- Must be valid RFC 8259 JSON: double-quoted strings, no trailing commas, no | union syntax in values.
- At most 12 topics. Keep strings short so the JSON completes.
- Use the exact schema below. Field names must match.
- `message_count` must equal the number of chat lines provided (not your estimate).
- Before extracting topics, group nearby chat lines that are part of the same conversation context:
  same question/answer thread, same complaint with replies, same event/balance discussion, or follow-up clarification.
- Put those groups in `conversation_contexts`. Each context should contain 2-30 related messages when possible;
  one-message contexts are allowed only for standalone substantive remarks.
- `conversation_contexts[].message_ids` must only use ids from the input, in chronological order.
- Extract topics from these conversation contexts, not from isolated keywords.
- Each topic SHOULD include `context_ids` for the contexts it came from.
- For each topic, set `distinct_nicks` to the count of unique `nick` values that discussed that topic in the period.
- Set `underrepresented` true when `distinct_nicks` < {min_distinct_nicks}.
- Do NOT invent message text. For `quote_refs`, only use `message_id` values from the input or `search_phrase` copied from real chat.
- Each topic MUST include `summary` (what was said/asked) and at least one `quote_refs` entry when messages exist for that topic.
- For bug topics, `summary` must name symptoms/context (where/when/how), not only "크래시 버그".
- Prefer substantive topics (balance, bugs, events). Ignore spam and pure reaction chains (ㅋㅋ, 동의 only).
- Cap inflated counts: one nick's repeated short reactions count at most once toward `mentions` per topic.
- Sort `topics` by importance (participation breadth first, then mentions).
- `coverage`: high if most messages are on-topic game discussion; partial if mixed; low if very few messages.

Schema (example shape — replace values with your analysis):
{OUTPUT_SCHEMA_HINT}

{_ENUM_NOTES}
"""


def format_message_block(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for r in rows:
        mid = r.get("message_id")
        nick = r.get("nick", "")
        at = r.get("message_at", "")
        body = str(r.get("body", "")).replace("\n", " ").strip()
        lines.append(f"{mid}\t{nick}\t{at}\t{body}")
    return "\n".join(lines)


def build_user_prompt(
    bucket: Bucket,
    *,
    room_label: str,
    messages_block: str,
    message_count: int,
    truncated_note: str | None = None,
) -> str:
    trunc = ""
    if truncated_note:
        trunc = f"\nNote: {truncated_note}\n"
    return f"""Room: {bucket.room_id} ({room_label})
Period key: {bucket.period_key}
Period type: {bucket.period_type}
Period start: {bucket.period_start.isoformat(timespec="seconds")}
Period end: {bucket.period_end.isoformat(timespec="seconds")}
Messages in bucket (count={message_count}):{trunc}

--- Chat log (message_id TAB nick TAB message_at TAB body) ---
{messages_block}
"""


def prompt_fingerprint(
    *,
    bucket: Bucket,
    model_label: str,
    prompt_version: str,
    min_distinct_nicks: int,
    message_count: int,
    truncated: bool,
) -> str:
    payload = (
        f"{bucket.period_type}\0{bucket.period_key}\0{model_label}\0"
        f"{prompt_version}\0{min_distinct_nicks}\0{message_count}\0"
        f"trunc={truncated}\0exaone-v3"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
