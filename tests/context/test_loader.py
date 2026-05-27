from context.loader import excerpt_for_llm


def test_excerpt_for_llm_truncates():
    text = "가" * 8000
    out = excerpt_for_llm(text, max_chars=1000)
    assert len(out) < len(text)
    assert "중략" in out


def test_excerpt_for_llm_short_unchanged():
    text = "짧은 패치노트"
    assert excerpt_for_llm(text, max_chars=6000) == text
