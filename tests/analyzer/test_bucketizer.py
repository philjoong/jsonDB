from datetime import datetime
from zoneinfo import ZoneInfo

from db.connection import init_db
from db.messages import ParsedMessage, compute_content_hash, insert_messages, sync_rooms
from openchat.config import RoomConfig

from analyzer.bucketizer import queue_unanalyzed_buckets


TZ = ZoneInfo("Asia/Seoul")


def _msg(room_id: str, body: str, when: datetime) -> ParsedMessage:
    return ParsedMessage(
        nick="u",
        message_at=when,
        body=body,
        content_hash=compute_content_hash(room_id, "u", when, body),
    )


def test_bucketize_1d_queues_missing_periods(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    sync_rooms(conn, [RoomConfig(id="r1", title="t1", label="l1")])

    d1 = datetime(2026, 5, 20, 10, 0, tzinfo=TZ)
    d2 = datetime(2026, 5, 21, 11, 0, tzinfo=TZ)
    insert_messages(conn, "r1", [_msg("r1", "a", d1)], collected_at=d1)
    insert_messages(conn, "r1", [_msg("r1", "b", d2)], collected_at=d2)

    # Mark 2026-05-20 as already analyzed for v1.
    conn.execute(
        """
        INSERT INTO periodic_insights
            (room_id, period_key, period_start, period_end, period_type, message_count,
             analyzer_version, created_at, topics_json, patch_reactions_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]')
        """,
        (
            "r1",
            "2026-05-20",
            "2026-05-20T00:00:00+09:00",
            "2026-05-21T00:00:00+09:00",
            "1d",
            1,
            "v1",
            datetime(2026, 5, 22, 0, 0, tzinfo=TZ).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()

    buckets = queue_unanalyzed_buckets(
        conn,
        analyzer_period="1d",
        analyzer_version="v1",
        tz=TZ,
        include_current=True,
    )
    # Should only queue 2026-05-21.
    assert [(b.room_id, b.period_key, b.message_count) for b in buckets] == [
        ("r1", "2026-05-21", 1)
    ]
    conn.close()


def test_bucketize_version_isolated(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    sync_rooms(conn, [RoomConfig(id="r1", title="t1", label="l1")])

    d1 = datetime(2026, 5, 20, 10, 0, tzinfo=TZ)
    insert_messages(conn, "r1", [_msg("r1", "a", d1)], collected_at=d1)

    # Analyzed but for different version (v0) should not block queue for v1.
    conn.execute(
        """
        INSERT INTO periodic_insights
            (room_id, period_key, period_start, period_end, period_type, message_count,
             analyzer_version, created_at, topics_json, patch_reactions_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]')
        """,
        (
            "r1",
            "2026-05-20",
            "2026-05-20T00:00:00+09:00",
            "2026-05-21T00:00:00+09:00",
            "1d",
            1,
            "v0",
            datetime(2026, 5, 22, 0, 0, tzinfo=TZ).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()

    buckets = queue_unanalyzed_buckets(
        conn,
        analyzer_period="1d",
        analyzer_version="v1",
        tz=TZ,
        include_current=True,
    )
    assert [(b.room_id, b.period_key, b.message_count) for b in buckets] == [
        ("r1", "2026-05-20", 1)
    ]
    conn.close()

