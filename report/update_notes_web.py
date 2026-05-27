"""Fetch update/patch notes from per-room base URLs (index + child pages)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from context.loader import excerpt_for_llm
from openchat.config import AppSettings, RoomConfig

logger = logging.getLogger(__name__)

_SKIP_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".css",
    ".js",
    ".zip",
    ".pdf",
    ".mp4",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
)


@dataclass(frozen=True)
class UpdateNotesPage:
    url: str
    title: str
    excerpt: str


@dataclass
class RoomUpdateNotes:
    room_id: str
    room_label: str
    base_url: str
    source: str  # "plaync_api" | "crawl" | "web_search" | "none"
    pages: list[UpdateNotesPage] = field(default_factory=list)
    error: str | None = None


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self._in_title = False
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)


def _fetch_url(url: str, *, timeout: float, user_agent: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def html_to_text(html: str) -> str:
    s = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    s = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", s)
    s = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _page_title(html: str) -> str:
    parser = _LinkParser()
    try:
        parser.feed(html[:100_000])
    except Exception:
        return ""
    return " ".join(parser.title_parts).strip()[:200]


def _is_under_base(candidate: str, base: str) -> bool:
    b = urlparse(base)
    c = urlparse(candidate)
    if c.scheme not in ("http", "https"):
        return False
    if c.netloc.lower() != b.netloc.lower():
        return False
    base_path = b.path.rstrip("/") or "/"
    cand_path = c.path or "/"
    if base_path == "/":
        return True
    return cand_path == base_path or cand_path.startswith(base_path + "/")


def _should_skip_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _SKIP_EXTENSIONS)


def _is_plaync_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host.endswith(".plaync.com") or host == "plaync.com"


def parse_plaync_board_config(html: str) -> dict[str, str] | None:
    """Extract NC community API settings embedded in plaync.com board pages."""
    api_match = re.search(r'apiPath:\s*"([^"]+)"', html)
    alias_match = re.search(r"boardAlias:\s*'([^']+)'", html)
    if not api_match or not alias_match:
        return None

    country = "kr"
    lang = "ko"
    country_match = re.search(r'_localeCountry\s*=\s*"([^"]+)"', html)
    if country_match:
        country = country_match.group(1).strip().lower()
    lang_match = re.search(r'_localeLanguage\s*=\s*"([^"]+)"', html)
    if lang_match:
        lang = lang_match.group(1).strip().lower()

    view_pattern = ""
    pattern_match = re.search(
        r'boardUrlPattern:\s*"([^"]+\{articleId\}[^"]*)"',
        html,
    )
    if pattern_match:
        view_pattern = pattern_match.group(1).strip()

    api_path = api_match.group(1).strip()
    if not api_path.endswith("/"):
        api_path += "/"

    return {
        "api_path": api_path,
        "board_alias": alias_match.group(1).strip(),
        "country": country,
        "lang": lang,
        "view_url_pattern": view_pattern,
    }


def _article_view_url(
    *,
    base_url: str,
    content_id: int | str,
    view_url_pattern: str,
) -> str:
    cid = str(content_id)
    if view_url_pattern and "{articleId}" in view_url_pattern:
        return view_url_pattern.replace("{articleId}", cid)

    parsed = urlparse(base_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/list"):
        path = f"{path[:-5]}/view"
    elif not path.endswith("/view"):
        path = f"{path}/view"
    return f"{parsed.scheme}://{parsed.netloc}{path}?articleId={cid}"


def _format_plaync_article(item: dict[str, Any]) -> tuple[str, str]:
    title = str(item.get("title") or "").strip()
    summary = str(
        item.get("summary")
        or item.get("searchSummary")
        or item.get("largeSummary")
        or ""
    ).strip()
    timestamps = item.get("timestamps") if isinstance(item.get("timestamps"), dict) else {}
    posted = str(
        timestamps.get("postDateTime")
        or timestamps.get("publishedAt")
        or timestamps.get("postedAt")
        or ""
    ).strip()
    parts: list[str] = []
    if posted:
        parts.append(f"게시: {posted}")
    if title:
        parts.append(title)
    if summary and summary != title:
        parts.append(summary)
    return title or "업데이트", "\n".join(parts).strip()


def fetch_plaync_community_articles(
    base_url: str,
    *,
    max_pages: int,
    timeout_seconds: float,
    user_agent: str,
    excerpt_chars: int,
) -> list[UpdateNotesPage]:
    """Fetch update articles via api-community.plaync.com (SPA board list API)."""
    try:
        index_html = _fetch_url(
            base_url, timeout=timeout_seconds, user_agent=user_agent
        )
    except Exception as exc:
        logger.warning("Failed to fetch plaync index %s: %s", base_url, exc)
        return []

    config = parse_plaync_board_config(index_html)
    if not config:
        logger.warning("plaync board config not found in %s", base_url)
        return []

    page_size = max(1, min(int(max_pages), 10))
    list_url = (
        f"{config['api_path']}board/{config['board_alias']}/article"
        f"?pageIndex=1&pageSize={page_size}"
        f"&country={config['country']}&lang={config['lang']}"
    )
    try:
        req = Request(
            list_url,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json",
                "Referer": base_url,
            },
        )
        with urlopen(req, timeout=timeout_seconds) as resp:
            payload = json.loads(resp.read())
    except Exception as exc:
        logger.warning("plaync community API failed %s: %s", list_url, exc)
        return []

    content_list = payload.get("contentList")
    if not isinstance(content_list, list) or not content_list:
        return []

    pages: list[UpdateNotesPage] = []
    for item in content_list:
        if not isinstance(item, dict):
            continue
        snow = item.get("snow") if isinstance(item.get("snow"), dict) else {}
        content_id = snow.get("contentId")
        if content_id is None:
            continue
        title, body = _format_plaync_article(item)
        if not body:
            continue
        pages.append(
            UpdateNotesPage(
                url=_article_view_url(
                    base_url=base_url,
                    content_id=content_id,
                    view_url_pattern=config.get("view_url_pattern") or "",
                ),
                title=title,
                excerpt=excerpt_for_llm(body, max_chars=excerpt_chars),
            )
        )
    return pages


def discover_child_urls(base_url: str, html: str) -> list[str]:
    """Links on the same host under the base URL path prefix."""
    parser = _LinkParser()
    try:
        parser.feed(html)
    except Exception:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for href in parser.links:
        href = (href or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        full = urljoin(base_url, href)
        full = full.split("#", 1)[0]
        if _should_skip_url(full):
            continue
        if not _is_under_base(full, base_url):
            continue
        if full in seen:
            continue
        seen.add(full)
        out.append(full)
    return out


def crawl_update_notes(
    base_url: str,
    *,
    max_pages: int,
    timeout_seconds: float,
    user_agent: str,
    excerpt_chars: int,
) -> list[UpdateNotesPage]:
    """Fetch base URL and up to max_pages child pages; return text excerpts."""
    base_url = base_url.strip()
    if not base_url:
        return []

    try:
        index_html = _fetch_url(base_url, timeout=timeout_seconds, user_agent=user_agent)
    except Exception as exc:
        logger.warning("Failed to fetch update notes index %s: %s", base_url, exc)
        return []

    candidates = [base_url]
    for link in discover_child_urls(base_url, index_html):
        if link not in candidates:
            candidates.append(link)
    candidates = candidates[: max(1, int(max_pages))]

    pages: list[UpdateNotesPage] = []
    for url in candidates:
        try:
            html = index_html if url == base_url else _fetch_url(
                url, timeout=timeout_seconds, user_agent=user_agent
            )
        except Exception as exc:
            logger.debug("Skip update notes page %s: %s", url, exc)
            continue
        text = html_to_text(html)
        if not text:
            continue
        title = _page_title(html) or url
        pages.append(
            UpdateNotesPage(
                url=url,
                title=title,
                excerpt=excerpt_for_llm(text, max_chars=excerpt_chars),
            )
        )
    return pages


def _is_openai_cloud(base_url: str) -> bool:
    host = urlparse(base_url.rstrip("/")).netloc.lower()
    return host in ("api.openai.com", "openai.com")


def fetch_via_openai_web_search(
    *,
    api_key: str,
    model: str,
    base_url: str,
    room_label: str,
    timeout_seconds: float,
) -> str | None:
    """Use OpenAI Responses API web_search to read pages under base_url."""
    if not api_key.strip():
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    client = OpenAI(api_key=api_key.strip())
    prompt = (
        f"게임 '{room_label}' 공식 업데이트·패치 노트를 찾아 요약하세요.\n"
        f"시작 URL(하위 경로에 개별 패치 글이 있음): {base_url}\n"
        "이 URL과 같은 사이트·경로 하위의 최신 패치/업데이트 글 3~5건을 읽고, "
        "제목·날짜(있으면)·핵심 변경 사항을 한국어 bullet로 정리하세요. "
        "출처 URL을 각 bullet 끝에 괄호로 붙이세요."
    )
    try:
        resp = client.responses.create(
            model=model,
            tools=[{"type": "web_search_preview"}],
            input=prompt,
            timeout=timeout_seconds,
        )
    except Exception as exc:
        logger.warning(
            "OpenAI web_search failed for %s (%s): %s",
            base_url,
            room_label,
            exc,
        )
        return None

    parts: list[str] = []
    for item in getattr(resp, "output", []) or []:
        content = getattr(item, "content", None)
        if not content:
            continue
        for block in content:
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
    combined = "\n".join(parts).strip()
    return combined or None


def gather_room_update_notes(
    room: RoomConfig,
    settings: AppSettings,
) -> RoomUpdateNotes:
    base = (room.update_notes_url or "").strip()
    if not base:
        return RoomUpdateNotes(
            room_id=room.id,
            room_label=room.label or room.title,
            base_url="",
            source="none",
        )

    if _is_plaync_url(base):
        pages = fetch_plaync_community_articles(
            base,
            max_pages=settings.reporter_update_notes_max_pages,
            timeout_seconds=settings.reporter_fetch_timeout_seconds,
            user_agent=settings.reporter_fetch_user_agent,
            excerpt_chars=settings.reporter_context_chars,
        )
        if pages:
            return RoomUpdateNotes(
                room_id=room.id,
                room_label=room.label or room.title,
                base_url=base,
                source="plaync_api",
                pages=pages,
            )

    pages = crawl_update_notes(
        base,
        max_pages=settings.reporter_update_notes_max_pages,
        timeout_seconds=settings.reporter_fetch_timeout_seconds,
        user_agent=settings.reporter_fetch_user_agent,
        excerpt_chars=settings.reporter_context_chars,
    )
    if pages:
        return RoomUpdateNotes(
            room_id=room.id,
            room_label=room.label or room.title,
            base_url=base,
            source="crawl",
            pages=pages,
        )

    if settings.reporter_web_search and _is_openai_cloud(
        settings.reporter_openai_api_base
    ):
        text = fetch_via_openai_web_search(
            api_key=settings.reporter_api_key,
            model=settings.reporter_model,
            base_url=base,
            room_label=room.label or room.title,
            timeout_seconds=settings.reporter_fetch_timeout_seconds,
        )
        if text:
            return RoomUpdateNotes(
                room_id=room.id,
                room_label=room.label or room.title,
                base_url=base,
                source="web_search",
                pages=[
                    UpdateNotesPage(
                        url=base,
                        title="OpenAI web_search 요약",
                        excerpt=excerpt_for_llm(
                            text, max_chars=settings.reporter_context_chars
                        ),
                    )
                ],
            )

    return RoomUpdateNotes(
        room_id=room.id,
        room_label=room.label or room.title,
        base_url=base,
        source="crawl",
        error="fetch_failed_or_empty",
    )


def gather_update_notes_for_rooms(
    settings: AppSettings,
    room_ids: Iterable[str],
) -> list[RoomUpdateNotes]:
    wanted = {str(r) for r in room_ids}
    by_id = {r.id: r for r in settings.rooms}
    out: list[RoomUpdateNotes] = []
    for rid in sorted(wanted):
        room = by_id.get(rid)
        if room is None:
            continue
        out.append(gather_room_update_notes(room, settings))
    return out


def room_notes_to_dict(notes: RoomUpdateNotes) -> dict:
    return {
        "room_id": notes.room_id,
        "room_label": notes.room_label,
        "base_url": notes.base_url,
        "source": notes.source,
        "error": notes.error,
        "pages": [
            {"url": p.url, "title": p.title, "excerpt": p.excerpt}
            for p in notes.pages
        ],
    }
