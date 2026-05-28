"""Bucketize raw messages into analysis periods and queue unanalyzed buckets.

Phase 3a (development-plan.md): assign `period_key` and produce a queue of
message buckets that have not been analyzed yet for the current analyzer version.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class PeriodSpec:
    size: int
    unit: str  # "h" | "d" | "w"

    @property
    def period_type(self) -> str:
        return f"{self.size}{self.unit}"


@dataclass(frozen=True)
class Bucket:
    room_id: str
    period_key: str
    period_type: str
    period_start: datetime
    period_end: datetime
    message_count: int


@dataclass(frozen=True)
class BucketizeDiagnostics:
    """Counts explaining why the unanalyzed queue may be empty."""

    queued: list[Bucket]
    buckets_with_messages: int
    already_analyzed: int
    excluded_incomplete_period: int
    insights_for_version: int
    total_messages: int


_PERIOD_RE = re.compile(r"^\s*(\d+)\s*([hdw])\s*$", re.IGNORECASE)
_EPOCH_MONDAY = datetime(1970, 1, 5, tzinfo=UTC)  # 1970-01-05 is a Monday


def parse_period_spec(raw: str) -> PeriodSpec:
    m = _PERIOD_RE.match(raw or "")
    if not m:
        raise ValueError(f"Invalid ANALYZER_PERIOD: {raw!r} (expected like '1d', '6h', '1w')")
    size = int(m.group(1))
    unit = m.group(2).lower()
    if size <= 0:
        raise ValueError(f"Invalid ANALYZER_PERIOD: {raw!r} (size must be > 0)")
    return PeriodSpec(size=size, unit=unit)


def _floor_to_period_start(dt: datetime, spec: PeriodSpec, tz: ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)

    if spec.unit == "h":
        base = dt.replace(minute=0, second=0, microsecond=0)
        if spec.size == 1:
            return base
        epoch = datetime(1970, 1, 1, tzinfo=tz)
        hours = int((base - epoch).total_seconds() // 3600)
        floored = hours - (hours % spec.size)
        return epoch + timedelta(hours=floored)

    if spec.unit == "d":
        base = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if spec.size == 1:
            return base
        epoch = datetime(1970, 1, 1, tzinfo=tz)
        days = (base.date() - epoch.date()).days
        floored = days - (days % spec.size)
        return datetime(epoch.year, epoch.month, epoch.day, tzinfo=tz) + timedelta(days=floored)

    if spec.unit == "w":
        # ISO week starts Monday. We anchor multi-week windows to epoch Monday.
        base = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        base_monday = base - timedelta(days=base.weekday())
        if spec.size == 1:
            return base_monday
        weeks = int((base_monday.astimezone(UTC) - _EPOCH_MONDAY).total_seconds() // (7 * 24 * 3600))
        floored = weeks - (weeks % spec.size)
        return (_EPOCH_MONDAY + timedelta(weeks=floored)).astimezone(tz)

    raise ValueError(f"Unsupported period unit: {spec.unit!r}")


def _period_end(start: datetime, spec: PeriodSpec) -> datetime:
    if spec.unit == "h":
        return start + timedelta(hours=spec.size)
    if spec.unit == "d":
        return start + timedelta(days=spec.size)
    if spec.unit == "w":
        return start + timedelta(weeks=spec.size)
    raise ValueError(f"Unsupported period unit: {spec.unit!r}")


def _period_key(start: datetime, spec: PeriodSpec, tz: ZoneInfo) -> str:
    s = start.astimezone(tz)
    if spec.unit == "h":
        return s.strftime("%Y-%m-%dT%H")
    if spec.unit == "d":
        return s.strftime("%Y-%m-%d")
    if spec.unit == "w":
        iso = s.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    raise ValueError(f"Unsupported period unit: {spec.unit!r}")


def _chunks(seq: list[str], n: int) -> Iterable[list[str]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _fetch_enabled_room_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT room_id FROM rooms WHERE enabled = 1 ORDER BY room_id"
    ).fetchall()
    return [str(r["room_id"]) for r in rows]


def _collect_message_buckets(
    conn: sqlite3.Connection,
    *,
    analyzer_period: str,
    tz: ZoneInfo,
    room_ids: list[str],
    include_current: bool,
) -> dict[tuple[str, str], Bucket]:
    spec = parse_period_spec(analyzer_period)
    now = datetime.now(tz)
    buckets: dict[tuple[str, str], Bucket] = {}

    for chunk in _chunks(room_ids, 200):
        placeholders = ",".join(["?"] * len(chunk))
        rows = conn.execute(
            f"""
            SELECT room_id, message_at
            FROM messages
            WHERE room_id IN ({placeholders})
            ORDER BY room_id, message_at
            """,
            tuple(chunk),
        ).fetchall()

        for row in rows:
            room_id = str(row["room_id"])
            msg_at = datetime.fromisoformat(str(row["message_at"])).astimezone(tz)
            start = _floor_to_period_start(msg_at, spec, tz)
            end = _period_end(start, spec)
            if not include_current and end > now:
                continue
            key = _period_key(start, spec, tz)
            bkey = (room_id, key)
            prev = buckets.get(bkey)
            if prev is None:
                buckets[bkey] = Bucket(
                    room_id=room_id,
                    period_key=key,
                    period_type=spec.period_type,
                    period_start=start,
                    period_end=end,
                    message_count=1,
                )
            else:
                buckets[bkey] = Bucket(
                    room_id=prev.room_id,
                    period_key=prev.period_key,
                    period_type=prev.period_type,
                    period_start=prev.period_start,
                    period_end=prev.period_end,
                    message_count=prev.message_count + 1,
                )
    return buckets


def _load_analyzed_keys(
    conn: sqlite3.Connection,
    analyzer_version: str,
) -> set[tuple[str, str]]:
    analyzed_rows = conn.execute(
        """
        SELECT room_id, period_key
        FROM periodic_insights
        WHERE analyzer_version = ?
          AND trim(COALESCE(topics_json, '')) NOT IN ('', '[]')
        """,
        (analyzer_version,),
    ).fetchall()
    return {(str(r["room_id"]), str(r["period_key"])) for r in analyzed_rows}


def bucketize_diagnostics(
    conn: sqlite3.Connection,
    *,
    analyzer_period: str,
    analyzer_version: str,
    tz: ZoneInfo | str = "Asia/Seoul",
    room_ids: list[str] | None = None,
    include_current: bool = False,
    include_analyzed: bool = False,
    limit: int | None = None,
) -> BucketizeDiagnostics:
    """Summarize bucket queue state for CLI output."""
    if isinstance(tz, str):
        tz = ZoneInfo(tz)

    room_ids = room_ids or _fetch_enabled_room_ids(conn)
    if not room_ids:
        return BucketizeDiagnostics(
            queued=[],
            buckets_with_messages=0,
            already_analyzed=0,
            excluded_incomplete_period=0,
            insights_for_version=0,
            total_messages=0,
        )

    analyzed = _load_analyzed_keys(conn, analyzer_version)
    all_closed = _collect_message_buckets(
        conn,
        analyzer_period=analyzer_period,
        tz=tz,
        room_ids=room_ids,
        include_current=False,
    )
    all_open = _collect_message_buckets(
        conn,
        analyzer_period=analyzer_period,
        tz=tz,
        room_ids=room_ids,
        include_current=True,
    )

    buckets_with_messages = len(all_open)
    excluded_incomplete_period = len(set(all_open.keys()) - set(all_closed.keys()))

    queued_map: dict[tuple[str, str], Bucket] = {}
    already_analyzed = 0
    source = all_open if include_current else all_closed
    for bkey, bucket in source.items():
        if not include_analyzed and bkey in analyzed:
            already_analyzed += 1
            continue
        queued_map[bkey] = bucket

    queued = sorted(
        queued_map.values(),
        key=lambda b: (b.period_start, b.room_id),
    )
    if limit is not None:
        queued = queued[: max(0, int(limit))]

    insights_for_version = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM periodic_insights WHERE analyzer_version = ?",
            (analyzer_version,),
        ).fetchone()["c"]
    )
    total_messages = int(conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"])

    return BucketizeDiagnostics(
        queued=queued,
        buckets_with_messages=buckets_with_messages,
        already_analyzed=already_analyzed,
        excluded_incomplete_period=excluded_incomplete_period,
        insights_for_version=insights_for_version,
        total_messages=total_messages,
    )


def queue_unanalyzed_buckets(
    conn: sqlite3.Connection,
    *,
    analyzer_period: str,
    analyzer_version: str,
    tz: ZoneInfo | str = "Asia/Seoul",
    room_ids: list[str] | None = None,
    include_current: bool = False,
    include_analyzed: bool = False,
    limit: int | None = None,
) -> list[Bucket]:
    """
    Return buckets that have messages but no periodic_insights row yet
    for (room_id, period_key, analyzer_version).
    """
    return bucketize_diagnostics(
        conn,
        analyzer_period=analyzer_period,
        analyzer_version=analyzer_version,
        tz=tz,
        room_ids=room_ids,
        include_current=include_current,
        include_analyzed=include_analyzed,
        limit=limit,
    ).queued


def print_bucketize_summary(
    diag: BucketizeDiagnostics,
    *,
    analyzer_period: str,
    analyzer_version: str,
    include_current: bool,
    tz: ZoneInfo | str = "Asia/Seoul",
    file: Any = None,
) -> None:
    """Human-readable bucketize output (mirrors collect cycle summary style)."""
    out = file or sys.stdout
    if isinstance(tz, str):
        tz = ZoneInfo(tz)
    now = datetime.now(tz)

    print(
        f"\n--- Bucketize @ {now.isoformat(timespec='seconds')} "
        f"(period={analyzer_period}, version={analyzer_version}) ---",
        file=out,
    )
    print(
        f"  DB: {diag.total_messages} message(s), "
        f"{diag.insights_for_version} insight row(s) for this version",
        file=out,
    )

    if diag.queued:
        print(
            "  room_id\tperiod_key\tperiod_type\tmessages\tstart\tend",
            file=out,
        )
        for b in diag.queued:
            start = b.period_start.isoformat(timespec="seconds")
            end = b.period_end.isoformat(timespec="seconds")
            print(
                f"  {b.room_id}\t{b.period_key}\t{b.period_type}\t"
                f"{b.message_count}\t{start}\t{end}",
                file=out,
            )
    else:
        print("  (no unanalyzed buckets in queue)", file=out)

    print(
        f"Summary: queued={len(diag.queued)} "
        f"buckets_with_messages={diag.buckets_with_messages} "
        f"already_analyzed={diag.already_analyzed} "
        f"excluded_incomplete_period={diag.excluded_incomplete_period}",
        file=out,
    )

    if not diag.queued:
        print("Hints:", file=out)
        if not include_current and diag.excluded_incomplete_period > 0:
            print(
                "  - 오늘(미완료) 기간 버킷은 기본 제외됩니다. "
                "포함하려면: python -m openchat bucketize --include-current",
                file=out,
            )
        if diag.already_analyzed > 0:
            print(
                "  - 수집 후에도 같은 period_key는 이미 분석된 것으로 간주됩니다. "
                "재분석: python -m openchat analyze --force",
                file=out,
            )
        if diag.total_messages == 0:
            print("  - messages 테이블이 비어 있습니다. 먼저 collect를 실행하세요.", file=out)
