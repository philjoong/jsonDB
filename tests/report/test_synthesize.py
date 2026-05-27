"""Reporter synthesis tests."""

import json
from unittest.mock import MagicMock

import pytest

from openchat.config import AppSettings
from report.payload import ReporterPayload
from report.reporter_llm import ReporterLLMError
from report.synthesize import normalize_synthesis, static_synthesis, synthesize_report


def test_normalize_synthesis_collects_quote_refs():
    raw = {
        "executive_summary": "요약",
        "highlights": ["a"],
        "topic_narratives": [
            {
                "title": "t",
                "summary": "s",
                "quote_refs": [{"message_id": 42}],
            }
        ],
        "patch_alignment": [
            {
                "patch_item": "p",
                "quote_refs": [{"search_phrase": "밸런스", "room_id": "r1"}],
            }
        ],
        "recommendations": ["r1"],
    }
    syn = normalize_synthesis(raw)
    assert syn.executive_summary == "요약"
    assert len(syn.quote_refs) == 1
    assert syn.quote_refs[0]["message_id"] == 42
    assert syn.backend == "llm"


def test_static_synthesis_without_key():
    settings = AppSettings(reporter_use_llm=True, reporter_api_key="")
    payload = ReporterPayload(
        meta={"bucket_count": 0, "period_keys": []},
        topic_stats=[{"title": "주제A", "mentions": 5, "distinct_nicks": 4}],
        patch_stats=[],
        insights=[],
        update_notes_by_room=[],
        roadmap_excerpt="",
    )
    syn = synthesize_report(payload, settings)
    assert syn.backend == "static"
    assert "주제A" in syn.executive_summary or any("주제A" in h for h in syn.highlights)


def test_synthesize_llm_with_mock(monkeypatch):
    settings = AppSettings(
        reporter_use_llm=True,
        reporter_api_key="sk-test",
        reporter_model="gpt-5.2",
        openai_api_base="http://localhost:11434/v1",
    )
    payload = ReporterPayload(
        meta={"bucket_count": 1, "period_keys": ["2026-05-26"]},
        topic_stats=[],
        patch_stats=[],
        insights=[],
        update_notes_by_room=[
            {
                "room_id": "r1",
                "base_url": "https://example.com/patch/",
                "source": "crawl",
                "pages": [{"title": "p", "url": "https://example.com/patch/1", "excerpt": "패치 1.0"}],
            }
        ],
        roadmap_excerpt="",
    )
    llm_out = {
        "executive_summary": "커뮤니티 요약",
        "highlights": [],
        "topic_narratives": [],
        "patch_alignment": [],
        "gap_topics": [],
        "recommendations": [],
    }

    def _fake_json(*_a, **_k):
        return llm_out

    monkeypatch.setattr("report.synthesize.reporter_chat_json", _fake_json)
    monkeypatch.setattr(
        "report.synthesize.create_reporter_client",
        lambda _s: MagicMock(),
    )
    syn = synthesize_report(payload, settings)
    assert syn.backend == "llm"
    assert syn.executive_summary == "커뮤니티 요약"


def test_synthesize_fallback_on_error(monkeypatch):
    settings = AppSettings(
        reporter_use_llm=True,
        reporter_api_key="sk-test",
        reporter_fallback_static=True,
    )
    payload = ReporterPayload(
        meta={"bucket_count": 0, "period_keys": []},
        topic_stats=[],
        patch_stats=[],
        insights=[],
        update_notes_by_room=[],
        roadmap_excerpt="",
    )

    def _raise(*_a, **_k):
        raise ReporterLLMError("down")

    monkeypatch.setattr("report.synthesize.reporter_chat_json", _raise)
    monkeypatch.setattr(
        "report.synthesize.create_reporter_client",
        lambda _s: MagicMock(),
    )
    syn = synthesize_report(payload, settings)
    assert syn.backend == "static"


def test_synthesize_raises_without_fallback(monkeypatch):
    settings = AppSettings(
        reporter_use_llm=True,
        reporter_api_key="sk-test",
        reporter_fallback_static=False,
    )
    payload = ReporterPayload(
        meta={},
        topic_stats=[],
        patch_stats=[],
        insights=[],
        update_notes_by_room=[],
        roadmap_excerpt="",
    )

    def _raise(*_a, **_k):
        raise ReporterLLMError("down")

    monkeypatch.setattr("report.synthesize.reporter_chat_json", _raise)
    monkeypatch.setattr(
        "report.synthesize.create_reporter_client",
        lambda _s: MagicMock(),
    )
    with pytest.raises(ReporterLLMError):
        synthesize_report(payload, settings)
