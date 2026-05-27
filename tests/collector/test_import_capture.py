from pathlib import Path

from collector.import_capture import split_capture_file

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_capture.txt"


def test_split_capture_file_on_saved_capture_header():
    header_block = (
        "room_id: lineage-m\n"
        "canonical_title: 리니지m방\n"
        "captured_at: 2026-05-26T05:47:33\n"
        "\n"
    )
    body = FIXTURE.read_text(encoding="utf-8")
    header, parsed_body = split_capture_file(header_block + body)
    assert header["room_id"] == "lineage-m"
    assert "2026년 5월 13일" in parsed_body
