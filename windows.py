from collections import namedtuple

import win32api
import win32con
import win32gui
import win32process

WindowInfo = namedtuple("WindowInfo", ["hwnd", "title", "process_name"])


def list_windows():
    """Sichtbare Top-Level-Fenster mit nicht-leerem Titel, samt Prozessname."""
    windows = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        windows.append(WindowInfo(hwnd, title, _process_name(hwnd)))

    win32gui.EnumWindows(callback, None)
    return windows


def _process_name(hwnd):
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid
        )
        try:
            path = win32process.GetModuleFileNameEx(handle, 0)
            return path.rsplit("\\", 1)[-1]
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return ""
