import argparse
import ctypes
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import win32api
    import win32con
    import win32gui
    import win32clipboard
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pywin32 is required. Install with `pip install pywin32`."
    ) from exc


DEFAULT_ROOM_NAME = "리니지 클래식 종합 커뮤니티 리니지클래식"
LIST_CONTROL_CLASS = "EVA_VH_ListControl_Dblclk"

PBYTE256 = ctypes.c_ubyte * 256
USER32 = ctypes.WinDLL("user32")
GetKeyboardState = USER32.GetKeyboardState
SetKeyboardState = USER32.SetKeyboardState
GetWindowThreadProcessId = USER32.GetWindowThreadProcessId
AttachThreadInput = USER32.AttachThreadInput
MapVirtualKeyA = USER32.MapVirtualKeyA


def enum_top_windows():
    windows = []

    def callback(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if title:
            windows.append((hwnd, title))
        return True

    win32gui.EnumWindows(callback, None)
    return windows


def find_room_window(room_name, contains=False):
    for hwnd, title in enum_top_windows():
        if (contains and room_name in title) or (not contains and title == room_name):
            return hwnd, title
    return None, None


def enum_child_windows(parent_hwnd):
    children = []

    def callback(hwnd, _):
        children.append(hwnd)
        return True

    win32gui.EnumChildWindows(parent_hwnd, callback, None)
    return children


def find_child_by_class(parent_hwnd, class_name):
    direct = win32gui.FindWindowEx(parent_hwnd, None, class_name, None)
    if direct:
        return direct

    for child in enum_child_windows(parent_hwnd):
        if win32gui.GetClassName(child) == class_name:
            return child
    return None


def get_clipboard_text():
    win32clipboard.OpenClipboard()
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
            data = win32clipboard.GetClipboardData(win32con.CF_TEXT)
            return data.decode("mbcs", errors="replace")
        return ""
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard_text(text):
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def post_key_ex(hwnd, key, modifiers=None, specialkey=False):
    modifiers = modifiers or []
    if not win32gui.IsWindow(hwnd):
        raise RuntimeError(f"Invalid window handle: {hwnd}")

    thread_id = GetWindowThreadProcessId(hwnd, None)
    lparam = win32api.MAKELONG(0, MapVirtualKeyA(key, 0))
    msg_down = win32con.WM_KEYDOWN
    msg_up = win32con.WM_KEYUP

    if specialkey:
        lparam |= 0x1000000

    if not modifiers:
        win32gui.SendMessage(hwnd, msg_down, key, lparam)
        win32gui.SendMessage(hwnd, msg_up, key, lparam | 0xC0000000)
        return

    key_state = PBYTE256()
    old_key_state = PBYTE256()

    win32gui.SendMessage(hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
    AttachThreadInput(win32api.GetCurrentThreadId(), thread_id, True)
    try:
        GetKeyboardState(ctypes.byref(old_key_state))
        for modkey in modifiers:
            if modkey == win32con.VK_MENU:
                lparam |= 0x20000000
                msg_down = win32con.WM_SYSKEYDOWN
                msg_up = win32con.WM_SYSKEYUP
            key_state[modkey] |= 128

        SetKeyboardState(ctypes.byref(key_state))
        time.sleep(0.03)
        win32api.PostMessage(hwnd, msg_down, key, lparam)
        time.sleep(0.03)
        win32api.PostMessage(hwnd, msg_up, key, lparam | 0xC0000000)
        time.sleep(0.03)
    finally:
        SetKeyboardState(ctypes.byref(old_key_state))
        AttachThreadInput(win32api.GetCurrentThreadId(), thread_id, False)


def normalize_kakao_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def capture_visible_messages(room_name, contains=False, restore_clipboard=True):
    original_clipboard = ""
    if restore_clipboard:
        try:
            original_clipboard = get_clipboard_text()
        except Exception:
            original_clipboard = ""

    hwnd_main, title = find_room_window(room_name, contains=contains)
    if not hwnd_main:
        raise RuntimeError(f"Room window not found: {room_name!r}")

    hwnd_list = find_child_by_class(hwnd_main, LIST_CONTROL_CLASS)
    if not hwnd_list:
        child_classes = sorted({win32gui.GetClassName(h) for h in enum_child_windows(hwnd_main)})
        raise RuntimeError(
            f"Message list control not found: {LIST_CONTROL_CLASS}. "
            f"Observed child classes: {', '.join(child_classes) or '(none)'}"
        )

    post_key_ex(hwnd_list, ord("A"), [win32con.VK_CONTROL])
    time.sleep(0.5)
    post_key_ex(hwnd_list, ord("C"), [win32con.VK_CONTROL])
    time.sleep(0.5)

    captured = normalize_kakao_text(get_clipboard_text())

    if restore_clipboard:
        try:
            set_clipboard_text(original_clipboard)
        except Exception:
            pass

    return {
        "room_title": title,
        "room_hwnd": hwnd_main,
        "list_hwnd": hwnd_list,
        "text": captured,
    }


def write_capture(result, output_dir):
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


def main():
    parser = argparse.ArgumentParser(
        description="Capture currently loaded KakaoTalk chatroom messages by Ctrl+A/C on the message list control."
    )
    parser.add_argument("--room", default=DEFAULT_ROOM_NAME, help="Exact KakaoTalk chatroom window title.")
    parser.add_argument("--contains", action="store_true", help="Match the room title by substring.")
    parser.add_argument("--output-dir", default="captures", help="Directory for captured text files.")
    parser.add_argument("--no-restore-clipboard", action="store_true", help="Leave captured text in the clipboard.")
    parser.add_argument("--preview-lines", type=int, default=10, help="Number of captured lines to print.")
    args = parser.parse_args()

    try:
        result = capture_visible_messages(
            room_name=args.room,
            contains=args.contains,
            restore_clipboard=not args.no_restore_clipboard,
        )
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
