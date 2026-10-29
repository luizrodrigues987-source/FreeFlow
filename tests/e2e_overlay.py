"""The island indicator follows the window in front across monitors, and its guard repairs a moved or
minimised pill in one step (it once "restored" the pill every second for hours without moving it: Tk's
pending move was overwritten by the SetWindowPos that keeps it on top).  Creates a test pill and two
small windows in this process; needs two monitors for the cross-monitor part."""
import ctypes
import ctypes.wintypes as wt
import sys
import time
import tkinter as tk

from _util import ROOT  # noqa: F401
ctypes.windll.shcore.SetProcessDpiAwareness(1)
from freeflow import overlay as ov  # noqa: E402

u = ctypes.windll.user32
u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
monitors = []
MONPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
u.EnumDisplayMonitors(None, None, MONPROC(lambda hm, hdc, rc, _: monitors.append(
    (rc.contents.left, rc.contents.top, rc.contents.right, rc.contents.bottom)) or True), 0)
if len(monitors) < 2:
    print("SKIPPED: needs two monitors")
    sys.exit(0)
(l1, t1, r1, b1), (l2, t2, r2, b2) = monitors[:2]

root = tk.Tk()
root.withdraw()
o = ov.Overlay(root, lambda: 0.0, "bottom", "island")
w1 = tk.Toplevel(root)
w1.geometry(f"300x200+{l1 + 200}+{t1 + 200}")
w2 = tk.Toplevel(root)
w2.geometry(f"300x200+{l2 + 200}+{t2 + 200}")
root.update()
h1 = u.GetParent(w1.winfo_id()) or w1.winfo_id()
h2 = u.GetParent(w2.winfo_id()) or w2.winfo_id()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def pos():
    r = wt.RECT()
    u.GetWindowRect(o._hwnd(), ctypes.byref(r))
    return r.left, r.top


def on(mon, p):
    return mon[0] <= p[0] < mon[2]


results = []
pump(1.2)
o.follow(h2); pump(1.2); results.append(("follows monitor 2", on(monitors[1], pos())))
o.follow(h1); pump(1.2); results.append(("follows monitor 1", on(monitors[0], pos())))
for _ in range(3):                                   # Windows moves it away again and again (display changes)
    u.SetWindowPos(o._hwnd(), None, l2 + 1000, b2 - 80, 0, 0, 0x0001 | 0x0004 | 0x0010)
    pump(1.3)
results.append(("moved away 3x -> back each time", on(monitors[0], pos())))
o.follow(h2)
u.SetWindowPos(o._hwnd(), None, l1 + 1000, b1 - 80, 0, 0, 0x0001 | 0x0004 | 0x0010)
pump(1.3); results.append(("moved the other way -> back", on(monitors[1], pos())))
u.ShowWindow(o._hwnd(), 6); pump(1.3); results.append(("minimised -> restored", not u.IsIconic(o._hwnd())))
results.append(("repeats logged once", o._repair_log.get("moved to", (0, 0))[1] >= 2))
o.set_mode("popup"); o.hide(); pump(0.3)
o.follow(h1); o.show("listening"); pump(0.1)
results.append(("popup appears in place at once", on(monitors[0], pos())))
root.destroy()
bad = [name for name, ok in results if not ok]
for name, ok in results:
    print("  %-4s %s" % ("ok" if ok else "FAIL", name))
print("OVERLAY OK" if not bad else "OVERLAY FAILED: %s" % bad)
sys.exit(1 if bad else 0)
