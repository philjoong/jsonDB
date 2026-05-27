"""HTML report renderer (phase 5a + Reporter LLM synthesis)."""

from __future__ import annotations

import html
import json
from typing import Any
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from context.loader import load_context
from openchat.config import AppSettings, effective_scope_days
from report.charts import build_chart_scripts
from report.payload import build_reporter_payload
from report.quote_resolver import ResolvedQuote, resolve_quote
from report.scope import ReportScope, bucket_sql_in_clause, resolve_report_scope
from report.synthesize import ReportSynthesis, synthesize_report


def _parse_window_days(window: str) -> int:
    w = (window or "").strip().lower()
    if not w:
        return 7
    if w.endswith("d"):
        try:
            n = int(w[:-1])
            return max(1, n)
        except ValueError:
            return 7
    try:
        return max(1, int(w))
    except ValueError:
        return 7


def _h(s: object) -> str:
    return html.escape("" if s is None else str(s))


def _row_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


@dataclass(frozen=True)
class ReportResult:
    output_path: Path
    period_keys: list[str]
    quote_miss_count: int
    scope_mode: str
    bucket_count: int
    reporter_backend: str
    email_snapshot: dict[str, Any]


def _build_email_snapshot(
    *,
    synthesis: ReportSynthesis,
    narratives: list[dict[str, Any]],
    topic_dicts: list[dict[str, Any]],
    scope_label: str,
    bucket_count: int,
) -> dict[str, Any]:
    topics_out: list[dict[str, Any]] = []
    for t in narratives[:8]:
        if not isinstance(t, dict):
            continue
        topics_out.append(
            {
                "tag": t.get("tag"),
                "title": t.get("title") or t.get("topic_key"),
                "topic_key": t.get("topic_key"),
                "mentions": t.get("mentions"),
                "distinct_nicks": t.get("distinct_nicks"),
                "summary": t.get("summary") or t.get("narrative"),
            }
        )
    if not topics_out:
        ranked = sorted(
            topic_dicts,
            key=lambda r: (-int(r.get("mentions") or 0), -int(r.get("distinct_nicks") or 0)),
        )
        for r in ranked[:8]:
            topics_out.append(
                {
                    "tag": r.get("tag"),
                    "title": r.get("title") or r.get("topic_key"),
                    "topic_key": r.get("topic_key"),
                    "mentions": r.get("mentions"),
                    "distinct_nicks": r.get("distinct_nicks"),
                }
            )
    return {
        "executive_summary": synthesis.executive_summary,
        "highlights": list(synthesis.highlights[:6]),
        "topics": topics_out,
        "scope_label": scope_label,
        "bucket_count": bucket_count,
    }


def _collect_insight_quote_refs(insight_rows: list[sqlite3.Row]) -> list[dict]:
    refs: list[dict] = []
    for ir in insight_rows:
        topics = []
        raw = ir["topics_json"]
        if isinstance(raw, str) and raw.strip():
            try:
                topics = json.loads(raw)
            except Exception:
                topics = []
        if isinstance(topics, list):
            for t in topics:
                if isinstance(t, dict) and isinstance(t.get("quote_refs"), list):
                    for ref in t.get("quote_refs") or []:
                        if isinstance(ref, dict):
                            refs.append(ref)
    return refs


def _topics_from_insights(insight_rows: list[sqlite3.Row]) -> list[dict]:
    out: list[dict] = []
    for ir in insight_rows:
        raw = ir["topics_json"]
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            topics = json.loads(raw)
        except Exception:
            continue
        if not isinstance(topics, list):
            continue
        for t in topics:
            if not isinstance(t, dict):
                continue
            title = str(t.get("title") or "").strip()
            if not title:
                continue
            summary = str(t.get("summary") or "").strip()
            if not summary:
                summary = (
                    f"언급 {int(t.get('mentions') or 0)}회, "
                    f"{int(t.get('distinct_nicks') or 0)}명 참여"
                )
            out.append(
                {
                    "tag": t.get("tag"),
                    "title": title,
                    "topic_key": t.get("topic_key"),
                    "summary": summary,
                    "mentions": t.get("mentions"),
                    "distinct_nicks": t.get("distinct_nicks"),
                    "quote_refs": t.get("quote_refs") or [],
                }
            )
    return out


def _merge_insight_quotes(
    narratives: list[dict],
    insight_topics: list[dict],
) -> list[dict]:
    by_title = {
        str(t.get("title") or ""): t for t in insight_topics if t.get("title")
    }
    by_key = {
        str(t.get("topic_key") or ""): t for t in insight_topics if t.get("topic_key")
    }
    merged: list[dict] = []
    for narrative in narratives:
        item = dict(narrative)
        match = by_title.get(str(item.get("title") or "")) or by_key.get(
            str(item.get("topic_key") or "")
        )
        if match:
            if not item.get("quote_refs"):
                item["quote_refs"] = match.get("quote_refs") or []
            if item.get("mentions") is None:
                item["mentions"] = match.get("mentions")
            if item.get("distinct_nicks") is None:
                item["distinct_nicks"] = match.get("distinct_nicks")
            if not item.get("summary") and match.get("summary"):
                item["summary"] = match.get("summary")
        merged.append(item)
    return merged


def _quote_ref_key(ref: dict) -> tuple:
    mid = ref.get("message_id")
    if mid is not None:
        try:
            return ("mid", int(mid))
        except (TypeError, ValueError):
            pass
    return (
        "sp",
        str(ref.get("search_phrase") or ref.get("phrase") or ref.get("q") or ""),
        str(ref.get("room_id") or ""),
        str(ref.get("around") or ""),
    )


def _build_quote_map(
    conn: sqlite3.Connection,
    refs: list[dict],
) -> tuple[dict[tuple, ResolvedQuote], int]:
    quote_map: dict[tuple, ResolvedQuote] = {}
    miss = 0
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        key = _quote_ref_key(ref)
        if key in quote_map:
            continue
        quote = resolve_quote(conn, ref)
        if quote is None:
            miss += 1
            continue
        quote_map[key] = quote
    return quote_map, miss


def _render_quote_block(q: ResolvedQuote) -> str:
    return (
        "<div class='quote'>"
        f"<div class='quote-meta'>{_h(q.nick)} · {_h(q.message_at)}</div>"
        f"<div class='quote-body'>{_h(q.body)}</div>"
        "</div>"
    )


def _render_topic_quotes(
    refs: list[dict] | None,
    quote_map: dict[tuple, ResolvedQuote],
    *,
    limit: int = 3,
) -> str:
    if not refs:
        return ""
    parts: list[str] = []
    seen_ids: set[int] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        quote = quote_map.get(_quote_ref_key(ref))
        if quote is None or quote.message_id in seen_ids:
            continue
        seen_ids.add(quote.message_id)
        parts.append(_render_quote_block(quote))
        if len(parts) >= limit:
            break
    return "\n".join(parts)


def render_html_report(
    conn: sqlite3.Connection,
    settings: AppSettings,
    *,
    now: datetime | None = None,
    output_path: Path | None = None,
    buckets: list[tuple[str, str]] | None = None,
    period_keys: list[str] | None = None,
    room_ids: list[str] | None = None,
    latest: int | None = None,
) -> ReportResult:
    tz = ZoneInfo(settings.tz)
    now_dt = now.astimezone(tz) if now else datetime.now(tz)
    window_days = effective_scope_days(settings)

    scope: ReportScope = resolve_report_scope(
        conn,
        tz=settings.tz,
        window_days=window_days,
        now_dt=now_dt,
        buckets=buckets,
        period_keys=period_keys,
        room_ids=room_ids,
        latest=latest,
    )
    period_keys_list = sorted({b.period_key for b in scope.buckets})
    scoped = scope.mode != "window"
    cutoff_iso: str | None = None

    if scope.mode == "window":
        cutoff = now_dt - timedelta(days=window_days)
        cutoff_iso = cutoff.isoformat(timespec="seconds")
        if period_keys_list:
            topic_rows = conn.execute(
                """
                SELECT NULL AS room_id, period_key, tag, topic_key, title,
                       mentions, distinct_nicks, messages_referenced
                FROM topic_stats
                WHERE room_id IS NULL
                  AND period_key IN ({})
                ORDER BY period_key ASC, mentions DESC, distinct_nicks DESC
                """.format(",".join(["?"] * len(period_keys_list))),
                tuple(period_keys_list),
            ).fetchall()
        else:
            topic_rows = []
    elif scope.buckets:
        in_clause, in_params = bucket_sql_in_clause(scope.buckets)
        topic_rows = conn.execute(
            f"""
            SELECT room_id, period_key, tag, topic_key, title,
                   mentions, distinct_nicks, messages_referenced
            FROM topic_stats
            WHERE room_id IS NOT NULL
              AND (room_id, period_key) IN {in_clause}
            ORDER BY period_key ASC, room_id ASC, mentions DESC, distinct_nicks DESC
            """,
            in_params,
        ).fetchall()
    else:
        topic_rows = []

    if period_keys_list:
        patch_rows = conn.execute(
            """
            SELECT period_key, patch_item, stance, mentions, distinct_nicks, summary
            FROM patch_reaction_stats
            WHERE period_key IN ({})
            ORDER BY period_key ASC, mentions DESC, distinct_nicks DESC
            """.format(",".join(["?"] * len(period_keys_list))),
            tuple(period_keys_list),
        ).fetchall()
    else:
        patch_rows = []

    if scope.mode == "window":
        insight_rows = conn.execute(
            """
            SELECT topics_json
            FROM periodic_insights
            WHERE period_end >= ?
            """,
            (cutoff_iso,),
        ).fetchall()
    elif scope.buckets:
        in_clause, in_params = bucket_sql_in_clause(scope.buckets)
        insight_rows = conn.execute(
            f"""
            SELECT topics_json
            FROM periodic_insights
            WHERE (room_id, period_key) IN {in_clause}
            """,
            in_params,
        ).fetchall()
    else:
        insight_rows = []

    ctx = load_context(
        patchnotes_path=settings.patchnotes_path,
        roadmap_path=settings.roadmap_path,
    )
    payload = build_reporter_payload(
        conn,
        scope=scope,
        settings=settings,
        topic_rows=topic_rows,
        patch_rows=patch_rows,
        ctx=ctx,
        now_dt=now_dt,
        cutoff_iso=cutoff_iso,
    )
    synthesis: ReportSynthesis = synthesize_report(payload, settings)

    insight_topics = _topics_from_insights(insight_rows)
    narratives = synthesis.topic_narratives or insight_topics
    if synthesis.topic_narratives and insight_topics:
        narratives = _merge_insight_quotes(narratives, insight_topics)

    all_quote_refs: list[dict] = []
    for topic in narratives:
        refs = topic.get("quote_refs")
        if isinstance(refs, list):
            all_quote_refs.extend(r for r in refs if isinstance(r, dict))
    if not all_quote_refs:
        all_quote_refs = _collect_insight_quote_refs(insight_rows)

    quote_map, quote_miss = _build_quote_map(conn, all_quote_refs)

    topic_dicts = [_row_dict(r) for r in topic_rows]
    patch_dicts = [_row_dict(r) for r in patch_rows]
    chart_scripts = build_chart_scripts(topic_dicts, patch_dicts)

    min_dn = int(settings.min_distinct_nicks)
    scope_label = {
        "window": (
            f"최근 {settings.data_scope.last_days}일 (message_at, 전체 합산)"
            if settings.data_scope.mode == "last_days"
            else f"최근 {settings.reporter_window} (전체 합산)"
        ),
        "latest": f"최근 분석 {len(scope.buckets)}건",
        "period_keys": "지정 기간",
        "buckets": f"이번 analyze {len(scope.buckets)}건",
    }.get(scope.mode, scope.mode)

    reporter_label = (
        f"{settings.reporter_model} ({synthesis.backend})"
        if synthesis.backend == "llm"
        else "static (LLM 미사용)"
    )

    def _synthesis_section() -> str:
        parts: list[str] = []
        if synthesis.executive_summary:
            parts.append(f"<p class='lead'>{_h(synthesis.executive_summary)}</p>")
        if synthesis.highlights:
            parts.append("<ul>")
            for hline in synthesis.highlights:
                parts.append(f"<li>{_h(hline)}</li>")
            parts.append("</ul>")
        if not parts:
            return "<p class='muted'>합성 요약 없음</p>"
        return "\n".join(parts)

    def _narrative_topics() -> str:
        if not narratives:
            return ""
        parts = ["<section><h2>주제별 논의</h2>"]
        for t in narratives:
            title = t.get("title") or t.get("topic_key") or "주제"
            tag = t.get("tag") or "general"
            stats = ""
            mentions = t.get("mentions")
            distinct = t.get("distinct_nicks")
            if mentions is not None or distinct is not None:
                stats = (
                    f" <span class='muted'>(언급 {int(mentions or 0)}, "
                    f"닉 {int(distinct or 0)})</span>"
                )
            quote_html = _render_topic_quotes(t.get("quote_refs"), quote_map)
            parts.append(
                f"<div class='card'><h3>{_h(tag)} · {_h(title)}{stats}</h3>"
                f"<p>{_h(t.get('summary'))}</p>"
                f"{quote_html}</div>"
            )
        parts.append("</section>")
        return "\n".join(parts)

    def _charts_section() -> str:
        return """
    <section>
      <h2>통계 차트</h2>
      <div class="chart-grid">
        <div class="chart-box"><canvas id="chartTagTrend"></canvas></div>
        <div class="chart-box"><canvas id="chartTopTopics"></canvas></div>
      </div>
    </section>
"""

    def _topic_table() -> str:
        if not scope.buckets and scope.mode != "window":
            return "<p>리포트에 포함할 분석 결과가 없습니다. analyze 후 aggregate를 실행했는지 확인하세요.</p>"
        if not topic_rows:
            return "<p>주제 통계가 없습니다.</p>"
        parts: list[str] = []
        cur_section: tuple[str, str] | None = None
        shown = 0
        for r in topic_rows:
            pk = str(r["period_key"])
            rid = r["room_id"]
            section = (pk, str(rid) if rid else "")
            if section != cur_section:
                if cur_section is not None:
                    parts.append("</tbody></table>")
                if rid:
                    parts.append(f"<h3>기간: {_h(pk)} · 방: {_h(rid)}</h3>")
                else:
                    parts.append(f"<h3>기간: {_h(pk)}</h3>")
                parts.append(
                    "<table><thead><tr>"
                    "<th>태그</th><th>주제</th><th>언급</th><th>참여 닉</th><th>근거 메시지</th>"
                    "</tr></thead><tbody>"
                )
                cur_section = section
                shown = 0
            if shown >= 12:
                continue
            dn = int(r["distinct_nicks"] or 0)
            flag = "" if dn >= min_dn else " <span class='flag'>(표본 부족)</span>"
            parts.append(
                "<tr>"
                f"<td>{_h(r['tag'])}</td>"
                f"<td>{_h(r['title'] or r['topic_key'])}{flag}</td>"
                f"<td class='num'>{_h(r['mentions'])}</td>"
                f"<td class='num'>{_h(dn)}</td>"
                f"<td class='num'>{_h(r['messages_referenced'])}</td>"
                "</tr>"
            )
            shown += 1
        if cur_section is not None:
            parts.append("</tbody></table>")
        return "\n".join(parts)

    html_doc = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OpenChat Report</title>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,"Apple SD Gothic Neo","Malgun Gothic",sans-serif; margin: 24px; color: #111; max-width: 1100px; }}
    h1 {{ margin: 0 0 6px 0; }}
    .meta {{ color:#555; margin-bottom: 18px; font-size: 14px; }}
    .lead {{ font-size: 1.05rem; line-height: 1.5; }}
    .grid {{ display: grid; grid-template-columns: 1fr; gap: 18px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #e6e6e6; padding: 8px 10px; vertical-align: top; }}
    th {{ background: #fafafa; text-align: left; }}
    td.num {{ text-align: right; white-space: nowrap; }}
    .flag {{ color: #b00020; font-weight: 600; }}
    .quote {{ border: 1px solid #eee; border-radius: 10px; padding: 10px 12px; margin: 10px 0; background: #fcfcfc; }}
    .quote-meta {{ color:#666; font-size: 12px; margin-bottom: 6px; }}
    .quote-body {{ white-space: pre-wrap; }}
    .muted {{ color:#666; }}
    pre {{ white-space: pre-wrap; background: #f7f7f7; padding: 10px 12px; border-radius: 10px; font-size: 13px; }}
    .card {{ border: 1px solid #e8e8e8; border-radius: 10px; padding: 12px 14px; margin: 10px 0; }}
    .stance {{ font-size: 12px; color: #555; font-weight: normal; }}
    .chart-grid {{ display: grid; grid-template-columns: 1fr; gap: 16px; }}
    @media (min-width: 900px) {{ .chart-grid {{ grid-template-columns: 1fr 1fr; }} }}
    .chart-box {{ min-height: 280px; padding: 8px; border: 1px solid #eee; border-radius: 10px; }}
  </style>
</head>
<body>
  <h1>OpenChat 리포트</h1>
  <div class="meta">
    생성: {_h(now_dt.isoformat(timespec="seconds"))} · 범위: {_h(scope_label)} ·
    min_distinct_nicks: {_h(min_dn)} · Reporter: {_h(reporter_label)}
  </div>

  <section>
    <h2>요약</h2>
    {_synthesis_section()}
  </section>

  {_charts_section()}

  {_narrative_topics()}

  <div class="grid">
    <section>
      <h2>주제/태그 추이{" (방별)" if scoped else ""}</h2>
      {_topic_table()}
    </section>
  </div>
  {chart_scripts}
</body>
</html>
"""

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    if output_path is None:
        stamp = now_dt.strftime("%Y%m%d_%H%M%S")
        output_path = settings.output_dir / f"report_{stamp}.html"
    output_path.write_text(html_doc, encoding="utf-8")
    email_snapshot = _build_email_snapshot(
        synthesis=synthesis,
        narratives=narratives,
        topic_dicts=topic_dicts,
        scope_label=scope_label,
        bucket_count=len(scope.buckets),
    )
    return ReportResult(
        output_path=output_path,
        period_keys=period_keys_list,
        quote_miss_count=int(quote_miss),
        scope_mode=scope.mode,
        bucket_count=len(scope.buckets),
        reporter_backend=synthesis.backend,
        email_snapshot=email_snapshot,
    )
