"""Putting text into the focused application: keystroke typing or clipboard paste."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN = 0x11, 0x10, 0x12, 0x5B, 0x5C
VK_RETURN, VK_TAB, VK_V = 0x0D, 0x09, 0x56
VK_DUMMY = 0xE8  # unassigned virtual key, used to "consume" a Win/Alt press
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.MapVirtualKeyW.restype = wt.UINT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.OpenClipboard.argtypes = [wt.HWND]
user32.OpenClipboard.restype = wt.BOOL
user32.CloseClipboard.restype = wt.BOOL
user32.EmptyClipboard.restype = wt.BOOL
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE
user32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
user32.IsClipboardFormatAvailable.restype = wt.BOOL
user32.CountClipboardFormats.restype = ctypes.c_int
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.restype = wt.LPVOID
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.restype = wt.BOOL
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.restype = wt.HGLOBAL


# --------------------------------------------------------------------------
# SendInput helpers
# --------------------------------------------------------------------------
def _key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return inp


def _send(inputs: list[INPUT]) -> int:
    if not inputs:
        return 0
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        log.warning("SendInput sent %d/%d events (err %s)", sent, len(inputs), ctypes.get_last_error())
    return sent


def _vk_events(vk: int, up: bool = False) -> INPUT:
    scan = user32.MapVirtualKeyW(vk, 0)
    return _key_input(vk, scan, KEYEVENTF_KEYUP if up else 0)


def press_vk(vk: int):
    _send([_vk_events(vk), _vk_events(vk, up=True)])


def send_dummy_key():
    press_vk(VK_DUMMY)


def send_ctrl_v():
    _send([_vk_events(VK_CONTROL), _vk_events(VK_V), _vk_events(VK_V, up=True), _vk_events(VK_CONTROL, up=True)])


def modifiers_down() -> bool:
    for vk in (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            return True
    return False


def wait_modifiers_released(timeout: float = 3.0) -> bool:
    """Wait until Ctrl/Shift/Alt/Win are physically released (so our keystrokes are not modified)."""
    end = time.monotonic() + timeout
    while modifiers_down():
        if time.monotonic() > end:
            return False
        time.sleep(0.01)
    return True


VK_LSHIFT, VK_CAPITAL = 0xA0, 0x14
user32.VkKeyScanW.argtypes = [wt.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short
user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetKeyState.restype = ctypes.c_short


def _key_for_char(ch: str):
    """(virtual key, needs shift) for a character on the current keyboard layout, or None."""
    res = user32.VkKeyScanW(ch)
    if res == -1:
        return None
    vk, state = res & 0xFF, (res >> 8) & 0xFF
    if vk == 0 or state & 0x06:      # needs Ctrl/Alt (AltGr) - send it as Unicode instead
        return None
    return vk, bool(state & 0x01)


def _scan_events(vk: int, up: bool = False) -> INPUT:
    """A key event carrying the hardware scan code - what games and DirectInput read."""
    scan = user32.MapVirtualKeyW(vk, 0)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    return _key_input(vk, scan, flags)


def press_enter():
    """Enter as a real key press (scan code), e.g. to open or send a game's chat box."""
    _send([_scan_events(VK_RETURN), _scan_events(VK_RETURN, up=True)])


def type_text(text: str, chunk_delay_ms: int = 0, scancodes: bool = True):
    """Type text as keystrokes. Newlines become Enter, tabs become Tab.

    Characters that exist on the keyboard layout are sent as real key presses with scan codes
    (games and other apps that ignore Unicode input still see them); anything else is sent as
    Unicode.  Caps Lock is taken into account.
    """
    caps = bool(user32.GetKeyState(VK_CAPITAL) & 1)
    events: list[INPUT] = []
    for ch in text:
        if ch == "\r":
            continue
        if ch == "\n":
            events += [_scan_events(VK_RETURN), _scan_events(VK_RETURN, up=True)]
        elif ch == "\t":
            events += [_scan_events(VK_TAB), _scan_events(VK_TAB, up=True)]
        else:
            key = _key_for_char(ch) if scancodes else None
            if key:
                vk, shift = key
                if caps and ch.isalpha():
                    shift = not shift
                if shift:
                    events.append(_scan_events(VK_LSHIFT))
                events += [_scan_events(vk), _scan_events(vk, up=True)]
                if shift:
                    events.append(_scan_events(VK_LSHIFT, up=True))
            else:
                units = ch.encode("utf-16-le")
                for i in range(0, len(units), 2):
                    code = units[i] | (units[i + 1] << 8)
                    events.append(_key_input(0, code, KEYEVENTF_UNICODE))
                    events.append(_key_input(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        if len(events) >= 64:
            _send(events)
            events = []
            if chunk_delay_ms:
                time.sleep(chunk_delay_ms / 1000.0)
    _send(events)


# --------------------------------------------------------------------------
# Clipboard
# --------------------------------------------------------------------------
def _open_clipboard(retries: int = 15) -> bool:
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def clipboard_get_text() -> Optional[str]:
    """Text on the clipboard, or None if there is no text (or it could not be read)."""
    if not _open_clipboard():
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def clipboard_has_non_text() -> bool:
    if not _open_clipboard():
        return False
    try:
        return user32.CountClipboardFormats() > 0 and not user32.IsClipboardFormatAvailable(CF_UNICODETEXT)
    finally:
        user32.CloseClipboard()


def clipboard_set_text(text: str) -> bool:
    data = text.encode("utf-16-le") + b"\x00\x00"
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        return False
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    if not _open_clipboard():
        kernel32.GlobalFree(h)
        return False
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            return False
        return True
    finally:
        user32.CloseClipboard()


def paste_text(text: str, restore: bool = True, restore_delay_ms: int = 500) -> bool:
    """Put text on the clipboard, send Ctrl+V, then restore the previous clipboard text."""
    previous = clipboard_get_text() if restore else None
    if restore and previous is None and clipboard_has_non_text():
        log.info("Clipboard held non-text data; it will not be restored after pasting")
    if not clipboard_set_text(text):
        log.error("Could not write to the clipboard")
        return False
    time.sleep(0.03)
    send_ctrl_v()
    if restore and previous is not None:
        def _restore():
            time.sleep(max(0.1, restore_delay_ms / 1000.0))
            clipboard_set_text(previous)
        threading.Thread(target=_restore, name="clipboard-restore", daemon=True).start()
    return True


def inject_text(text: str, method: str = "paste", restore_clipboard: bool = True,
                restore_delay_ms: int = 500, chunk_delay_ms: int = 0) -> bool:
    wait_modifiers_released()
    if method == "type":
        type_text(text, chunk_delay_ms)
        return True
    return paste_text(text, restore_clipboard, restore_delay_ms)
