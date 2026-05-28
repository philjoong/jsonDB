"""Tests for EXAONE / OpenAI-compatible periodic analyzer."""

import json
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

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
    cov, topics = normalize_llm_payload(
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
        },
        bucket=bucket,
        message_count=2,
        min_distinct_nicks=3,
    )
    assert cov == "high"
    assert topics[0]["underrepresented"] is True
    assert topics[0]["tag"] == "balance"


def test_normalize_llm_payload_attaches_conversation_contexts():
    bucket = Bucket(
        room_id="r1",
        period_key="2026-05-20",
        period_type="1d",
        period_start=datetime(2026, 5, 20, 0, 0, tzinfo=TZ),
        period_end=datetime(2026, 5, 21, 0, 0, tzinfo=TZ),
        message_count=3,
    )
    _cov, topics = normalize_llm_payload(
        {
            "coverage": "partial",
            "conversation_contexts": [
                {
                    "context_id": "ctx_balance",
                    "label": "raid balance replies",
                    "summary": "Players compared raid tuning after a question.",
                    "message_ids": [10, 11, 12],
                    "nicks": ["a", "b"],
                }
            ],
            "topics": [
                {
                    "tag": "balance",
                    "title": "raid balance",
                    "topic_key": "raid_balance",
                    "mentions": 3,
                    "distinct_nicks": 2,
                    "context_ids": ["ctx_balance"],
                }
            ],
        },
        bucket=bucket,
        message_count=3,
        min_distinct_nicks=3,
    )
    assert topics[0]["context_ids"] == ["ctx_balance"]
    assert topics[0]["contexts"][0]["message_ids"] == [10, 11, 12]
    assert topics[0]["contexts"][0]["summary"].startswith("Players compared")


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


def test_analyze_bucket_raises_on_empty_llm_topics(tmp_path):
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
                body="dragon raid balance discussion",
                content_hash=compute_content_hash(
                    "r1", "a", t1, "dragon raid balance discussion"
                ),
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

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(message=MagicMock(content=json.dumps({"coverage": "high", "topics": []})))
    ]
    mock_client.chat.completions.create.return_value = mock_resp

    settings = AppSettings(analyzer_fallback_heuristic=False)
    with pytest.raises(AnalyzerLLMError, match="returned no topics"):
        analyze_bucket_llm(conn, bucket, settings, client=mock_client)
    conn.close()


def test_analyze_bucket_llm_chunks_large_bucket(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    sync_rooms(conn, [RoomConfig(id="r1", title="t1", label="l1")])

    base = datetime(2026, 5, 20, 10, 0, tzinfo=TZ)
    messages = []
    for i in range(8):
        body = f"raid balance topic {i} " + ("x" * 80)
        messages.append(
            ParsedMessage(
                nick=f"u{i % 3}",
                message_at=base,
                body=body,
                content_hash=compute_content_hash("r1", f"u{i % 3}", base, body),
            )
        )
    insert_messages(conn, "r1", messages, collected_at=base)

    bucket = Bucket(
        room_id="r1",
        period_key="2026-05-20",
        period_type="1d",
        period_start=datetime(2026, 5, 20, 0, 0, tzinfo=TZ),
        period_end=datetime(2026, 5, 21, 0, 0, tzinfo=TZ),
        message_count=len(messages),
    )

    def _response(title: str, mentions: int):
        payload = {
            "coverage": "partial",
            "topics": [
                {
                    "tag": "balance",
                    "title": title,
                    "topic_key": "raid_balance",
                    "mentions": mentions,
                    "distinct_nicks": 2,
                    "quote_refs": [{"message_id": 1}],
                }
            ],
        }
        return MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(payload)))]
        )

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        _response("레이드 밸런스", 1) for _ in range(10)
    ]

    settings = AppSettings(
        analyzer_fallback_heuristic=False,
        analyzer_max_transcript_chars=450,
    )
    insight = analyze_bucket_llm(conn, bucket, settings, client=mock_client)

    assert mock_client.chat.completions.create.call_count > 1
    assert insight.topics[0]["title"] == "레이드 밸런스"
    assert insight.topics[0]["mentions"] == mock_client.chat.completions.create.call_count
    assert insight.analyzer_backend == "llm"
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
