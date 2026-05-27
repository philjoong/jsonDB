"""
2차 하이라이트 분석 결과를 이메일로 발송하는 모듈.

test_api.py의 Email API 형식을 따른다.
POST /api/email/users/{sender}/emails
"""

import os
import urllib.request
import urllib.parse
import json
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

EMAIL_API_BASE_URL = os.getenv("EMAIL_API_BASE_URL", "").rstrip("/")


def _format_stream_time_range(start_iso: str, end_iso: str) -> str:
    """ISO 8601 시작/종료 시간을 KST 기준 읽기 좋은 문자열로 변환한다."""
    from datetime import timedelta
    KST = timezone(timedelta(hours=9))

    try:
        dt_start = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone(KST)
        dt_end = datetime.fromisoformat(end_iso.replace("Z", "+00:00")).astimezone(KST)

        date_str = dt_start.strftime("%Y-%m-%d")
        start_str = dt_start.strftime("%H:%M")
        end_str = dt_end.strftime("%H:%M")

        # 날짜가 다른 경우 (자정 넘긴 스트리밍)
        if dt_start.date() != dt_end.date():
            end_str = dt_end.strftime("%m-%d %H:%M")

        return f"{date_str}  {start_str} ~ {end_str}"
    except (ValueError, TypeError):
        return ""


def _build_html_body(summary_meta: dict[str, Any]) -> str:
    """summary_meta.json 데이터를 Outlook 호환형 HTML 이메일 본문으로 변환한다."""
    channel = summary_meta.get("channel", "")
    stream_date = summary_meta.get("stream_date", "")
    stream_start_time = summary_meta.get("stream_start_time", "")
    stream_end_time = summary_meta.get("stream_end_time", "")
    stream_summary = summary_meta.get("stream_summary", "")
    total_highlights = summary_meta.get("total_highlights", 0)
    total_selected = summary_meta.get("total_selected", 0)
    total_excluded = summary_meta.get("total_excluded", 0)
    topics = summary_meta.get("topics", [])
    unselected = summary_meta.get("unselected", [])

    # 스트리밍 시간 범위 (시작/종료가 있으면 범위 표시, 없으면 기존 stream_date 폴백)
    stream_time_display = ""
    if stream_start_time and stream_end_time:
        stream_time_display = _format_stream_time_range(stream_start_time, stream_end_time)
    if not stream_time_display:
        stream_time_display = stream_date

    safe_header_channel = _esc_ascii(channel)
    safe_header_stream_time = _esc_ascii(stream_time_display)

    def _build_button(url: str, label: str, width: int, bg_color: str, border_color: str) -> str:
        safe_url = _esc_attr(url)
        safe_label = _esc(label)
        return f"""\
<table cellpadding="0" cellspacing="0" border="0">
  <tr>
    <td bgcolor="{bg_color}" style="background:{bg_color};border:1px solid {border_color};">
      <!--[if mso]>
      <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml"
                   href="{safe_url}"
                   style="height:36px;v-text-anchor:middle;width:{width}px;"
                   arcsize="8%" strokecolor="{border_color}" fillcolor="{bg_color}">
        <w:anchorlock/>
        <center style="color:#ffffff;font-family:'Malgun Gothic',sans-serif;
                       font-size:13px;font-weight:bold;">
          {safe_label}
        </center>
      </v:roundrect>
      <![endif]-->
      <!--[if !mso]><!-->
      <a href="{safe_url}" target="_blank"
         style="display:inline-block;background:{bg_color};color:#ffffff;
                 font-family:'Malgun Gothic',sans-serif;
                font-size:13px;font-weight:bold;line-height:36px;text-align:center;
                text-decoration:none;width:{width}px;mso-hide:all;">
        {safe_label}
      </a>
      <!--<![endif]-->
    </td>
  </tr>
</table>"""

    # 카테고리 → (한글, 배지 배경색, 카드 좌측 바 색상)
    cat_style = {
        "bug":     ("버그", "#e0e7ff", "#3730a3", "#1b2838"),
        "balance": ("밸런스", "#ffedd5", "#9a3412", "#1b2838"),
        "qa":      ("QA", "#dbeafe", "#1d4ed8", "#1b2838"),
        "keyword": ("키워드", "#ede9fe", "#6d28d9", "#1b2838"),
        "other":   ("기타", "#e2e8f0", "#475569", "#1b2838"),
    }

    # ── 주제별 카드 생성 ──
    topic_cards = ""
    wiki_page_url = summary_meta.get("wiki_page_url", "")
    for i, topic in enumerate(topics, 1):
        title = topic.get("title", "")
        raw_cat = topic.get("category", "other")
        cat_label, badge_bg, badge_color, accent_color = cat_style.get(raw_cat, cat_style["other"])
        summary = topic.get("summary", "")
        selection_reason = topic.get("selection_reason", "")
        clip_count = len(topic.get("clips_used", []))
        wiki_clip_url = topic.get("wiki_clip_url", "")
        clip_link_url = wiki_clip_url or wiki_page_url
        clip_link_label = "클립 보기 (위키)" if wiki_clip_url else "리포트 보기 (위키)"

        clip_link_html = ""
        if clip_link_url:
            clip_link_html = f"""
      <table cellpadding="0" cellspacing="0" border="0" style="margin-top:12px;">
        <tr>
          <td>
            {_build_button(clip_link_url, clip_link_label, 140, "#2563eb", "#2563eb")}
          </td>
        </tr>
      </table>"""

        topic_spacing_html = ""
        if i < len(topics):
            topic_spacing_html = """
<table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
  <tr><td height="16" style="height:16px;font-size:0;line-height:0;">&nbsp;</td></tr>
</table>"""

        topic_cards += f"""
<table cellpadding="0" cellspacing="0" border="0" width="100%"
       style="border-collapse:collapse;">
  <tr>
    <td style="background:#f8fafc;border:1px solid #d9e2ec;padding:16px 18px;">
      <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
        <tr>
          <td style="font-family:'Malgun Gothic',sans-serif;
                     font-size:16px;line-height:24px;font-weight:bold;color:{accent_color};padding:0 0 8px 0;">
            <span style="font-weight:bold;color:{accent_color};">#{i}</span>
            &nbsp;{_esc(title)}
          </td>
          <td width="84" align="right" valign="top" style="padding:0 0 8px 12px;">
            <table cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td bgcolor="{badge_bg}"
                    style="background:{badge_bg};color:{badge_color};font-family:'Malgun Gothic',sans-serif;
                           font-size:11px;line-height:18px;font-weight:bold;padding:0 8px;text-align:center;">
                  {_esc(cat_label)}
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
      <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
        <tr>
          <td style="font-size:0;line-height:0;border-top:2px solid {accent_color};height:0;">&nbsp;</td>
        </tr>
        <tr>
          <td style="font-family:'Malgun Gothic',sans-serif;
                     font-size:14px;line-height:22px;color:#334e68;padding:12px 0 12px 0;">
            {_esc(summary)}
          </td>
        </tr>
      </table>
      <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
        <tr>
          <td
              style="background:#f8fafc;border-top:1px solid #d9e2ec;padding:9px 0 0 0;
                     font-family:'Malgun Gothic',sans-serif;
                     font-size:12px;line-height:18px;color:#64748b;">
            <strong>선별 사유:</strong> {_esc(selection_reason)}
          </td>
        </tr>
      </table>
      <table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:8px;border-collapse:collapse;">
        <tr>
          <td style="font-family:'Malgun Gothic',sans-serif;
                     font-size:12px;line-height:18px;color:#829ab1;">
            포함 클립 수: {clip_count}
          </td>
        </tr>
      </table>
      {clip_link_html}
    </td>
  </tr>
</table>{topic_spacing_html}"""

    # ── 제외 항목 ──
    excluded_rows = ""
    for u in unselected:
        original_title = u.get("original_title", "")
        reason = u.get("reason", "")
        excluded_rows += f"""<tr>
  <td style="padding:8px 12px;border-bottom:1px solid #fde68a;font-size:13px;color:#92400e;">
    {_esc(original_title)}
  </td>
  <td style="padding:8px 12px;border-bottom:1px solid #fde68a;font-size:12px;color:#b45309;">
    {_esc(reason)}
  </td>
</tr>"""

    excluded_section = ""
    if excluded_rows:
        excluded_section = f"""
<!-- 제외 항목 -->
<table cellpadding="0" cellspacing="0" border="0" width="100%"
       style="margin-top:8px;border-collapse:collapse;">
  <tr>
    <td style="padding:18px 0 8px 0;font-family:'Malgun Gothic',sans-serif;
               font-size:13px;line-height:20px;font-weight:bold;color:#a16207;">
      제외된 항목 ({total_excluded}건)
    </td>
  </tr>
  <tr>
    <td>
      <table cellpadding="0" cellspacing="0" border="0" width="100%"
             style="border-collapse:collapse;background:#fffbeb;border:1px solid #fcd34d;">
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #fcd34d;font-family:'Malgun Gothic',sans-serif;
                     font-size:12px;line-height:18px;font-weight:bold;color:#92400e;width:40%;">항목</td>
          <td style="padding:8px 12px;border-bottom:1px solid #fcd34d;font-family:'Malgun Gothic',sans-serif;
                     font-size:12px;line-height:18px;font-weight:bold;color:#92400e;">제외 사유</td>
        </tr>
        {excluded_rows}
      </table>
    </td>
  </tr>
</table>"""

    # ── 위키 버튼 (Outlook 호환: table 기반) ──
    wiki_btn_html = ""
    if wiki_page_url:
        wiki_btn_html = f"""\
        <table cellpadding="0" cellspacing="0" border="0" style="margin-top:12px;">
          <tr>
            <td>
              {_build_button(wiki_page_url, "위키에서 전체 리포트 보기", 220, "#2563eb", "#2563eb")}
            </td>
          </tr>
        </table>"""

    empty_topics_html = """
        <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
          <tr>
            <td align="center"
                style="padding:28px 16px;border:1px solid #d9e2ec;font-family:'Malgun Gothic',sans-serif;
                       font-size:14px;line-height:22px;color:#7b8794;">
              선별된 주제가 없습니다.
            </td>
          </tr>
        </table>"""

    html = f"""\
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>하이라이트 리포트</title>
</head>
<body style="margin:0;padding:0;background:#eef2f6;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#eef2f6;border-collapse:collapse;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="700" style="width:700px;max-width:700px;border-collapse:collapse;background:#ffffff;border:1px solid #d9e2ec;">
          <tr>
            <td bgcolor="#1f3a5f" style="background:#1f3a5f;padding:24px 28px;font-family:'Malgun Gothic',sans-serif;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
                <tr>
                  <td valign="top" style="font-family:'Malgun Gothic',sans-serif;">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
                      <tr>
                        <td style="font-family:'Malgun Gothic',sans-serif;font-size:12px;line-height:18px;font-weight:bold;color:#9fb3c8;">
                          AI REPORT - YouTube Live Streaming
                        </td>
                      </tr>
                      <tr>
                        <td style="padding-top:6px;font-family:'Malgun Gothic',sans-serif;font-size:26px;line-height:34px;font-weight:bold;color:#ffffff;">
                          {safe_header_channel}
                        </td>
                      </tr>
                      <tr>
                        <td style="padding-top:6px;font-family:'Malgun Gothic',sans-serif;font-size:14px;line-height:22px;color:#d9e2ec;">
                          &#49828;&#53944;&#47532;&#48141; &#49884;&#44036;: {safe_header_stream_time}
                        </td>
                      </tr>
                      <tr>
                        <td style="padding-top:12px;">{wiki_btn_html}</td>
                      </tr>
                    </table>
                  </td>
                  <td width="220" align="right" valign="top" style="font-family:'Malgun Gothic',sans-serif;">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="220" style="width:220px;border-collapse:separate;border-spacing:0;mso-table-lspace:0pt;mso-table-rspace:0pt;">
                      <tr>
                        <td width="72" align="center" valign="top" bgcolor="#253649" style="width:72px;background:#253649;padding:10px 4px;border:1px solid #31465f;font-family:'Malgun Gothic',sans-serif;">
                          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                            <tr><td align="center" style="font-family:'Malgun Gothic',sans-serif;font-size:22px;line-height:26px;font-weight:bold;color:#ffffff;">{total_highlights}</td></tr>
                            <tr><td align="center" style="padding-top:4px;font-family:'Malgun Gothic',sans-serif;font-size:10px;line-height:14px;color:#9fb3c8;">&#51204;&#52404;</td></tr>
                          </table>
                        </td>
                        <td width="72" align="center" valign="top" bgcolor="#253649" style="width:72px;background:#253649;padding:10px 4px;border-top:1px solid #31465f;border-right:1px solid #31465f;border-bottom:1px solid #31465f;border-left:4px solid #1f3a5f;font-family:'Malgun Gothic',sans-serif;">
                          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                            <tr><td align="center" style="font-family:'Malgun Gothic',sans-serif;font-size:22px;line-height:26px;font-weight:bold;color:#4ade80;">{total_selected}</td></tr>
                            <tr><td align="center" style="padding-top:4px;font-family:'Malgun Gothic',sans-serif;font-size:10px;line-height:14px;color:#9fb3c8;">&#49440;&#48324;</td></tr>
                          </table>
                        </td>
                        <td width="72" align="center" valign="top" bgcolor="#253649" style="width:72px;background:#253649;padding:10px 4px;border-top:1px solid #31465f;border-right:1px solid #31465f;border-bottom:1px solid #31465f;border-left:4px solid #1f3a5f;font-family:'Malgun Gothic',sans-serif;">
                          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                            <tr><td align="center" style="font-family:'Malgun Gothic',sans-serif;font-size:22px;line-height:26px;font-weight:bold;color:#cbd5e1;">{total_excluded}</td></tr>
                            <tr><td align="center" style="padding-top:4px;font-family:'Malgun Gothic',sans-serif;font-size:10px;line-height:14px;color:#9fb3c8;">&#51228;&#50808;</td></tr>
                          </table>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 28px 24px 28px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:20px;border-collapse:collapse;">
                <tr>
                  <td bgcolor="#f8fbff"
                      style="background:#f8fbff;border-top:1px solid #bfdbfe;border-left:1px solid #bfdbfe;border-right:1px solid #bfdbfe;padding:12px 16px;
                             font-family:'Malgun Gothic',sans-serif;
                             font-size:14px;line-height:22px;font-weight:bold;color:#1d4ed8;">
                    본 하이라이트 리포트는 NC QA에서 자체 개발한 AI 시스템으로, YouTube 라이브 스트리밍의 실시간 채팅과 음성을 AI가 자동 분석하여 신뢰도가 높은 순으로 작성되었습니다.
                  </td>
                </tr>
              </table>
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
                <tr>
                  <td height="18" style="height:18px;font-size:0;line-height:0;">&nbsp;</td>
                </tr>
              </table>
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:24px;border-collapse:collapse;">
                <tr>
                  <td bgcolor="#ffffff"
                      style="background:#ffffff;padding:0;">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
                      <tr>
                        <td style="font-family:'Malgun Gothic',sans-serif;
                                   font-size:13px;line-height:18px;font-weight:bold;color:#1b2838;padding-bottom:6px;letter-spacing:0.5px;">
                          방송 요약
                        </td>
                      </tr>
                      <tr>
                        <td style="font-size:0;line-height:0;border-top:2px solid #1b2838;height:0;">&nbsp;</td>
                      </tr>
                      <tr>
                        <td style="font-family:'Malgun Gothic',sans-serif;
                                   font-size:14px;line-height:22px;color:#334e68;padding-top:12px;">
                          {_esc(stream_summary)}
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:14px;border-collapse:collapse;">
                <tr>
                  <td style="padding-bottom:8px;border-bottom:2px solid #1b2838;font-family:'Malgun Gothic',sans-serif;
                             font-size:14px;line-height:24px;font-weight:bold;color:#1b2838;letter-spacing:0.5px;">
                    주요 하이라이트
                  </td>
                </tr>
              </table>
              {topic_cards if topic_cards else empty_topics_html}
              {excluded_section}
            </td>
          </tr>
          <tr>
            <td bgcolor="#1f3a5f"
                style="background:#1f3a5f;padding:16px 28px;border-top:1px solid #31465f;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
                <tr>
                  <td valign="middle" style="font-family:'Malgun Gothic',sans-serif;
                             font-size:11px;line-height:18px;color:#d9e2ec;">
                    AI Report - YouTube Live Streaming
                  </td>
                  <td width="220" align="right" valign="middle">{wiki_btn_html}</td>
                </tr>
                <tr>
                  <td colspan="2" style="padding-top:4px;font-family:'Malgun Gothic',sans-serif;
                             font-size:11px;line-height:18px;color:#9fb3c8;">
                    AI 기반 라이브 스트리밍 자동 분석 시스템에 의해 생성된 리포트입니다.
                    분석 정확도는 방송 환경에 따라 달라질 수 있습니다.
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
    return html


def _esc(text: str) -> str:
    """HTML escape."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\n", "<br>")
    )


def _esc_attr(text: str) -> str:
    """HTML attribute escape (URL 등에 사용)."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _esc_ascii(text: str) -> str:
    """Outlook classic에서도 덜 깨지도록 비 ASCII 문자를 숫자 엔티티로 변환한다."""
    escaped = _esc(text)
    return "".join(ch if ord(ch) < 128 else f"&#{ord(ch)};" for ch in escaped)


def build_vml_button(
    url: str,
    label: str,
    *,
    width: int = 200,
    bg_color: str = "#1f3a5f",
    border_color: str = "#1f3a5f",
) -> str:
    """Outlook-friendly CTA button (table + VML)."""
    safe_url = _esc_attr(url)
    safe_label = _esc(label)
    return f"""\
<table cellpadding="0" cellspacing="0" border="0">
  <tr>
    <td bgcolor="{bg_color}" style="background:{bg_color};border:1px solid {border_color};">
      <!--[if mso]>
      <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml"
                   href="{safe_url}"
                   style="height:36px;v-text-anchor:middle;width:{width}px;"
                   arcsize="8%" strokecolor="{border_color}" fillcolor="{bg_color}">
        <w:anchorlock/>
        <center style="color:#ffffff;font-family:'Malgun Gothic',sans-serif;
                       font-size:13px;font-weight:bold;">
          {safe_label}
        </center>
      </v:roundrect>
      <![endif]-->
      <!--[if !mso]><!-->
      <a href="{safe_url}" target="_blank"
         style="display:inline-block;background:{bg_color};color:#ffffff;
                 font-family:'Malgun Gothic',sans-serif;
                font-size:13px;font-weight:bold;line-height:36px;text-align:center;
                text-decoration:none;width:{width}px;mso-hide:all;">
        {safe_label}
      </a>
      <!--<![endif]-->
    </td>
  </tr>
</table>"""


def send_html_email(
    *,
    sender: str,
    recipients: list[str],
    subject: str,
    html_body: str,
    api_base_url: str | None = None,
    log_fn: Callable[[str, str], None] = lambda msg, lvl: None,
) -> bool:
    """Send HTML email via the internal Email API (form-urlencoded)."""
    base = (api_base_url or EMAIL_API_BASE_URL or "").rstrip("/")
    if not base:
        log_fn("EMAIL_API_BASE_URL not configured", "WARN")
        return False
    if not sender or not recipients:
        log_fn("sender or recipients empty", "WARN")
        return False

    url = f"{base}/api/email/users/{urllib.parse.quote(sender, safe='@')}/emails"
    form_data = {
        "subject": subject,
        "contentType": "HTML",
        "content": html_body,
    }
    encoded_parts = urllib.parse.urlencode(form_data)
    for r in recipients:
        encoded_parts += "&" + urllib.parse.urlencode({"toRecipients[]": r.strip()})
    body = encoded_parts.encode("utf-8")

    log_fn(f"Sending email: {sender} -> {', '.join(recipients)}", "INFO")
    try:
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
        with urllib.request.urlopen(req, timeout=30) as res:
            response_data = res.read().decode("utf-8")
            log_fn(f"Email sent: {response_data[:200]}", "INFO")
        return True
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        log_fn(f"Email failed (HTTP {e.code}): {error_body}", "ERROR")
        return False
    except Exception as e:
        log_fn(f"Email failed: {e}", "ERROR")
        return False


def send_highlight_email(
    *,
    summary_meta: dict[str, Any],
    sender: str,
    recipients: list[str],
    log_fn: Callable[[str, str], None] = lambda msg, lvl: None,
) -> bool:
    """
    2차 하이라이트 분석 결과를 이메일로 발송한다.

    Args:
        summary_meta: summary_meta.json 데이터
        sender: 발송인 이메일 주소
        recipients: 수신인 이메일 주소 목록
        log_fn: 로그 함수

    Returns:
        성공 여부
    """
    if not EMAIL_API_BASE_URL:
        log_fn("이메일: EMAIL_API_BASE_URL이 .env에 설정되지 않음, 스킵", "WARN")
        return False

    if not sender or not recipients:
        log_fn("이메일: 발송인 또는 수신인이 비어 있음, 스킵", "WARN")
        return False

    channel = summary_meta.get("channel", "알 수 없음")
    stream_date = summary_meta.get("stream_date", "")
    total_selected = summary_meta.get("total_selected", 0)

    subject = f"[하이라이트 리포트] {channel} - {stream_date} ({total_selected}건 선별)"
    html_body = _build_html_body(summary_meta)
    return send_html_email(
        sender=sender,
        recipients=recipients,
        subject=subject,
        html_body=html_body,
        log_fn=log_fn,
    )
