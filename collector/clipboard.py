"""KakaoTalk PC chat list capture via Ctrl+A/C on EVA_VH_ListControl_Dblclk."""

from __future__ import annotations

import ctypes
import re
import time
from typing import Any

try:
    import win32api
    import win32con
    import win32gui
    import win32clipboard
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pywin32 is required. Install with `pip install -r requirements.txt`."
    ) from exc

from collector.title import normalize_title, titles_match

LIST_CONTROL_CLASS = "EVA_VH_ListControl_Dblclk"

PBYTE256 = ctypes.c_ubyte * 256
USER32 = ctypes.WinDLL("user32")
GetKeyboardState = USER32.GetKeyboardState
SetKeyboardState = USER32.SetKeyboardState
GetWindowThreadProcessId = USER32.GetWindowThreadProcessId
AttachThreadInput = USER32.AttachThreadInput
MapVirtualKeyA = USER32.MapVirtualKeyA


def enum_top_windows() -> list[tuple[int, str]]:
    windows: list[tuple[int, str]] = []

    def callback(hwnd: int, _: Any) -> bool:
        title = win32gui.GetWindowText(hwnd)
        if title:
            windows.append((hwnd, title))
        return True

    win32gui.EnumWindows(callback, None)
    return windows


def find_room_window(canonical_title: str) -> tuple[int | None, str | None]:
    """Find first top-level window whose normalized title equals canonical_title."""
    matches = find_room_windows(canonical_title)
    if not matches:
        return None, None
    hwnd, title = matches[0]
    return hwnd, title


def find_room_windows(canonical_title: str) -> list[tuple[int, str]]:
    """All windows matching canonical_title after normalize_title (unread suffix stripped)."""
    result: list[tuple[int, str]] = []
    for hwnd, title in enum_top_windows():
        if titles_match(canonical_title, title):
            result.append((hwnd, title))
    return result


def enum_child_windows(parent_hwnd: int) -> list[int]:
    children: list[int] = []

    def callback(hwnd: int, _: Any) -> bool:
        children.append(hwnd)
        return True

    win32gui.EnumChildWindows(parent_hwnd, callback, None)
    return children


def find_child_by_class(parent_hwnd: int, class_name: str) -> int | None:
    direct = win32gui.FindWindowEx(parent_hwnd, None, class_name, None)
    if direct:
        return direct

    for child in enum_child_windows(parent_hwnd):
        if win32gui.GetClassName(child) == class_name:
            return child
    return None


def get_clipboard_text() -> str:
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


def set_clipboard_text(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def post_key_ex(
    hwnd: int,
    key: int,
    modifiers: list[int] | None = None,
    specialkey: bool = False,
) -> None:
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


def normalize_kakao_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def capture_visible_messages_from_hwnd(
    hwnd_main: int,
    room_title: str,
    *,
    restore_clipboard: bool = True,
) -> dict[str, Any]:
    """Capture chat list text from an already-resolved room window."""
    original_clipboard = ""
    if restore_clipboard:
        try:
            original_clipboard = get_clipboard_text()
        except Exception:
            original_clipboard = ""

    hwnd_list = find_child_by_class(hwnd_main, LIST_CONTROL_CLASS)
    if not hwnd_list:
        child_classes = sorted(
            {win32gui.GetClassName(h) for h in enum_child_windows(hwnd_main)}
        )
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
        "room_title": room_title,
        "room_hwnd": hwnd_main,
        "list_hwnd": hwnd_list,
        "text": captured,
    }


def capture_visible_messages(
    canonical_title: str,
    *,
    restore_clipboard: bool = True,
) -> dict[str, Any]:
    """
    Capture currently loaded messages for a room identified by canonical_title.

    Window title may include a trailing (N) unread count; matching uses normalize_title.
    """
    hwnd_main, title = find_room_window(canonical_title)
    if not hwnd_main or not title:
        raise RuntimeError(f"Room window not found: {canonical_title!r}")

    result = capture_visible_messages_from_hwnd(
        hwnd_main,
        title,
        restore_clipboard=restore_clipboard,
    )
    return result
