"""Internal Email API client and Outlook-compatible HTML helpers."""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

EMAIL_API_BASE_URL = os.getenv("EMAIL_API_BASE_URL", "").rstrip("/")


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\n", "<br>")
    )


def _esc_attr(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _esc_ascii(text: str) -> str:
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
