"""
Legacy single-room capture CLI.

Product entry point: python -m openchat collect [--watch]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from collector.clipboard import capture_visible_messages, normalize_kakao_text

DEFAULT_ROOM_NAME = "리니지 클래식 종합 커뮤니티 리니지클래식"


def write_capture(result: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"kakao_capture_{stamp}.txt"
    header = [
        f"room_title: {result['room_title']}",
        f"room_hwnd: {result['room_hwnd']}",
        f"list_hwnd: {result['list_hwnd']}",
        f"captured_at: {datetime.now().isoformat(timespec='seconds')}",
        "",
    ]
    path.write_text("\n".join(header) + result["text"] + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture currently loaded KakaoTalk chatroom messages (single room, legacy CLI)."
    )
    parser.add_argument(
        "--room",
        default=DEFAULT_ROOM_NAME,
        help="Canonical chatroom title (unread (N) suffix allowed on window).",
    )
    parser.add_argument("--output-dir", default="captures", help="Directory for captured text files.")
    parser.add_argument("--no-restore-clipboard", action="store_true")
    parser.add_argument("--preview-lines", type=int, default=10)
    args = parser.parse_args()

    try:
        result = capture_visible_messages(
            args.room,
            restore_clipboard=not args.no_restore_clipboard,
        )
        result["text"] = normalize_kakao_text(result["text"])
        path = write_capture(result, Path(args.output_dir))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    lines = result["text"].splitlines()
    print(f"Saved: {path}")
    print(f"Room: {result['room_title']}")
    print(f"Captured lines: {len(lines)}")
    print("")
    for line in lines[: args.preview_lines]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
