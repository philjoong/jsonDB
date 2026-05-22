from collector.diff import extract_new_content


def test_no_previous_returns_full():
    text = "line1\nline2"
    new, count = extract_new_content("", text)
    assert new == text
    assert count == 2


def test_overlap_extracts_tail():
    prev = "a\nb\nc"
    curr = "b\nc\nd\ne"
    new, count = extract_new_content(prev, curr)
    assert new == "d\ne"
    assert count == 2


def test_identical_snapshot_yields_empty():
    text = "x\ny\nz"
    new, count = extract_new_content(text, text)
    assert new == ""
    assert count == 0
