from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db.connection import init_db
from db.messages import ParsedMessage, compute_content_hash, insert_messages, sync_rooms
from openchat.config import RoomConfig
from parser.kakao_clipboard import parse_kakao_clipboard_text


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_capture.txt"
ROOM_ID = "test-room"


def test_parse_sample_capture():
    text = FIXTURE.read_text(encoding="utf-8")
    messages = parse_kakao_clipboard_text(
        text,
        ROOM_ID,
        exclude_nicks=["오픈채팅봇"],
        exclude_body_patterns=["사진", "이모티콘", "삭제된 메시지"],
    )
    nicks = [m.nick for m in messages]
    assert "오픈채팅봇" not in nicks
    assert "테스트" not in nicks
    assert "테스트2" not in nicks
    assert messages[0].nick == "군터"
    assert messages[0].body == "먹는재미가 없으니 ㅋ"
    assert messages[1].nick == "조우/요정/붉사"
    assert "9메일 9일도 다 쓰레기 7싸울승" in messages[1].body
    assert messages[1].message_at == datetime(2026, 5, 13, 17, 40, tzinfo=ZoneInfo("Asia/Seoul"))
    assert messages[-1].body == "정상 메시지"


def test_continuation_appends_to_previous_body():
    text = "2026년 5월 13일 수요일\n[A] [오후 1:00] 첫줄\n둘째줄"
    messages = parse_kakao_clipboard_text(text, ROOM_ID)
    assert len(messages) == 1
    assert messages[0].body == "첫줄\n둘째줄"


def test_content_hash_dedup_on_insert(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    sync_rooms(
        conn,
        [RoomConfig(id=ROOM_ID, title="t", label="l")],
    )
    msg = ParsedMessage(
        nick="A",
        message_at=datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        body="hello",
        content_hash=compute_content_hash(
            ROOM_ID,
            "A",
            datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            "hello",
        ),
    )
    collected_at = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert insert_messages(conn, ROOM_ID, [msg], collected_at=collected_at) == 1
    assert insert_messages(conn, ROOM_ID, [msg], collected_at=collected_at) == 0
    conn.close()
