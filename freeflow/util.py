"""Small helpers: logging, single-instance guard, foreground window info."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import logging.handlers
import os
import sys

from .config import DATA_DIR, LOG_PATH

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_mutex_handle = None


def setup_logging(debug: bool = False):
    os.makedirs(DATA_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:  # pythonw.exe has no console
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    for name in ("comtypes", "urllib3", "httpx", "httpcore", "httpx2", "httpcore2", "faster_whisper", "PIL"):
        logging.getLogger(name).setLevel(logging.WARNING)


def acquire_single_instance(name: str = r"Local\FreeFlow.SingleInstance") -> bool:
    """Return True if this is the only running instance."""
    global _mutex_handle
    if os.environ.get("FREEFLOW_CONFIG"):   # a test copy with its own config may run next to the real one
        import hashlib
        name += "." + hashlib.md5(os.environ["FREEFLOW_CONFIG"].lower().encode()).hexdigest()[:8]
    kernel32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
    kernel32.CreateMutexW.restype = wt.HANDLE
    _mutex_handle = kernel32.CreateMutexW(None, False, name)
    ERROR_ALREADY_EXISTS = 183
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def window_info(hwnd: int) -> tuple[str, str, int]:
    """Return (exe_name, window_title, pid) for a window handle."""
    if not hwnd:
        return "", "", 0
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    exe = ""
    try:
        import psutil
        exe = psutil.Process(pid.value).name()
    except Exception:
        pass
    return exe, buf.value, pid.value


def foreground_window_info() -> tuple[str, str, int, int]:
    """Return (exe_name, window_title, pid, hwnd) of the foreground window."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", "", 0, 0
    exe, title, pid = window_info(hwnd)
    return exe, title, pid, int(hwnd)


def message_box(text: str, title: str = "FreeFlow", flags: int = 0x40):
    user32.MessageBoxW(None, text, title, flags)
