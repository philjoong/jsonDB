from collector.title import normalize_title, titles_match


def test_normalize_unread_suffix():
    assert normalize_title("리니지 클래식 (3)") == "리니지 클래식"
    assert normalize_title("방 이름 (12) ") == "방 이름"


def test_titles_match():
    assert titles_match("리니지 클래식", "리니지 클래식 (5)")
    assert not titles_match("A", "B")
