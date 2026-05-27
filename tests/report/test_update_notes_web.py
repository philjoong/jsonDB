from report.update_notes_web import (
    discover_child_urls,
    html_to_text,
    parse_plaync_board_config,
    _article_view_url,
    _is_under_base,
)


def test_discover_child_urls_same_site_under_base():
    base = "https://game.example.com/news/patch/"
    html = """
    <html><body>
    <a href="/news/patch/2026-05-01">p1</a>
    <a href="https://game.example.com/news/patch/2026-05-02">p2</a>
    <a href="https://other.example.com/x">skip</a>
    <a href="/about">skip2</a>
    </body></html>
    """
    links = discover_child_urls(base, html)
    assert "https://game.example.com/news/patch/2026-05-01" in links
    assert "https://game.example.com/news/patch/2026-05-02" in links
    assert not any("other.example" in u for u in links)
    assert not any(u.endswith("/about") for u in links)


def test_is_under_base():
    base = "https://game.example.com/news/"
    assert _is_under_base("https://game.example.com/news/patch-1", base)
    assert not _is_under_base("https://game.example.com/blog/x", base)


def test_html_to_text_strips_tags():
    assert "hello" in html_to_text("<p>hello <b>world</b></p>")


def test_parse_plaync_board_config():
    html = """
    <script>
    boardConfig = { default: { common: {
      apiPath: "https://api-community.plaync.com/aion2/",
      boardUrlPattern: "https://aion2.plaync.com/ko-kr/board/update/view?articleId={articleId}"
    }}};
    createBoard({ type: 'list', boardAlias: 'update_ko' });
    var _localeCountry = "KR", _localeLanguage = "ko";
    </script>
    """
    cfg = parse_plaync_board_config(html)
    assert cfg is not None
    assert cfg["api_path"] == "https://api-community.plaync.com/aion2/"
    assert cfg["board_alias"] == "update_ko"
    assert cfg["country"] == "kr"
    assert cfg["lang"] == "ko"


def test_article_view_url_from_pattern():
    url = _article_view_url(
        base_url="https://aion2.plaync.com/ko-kr/board/update/list",
        content_id=12345,
        view_url_pattern=(
            "https://aion2.plaync.com/ko-kr/board/update/view?articleId={articleId}"
        ),
    )
    assert url.endswith("articleId=12345")
