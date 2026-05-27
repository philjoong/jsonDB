"""Reporter LLM synthesis with static fallback."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from openchat.config import AppSettings
from report.payload import ReporterPayload, payload_to_user_dict
from report.reporter_llm import ReporterLLMError, create_reporter_client, reporter_chat_json
from report.reporter_prompts import build_system_prompt, build_user_prompt

logger = logging.getLogger(__name__)


@dataclass
class ReportSynthesis:
    executive_summary: str = ""
    highlights: list[str] = field(default_factory=list)
    topic_narratives: list[dict[str, Any]] = field(default_factory=list)
    patch_alignment: list[dict[str, Any]] = field(default_factory=list)
    gap_topics: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    quote_refs: list[dict[str, Any]] = field(default_factory=list)
    backend: str = "static"


def _as_str_list(val: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(val, list):
        return []
    out: list[str] = []
    for item in val[:limit]:
        if item is None:
            continue
        s = str(item).strip()
        if s:
            out.append(s)
    return out


def _collect_quote_refs(blocks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if not blocks:
        return refs
    for block in blocks:
        if not isinstance(block, dict):
            continue
        raw = block.get("quote_refs")
        if not isinstance(raw, list):
            continue
        for ref in raw:
            if isinstance(ref, dict) and (
                ref.get("message_id") is not None or ref.get("search_phrase")
            ):
                refs.append(ref)
    return refs


def normalize_synthesis(raw: dict[str, Any]) -> ReportSynthesis:
    topics = raw.get("topic_narratives")
    topic_list = [t for t in topics if isinstance(t, dict)] if isinstance(topics, list) else []
    refs = _collect_quote_refs(topic_list)
    return ReportSynthesis(
        executive_summary=str(raw.get("executive_summary") or "").strip(),
        highlights=_as_str_list(raw.get("highlights")),
        topic_narratives=topic_list[:8],
        quote_refs=refs,
        backend="llm",
    )


def static_synthesis(payload: ReporterPayload) -> ReportSynthesis:
    """Deterministic fallback when Reporter LLM is off or fails."""
    narratives: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    for ins in payload.insights:
        for t in ins.get("topics") or []:
            if not isinstance(t, dict):
                continue
            title = t.get("title") or t.get("topic_key")
            if not title:
                continue
            topic_refs = t.get("quote_refs") or []
            if isinstance(topic_refs, list):
                refs.extend(r for r in topic_refs if isinstance(r, dict))
            summary = str(t.get("summary") or "").strip()
            if not summary:
                summary = (
                    f"언급 {int(t.get('mentions') or 0)}회, "
                    f"{int(t.get('distinct_nicks') or 0)}명 참여"
                )
            narratives.append(
                {
                    "tag": t.get("tag"),
                    "title": title,
                    "topic_key": t.get("topic_key"),
                    "summary": summary,
                    "mentions": t.get("mentions"),
                    "distinct_nicks": t.get("distinct_nicks"),
                    "quote_refs": topic_refs if isinstance(topic_refs, list) else [],
                }
            )

    topics = payload.topic_stats[:5]
    lines: list[str] = []
    if not narratives:
        for t in topics:
            title = t.get("title") or t.get("topic_key")
            if not title:
                continue
            line = f"· {title} (언급 {t.get('mentions')}, 닉 {t.get('distinct_nicks')})"
            summary = str(t.get("summary") or "").strip()
            if summary:
                line += f" — {summary}"
            lines.append(line)
            narratives.append(
                {
                    "tag": t.get("tag"),
                    "title": title,
                    "topic_key": t.get("topic_key"),
                    "summary": summary or line,
                    "mentions": t.get("mentions"),
                    "distinct_nicks": t.get("distinct_nicks"),
                    "quote_refs": [],
                }
            )
    else:
        for t in narratives[:5]:
            title = t.get("title")
            line = (
                f"· {title} (언급 {t.get('mentions')}, 닉 {t.get('distinct_nicks')})"
            )
            summary = str(t.get("summary") or "").strip()
            if summary:
                line += f" — {summary}"
            lines.append(line)

    summary = (
        "Reporter LLM 없이 분석 요약만 반영한 리포트입니다. "
        f"기간 키 {len(payload.meta.get('period_keys') or [])}개, "
        f"분석 버킷 {payload.meta.get('bucket_count', 0)}건."
    )
    if lines:
        summary += " 상위 주제: " + "; ".join(lines[:3])
    return ReportSynthesis(
        executive_summary=summary,
        highlights=lines[:5],
        topic_narratives=narratives[:8],
        quote_refs=refs,
        backend="static",
    )


def synthesize_report(
    payload: ReporterPayload,
    settings: AppSettings,
) -> ReportSynthesis:
    """Call Reporter LLM or return static synthesis."""
    if not settings.reporter_use_llm:
        return static_synthesis(payload)

    api_key = (settings.reporter_api_key or "").strip()
    if not api_key:
        logger.warning(
            "REPORTER_API_KEY not set — skipping Reporter LLM, using static summary"
        )
        return static_synthesis(payload)

    system = build_system_prompt(min_distinct_nicks=settings.min_distinct_nicks)
    user = build_user_prompt(payload_to_user_dict(payload))
    client = create_reporter_client(settings)
    try:
        raw = reporter_chat_json(
            client,
            model=settings.reporter_model,
            system=system,
            user=user,
            temperature=settings.reporter_temperature,
            timeout_seconds=settings.reporter_timeout_seconds,
        )
        return normalize_synthesis(raw)
    except ReporterLLMError as exc:
        if settings.reporter_fallback_static:
            logger.warning("Reporter LLM failed (%s) — static fallback", exc)
            return static_synthesis(payload)
        raise
