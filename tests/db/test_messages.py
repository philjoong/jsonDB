from datetime import datetime
from zoneinfo import ZoneInfo

from db.connection import init_db
from db.messages import ParsedMessage, compute_content_hash, insert_messages, purge_messages_older_than
from openchat.config import RoomConfig
from db.messages import sync_rooms

ROOM_ID = "purge-room"
TZ = ZoneInfo("Asia/Seoul")


def _msg(body: str, when: datetime) -> ParsedMessage:
    return ParsedMessage(
        nick="u",
        message_at=when,
        body=body,
        content_hash=compute_content_hash(ROOM_ID, "u", when, body),
    )


def test_purge_deletes_old_collected_at(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    sync_rooms(conn, [RoomConfig(id=ROOM_ID, title="t", label="l")])

    old_time = datetime(2026, 5, 1, 12, 0, tzinfo=TZ)
    new_time = datetime(2026, 5, 20, 12, 0, tzinfo=TZ)
    insert_messages(conn, ROOM_ID, [_msg("old", old_time)], collected_at=old_time)
    insert_messages(conn, ROOM_ID, [_msg("new", new_time)], collected_at=new_time)

    cutoff = datetime(2026, 5, 10, 0, 0, tzinfo=TZ)
    deleted = purge_messages_older_than(conn, cutoff=cutoff)
    assert deleted == 1

    remaining = conn.execute("SELECT body FROM messages").fetchall()
    assert len(remaining) == 1
    assert remaining[0]["body"] == "new"
    conn.close()
