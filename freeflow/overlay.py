"""The FreeFlow "island": a small pill at the bottom of the screen that expands while dictating.

Modes
  island  always visible as a tiny dark pill; animates open to show the level bars / status and
          shrinks back afterwards.  Left-click starts or stops hands-free dictation, right-click
          opens Settings.
  popup   only visible while dictating (click-through).

The window never takes keyboard focus (WS_EX_NOACTIVATE).  It stays mapped for its whole life
and is shown/hidden by toggling its alpha: tkinter's deiconify() would activate it and steal the
focus from the app the user is dictating into.  All methods must be called on the Tk main thread.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import math
import time
import tkinter as tk
import tkinter.font as tkfont
from typing import Callable, Optional

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = ctypes.c_void_p(-1 & 0xFFFFFFFFFFFFFFFF)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
SW_SHOWNOACTIVATE = 4
user32.IsIconic.argtypes = [ctypes.c_void_p]
user32.IsIconic.restype = ctypes.c_bool
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.IsWindowVisible.restype = ctypes.c_bool
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.ShowWindow.restype = ctypes.c_bool
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = ctypes.c_bool
user32.GetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.DWORD), ctypes.POINTER(ctypes.c_ubyte),
                                              ctypes.POINTER(wt.DWORD)]
user32.GetLayeredWindowAttributes.restype = ctypes.c_bool
GUARD_MS = 1000           # how often the indicator checks that nothing hid or moved it
SPI_GETWORKAREA = 0x0030
user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = ctypes.c_bool
user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.GetParent.argtypes = [ctypes.c_void_p]
user32.GetParent.restype = ctypes.c_void_p

KEY = "#010203"          # transparent colour key
BG = "#121216"
BORDER = "#2a2a33"
BORDER_HOVER = "#4a4a58"
BAR = "#ffffff"
BAR_HF = "#8f7dff"       # hands-free mode
TEXT = "#ececf4"
DIM = "#9b9bad"
OK = "#4fd18b"
ERR = "#ff6b6b"


def dpi_scale() -> float:
    try:
        return max(1.0, user32.GetDpiForSystem() / 96.0)
    except Exception:
        return 1.0


MONITOR_DEFAULTTONEAREST = 2


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.MonitorFromWindow.restype = ctypes.c_void_p
user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
user32.MonitorFromPoint.restype = ctypes.c_void_p
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
DESKTOP_CLASSES = {"Progman", "WorkerW"}       # the desktop spans every monitor: use the mouse instead


def monitor_of(hwnd: int = 0):
    """The monitor showing this window; the one under the mouse when there is no (usable) window."""
    mon = None
    try:
        if hwnd and user32.IsWindow(hwnd):
            buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, buf, 64)
            if buf.value not in DESKTOP_CLASSES:
                mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not mon:
            pt = wt.POINT()
            if user32.GetCursorPos(ctypes.byref(pt)):
                mon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
    except Exception:
        mon = None
    return mon


def work_area(hwnd: int = 0) -> Optional[tuple[int, int, int, int]]:
    """Work area (screen minus taskbar) of the monitor to use for the indicator: the monitor of the
    given window, else the monitor with the mouse, else the primary one."""
    mon = monitor_of(hwnd)
    if mon:
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            return mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom
    r = wt.RECT()
    if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
        return r.left, r.top, r.right, r.bottom
    return None


def _capsule_points(x1, y1, x2, y2, n: int = 18) -> list[float]:
    """Outline of a capsule (stadium): two semicircles joined by straight edges, as one polygon."""
    r = (y2 - y1) / 2
    cx_left, cx_right, cy = x1 + r, max(x1 + r, x2 - r), (y1 + y2) / 2
    pts: list[float] = []
    for k in range(n + 1):                      # right end, top to bottom
        a = -math.pi / 2 + math.pi * k / n
        pts += [cx_right + r * math.cos(a), cy + r * math.sin(a)]
    for k in range(n + 1):                      # left end, bottom to top
        a = math.pi / 2 + math.pi * k / n
        pts += [cx_left + r * math.cos(a), cy + r * math.sin(a)]
    return pts


def capsule(canvas: tk.Canvas, x1, y1, x2, y2, fill, outline):
    canvas.create_polygon(_capsule_points(x1, y1, x2 - 1, y2 - 1), fill=fill, outline=outline, width=1)


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


class Overlay:
    BASE_W, BASE_H = 128, 30          # expanded
    IDLE_W, IDLE_H = 40, 12           # island at rest (a round-ended capsule)
    N_BARS = 7
    WEIGHTS = [0.5, 0.8, 1.0, 0.85, 1.0, 0.8, 0.5]
    EXPAND_MS, COLLAPSE_MS = 150, 220

    def __init__(self, root: tk.Tk, get_level: Callable[[], float], position: str = "bottom",
                 mode: str = "island", on_click: Optional[Callable[[], None]] = None,
                 on_right_click: Optional[Callable[[], None]] = None):
        self.root = root
        self.get_level = get_level
        self.position = position
        self.mode = mode if mode in ("island", "popup") else "island"
        self.on_click = on_click
        self.on_right_click = on_right_click
        self.s = dpi_scale()
        self.state = "hidden"
        self.message = ""
        self.follow_hwnd = 0          # window whose monitor the indicator sits on (0 = mouse)
        self._monitor = None
        self._hover = False
        self._job: Optional[str] = None
        self._hide_job: Optional[str] = None
        self._anim: Optional[dict] = None
        self._levels = [0.0] * self.N_BARS
        self._t0 = time.time()
        self._visible = False

        idle_w, idle_h = self._idle_size()
        self._cur_w, self._cur_h = float(idle_w), float(idle_h)
        self._target = (idle_w, idle_h)

        # Tk activates a toplevel the first time it is mapped; that single
        # activation is undone right after creation (see _give_focus_back).
        previous_fg = user32.GetForegroundWindow()
        self.win = tk.Toplevel(root)
        self.win.title("FreeFlow Indicator")
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=KEY)
        try:
            self.win.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        self.win.attributes("-alpha", 0.0)
        self.font = tkfont.Font(family="Segoe UI", size=9)
        self.canvas = tk.Canvas(self.win, width=idle_w, height=idle_h, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._clicked)
        self.canvas.bind("<Button-3>", self._right_clicked)
        self.canvas.bind("<Enter>", lambda e: self._set_hover(True))
        self.canvas.bind("<Leave>", lambda e: self._set_hover(False))
        self._place(idle_w, idle_h)
        self.win.update_idletasks()
        self._apply_exstyle()
        self._give_focus_back(previous_fg)
        self.root.after(1, lambda: self._give_focus_back(previous_fg))
        self.repairs = 0
        self._last_top = 0.0
        self.win.after(GUARD_MS, self._guard)
        if self.mode == "island":
            self.root.after(50, self.show_idle)

    # ------------------------------------------------------------------
    # window plumbing
    # ------------------------------------------------------------------
    def _hwnd(self) -> int:
        h = user32.GetParent(self.win.winfo_id())
        return h or self.win.winfo_id()

    def _apply_exstyle(self):
        try:
            hwnd = self._hwnd()
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style |= WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
            if self.mode == "popup":
                style |= WS_EX_TRANSPARENT          # click-through
            else:
                style &= ~WS_EX_TRANSPARENT         # the island is clickable
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception as e:
            log.warning("overlay ex-style failed: %s", e)

    def _give_focus_back(self, previous_fg):
        """Safety net: if the indicator ever became the foreground window, hand focus back."""
        try:
            fg = user32.GetForegroundWindow()
            if fg and fg == self._hwnd() and previous_fg and previous_fg != fg:
                user32.SetForegroundWindow(previous_fg)
                log.debug("overlay had taken focus; restored previous window")
        except Exception:
            pass

    def follow(self, hwnd: int):
        """Show the indicator on the monitor of this window (0: the monitor with the mouse).  Called
        while idle for the window in front and, during a dictation, for the window that gets the text."""
        self.follow_hwnd = int(hwnd or 0)
        mon = monitor_of(self.follow_hwnd)
        if mon != self._monitor:
            self._monitor = mon
            self._place(max(2, int(round(self._cur_w))), max(2, int(round(self._cur_h))))

    def _target_xy(self, w: int, h: int) -> tuple[int, int]:
        """Where a w x h indicator belongs on the monitor it follows."""
        wa = work_area(self.follow_hwnd)
        self._monitor = monitor_of(self.follow_hwnd)
        if wa:
            left, top, right, bottom = wa
        else:
            left, top, right, bottom = 0, 0, self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        margin = round(14 * self.s)
        x = left + (right - left - w) // 2
        y = top + margin if self.position == "top" else bottom - h - margin
        return x, y

    def _place(self, w: int, h: int):
        x, y = self._target_xy(w, h)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    def _guard(self):
        """Once a second: undo whatever hid or moved the indicator - "Show desktop" minimising it, a
        display change moving it, another window taking the top spot, a lost alpha - and log it."""
        try:
            self.win.after(GUARD_MS, self._guard)
        except tk.TclError:
            return                                      # window gone (shutdown)
        if not self._visible or self._anim is not None:
            return                                      # hidden on purpose, or moving right now
        try:
            hwnd = self._hwnd()
            fixed = []
            if user32.IsIconic(hwnd):
                fixed.append("minimised")
            elif not user32.IsWindowVisible(hwnd):
                fixed.append("hidden")
            if not user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST:
                fixed.append("not on top")
            key, alpha, flags = wt.DWORD(), ctypes.c_ubyte(), wt.DWORD()
            if user32.GetLayeredWindowAttributes(hwnd, ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags)) \
                    and flags.value & 0x02 and alpha.value == 0:
                fixed.append("transparent")
            w, h = max(2, int(round(self._cur_w))), max(2, int(round(self._cur_h)))
            x, y = self._target_xy(w, h)
            r = wt.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(r)) and not user32.IsIconic(hwnd) \
                    and (abs(r.left - x) > 2 or abs(r.top - y) > 2):
                fixed.append(f"moved to {r.left},{r.top}")
            now = time.time()
            if fixed:
                if "minimised" in fixed or "hidden" in fixed:
                    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                if "transparent" in fixed:
                    self.win.attributes("-alpha", 1.0)
                self.win.geometry(f"{w}x{h}+{x}+{y}")
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                self._last_top = now
                self.repairs += 1
                log.info("Indicator restored (%s)", ", ".join(fixed))
            elif now - self._last_top > 5:
                # quietly keep the top spot (other windows can be raised above a topmost window)
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                self._last_top = now
        except Exception as e:
            log.debug("indicator guard failed: %s", e)

    def _set_size(self, w: float, h: float):
        self._cur_w, self._cur_h = float(w), float(h)
        wi, hi = max(2, int(round(w))), max(2, int(round(h)))
        self.canvas.configure(width=wi, height=hi)
        self._place(wi, hi)

    def _set_visible(self, visible: bool):
        if visible == self._visible:
            return
        self._visible = visible
        try:
            self.win.attributes("-alpha", 1.0 if visible else 0.0)
            if visible:
                user32.SetWindowPos(self._hwnd(), HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception as e:
            log.debug("overlay visibility failed: %s", e)

    def set_mode(self, mode: str):
        mode = mode if mode in ("island", "popup") else "island"
        if mode == self.mode:
            return
        self.mode = mode
        self._apply_exstyle()
        if self.state in ("hidden", "idle"):
            self.show_idle() if mode == "island" else self.hide()

    # ------------------------------------------------------------------
    # sizes and animation
    # ------------------------------------------------------------------
    def _idle_size(self) -> tuple[int, int]:
        return round(self.IDLE_W * self.s), round(self.IDLE_H * self.s)

    def _text_for(self, state: str, message: str) -> str:
        if state == "done":
            return ""
        if state == "transcribing" and message in ("", "Transcribing…"):
            return ""
        return message

    def _size_for(self, state: str, message: str) -> tuple[int, int]:
        if state in ("idle", "hidden"):
            return self._idle_size()
        text = self._text_for(state, message)
        w = round(self.BASE_W * self.s)
        if text:
            if state in ("listening", "handsfree"):
                w = w + self.font.measure(text) + round(14 * self.s)   # bars keep their space, text on the right
            else:
                w = max(w, self.font.measure(text) + round(46 * self.s))
            w = min(w, round(520 * self.s))
        return w, round(self.BASE_H * self.s)

    def _animate_to(self, w: int, h: int, duration_ms: int, then: Optional[Callable[[], None]] = None):
        self._target = (w, h)
        if abs(self._cur_w - w) < 1 and abs(self._cur_h - h) < 1:
            self._set_size(w, h)
            self._anim = None
            if then:
                then()
            return
        self._anim = {"from": (self._cur_w, self._cur_h), "to": (w, h), "t0": time.time(),
                      "dur": max(1, duration_ms) / 1000.0, "then": then}
        if self._job is None:
            self._tick()

    def _step_animation(self) -> bool:
        a = self._anim
        if not a:
            return False
        t = _ease((time.time() - a["t0"]) / a["dur"])
        w = a["from"][0] + (a["to"][0] - a["from"][0]) * t
        h = a["from"][1] + (a["to"][1] - a["from"][1]) * t
        self._set_size(w, h)
        if t >= 1.0:
            self._anim = None
            if a["then"]:
                a["then"]()
            return False
        return True

    def _expanded(self) -> bool:
        return self._anim is None and self.state not in ("idle", "hidden")

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def show_idle(self):
        """Island mode: the small resting pill."""
        if self.mode != "island":
            return
        self.state = "idle"
        self.message = ""
        if self._hide_job:
            self.win.after_cancel(self._hide_job)
            self._hide_job = None
        w, h = self._idle_size()
        self._animate_to(w, h, self.COLLAPSE_MS)
        self._set_visible(True)
        if self._job is None:
            self._tick()

    def show(self, state: str, message: str = "", auto_hide_ms: Optional[int] = None):
        previous_fg = user32.GetForegroundWindow()
        self.state, self.message = state, message
        if self._hide_job:
            self.win.after_cancel(self._hide_job)
            self._hide_job = None
        w, h = self._size_for(state, message)
        if not self._visible:
            self._set_size(w, h)          # popping up: no animation from nothing
            self._anim = None
            self._target = (w, h)
        else:
            self._animate_to(w, h, self.EXPAND_MS)
        self._set_visible(True)
        self._draw()
        self._give_focus_back(previous_fg)
        self.root.after(1, lambda: self._give_focus_back(previous_fg))
        if self._job is None:
            self._tick()
        if auto_hide_ms:
            self._hide_job = self.win.after(auto_hide_ms, self.hide)

    def hide(self):
        """Back to the resting pill (island) or invisible (popup)."""
        if self._hide_job:
            self.win.after_cancel(self._hide_job)
            self._hide_job = None
        if self.mode == "island":
            self.show_idle()
            return
        self.state = "hidden"
        self._anim = None
        self._set_visible(False)

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def _clicked(self, _event=None):
        if self.on_click:
            try:
                self.on_click()
            except Exception:
                log.exception("island click handler failed")

    def _right_clicked(self, _event=None):
        if self.on_right_click:
            try:
                self.on_right_click()
            except Exception:
                log.exception("island right-click handler failed")

    def _set_hover(self, flag: bool):
        self._hover = flag
        if self.state == "idle":
            self._draw()

    # ------------------------------------------------------------------
    # drawing
    # ------------------------------------------------------------------
    def _tick(self):
        animating = self._step_animation()
        try:
            self._draw()
        except tk.TclError:
            self._job = None
            return
        if not animating and self.state in ("idle", "hidden"):
            self._job = None            # nothing moves at rest: stop the 30 fps loop
            return
        self._job = self.win.after(33, self._tick)

    def _draw(self):
        c = self.canvas
        c.delete("all")
        s = self.s
        W, H = max(2, int(round(self._cur_w))), max(2, int(round(self._cur_h)))
        cy = H / 2
        border = BORDER_HOVER if (self._hover and self.state == "idle") else BORDER
        capsule(c, 0, 0, W, H, BG, border)
        if self.state in ("idle", "hidden") or not self._expanded():
            return                       # resting pill, or still growing/shrinking: no content yet
        t = time.time() - self._t0
        state = self.state
        text = self._text_for(state, self.message)
        if state in ("listening", "handsfree"):
            color = BAR_HF if state == "handsfree" else BAR
            left = round(22 * s)
            right = W - round(22 * s)
            if text:
                tw = self.font.measure(text)
                right = W - round(20 * s) - tw - round(8 * s)
                c.create_text(W - round(16 * s), cy, text=text, fill=DIM, font=self.font, anchor="e")
            level = max(0.0, min(1.0, self.get_level()))
            gap = max(4 * s, (right - left) / (self.N_BARS - 1))
            width = max(2, round(2.6 * s))
            h_min, h_max = 3 * s, H - 9 * s
            for i in range(self.N_BARS):
                # each bar follows the microphone level with its own weight and a little life of its own
                target = level * self.WEIGHTS[i] * (0.75 + 0.25 * math.sin(t * 9 + i * 1.3))
                if target > self._levels[i]:
                    self._levels[i] += (target - self._levels[i]) * 0.7     # snap up quickly
                else:
                    self._levels[i] += (target - self._levels[i]) * 0.3     # fall back smoothly
                h = h_min + (h_max - h_min) * self._levels[i]
                x = left + i * gap
                c.create_line(x, cy - h / 2, x, cy + h / 2, fill=color, width=width, capstyle=tk.ROUND)
        elif state in ("transcribing", "loading"):
            x0 = W / 2 - 12 * s if not text else 16 * s
            for i in range(3):
                phase = (t * 2.6 - i * 0.3) % 1.0
                size = (1.8 + 1.6 * max(0.0, math.sin(phase * math.pi))) * s
                x = x0 + i * 12 * s
                c.create_oval(x - size, cy - size, x + size, cy + size, fill=BAR, outline="")
            if text:
                c.create_text(x0 + 34 * s, cy, text=text, fill=TEXT, font=self.font, anchor="w")
        elif state == "done":
            c.create_text(W / 2, cy, text="✓", fill=OK, font=("Segoe UI", 12, "bold"))
        elif state == "error":
            c.create_text(16 * s, cy, text="!", fill=ERR, font=("Segoe UI", 12, "bold"))
            c.create_text(30 * s, cy, text=text, fill=ERR, font=self.font, anchor="w")
        else:  # info
            c.create_text(W / 2, cy, text=text, fill=TEXT, font=self.font)
