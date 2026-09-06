"""Global hotkeys via a low-level keyboard hook (WH_KEYBOARD_LL).

The hook runs in its own thread with a Win32 message loop.  Key events are
turned into activate / deactivate / cancel callbacks by HotkeyManager.  The
callbacks are invoked on the hook thread and must return quickly (they should
only post to a queue).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x10
LLKHF_LOWER_IL_INJECTED = 0x02

LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = wt.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostThreadMessageW.restype = wt.BOOL
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetCurrentThreadId.restype = wt.DWORD

# --------------------------------------------------------------------------
# Key names
# --------------------------------------------------------------------------
VK_NAMES: dict[str, set[int]] = {
    "ctrl": {0xA2, 0xA3}, "lctrl": {0xA2}, "rctrl": {0xA3},
    "shift": {0xA0, 0xA1}, "lshift": {0xA0}, "rshift": {0xA1},
    "alt": {0xA4, 0xA5}, "lalt": {0xA4}, "ralt": {0xA5},
    "win": {0x5B, 0x5C}, "lwin": {0x5B}, "rwin": {0x5C},
    "space": {0x20}, "esc": {0x1B}, "tab": {0x09}, "enter": {0x0D}, "backspace": {0x08},
    "capslock": {0x14}, "scrolllock": {0x91}, "numlock": {0x90}, "pause": {0x13},
    "insert": {0x2D}, "delete": {0x2E}, "home": {0x24}, "end": {0x23},
    "pageup": {0x21}, "pagedown": {0x22},
    "up": {0x26}, "down": {0x28}, "left": {0x25}, "right": {0x27},
    "printscreen": {0x2C}, "menu": {0x5D},
    "backtick": {0xC0}, "minus": {0xBD}, "equals": {0xBB}, "lbracket": {0xDB}, "rbracket": {0xDD},
    "backslash": {0xDC}, "semicolon": {0xBA}, "quote": {0xDE}, "comma": {0xBC},
    "period": {0xBE}, "slash": {0xBF},
}
for _i in range(1, 25):
    VK_NAMES[f"f{_i}"] = {0x6F + _i}
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK_NAMES[_c] = {ord(_c.upper())}
for _d in "0123456789":
    VK_NAMES[_d] = {ord(_d)}
    VK_NAMES[f"numpad{_d}"] = {0x60 + int(_d)}

ALIASES = {
    "control": "ctrl", "windows": "win", "super": "win", "cmd": "win", "meta": "win",
    "escape": "esc", "return": "enter", "caps lock": "capslock", "caps": "capslock",
    "left ctrl": "lctrl", "right ctrl": "rctrl", "left shift": "lshift", "right shift": "rshift",
    "left alt": "lalt", "right alt": "ralt", "altgr": "ralt", "left win": "lwin", "right win": "rwin",
    "del": "delete", "ins": "insert", "pgup": "pageup", "pgdn": "pagedown",
    "spacebar": "space", "space bar": "space", "option": "alt", "print": "printscreen",
    "apps": "menu", "`": "backtick", "-": "minus", "=": "equals", "[": "lbracket", "]": "rbracket",
    "\\": "backslash", ";": "semicolon", "'": "quote", ",": "comma", ".": "period", "/": "slash",
}

MODIFIER_VKS = {0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C, 0x10, 0x11, 0x12}
WIN_ALT_VKS = {0x5B, 0x5C, 0xA4, 0xA5}

# reverse map, generic modifier names win over the left/right variants
VK_TO_NAME: dict[int, str] = {}
for _name in ("ctrl", "shift", "alt", "win"):
    for _vk in VK_NAMES[_name]:
        VK_TO_NAME[_vk] = _name
for _name, _vks in VK_NAMES.items():
    for _vk in _vks:
        VK_TO_NAME.setdefault(_vk, _name)


def parse_combo(text: str) -> list[frozenset[int]]:
    """Parse "ctrl+win" into a list of VK groups, one group per key."""
    groups = []
    for part in text.lower().replace(" + ", "+").split("+"):
        part = part.strip()
        if not part:
            continue
        part = ALIASES.get(part, part)
        if part not in VK_NAMES:
            raise ValueError(f"Unknown key name: {part!r}")
        groups.append(frozenset(VK_NAMES[part]))
    if not groups:
        raise ValueError("Empty hotkey")
    return groups


def combo_from_vks(vks: set[int]) -> str:
    order = {"ctrl": 0, "shift": 1, "alt": 2, "win": 3}
    names = []
    for vk in vks:
        name = VK_TO_NAME.get(vk, f"vk{vk:02x}")
        if name not in names:
            names.append(name)
    names.sort(key=lambda n: (order.get(n, 10), n))
    return "+".join(names)


def pretty_combo(text: str, gesture: str = "hold") -> str:
    pretty = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "win": "Win", "esc": "Esc",
              "space": "Space", "capslock": "Caps Lock", "rctrl": "Right Ctrl", "lctrl": "Left Ctrl",
              "ralt": "Right Alt", "lalt": "Left Alt", "rshift": "Right Shift", "lshift": "Left Shift",
              "rwin": "Right Win", "lwin": "Left Win"}
    name = " + ".join(pretty.get(p, p.upper() if len(p) <= 3 else p.capitalize()) for p in text.split("+"))
    if gesture == "double_tap":
        return f"double-tap {name}"
    if gesture == "both":
        return f"{name} (hold, or double-tap)"
    return name


# --------------------------------------------------------------------------
# Low level hook
# --------------------------------------------------------------------------
class KeyboardHook:
    """WH_KEYBOARD_LL hook on a dedicated thread. handler(vk, is_down, injected) -> suppress?"""

    def __init__(self, handler: Callable[[int, bool, bool], bool]):
        self.handler = handler
        self._thread: Optional[threading.Thread] = None
        self._tid = 0
        self._hook = None
        self._proc = None
        self._ready = threading.Event()
        self.ok = False

    def start(self) -> bool:
        self._thread = threading.Thread(target=self._run, name="kbd-hook", daemon=True)
        self._thread.start()
        self._ready.wait(3)
        return self.ok

    def stop(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        self._proc = HOOKPROC(self._callback)  # keep a reference alive!
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            log.error("SetWindowsHookEx failed: %s", ctypes.get_last_error())
            self._ready.set()
            return
        self.ok = True
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        log.info("Keyboard hook removed")

    def _callback(self, nCode, wParam, lParam):
        if nCode >= 0:
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            injected = bool(kb.flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED))
            down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            try:
                if self.handler(int(kb.vkCode), down, injected):
                    return 1
            except Exception:
                log.exception("hotkey handler failed")
        return user32.CallNextHookEx(None, nCode, wParam, lParam)


# --------------------------------------------------------------------------
# Hotkey state machine
# --------------------------------------------------------------------------
class HotkeyManager:
    """Detects the configured key combination.

    Gestures:
      hold        the combo activates as soon as it is pressed
      double_tap  tap the combo, then press it again within double_tap_ms
      both        a press activates at once (hold to talk); a quick tap followed by a second
                  press within double_tap_ms reports kind "double_tap" (hands-free)
    A press while the app is recording always activates (the app stops on it).

    Callbacks (all called on the hook thread, keep them fast):
      on_activate(kind, t)   combo pressed; kind is "press" or "double_tap", t = time.monotonic()
      on_deactivate(t)       combo released (any key of it)
      on_cancel()            cancel key pressed while recording
      on_other_key(vk)       another key pressed while the combo is held
    """

    def __init__(self, on_activate, on_deactivate, on_cancel, on_other_key,
                 is_recording: Callable[[], bool]):
        self.on_activate = on_activate
        self.on_deactivate = on_deactivate
        self.on_cancel = on_cancel
        self.on_other_key = on_other_key
        self.is_recording = is_recording
        self._lock = threading.Lock()
        self.pressed: set[int] = set()
        self.combo: list[frozenset[int]] = []
        self.combo_vks: set[int] = set()
        self.cancel_vks: set[int] = set()
        self.active = False
        self.enabled = True
        self._capture_cb = None
        self._capture_keys: set[int] = set()
        # gesture: "hold" = the combo activates as soon as it is pressed;
        # "double_tap" = tap the combo, then press it again within double_tap_ms
        self.gesture = "hold"
        self.double_tap_ms = 400.0
        self.tap_max_hold_ms = 300.0
        self._combo_was_down = False
        self._tap_pending = False     # first press of a double tap is being held
        self._tap_dirty = False       # another key was pressed during/after the first tap
        self._tap_press_t = 0.0
        self._tap_armed = False       # first tap completed, waiting for the second press
        self._tap_release_t = 0.0
        self.hook = KeyboardHook(self._on_key)

    def configure(self, hotkey: str, cancel_key: str = "esc", gesture: str = "hold",
                  double_tap_ms: float = 400, tap_max_hold_ms: float = 300) -> Optional[str]:
        """Apply a new hotkey. Returns an error string or None."""
        try:
            combo = parse_combo(hotkey)
        except ValueError as e:
            return str(e)
        try:
            cancel = set().union(*parse_combo(cancel_key)) if cancel_key else set()
        except ValueError as e:
            return str(e)
        with self._lock:
            self.combo = combo
            self.combo_vks = set().union(*combo)
            self.cancel_vks = cancel
            self.gesture = gesture if gesture in ("double_tap", "both") else "hold"
            self.double_tap_ms = float(double_tap_ms or 400)
            self.tap_max_hold_ms = float(tap_max_hold_ms or 300)
            self.active = False
            self._combo_was_down = False
            self._tap_pending = self._tap_armed = self._tap_dirty = False
        log.info("Hotkey set to %s, gesture %s (cancel: %s)", hotkey, self.gesture, cancel_key)
        return None

    def start(self) -> bool:
        return self.hook.start()

    def stop(self):
        self.hook.stop()

    def begin_capture(self, callback: Callable[[str], None]):
        """Swallow all keys until a combination has been pressed and released; then callback(combo)."""
        with self._lock:
            self._capture_cb = callback
            self._capture_keys = set()

    def cancel_capture(self):
        with self._lock:
            self._capture_cb = None
            self._capture_keys = set()

    # ------------------------------------------------------------------
    def _on_key(self, vk: int, down: bool, injected: bool) -> bool:
        if injected:
            return False
        fire = None
        suppress = False
        dummy = False
        with self._lock:
            if down:
                self.pressed.add(vk)
            else:
                self.pressed.discard(vk)

            if self._capture_cb is not None:
                if down:
                    self._capture_keys.add(vk)
                elif self._capture_keys and not (self.pressed & self._capture_keys):
                    cb, keys = self._capture_cb, set(self._capture_keys)
                    self._capture_cb, self._capture_keys = None, set()
                    threading.Thread(target=cb, args=(combo_from_vks(keys),), daemon=True).start()
                return True

            if not self.enabled or not self.combo:
                return False

            if down and vk in self.cancel_vks and self.is_recording():
                fire = ("cancel",)
                suppress = True
            else:
                now = time.monotonic()
                combo_down = all(any(v in self.pressed for v in group) for group in self.combo)
                new_press = combo_down and not self._combo_was_down
                released = self._combo_was_down and not combo_down
                self._combo_was_down = combo_down
                if new_press and (self.combo_vks & WIN_ALT_VKS):
                    dummy = True   # stops the Start menu / Alt menu from reacting to the tap
                if new_press and not self.active:
                    second_tap = (self._tap_armed and not self._tap_dirty
                                  and (now - self._tap_release_t) * 1000 <= self.double_tap_ms)
                    if self.is_recording():
                        # already dictating (hands-free): a single press is enough to stop
                        self._tap_armed = self._tap_pending = False
                        self.active = True
                        fire = ("activate", "press", now)
                        suppress = vk not in MODIFIER_VKS
                    elif self.gesture == "double_tap":
                        if second_tap:
                            self._tap_armed = False
                            self.active = True
                            fire = ("activate", "double_tap", now)
                            suppress = vk not in MODIFIER_VKS
                        else:  # first tap of a (possible) double tap: nothing happens yet
                            self._tap_pending = True
                            self._tap_dirty = False
                            self._tap_armed = False
                            self._tap_press_t = now
                    elif self.gesture == "both":
                        self.active = True
                        if second_tap:
                            self._tap_armed = self._tap_pending = False
                            fire = ("activate", "double_tap", now)
                        else:
                            # activates right away (hold to talk); if it turns out to be a quick tap,
                            # the app discards it and a second press within the window is a double tap
                            self._tap_pending = True
                            self._tap_dirty = False
                            self._tap_armed = False
                            self._tap_press_t = now
                            fire = ("activate", "press", now)
                        suppress = vk not in MODIFIER_VKS
                    else:
                        self.active = True
                        fire = ("activate", "press", now)
                        suppress = vk not in MODIFIER_VKS
                elif self.active:
                    if released:
                        self.active = False
                        fire = ("deactivate", now)
                        suppress = (vk in self.combo_vks) and (vk not in MODIFIER_VKS)
                        if self._tap_pending:      # gesture "both": was that press a quick tap?
                            self._tap_pending = False
                            if not self._tap_dirty and (now - self._tap_press_t) * 1000 <= self.tap_max_hold_ms:
                                self._tap_armed = True
                                self._tap_release_t = now
                    elif down and vk not in self.combo_vks:
                        fire = ("other", vk)
                        self._tap_dirty = True
                    elif down and vk in self.combo_vks and vk not in MODIFIER_VKS:
                        suppress = True  # key auto-repeat while holding
                elif self._tap_pending and released:
                    # gesture "double_tap": first tap finished; only a short, clean press counts
                    self._tap_pending = False
                    if not self._tap_dirty and (now - self._tap_press_t) * 1000 <= self.tap_max_hold_ms:
                        self._tap_armed = True
                        self._tap_release_t = now
                if down and vk not in self.combo_vks and (self._tap_pending or self._tap_armed):
                    # any other key breaks the double-tap gesture (Ctrl+C, Ctrl+V, ...)
                    self._tap_dirty = True
                    self._tap_armed = False
        if dummy:
            try:
                from .inject import send_dummy_key
                send_dummy_key()
            except Exception:
                log.exception("dummy key failed")
        if fire:
            try:
                if fire[0] == "activate":
                    self.on_activate(fire[1], fire[2])
                elif fire[0] == "deactivate":
                    self.on_deactivate(fire[1])
                elif fire[0] == "cancel":
                    self.on_cancel()
                elif fire[0] == "other":
                    self.on_other_key(fire[1])
            except Exception:
                log.exception("hotkey callback failed")
        return suppress
