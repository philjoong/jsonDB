"""Outlook-compatible HTML email body for open-chat insight reports."""

from __future__ import annotations

from typing import Any

from openchat.email_api import _esc, _esc_ascii, build_vml_button

_TAG_STYLE = {
    "bug": ("버그", "#e0e7ff", "#3730a3"),
    "balance": ("밸런스", "#ffedd5", "#9a3412"),
    "event": ("이벤트", "#dbeafe", "#1d4ed8"),
    "general": ("일반", "#e2e8f0", "#475569"),
    "patch": ("패치", "#ede9fe", "#6d28d9"),
}


def _topic_card(topic: dict[str, Any], index: int) -> str:
    tag = str(topic.get("tag") or "general")
    tag_label, badge_bg, badge_color = _TAG_STYLE.get(tag, _TAG_STYLE["general"])
    title = _esc_ascii(str(topic.get("title") or topic.get("topic_key") or f"주제 {index}"))
    mentions = int(topic.get("mentions") or 0)
    distinct = int(topic.get("distinct_nicks") or 0)
    summary = topic.get("summary") or topic.get("narrative") or ""
    summary_html = _esc_ascii(str(summary)) if summary else ""
    quotes = topic.get("quotes") or []
    quote_bits = ""
    for q in quotes[:2]:
        if not isinstance(q, dict):
            continue
        body = _esc_ascii(str(q.get("body") or q.get("text") or ""))[:200]
        nick = _esc_ascii(str(q.get("nick") or ""))
        if body:
            quote_bits += f"""
            <p style="margin:8px 0 0 0;padding:8px 10px;background:#f8fafc;border-left:3px solid #94a3b8;
                      font-family:'Malgun Gothic',sans-serif;font-size:12px;color:#334155;line-height:1.45;">
              <span style="color:#64748b;">{nick}</span><br>{body}
            </p>"""

    stats = f"언급 {mentions} · 참여 닉 {distinct}"
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
           style="margin:0 0 14px 0;border-collapse:collapse;background:#ffffff;border:1px solid #e2e8f0;">
      <tr>
        <td style="padding:14px 16px;border-left:4px solid #1f3a5f;font-family:'Malgun Gothic',sans-serif;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
            <tr>
              <td>
                <span style="display:inline-block;padding:2px 8px;background:{badge_bg};color:{badge_color};
                             font-size:11px;font-weight:bold;border-radius:4px;">{_esc_ascii(tag_label)}</span>
                <span style="font-size:15px;font-weight:bold;color:#0f172a;margin-left:8px;">{title}</span>
              </td>
              <td align="right" style="font-size:12px;color:#64748b;white-space:nowrap;">{_esc_ascii(stats)}</td>
            </tr>
          </table>
          {f'<p style="margin:10px 0 0 0;font-size:13px;color:#334155;line-height:1.5;">{summary_html}</p>' if summary_html else ''}
          {quote_bits}
        </td>
      </tr>
    </table>"""


def build_openchat_report_email_html(
    *,
    project_label: str,
    snapshot: dict[str, Any],
    report_view_url: str,
) -> str:
    """Build email HTML from report email snapshot dict."""
    scope_label = _esc_ascii(str(snapshot.get("scope_label") or ""))
    bucket_count = int(snapshot.get("bucket_count") or 0)
    executive = _esc_ascii(str(snapshot.get("executive_summary") or ""))
    highlights = snapshot.get("highlights") or []
    topics = snapshot.get("topics") or []

    highlight_items = ""
    if isinstance(highlights, list):
        for h in highlights[:6]:
            if h:
                highlight_items += (
                    f"<li style='margin:0 0 6px 0;'>{_esc_ascii(str(h))}</li>"
                )

    topic_cards = ""
    if isinstance(topics, list):
        for i, t in enumerate(topics[:8], 1):
            if isinstance(t, dict):
                topic_cards += _topic_card(t, i)

    if not topic_cards:
        topic_cards = (
            "<p style='font-family:\"Malgun Gothic\",sans-serif;color:#64748b;"
            "font-size:13px;'>표시할 주제 요약이 없습니다.</p>"
        )

    safe_label = _esc_ascii(project_label)
    btn = build_vml_button(report_view_url, "전체 리포트 보기", width=180)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#eef2f6;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
         style="background:#eef2f6;border-collapse:collapse;">
    <tr><td align="center" style="padding:24px 12px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="640"
             style="width:640px;max-width:640px;border-collapse:collapse;background:#ffffff;border:1px solid #d9e2ec;">
        <tr>
          <td bgcolor="#1f3a5f" style="background:#1f3a5f;padding:22px 26px;font-family:'Malgun Gothic',sans-serif;">
            <p style="margin:0;font-size:12px;color:#bfdbfe;letter-spacing:0.04em;">OPEN-CHAT INSIGHT</p>
            <h1 style="margin:6px 0 0 0;font-size:20px;color:#ffffff;font-weight:bold;">{safe_label}</h1>
            <p style="margin:8px 0 0 0;font-size:13px;color:#dbeafe;">{scope_label} · 분석 버킷 {bucket_count}건</p>
          </td>
        </tr>
        <tr>
          <td style="padding:22px 26px;font-family:'Malgun Gothic',sans-serif;">
            <h2 style="margin:0 0 10px 0;font-size:15px;color:#0f172a;">요약</h2>
            <p style="margin:0 0 14px 0;font-size:14px;color:#334155;line-height:1.55;">
              {executive or "요약 텍스트가 없습니다. 웹 리포트에서 상세 내용을 확인하세요."}
            </p>
            {"<ul style='margin:0 0 18px 0;padding-left:20px;font-size:13px;color:#334155;line-height:1.5;'>" + highlight_items + "</ul>" if highlight_items else ""}
            <h2 style="margin:18px 0 10px 0;font-size:15px;color:#0f172a;">주요 주제</h2>
            {topic_cards}
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:18px;">
              <tr><td align="center">{btn}</td></tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="background:#f8fafc;padding:14px 26px;border-top:1px solid #e2e8f0;
                     font-family:'Malgun Gothic',sans-serif;font-size:11px;color:#64748b;line-height:1.45;">
            본 메일은 오픈채팅 인사이트 파이프라인이 생성한 리포트 요약입니다.
            전체 차트·인용문은 위 버튼의 HTML 리포트에서 확인하세요.
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
