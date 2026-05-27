"""JSON parsing helpers for analyzer LLM responses."""

import pytest

from analyzer.llm import AnalyzerLLMError, parse_json_object


def test_parse_json_object_strips_fence():
    raw = 'Here is output:\n```json\n{"topics": []}\n```'
    assert parse_json_object(raw) == {"topics": []}


def test_parse_json_repairs_trailing_comma():
    raw = '{"topics": [], "coverage": "high",}'
    assert parse_json_object(raw)["coverage"] == "high"


def test_parse_json_repairs_union_literal_copied_from_schema():
    raw = '{"coverage": "high" | "partial" | "low", "topics": []}'
    assert parse_json_object(raw)["coverage"] == "high"


def test_parse_json_rejects_non_object():
    with pytest.raises(AnalyzerLLMError):
        parse_json_object("[1, 2]")
