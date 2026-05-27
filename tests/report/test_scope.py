from datetime import datetime
from zoneinfo import ZoneInfo

from db.connection import init_db
from db.insights import PeriodicInsightRow, upsert_periodic_insight
from db.messages import sync_rooms
from openchat.config import RoomConfig
from report.scope import resolve_report_scope

TZ = "Asia/Seoul"


def _insight(room_id: str, period_key: str, created_at: datetime) -> PeriodicInsightRow:
    start = datetime(2026, 5, 20, 0, 0, 0, tzinfo=ZoneInfo(TZ))
    end = datetime(2026, 5, 21, 0, 0, 0, tzinfo=ZoneInfo(TZ))
    return PeriodicInsightRow(
        room_id=room_id,
        period_key=period_key,
        period_start=start,
        period_end=end,
        period_type="1d",
        message_count=1,
        coverage="low",
        topics=[],
        patch_reactions=[],
        analyzer_model="test",
        analyzer_version="v1",
        prompt_hash="x",
        created_at=created_at,
    )


def test_resolve_latest_scope(tmp_path):
    conn = init_db(tmp_path / "t.db")
    sync_rooms(
        conn,
        [
            RoomConfig(id="r1", title="r1", label="r1", enabled=True),
            RoomConfig(id="r2", title="r2", label="r2", enabled=True),
        ],
    )
    upsert_periodic_insight(
        conn,
        _insight("r1", "2026-05-20", datetime(2026, 5, 20, 10, 0, 0, tzinfo=ZoneInfo(TZ))),
    )
    upsert_periodic_insight(
        conn,
        _insight("r2", "2026-05-21", datetime(2026, 5, 21, 10, 0, 0, tzinfo=ZoneInfo(TZ))),
    )
    scope = resolve_report_scope(
        conn,
        tz=TZ,
        window_days=7,
        now_dt=datetime(2026, 5, 26, 12, 0, 0, tzinfo=ZoneInfo(TZ)),
        latest=1,
    )
    assert scope.mode == "latest"
    assert len(scope.buckets) == 1
    assert scope.buckets[0].room_id == "r2"


def test_resolve_window_scope_filters_room_ids(tmp_path):
    conn = init_db(tmp_path / "t.db")
    sync_rooms(
        conn,
        [
            RoomConfig(id="r1", title="r1", label="r1", enabled=True),
            RoomConfig(id="r2", title="r2", label="r2", enabled=True),
        ],
    )
    upsert_periodic_insight(
        conn,
        _insight("r1", "2026-05-20", datetime(2026, 5, 20, 10, 0, 0, tzinfo=ZoneInfo(TZ))),
    )
    upsert_periodic_insight(
        conn,
        _insight("r2", "2026-05-21", datetime(2026, 5, 21, 10, 0, 0, tzinfo=ZoneInfo(TZ))),
    )
    scope = resolve_report_scope(
        conn,
        tz=TZ,
        window_days=30,
        now_dt=datetime(2026, 5, 26, 12, 0, 0, tzinfo=ZoneInfo(TZ)),
        room_ids=["r1"],
    )
    assert scope.mode == "window"
    assert all(b.room_id == "r1" for b in scope.buckets)
