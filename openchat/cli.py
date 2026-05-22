"""CLI entry: python -m openchat collect [--watch]."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from collector.runner import print_cycle_summary, run_collect_cycle, run_watch
from openchat.config import load_settings


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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
