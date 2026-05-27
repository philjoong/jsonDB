"""Prompt templates for Reporter LLM (final report synthesis)."""

from __future__ import annotations

import json
from typing import Any

OUTPUT_SCHEMA_HINT = """
{
  "executive_summary": "2-4문장 한국어 요약",
  "highlights": ["핵심 불릿 1", "핵심 불릿 2"],
  "topic_narratives": [
    {
      "tag": "balance",
      "title": "주제 제목",
      "summary": "2-3문장 설명 — 무엇이 논의/질문/불만인지 구체적으로",
      "quote_refs": [{ "message_id": 1 }]
    }
  ]
}
""".strip()

_ENUM_NOTES = """
Enums (문자열 하나만):
- topic_narratives[].tag: bug, balance, event, ops, meta, general
""".strip()


def build_system_prompt(*, min_distinct_nicks: int) -> str:
    return f"""You synthesize a Korean community insight report for game open-chat monitoring (Reporter).
You receive aggregated statistics, periodic insight summaries, and official update notes
fetched from per-room base URLs. You do NOT receive raw chat logs.

Rules:
- Output ONLY one JSON object. Valid RFC 8259 JSON. No markdown.
- All narrative text must be in Korean.
- Do NOT discuss roadmap, scheduled releases, future plans, recommendations, or gap analysis.
- Do NOT output patch_alignment, gap_topics, or recommendations fields.
- Do NOT invent chat quotes or message text. For evidence use `quote_refs` only:
  - `{{"message_id": <int>}}` if that id appeared in the input insights, OR
  - `{{"search_phrase": "<short phrase from input>", "room_id": "<slug>", "around": "YYYY-MM-DD"}}`
  The HTML report embeds resolved quotes under each topic card; you do not write quote text.
- Prefer topics with distinct_nicks >= {min_distinct_nicks}; mark underrepresented topics in summary text.
- At most 8 topic_narratives.
- `executive_summary` and `highlights` must be concrete: name WHAT was discussed
  (which event, balance item, crash symptom/context) — never only "주제명 (N회/M명)".
- For bug/event/balance topics, each `topic_narratives` entry SHOULD include 1-3 `quote_refs`
  from input `periodic_insights` when available, so the report can show original chat under the topic.
- If a topic lacks quote_refs or summary in the input, say what is unknown in `summary`.

Schema:
{OUTPUT_SCHEMA_HINT}

{_ENUM_NOTES}
"""


def build_user_prompt(payload_dict: dict[str, Any]) -> str:
    body = json.dumps(payload_dict, ensure_ascii=False, indent=2)
    return f"""아래 JSON은 리포트 기간의 집계·주기별 분석 요약·방별 공식 업데이트 노트입니다.
운영자용 HTML 리포트(요약 + 주제별 논의)에 들어갈 합성 JSON을 작성하세요.
주제별 논의에는 가능하면 quote_refs를 포함하세요 — 원문 인용은 각 주제 설명 아래에 표시됩니다.

--- 입력 데이터 ---
{body}
"""
