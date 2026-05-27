"""Tests for EXAONE / OpenAI-compatible periodic analyzer."""

import json
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from analyzer.bucketizer import Bucket
from analyzer.llm import AnalyzerLLMError, parse_json_object
from analyzer.periodic import (
    analyze_bucket,
    analyze_bucket_llm,
    normalize_llm_payload,
)
from db.connection import init_db
from db.messages import ParsedMessage, compute_content_hash, insert_messages, sync_rooms
from openchat.config import AppSettings, RoomConfig

TZ = ZoneInfo("Asia/Seoul")


def test_parse_json_object_strips_fence():
    raw = 'Here is output:\n```json\n{"topics": []}\n```'
    assert parse_json_object(raw) == {"topics": []}


def test_normalize_llm_payload_distinct_nicks_flag():
    bucket = Bucket(
        room_id="r1",
        period_key="2026-05-20",
        period_type="1d",
        period_start=datetime(2026, 5, 20, 0, 0, tzinfo=TZ),
        period_end=datetime(2026, 5, 21, 0, 0, tzinfo=TZ),
        message_count=2,
    )
    cov, topics, patches = normalize_llm_payload(
        {
            "coverage": "high",
            "topics": [
                {
                    "tag": "balance",
                    "title": "7싸울",
                    "mentions": 10,
                    "distinct_nicks": 1,
                }
            ],
            "patch_reactions": [],
        },
        bucket=bucket,
        message_count=2,
        min_distinct_nicks=3,
    )
    assert cov == "high"
    assert topics[0]["underrepresented"] is True
    assert topics[0]["tag"] == "balance"
    assert patches == []


def test_analyze_bucket_llm_with_mock_client(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    sync_rooms(conn, [RoomConfig(id="r1", title="t1", label="방1")])

    t1 = datetime(2026, 5, 20, 10, 0, tzinfo=TZ)
    msg = ParsedMessage(
        nick="a",
        message_at=t1,
        body="패치 밸런스 논의",
        content_hash=compute_content_hash("r1", "a", t1, "패치 밸런스 논의"),
    )
    insert_messages(conn, "r1", [msg], collected_at=t1)

    bucket = Bucket(
        room_id="r1",
        period_key="2026-05-20",
        period_type="1d",
        period_start=datetime(2026, 5, 20, 0, 0, tzinfo=TZ),
        period_end=datetime(2026, 5, 21, 0, 0, tzinfo=TZ),
        message_count=1,
    )

    llm_json = {
        "coverage": "partial",
        "topics": [
            {
                "tag": "balance",
                "title": "밸런스",
                "topic_key": "balance_talk",
                "mentions": 1,
                "distinct_nicks": 1,
                "quote_refs": [{"message_id": 1}],
            }
        ],
        "patch_reactions": [],
    }

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=json.dumps(llm_json)))]
    mock_client.chat.completions.create.return_value = mock_resp

    settings = AppSettings(
        analyzer_model="EXAONE-3.5-7.8B-Instruct",
        analyzer_quantization="Q4_K_M",
        min_distinct_nicks=3,
    )

    insight = analyze_bucket_llm(
        conn,
        bucket,
        settings,
        client=mock_client,
        room_label="방1",
    )
    assert insight.message_count == 1
    assert insight.analyzer_backend == "llm"
    assert insight.topics[0]["title"] == "밸런스"
    assert insight.topics[0]["quote_refs"][0]["message_id"] == 1
    conn.close()


def test_analyze_bucket_falls_back_on_llm_error(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    sync_rooms(conn, [RoomConfig(id="r1", title="t1", label="l1")])

    t1 = datetime(2026, 5, 20, 10, 0, tzinfo=TZ)
    insert_messages(
        conn,
        "r1",
        [
            ParsedMessage(
                nick="a",
                message_at=t1,
                body="hello world test",
                content_hash=compute_content_hash("r1", "a", t1, "hello world test"),
            )
        ],
        collected_at=t1,
    )

    bucket = Bucket(
        room_id="r1",
        period_key="2026-05-20",
        period_type="1d",
        period_start=datetime(2026, 5, 20, 0, 0, tzinfo=TZ),
        period_end=datetime(2026, 5, 21, 0, 0, tzinfo=TZ),
        message_count=1,
    )

    def _raise_llm(*_a, **_k):
        raise AnalyzerLLMError("down")

    monkeypatch.setattr("analyzer.periodic.analyze_bucket_llm", _raise_llm)

    settings = AppSettings(
        analyzer_use_llm=True,
        analyzer_fallback_heuristic=True,
        analyzer_model="EXAONE-3.5-7.8B-Instruct",
        analyzer_quantization="Q4_K_M",
    )
    insight = analyze_bucket(conn, bucket, settings)
    assert insight.analyzer_backend == "heuristic_fallback"
    assert insight.topics
    conn.close()


def test_analyze_bucket_force_heuristic(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    sync_rooms(conn, [RoomConfig(id="r1", title="t1", label="l1")])

    t1 = datetime(2026, 5, 20, 10, 0, tzinfo=TZ)
    insert_messages(
        conn,
        "r1",
        [
            ParsedMessage(
                nick="a",
                message_at=t1,
                body="keyword alpha",
                content_hash=compute_content_hash("r1", "a", t1, "keyword alpha"),
            )
        ],
        collected_at=t1,
    )

    bucket = Bucket(
        room_id="r1",
        period_key="2026-05-20",
        period_type="1d",
        period_start=datetime(2026, 5, 20, 0, 0, tzinfo=TZ),
        period_end=datetime(2026, 5, 21, 0, 0, tzinfo=TZ),
        message_count=1,
    )

    settings = AppSettings(analyzer_use_llm=True)
    insight = analyze_bucket(conn, bucket, settings, force_heuristic=True)
    assert insight.analyzer_backend == "heuristic"
    conn.close()
