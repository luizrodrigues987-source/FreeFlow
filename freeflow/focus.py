"""Where should the text go?  Focused-control inspection (UI Automation) and window activation.

UI Automation asks the window in front to describe its focused control.  A game (or any busy app)
may never answer: with the default time-outs of 2 s + 20 s per call, one press of the hotkey in a
League of Legends match froze FreeFlow for 30 s.  The inspection therefore runs on its own thread
with short time-outs, and the caller waits at most INSPECT_TIMEOUT; no answer means "unknown", and
the window in front simply keeps the text.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
import queue
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.GetForegroundWindow.restype = wt.HWND
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.AttachThreadInput.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
kernel32.GetCurrentThreadId.restype = wt.DWORD
SW_RESTORE = 9

# UI Automation ids
UIA_ControlTypePropertyId = 30003
UIA_IsValuePatternAvailablePropertyId = 30043
UIA_IsTextPatternAvailablePropertyId = 30040
UIA_IsTextEditPatternAvailablePropertyId = 30149
UIA_ValueIsReadOnlyPropertyId = 30046
UIA_NamePropertyId = 30005
UIA_ClassNamePropertyId = 30012
UIA_EditControlTypeId = 50004
UIA_ComboBoxControlTypeId = 50003
UIA_DocumentControlTypeId = 50030
CONTROL_TYPE_NAMES = {50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox", 50004: "Edit",
                      50005: "Hyperlink", 50006: "Image", 50007: "ListItem", 50008: "List", 50009: "Menu",
                      50010: "MenuBar", 50011: "MenuItem", 50012: "ProgressBar", 50013: "RadioButton",
                      50014: "ScrollBar", 50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
                      50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip", 50023: "Tree",
                      50024: "TreeItem", 50025: "Custom", 50026: "Group", 50027: "Thumb", 50028: "DataGrid",
                      50029: "DataItem", 50030: "Document", 50031: "SplitButton", 50032: "Window", 50033: "Pane",
                      50034: "Header", 50035: "HeaderItem", 50036: "Table", 50037: "TitleBar", 50038: "Separator"}
_PROPERTIES = (UIA_ControlTypePropertyId, UIA_NamePropertyId, UIA_ClassNamePropertyId,
               UIA_IsValuePatternAvailablePropertyId, UIA_IsTextPatternAvailablePropertyId,
               UIA_IsTextEditPatternAvailablePropertyId, UIA_ValueIsReadOnlyPropertyId)

# How long a window may take to answer UI Automation (milliseconds; Windows' defaults are 2000 / 20000)
UIA_CONNECTION_TIMEOUT_MS = 400
UIA_TRANSACTION_TIMEOUT_MS = 700
# How long the app waits for the inspection before it goes on without an answer (seconds)
INSPECT_TIMEOUT = 0.5

BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe",
            "chromium.exe", "iexplore.exe", "zen.exe", "librewolf.exe"}
# apps whose main area is a text editor that does not describe itself through UI Automation
TEXT_APPS = {"windowsterminal.exe", "conhost.exe", "openconsole.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
             "mintty.exe", "putty.exe", "alacritty.exe", "wezterm-gui.exe", "notepad++.exe", "sublime_text.exe",
             "notepad.exe", "wordpad.exe", "winword.exe", "excel.exe", "powerpnt.exe", "onenote.exe",
             "devenv.exe", "idea64.exe", "pycharm64.exe", "webstorm64.exe", "rider64.exe", "clion64.exe",
             "goland64.exe", "phpstorm64.exe", "obsidian.exe", "typora.exe", "vim.exe", "gvim.exe", "emacs.exe",
             "code.exe", "cursor.exe", "windsurf.exe"}

_uia = None
_uia_failed = False


def _automation():
    """Lazily create the IUIAutomation COM object (comtypes generates the wrapper module once).

    Only ever called on the inspector thread, which owns the object.
    """
    global _uia, _uia_failed
    if _uia is not None or _uia_failed:
        return _uia
    try:
        import comtypes
        import comtypes.client
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:
            try:
                comtypes.CoInitialize()
            except Exception:
                pass
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA  # noqa: N814
        try:
            # CUIAutomation8 (Windows 8+) exposes the time-outs; a game that never answers must not stall us
            uia = comtypes.client.CreateObject(UIA.CUIAutomation8._reg_clsid_, interface=UIA.IUIAutomation2)
            uia.ConnectionTimeout = UIA_CONNECTION_TIMEOUT_MS
            uia.TransactionTimeout = UIA_TRANSACTION_TIMEOUT_MS
        except Exception as e:
            log.debug("CUIAutomation8 unavailable (%s); using the plain CUIAutomation", e)
            uia = comtypes.client.CreateObject(UIA.CUIAutomation._reg_clsid_, interface=UIA.IUIAutomation)
        _uia = uia
    except Exception as e:
        log.warning("UI Automation unavailable (%s); smart targeting will be limited", e)
        _uia_failed = True
    return _uia


def describe_focus() -> dict:
    """Details about the control that currently has the keyboard focus.  Talks to the window in front
    and may block for the UI Automation time-outs: call it through inspect_focus()."""
    uia = _automation()
    if uia is None:
        return {}
    try:
        try:
            # one round trip for all properties instead of one per property
            cache = uia.CreateCacheRequest()
            for prop in _PROPERTIES:
                cache.AddProperty(prop)
            el = uia.GetFocusedElementBuildCache(cache)
            if not el:
                return {}
            get = el.GetCachedPropertyValue
            ctype = int(get(UIA_ControlTypePropertyId) or 0)
        except Exception as e:
            log.debug("cached focus query failed (%s); asking property by property", e)
            el = uia.GetFocusedElement()
            if not el:
                return {}
            get = el.GetCurrentPropertyValue
            ctype = int(get(UIA_ControlTypePropertyId) or 0)
        return {
            "control_type": CONTROL_TYPE_NAMES.get(ctype, str(ctype)),
            "name": str(get(UIA_NamePropertyId) or "")[:60],
            "class": str(get(UIA_ClassNamePropertyId) or "")[:40],
            "value_pattern": bool(get(UIA_IsValuePatternAvailablePropertyId)),
            "text_pattern": bool(get(UIA_IsTextPatternAvailablePropertyId)),
            "text_edit_pattern": bool(get(UIA_IsTextEditPatternAvailablePropertyId)),
            "read_only": bool(get(UIA_ValueIsReadOnlyPropertyId)),
        }
    except Exception as e:
        log.debug("describe_focus failed: %s", e)
        return {}


class _Inspector:
    """Runs UI Automation work on its own thread so that a window which never answers cannot
    block the app.  One request at a time: while an old one is still stuck, new ones report 'busy'."""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._busy = 0
        self.timeouts = 0

    def _ensure_thread(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="focus-inspect", daemon=True)
            self._thread.start()

    def _run(self):
        while True:
            fn, box = self._q.get()
            try:
                box["value"] = fn()
            except Exception as e:
                box["error"] = e
            finally:
                with self._lock:
                    self._busy -= 1
                box["done"].set()

    def submit(self, fn):
        """Run fn on the inspector thread without waiting for it."""
        with self._lock:
            self._busy += 1
        self._ensure_thread()
        self._q.put((fn, {"done": threading.Event()}))

    def call(self, fn, timeout: float):
        """Run fn on the inspector thread; (result, "ok") or (None, "busy" | "timeout")."""
        with self._lock:
            if self._busy:
                return None, "busy"
            self._busy += 1
        self._ensure_thread()
        box = {"done": threading.Event()}
        self._q.put((fn, box))
        if not box["done"].wait(timeout):
            self.timeouts += 1
            return None, "timeout"
        if "error" in box:
            raise box["error"]
        return box.get("value"), "ok"


_inspector = _Inspector()


def warm_up():
    """Create the UI Automation object in the background (the first use can take a second)."""
    _inspector.submit(_automation)


def inspect_focus(timeout: float = INSPECT_TIMEOUT) -> dict:
    """describe_focus() with a deadline; {} when the window in front gives no answer in time."""
    try:
        info, status = _inspector.call(describe_focus, timeout)
    except Exception as e:
        log.debug("describe_focus failed: %s", e)
        return {}
    if status == "timeout":
        log.info("The window in front did not describe its focused control within %.0f ms; keeping it",
                 timeout * 1000)
        return {}
    if status == "busy":
        log.info("Focus inspection still waiting for an earlier window; keeping the window in front")
        return {}
    return info or {}


def classify_focus(info: dict, exe: str = "") -> Optional[bool]:
    """Does the described control accept typed text?  True / False, or None when unknown."""
    exe_l = (exe or "").lower()
    if not info:
        return None
    ctype = info["control_type"]
    if ctype in ("Edit", "ComboBox") or info["text_edit_pattern"]:
        return True
    if info["value_pattern"] and not info["read_only"]:
        return True
    if ctype == "Document":
        # Browsers expose a plain page as a read-only Document; other apps' documents may be editors.
        return exe_l not in BROWSERS
    if ctype in ("Window", "Pane", "Custom", "Group", "Unknown") or ctype.isdigit():
        if info["text_pattern"]:
            return True
        # No accessibility information: games (bare "Window"), Electron/chat apps with the focus on a
        # pane (they route typing to their message box anyway), custom UIs.  Unknown - keep the window.
        # The Windows shell is the exception: its desktop / file lists never take dictated text.
        return False if exe_l == "explorer.exe" else None
    return False   # buttons, lists, images, links, menus, tabs, static text, ...


def inspect_editable(exe: str = "", extra_text_apps: Optional[set] = None,
                     timeout: float = INSPECT_TIMEOUT) -> tuple[Optional[bool], dict]:
    """(editable, focus details) for the window in front.  editable is True / False, or None when
    unknown - including when the window did not answer in time.

    Conservative: anything that might be an editor counts as editable, so text is only
    redirected away from windows that clearly have no text box (a web page without a
    focused field, a file list, the desktop, a video player, ...).
    """
    exe_l = (exe or "").lower()
    if exe_l in TEXT_APPS or (extra_text_apps and exe_l in extra_text_apps):
        return True, {}
    info = inspect_focus(timeout)
    return classify_focus(info, exe_l), info


def focused_editable(exe: str = "", extra_text_apps: Optional[set] = None) -> Optional[bool]:
    """Does the focused control accept typed text?  True / False, or None when unknown."""
    return inspect_editable(exe, extra_text_apps)[0]


def window_exists(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def activate_window(hwnd: int, timeout: float = 1.2) -> bool:
    """Bring a window to the foreground from a background process."""
    if not window_exists(hwnd):
        return False
    if user32.GetForegroundWindow() == hwnd:
        return True
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    from .inject import send_dummy_key
    attempts = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        attempts += 1
        if attempts == 1:
            send_dummy_key()               # makes us the "last input" process, which may set the foreground
            user32.SetForegroundWindow(hwnd)
        else:
            fg = user32.GetForegroundWindow()
            tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            tid_me = kernel32.GetCurrentThreadId()
            attached = bool(tid_fg) and tid_fg != tid_me and user32.AttachThreadInput(tid_me, tid_fg, True)
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(tid_me, tid_fg, False)
        for _ in range(10):
            if user32.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.02)
    return user32.GetForegroundWindow() == hwnd


def app_label(exe: str, title: str = "") -> str:
    name = os.path.splitext(exe or "")[0]
    return name[:1].upper() + name[1:] if name else (title[:20] or "window")
