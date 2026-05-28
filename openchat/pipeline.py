"""Project-scoped collect / analyze / report runners for web and CLI reuse."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from analyzer.periodic import analyze_bucket
from collector.runner import CollectCycleResult, run_collect_cycle
from db.connection import init_db
from db.insights import PeriodicInsightRow, upsert_periodic_insight
from db.web_jobs import append_job_log, insert_report_run
from openchat.config import AppSettings, ProjectConfig
from report.render_html import ReportResult, render_html_report
from stats.aggregator import aggregate_stats

from analyzer.bucketizer import bucketize_diagnostics


@dataclass
class CollectProjectResult:
    cycle: CollectCycleResult
    project_id: str


@dataclass
class AnalyzeProjectResult:
    project_id: str
    processed: int
    processed_buckets: list[tuple[str, str]]
    failed_buckets: list[dict[str, str]]
    already_analyzed: int
    buckets_with_messages: int


@dataclass
class ReportProjectResult:
    project_id: str
    report: ReportResult
    run_id: int | None
    aggregate_periods: int


def find_project(settings: AppSettings, project_id: str) -> ProjectConfig:
    for room in settings.rooms:
        if room.id == project_id:
            return room
    raise KeyError(f"project not found: {project_id}")


def run_collect_project(
    settings: AppSettings,
    project_id: str,
    *,
    save_captures: bool = True,
) -> CollectProjectResult:
    room = find_project(settings, project_id)
    if not room.enabled:
        raise ValueError(f"project is disabled: {project_id}")
    cycle = run_collect_cycle(settings, rooms=[room], save_captures=save_captures)
    return CollectProjectResult(cycle=cycle, project_id=project_id)


def run_analyze_project(
    conn: sqlite3.Connection,
    settings: AppSettings,
    project_id: str,
    *,
    include_current: bool = False,
    force: bool = False,
    limit: int | None = None,
    heuristic: bool = False,
    top_n: int = 12,
    job_id: str | None = None,
) -> AnalyzeProjectResult:
    find_project(settings, project_id)
    room_labels = {r.id: r.label for r in settings.rooms}
    diag = bucketize_diagnostics(
        conn,
        analyzer_period=settings.analyzer_period,
        analyzer_version=settings.analyzer_prompt_version,
        tz=settings.tz,
        room_ids=[project_id],
        include_current=include_current,
        include_analyzed=force,
        limit=limit,
    )
    processed = 0
    processed_buckets: list[tuple[str, str]] = []
    failed_buckets: list[dict[str, str]] = []
    for b in diag.queued:
        if b.room_id != project_id:
            continue
        if job_id:
            append_job_log(conn, job_id, f"analyze bucket {b.period_key}")
        try:
            progress = (
                (lambda line, jid=job_id: append_job_log(conn, jid, line))
                if job_id
                else None
            )
            insight = analyze_bucket(
                conn,
                b,
                settings,
                room_label=room_labels.get(b.room_id, b.room_id),
                force_heuristic=heuristic,
                top_n=top_n,
                progress=progress,
            )
        except Exception as exc:
            err = str(exc)
            failed_buckets.append(
                {"room_id": b.room_id, "period_key": b.period_key, "error": err}
            )
            if job_id:
                append_job_log(conn, job_id, f"analyze bucket {b.period_key} failed: {err}")
            continue
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
                analyzer_model=settings.analyzer_model_label,
                analyzer_version=settings.analyzer_prompt_version,
                prompt_hash=insight.prompt_hash,
                created_at=datetime.now(ZoneInfo(settings.tz)),
            ),
        )
        processed += 1
        processed_buckets.append((b.room_id, b.period_key))

    return AnalyzeProjectResult(
        project_id=project_id,
        processed=processed,
        processed_buckets=processed_buckets,
        failed_buckets=failed_buckets,
        already_analyzed=diag.already_analyzed,
        buckets_with_messages=diag.buckets_with_messages,
    )


def _scope_json(settings: AppSettings) -> str:
    scope = settings.data_scope
    return json.dumps(
        {
            "mode": scope.mode,
            "last_days": scope.last_days,
            "time_field": scope.time_field,
            "tz": scope.tz,
        },
        ensure_ascii=False,
    )


def _window_label(settings: AppSettings) -> str:
    if settings.data_scope.mode == "last_days":
        return f"최근 {settings.data_scope.last_days}일"
    return settings.reporter_window


def run_report_project(
    conn: sqlite3.Connection,
    settings: AppSettings,
    project_id: str,
    *,
    job_id: str | None = None,
    buckets: list[tuple[str, str]] | None = None,
) -> ReportProjectResult:
    find_project(settings, project_id)
    if job_id:
        append_job_log(conn, job_id, "aggregate stats")
    agg = aggregate_stats(conn)
    if job_id:
        append_job_log(conn, job_id, "render HTML report")

    out_dir = settings.output_dir / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo(settings.tz)).strftime("%Y%m%d_%H%M%S")
    output_path = out_dir / f"report_{stamp}.html"

    report = render_html_report(
        conn,
        settings,
        output_path=output_path,
        room_ids=[project_id],
        buckets=buckets,
    )

    created = datetime.now(ZoneInfo(settings.tz))
    run_id = insert_report_run(
        conn,
        project_id=project_id,
        job_id=job_id,
        created_at=created,
        output_path=str(report.output_path.resolve()),
        window_label=_window_label(settings),
        scope_json=_scope_json(settings),
        period_keys=report.period_keys,
        bucket_count=report.bucket_count,
        reporter_backend=report.reporter_backend,
        scope_mode=report.scope_mode,
        email_snapshot_json=json.dumps(report.email_snapshot, ensure_ascii=False),
    )
    return ReportProjectResult(
        project_id=project_id,
        report=report,
        run_id=run_id,
        aggregate_periods=agg.periods_processed,
    )


def run_analyze_and_report_project(
    settings: AppSettings,
    project_id: str,
    *,
    job_id: str | None = None,
) -> ReportProjectResult:
    conn = init_db(settings.database_path)
    try:
        if job_id:
            append_job_log(conn, job_id, "start analyze")
        analyze = run_analyze_project(
            conn,
            settings,
            project_id,
            job_id=job_id,
        )
        buckets = analyze.processed_buckets or None
        return run_report_project(
            conn,
            settings,
            project_id,
            job_id=job_id,
            buckets=buckets,
        )
    finally:
        conn.close()
