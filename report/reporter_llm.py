"""OpenAI-compatible client for Reporter (shares OPENAI_API_BASE with Analyzer)."""

from __future__ import annotations

from openai import OpenAI

from analyzer.llm import AnalyzerLLMError, chat_completion_json, create_analyzer_client

ReporterLLMError = AnalyzerLLMError


def create_reporter_client(
    settings,
) -> OpenAI:
    base = (
        getattr(settings, "reporter_openai_api_base", None)
        or settings.openai_api_base
    )
    return create_analyzer_client(
        api_base=base,
        api_key=settings.reporter_api_key,
    )


def reporter_chat_json(
    client: OpenAI,
    *,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.3,
    timeout_seconds: float = 300.0,
) -> dict:
    return chat_completion_json(
        client,
        model=model,
        system=system,
        user=user,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
        max_attempts=2,
    )
