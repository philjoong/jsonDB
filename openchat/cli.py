"""CLI entry: python -m openchat collect | purge."""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from collector.import_capture import import_capture_file
from collector.runner import print_cycle_summary, run_collect_cycle, run_watch
from db.connection import init_db
from db.insights import PeriodicInsightRow, upsert_periodic_insight
from jobs.purge_raw import purge_raw_messages
from openchat.config import AppSettings, load_settings
from analyzer.bucketizer import bucketize_diagnostics, print_bucketize_summary
from analyzer.periodic import analyze_bucket
from context.loader import load_context
from stats.aggregator import aggregate_stats
from report.render_html import ReportResult, render_html_report


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_collect(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.env) if args.env else None)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    settings.captures_dir.mkdir(parents=True, exist_ok=True)

    if args.watch:
        run_watch(settings, once=args.once)
        return 0

    cycle = run_collect_cycle(settings, save_captures=not args.no_save)
    print_cycle_summary(cycle)
    return 1 if cycle.error_count else 0


def cmd_purge(args: argparse.Namespace) -> int:
    env_path = Path(args.env) if args.env else None
    deleted = purge_raw_messages(
        env_path=env_path,
        retention_days=args.days,
    )
    print(f"Purged {deleted} message(s)")
    return 0


def cmd_bucketize(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.env) if args.env else None)
    conn = init_db(settings.database_path)
    try:
        diag = bucketize_diagnostics(
            conn,
            analyzer_period=settings.analyzer_period,
            analyzer_version=settings.analyzer_prompt_version,
            tz=settings.tz,
            include_current=bool(args.include_current),
            limit=args.limit,
        )
        print_bucketize_summary(
            diag,
            analyzer_period=settings.analyzer_period,
            analyzer_version=settings.analyzer_prompt_version,
            include_current=bool(args.include_current),
            tz=settings.tz,
        )
    finally:
        conn.close()
    return 0


def _run_analyze_phase(
    conn,
    settings,
    *,
    include_current: bool,
    force: bool,
    limit: int | None,
    heuristic: bool,
    top_n: int,
) -> tuple[int, object, list[tuple[str, str]]]:
    """Analyze queued buckets; return (count, diagnostics, processed (room_id, period_key))."""
    context = load_context(
        patchnotes_path=settings.patchnotes_path,
        roadmap_path=settings.roadmap_path,
    )
    room_labels = {r.id: r.label for r in settings.rooms}
    diag = bucketize_diagnostics(
        conn,
        analyzer_period=settings.analyzer_period,
        analyzer_version=settings.analyzer_prompt_version,
        tz=settings.tz,
        include_current=include_current,
        include_analyzed=force,
        limit=limit,
    )
    processed = 0
    processed_buckets: list[tuple[str, str]] = []
    for b in diag.queued:
        insight = analyze_bucket(
            conn,
            b,
            settings,
            context=context,
            room_label=room_labels.get(b.room_id, b.room_id),
            force_heuristic=heuristic,
            top_n=top_n,
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
                analyzer_model=settings.analyzer_model_label,
                analyzer_version=settings.analyzer_prompt_version,
                prompt_hash=insight.prompt_hash,
                created_at=datetime.now(ZoneInfo(settings.tz)),
            ),
        )
        processed += 1
        processed_buckets.append((b.room_id, b.period_key))
    return processed, diag, processed_buckets


def cmd_analyze(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.env) if args.env else None)
    conn = init_db(settings.database_path)
    try:
        processed, diag, _processed_buckets = _run_analyze_phase(
            conn,
            settings,
            include_current=bool(args.include_current),
            force=bool(args.force),
            limit=args.limit,
            heuristic=bool(args.heuristic),
            top_n=args.top_n,
        )
    finally:
        conn.close()

    mode = "heuristic" if args.heuristic else (
        "heuristic-only" if not settings.analyzer_use_llm else settings.analyzer_model_label
    )
    print(f"Analyzed {processed} bucket(s) [{mode}]")
    if processed == 0:
        _print_analyze_zero_hints(
            diag=diag,
            include_current=bool(args.include_current),
            force=bool(args.force),
        )
    return 0


def _print_analyze_zero_hints(
    *,
    diag,
    include_current: bool,
    force: bool,
) -> None:
    """Explain why analyze processed nothing (mirrors bucketize hints)."""
    if not include_current and diag.excluded_incomplete_period > 0:
        print(
            f"Hint: {diag.excluded_incomplete_period} bucket(s) are in today's "
            "incomplete period (excluded by default). Run:\n"
            "  python -m openchat analyze --include-current"
        )
        return
    if not force and diag.already_analyzed > 0:
        print(
            "Hint: matching buckets are already in periodic_insights. "
            "Re-analyze after new collect using:\n"
            "  python -m openchat analyze --force"
        )
        return
    if diag.total_messages == 0:
        print("Hint: no messages in DB. Run collect first.")


def cmd_aggregate(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.env) if args.env else None)
    conn = init_db(settings.database_path)
    try:
        res = aggregate_stats(conn)
    finally:
        conn.close()
    print(
        "Aggregated stats:"
        f" periods={res.periods_processed}"
        f" topic_rows={res.topic_rows_inserted}"
        f" patch_rows={res.patch_rows_inserted}"
    )
    return 0


def _apply_reporter_cli_flags(settings: AppSettings, args: argparse.Namespace) -> None:
    if getattr(args, "no_reporter_llm", False):
        settings.reporter_use_llm = False


def _print_reporter_plan(settings: AppSettings) -> None:
    """Log which report path will run (OpenAI-compatible Reporter vs static)."""
    if not settings.reporter_use_llm:
        print("Reporter: static HTML only (--no-reporter-llm or REPORTER_USE_LLM=false)")
        return
    if not (settings.reporter_api_key or "").strip():
        print(
            "Reporter: static summary (REPORTER_API_KEY unset; "
            f"set key to enable {settings.reporter_model})"
        )
        return
    print(
        "Reporter LLM: "
        f"{settings.reporter_model} @ {settings.reporter_openai_api_base.rstrip('/')}"
        f" (update notes: rooms.yaml URLs"
        f"{', web_search' if settings.reporter_web_search else ', crawl'})"
    )


def _print_report_success(res: ReportResult, settings: AppSettings) -> None:
    print(
        f"Wrote report: {res.output_path}"
        f" (scope={res.scope_mode}, buckets={res.bucket_count},"
        f" reporter={res.reporter_backend})"
    )
    if res.quote_miss_count:
        print(f"  quote_miss_count={res.quote_miss_count}")
    if settings.reporter_use_llm and (settings.reporter_api_key or "").strip():
        if res.reporter_backend == "llm":
            print(
                "  AI report: 요약·차트(Chart.js)·주제/패치 합성 섹션 포함 — "
                "브라우저에서 상단 '요약' 확인"
            )
        else:
            print(
                "  AI report: Reporter API 실패 또는 비활성 — "
                "정적 요약만 포함 (로그 확인)"
            )


def _report_kwargs_from_args(args: argparse.Namespace) -> dict:
    kwargs: dict = {}
    if getattr(args, "latest", None):
        kwargs["latest"] = int(args.latest)
    if getattr(args, "period_keys", None):
        kwargs["period_keys"] = list(args.period_keys)
    if getattr(args, "room_ids", None):
        kwargs["room_ids"] = list(args.room_ids)
    if getattr(args, "report_buckets", None):
        kwargs["buckets"] = list(args.report_buckets)
    return kwargs


def cmd_report(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.env) if args.env else None)
    _apply_reporter_cli_flags(settings, args)
    _print_reporter_plan(settings)
    conn = init_db(settings.database_path)
    try:
        out = Path(args.output) if args.output else None
        res = render_html_report(
            conn,
            settings,
            output_path=out,
            **_report_kwargs_from_args(args),
        )
    finally:
        conn.close()
    _print_report_success(res, settings)
    if getattr(args, "open", False):
        webbrowser.open(res.output_path.resolve().as_uri())
    return 0


def cmd_import_capture(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.env) if args.env else None)
    paths = [Path(p) for p in args.paths]
    conn = init_db(settings.database_path)
    total_new = 0
    try:
        for path in paths:
            rid, new_count = import_capture_file(
                conn,
                path,
                settings,
                room_id=args.room_id,
            )
            total_new += new_count
            print(f"Imported {path.name}: room={rid} new_messages={new_count}")
    finally:
        conn.close()
    print(f"Done: {len(paths)} file(s), {total_new} new message(s)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from openchat.webapp import run_server

    run_server(host=args.host, port=args.port)
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    """collect → analyze → aggregate → report (+ Reporter LLM) in one run."""
    settings = load_settings(Path(args.env) if args.env else None)
    env = Path(args.env) if args.env else None
    _apply_reporter_cli_flags(settings, args)

    if args.from_capture:
        cap_paths = [Path(p) for p in args.from_capture]
        print(f"=== Import {len(cap_paths)} capture file(s) ===")
        imp_args = argparse.Namespace(
            env=str(env) if env else None,
            paths=[str(p) for p in cap_paths],
            room_id=args.room_id,
        )
        cmd_import_capture(imp_args)
        total_steps = 4
        step = 2
    elif not args.skip_collect:
        print("=== 1/4 Collect ===")
        collect_args = argparse.Namespace(
            env=str(env) if env else None,
            watch=False,
            once=False,
            no_save=bool(args.no_save),
        )
        code = cmd_collect(collect_args)
        if code != 0 and not args.continue_on_error:
            print("Collect had errors; stopping pipeline (use --continue-on-error to proceed)")
            return code
        total_steps = 4
        step = 2
    else:
        total_steps = 3
        step = 1

    print(f"=== {step}/{total_steps} Analyze ===")
    settings = load_settings(env)
    conn = init_db(settings.database_path)
    try:
        processed, diag, processed_buckets = _run_analyze_phase(
            conn,
            settings,
            include_current=bool(args.include_current),
            force=bool(args.force),
            limit=args.limit,
            heuristic=bool(args.heuristic),
            top_n=args.top_n,
        )
    finally:
        conn.close()

    mode = "heuristic" if args.heuristic else (
        "heuristic-only" if not settings.analyzer_use_llm else settings.analyzer_model_label
    )
    print(f"Analyzed {processed} bucket(s) [{mode}]")
    if processed == 0:
        _print_analyze_zero_hints(
            diag=diag,
            include_current=bool(args.include_current),
            force=bool(args.force),
        )

    step += 1
    print(f"=== {step}/{total_steps} Aggregate ===")
    cmd_aggregate(argparse.Namespace(env=str(env) if env else None))

    step += 1
    print(
        f"=== {step}/{total_steps} Report "
        f"(stats + Quote Resolver + Reporter LLM + charts) ==="
    )
    settings = load_settings(env)
    _apply_reporter_cli_flags(settings, args)

    report_kwargs: dict = {}
    if not getattr(args, "full_report", False):
        if processed_buckets:
            report_kwargs["report_buckets"] = processed_buckets
            print(
                f"Report scope: {len(processed_buckets)} bucket(s) from this analyze run"
            )
        elif getattr(args, "latest", None):
            report_kwargs["latest"] = int(args.latest)
            print(f"Report scope: --latest {args.latest}")
        elif processed == 0:
            report_kwargs["latest"] = 1
            print("Report scope: analyze processed 0 buckets — using --latest 1")
    else:
        print(f"Report scope: full window ({settings.reporter_window})")

    report_args = argparse.Namespace(
        env=str(env) if env else None,
        output=args.output,
        open=bool(args.open),
        no_reporter_llm=bool(getattr(args, "no_reporter_llm", False)),
        latest=report_kwargs.get("latest"),
        period_keys=None,
        room_ids=None,
        report_buckets=report_kwargs.get("report_buckets"),
    )
    return cmd_report(report_args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openchat",
        description="KakaoTalk open-chat insight pipeline (clipboard collector).",
    )
    parser.add_argument(
        "--env",
        metavar="PATH",
        help="Path to .env file (default: auto-load from cwd)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser(
        "collect",
        help="Capture visible chat text from configured rooms",
    )
    collect.add_argument(
        "--watch",
        action="store_true",
        help="Run on COLLECT_INTERVAL_MINUTES loop (default 10)",
    )
    collect.add_argument(
        "--once",
        action="store_true",
        help="With --watch: run a single cycle then exit",
    )
    collect.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write capture files under CAPTURES_DIR",
    )
    collect.set_defaults(func=cmd_collect)

    purge = sub.add_parser(
        "purge",
        help="Delete messages older than RETENTION_RAW_DAYS (default 7)",
    )
    purge.add_argument(
        "--days",
        type=int,
        default=None,
        help="Override retention days (default: from .env RETENTION_RAW_DAYS)",
    )
    purge.set_defaults(func=cmd_purge)

    bucketize = sub.add_parser(
        "bucketize",
        help="List unanalyzed analysis buckets (phase 3a)",
    )
    bucketize.add_argument(
        "--include-current",
        action="store_true",
        help="Include the current (not-yet-finished) period bucket",
    )
    bucketize.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of buckets printed",
    )
    bucketize.set_defaults(func=cmd_bucketize)

    analyze = sub.add_parser(
        "analyze",
        help="Analyze queued buckets into periodic_insights (phase 3b)",
    )
    analyze.add_argument(
        "--include-current",
        action="store_true",
        help="Include the current (not-yet-finished) period bucket",
    )
    analyze.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of buckets processed",
    )
    analyze.add_argument(
        "--top-n",
        type=int,
        default=12,
        help="Number of topics when using heuristic / LLM fallback",
    )
    analyze.add_argument(
        "--heuristic",
        action="store_true",
        help="Force heuristic analyzer (skip EXAONE LLM)",
    )
    analyze.add_argument(
        "--force",
        action="store_true",
        help="Re-analyze even if periodic_insights already exists for this version (upsert)",
    )
    analyze.set_defaults(func=cmd_analyze)

    aggregate = sub.add_parser(
        "aggregate",
        help="Aggregate periodic_insights into stats tables (phase 4a)",
    )
    aggregate.set_defaults(func=cmd_aggregate)

    report = sub.add_parser(
        "report",
        help="HTML report: stats, Chart.js, Reporter LLM summary (REPORTER_API_KEY)",
    )
    report.add_argument(
        "--output",
        metavar="PATH",
        default=None,
        help="Write to a specific file path (default: OUTPUT_DIR/report_*.html)",
    )
    report.add_argument(
        "--open",
        action="store_true",
        help="Open the generated HTML report in the default browser",
    )
    report.add_argument(
        "--latest",
        type=int,
        metavar="N",
        default=None,
        help="Only the N most recently stored periodic_insights rows (by created_at)",
    )
    report.add_argument(
        "--period-key",
        action="append",
        dest="period_keys",
        metavar="KEY",
        help="Only these period_key values (repeatable)",
    )
    report.add_argument(
        "--room-id",
        action="append",
        dest="room_ids",
        metavar="ID",
        help="With --period-key: limit to these room_id values",
    )
    report.add_argument(
        "--no-reporter-llm",
        action="store_true",
        help="Skip Reporter LLM; static summary + tables/charts only",
    )
    report.set_defaults(func=cmd_report)

    import_cap = sub.add_parser(
        "import-capture",
        help="Load capture_*.txt into DB without Kakao (for offline / re-test)",
    )
    import_cap.add_argument(
        "paths",
        nargs="+",
        metavar="PATH",
        help="Capture file path(s), e.g. captures/room/capture_*_full.txt",
    )
    import_cap.add_argument(
        "--room-id",
        default=None,
        help="Room id when the file has no room_id header",
    )
    import_cap.set_defaults(func=cmd_import_capture)

    pipeline = sub.add_parser(
        "pipeline",
        help="E2E: collect → analyze → aggregate → report (Reporter LLM + charts)",
    )
    pipeline.add_argument(
        "--skip-collect",
        action="store_true",
        help="Skip collect; use existing DB messages",
    )
    pipeline.add_argument(
        "--from-capture",
        nargs="+",
        metavar="PATH",
        help="Import capture file(s) then analyze (implies --skip-collect)",
    )
    pipeline.add_argument(
        "--room-id",
        default=None,
        help="With --from-capture: room id if capture header lacks room_id",
    )
    pipeline.add_argument(
        "--no-save",
        action="store_true",
        help="During collect: do not write capture files",
    )
    pipeline.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Run analyze/report even if collect had errors",
    )
    pipeline.add_argument(
        "--include-current",
        action="store_true",
        default=True,
        help="Include today's incomplete period bucket (default: on for pipeline)",
    )
    pipeline.add_argument(
        "--no-include-current",
        action="store_false",
        dest="include_current",
        help="Exclude today's incomplete period bucket",
    )
    pipeline.add_argument(
        "--force",
        action="store_true",
        default=True,
        help="Re-analyze buckets already in periodic_insights (default: on)",
    )
    pipeline.add_argument(
        "--no-force",
        action="store_false",
        dest="force",
        help="Skip buckets that were already analyzed for this version",
    )
    pipeline.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of buckets analyzed (use 1 for a quick LLM smoke test)",
    )
    pipeline.add_argument(
        "--top-n",
        type=int,
        default=12,
        help="Heuristic / fallback topic count",
    )
    pipeline.add_argument(
        "--heuristic",
        action="store_true",
        help="Force heuristic analyzer (skip LLM)",
    )
    pipeline.add_argument(
        "--output",
        metavar="PATH",
        default=None,
        help="Report output path",
    )
    pipeline.add_argument(
        "--open",
        action="store_true",
        help="Open the HTML report when finished",
    )
    pipeline.add_argument(
        "--full-report",
        action="store_true",
        help="Report uses REPORTER_WINDOW (7d) instead of only buckets analyzed in this run",
    )
    pipeline.add_argument(
        "--latest",
        type=int,
        metavar="N",
        default=None,
        help="If analyze processed 0 buckets: report --latest N instead",
    )
    pipeline.add_argument(
        "--no-reporter-llm",
        action="store_true",
        help="Report without Reporter LLM (static summary only)",
    )
    pipeline.set_defaults(func=cmd_pipeline)

    serve = sub.add_parser(
        "serve",
        help="Run web UI (FastAPI) for project CRUD on 0.0.0.0",
    )
    serve.add_argument(
        "--host",
        default=None,
        help="Bind host (default: SERVE_HOST or 0.0.0.0)",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: SERVE_PORT or 8000)",
    )
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
