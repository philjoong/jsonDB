import json
from datetime import datetime
from zoneinfo import ZoneInfo

from analyzer.bucketizer import queue_unanalyzed_buckets
from analyzer.periodic import analyze_bucket_heuristic
from db.connection import init_db
from db.insights import PeriodicInsightRow, upsert_periodic_insight
from db.messages import ParsedMessage, compute_content_hash, insert_messages, sync_rooms
from openchat.config import RoomConfig


TZ = ZoneInfo("Asia/Seoul")


def _msg(room_id: str, nick: str, body: str, when: datetime) -> ParsedMessage:
    return ParsedMessage(
        nick=nick,
        message_at=when,
        body=body,
        content_hash=compute_content_hash(room_id, nick, when, body),
    )


def test_analyze_writes_periodic_insights_with_distinct_nicks(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    sync_rooms(conn, [RoomConfig(id="r1", title="t1", label="l1")])

    t1 = datetime(2026, 5, 20, 10, 0, tzinfo=TZ)
    t2 = datetime(2026, 5, 20, 11, 0, tzinfo=TZ)
    insert_messages(conn, "r1", [_msg("r1", "a", "패치 좋다", t1)], collected_at=t1)
    insert_messages(conn, "r1", [_msg("r1", "b", "패치 별로다", t2)], collected_at=t2)

    buckets = queue_unanalyzed_buckets(
        conn,
        analyzer_period="1d",
        analyzer_version="v1",
        tz=TZ,
        include_current=True,
    )
    assert len(buckets) == 1
    b = buckets[0]

    insight = analyze_bucket_heuristic(
        conn, b, tz=TZ, top_n=5, analyzer_model="heuristic", analyzer_version="v1"
    )
    upsert_periodic_insight(
        conn,
        PeriodicInsightRow(
            room_id=b.room_id,
            period_key=b.period_key,
            period_start=b.period_start,
            period_end=b.period_end,
            period_type=b.period_type,
            message_count=insight.message_count,
            coverage=insight.coverage,
            topics=insight.topics,
            patch_reactions=insight.patch_reactions,
            analyzer_model="heuristic",
            analyzer_version="v1",
            prompt_hash=insight.prompt_hash,
            created_at=datetime(2026, 5, 21, 0, 0, tzinfo=TZ),
        ),
    )
    assert (
        conn.execute("SELECT COUNT(*) AS c FROM periodic_insights").fetchone()["c"] == 1
    )

    row = conn.execute(
        "SELECT topics_json, patch_reactions_json, message_count FROM periodic_insights"
    ).fetchone()
    assert int(row["message_count"]) == 2
    topics = json.loads(row["topics_json"])
    assert isinstance(topics, list)
    assert any("distinct_nicks" in t for t in topics)
    assert json.loads(row["patch_reactions_json"]) == []
    conn.close()


def test_upsert_overwrites_same_version(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    sync_rooms(conn, [RoomConfig(id="r1", title="t1", label="l1")])

    t1 = datetime(2026, 5, 20, 10, 0, tzinfo=TZ)
    insert_messages(conn, "r1", [_msg("r1", "a", "hello world", t1)], collected_at=t1)

    b = queue_unanalyzed_buckets(
        conn,
        analyzer_period="1d",
        analyzer_version="v1",
        tz=TZ,
        include_current=True,
        include_analyzed=True,
        limit=1,
    )[0]

    upsert_periodic_insight(
        conn,
        PeriodicInsightRow(
            room_id="r1",
            period_key=b.period_key,
            period_start=b.period_start,
            period_end=b.period_end,
            period_type=b.period_type,
            message_count=1,
            coverage=None,
            topics=[{"tag": "general", "title": "x", "mentions": 1, "distinct_nicks": 1}],
            patch_reactions=[],
            analyzer_model="heuristic",
            analyzer_version="v1",
            prompt_hash="p1",
            created_at=datetime(2026, 5, 21, 0, 0, tzinfo=TZ),
        ),
    )
    upsert_periodic_insight(
        conn,
        PeriodicInsightRow(
            room_id="r1",
            period_key=b.period_key,
            period_start=b.period_start,
            period_end=b.period_end,
            period_type=b.period_type,
            message_count=9,
            coverage="updated",
            topics=[{"tag": "general", "title": "y", "mentions": 2, "distinct_nicks": 1}],
            patch_reactions=[],
            analyzer_model="heuristic",
            analyzer_version="v1",
            prompt_hash="p2",
            created_at=datetime(2026, 5, 22, 0, 0, tzinfo=TZ),
        ),
    )

    row = conn.execute(
        "SELECT message_count, coverage, topics_json, prompt_hash FROM periodic_insights"
    ).fetchone()
    assert int(row["message_count"]) == 9
    assert row["coverage"] == "updated"
    assert json.loads(row["topics_json"])[0]["title"] == "y"
    assert row["prompt_hash"] == "p2"
    conn.close()

