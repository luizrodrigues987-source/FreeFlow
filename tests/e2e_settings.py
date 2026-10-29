"""The Settings window on this screen and on a simulated 1080p laptop at 150% scaling: it fits the screen,
tall pages scroll, Save stays visible, and the per-app clean-up styles are saved.  A test FreeFlow with its
own config (hotkey Ctrl+Alt+Shift+F9, no muting); a second tray icon shows up for a few seconds."""
import json
import os
import sys
import tempfile
import threading
import time

from _util import ROOT  # noqa: F401

TMP = tempfile.mkdtemp(prefix="ff-settings-")
CFG = os.path.join(TMP, "config.json")
json.dump({"hotkey": "ctrl+alt+shift+f9", "sounds": False, "overlay": False, "notifications": False,
           "history_enabled": False, "mute_output": False, "mute_mic_mode": "off", "polish": "off",
           "first_run_done": True, "auto_update": False, "autostart": False, "window_context": False},
          open(CFG, "w"))
os.environ["FREEFLOW_CONFIG"] = CFG                    # before anything from freeflow is imported

from tkinter import messagebox  # noqa: E402
from freeflow import app as appmod, overlay, transcribe  # noqa: E402

transcribe.LocalWhisperEngine.load = lambda self: self.ready.set()   # no speech model needed here

messagebox.showerror = messagebox.showwarning = lambda *a, **k: None
appmod.acquire_single_instance = lambda: True
appmod.DATA_DIR = TMP
box = {}


def check(w, area):
    w.update_idletasks()
    left, top, right, bottom = area
    res = {"size": (w.winfo_width(), w.winfo_height()), "fits": w.winfo_width() <= right - left and
           w.winfo_height() <= bottom - top}
    save_btn = [c for c in w.winfo_children()[0].winfo_children() if str(c.cget("text")) == "Save & Close"]
    res["save_visible"] = bool(save_btn) and save_btn[0].winfo_rooty() + save_btn[0].winfo_height() <= \
        w.winfo_rooty() + w.winfo_height()
    w.select_tab("Formatting")
    w.update()
    inner = w.tabs["Formatting"]
    canvas = inner._scroll_canvas
    res["formatting_taller_than_window"] = inner.winfo_reqheight() > canvas.winfo_height()
    res["scrolls"] = canvas.yview() != (0.0, 1.0)
    return res


def driver():
    a = box["app"]
    time.sleep(2.0)
    if a.cfg.get("hotkey") != "ctrl+alt+shift+f9":
        os._exit(3)
    try:
        # 1) this PC, as it is
        a.ui(a.open_settings, "General")
        time.sleep(2.0)
        w = a.settings_win
        box["normal"] = check(w, overlay.work_area(0))
        # per-app styles: defaults shown, a change is saved
        box["style_defaults"] = {k: v.get() for k, v in w._style_vars.items()}
        w._style_vars["chat"].set("Formal")
        w._style_vars["ai"].set("Same as above")
        box["saved"] = w.save()
        box["per_app"] = dict(a.cfg.get("polish_style_per_app"))
        a.ui(w.close)
        time.sleep(1.0)
        # 2) a 1080p laptop at 150%: fonts 1.5x, work area 1920 x 1032
        laptop = (0, 0, 1920, 1032)
        overlay.work_area = lambda hwnd=0: laptop
        a.ui(lambda: a.root.tk.call("tk", "scaling", 2.0))
        time.sleep(0.5)
        a.ui(a.open_settings, "General")
        time.sleep(2.5)
        box["laptop"] = check(a.settings_win, laptop)
        a.ui(a.settings_win.close)
        time.sleep(0.5)
    finally:
        a.ui(a.quit)


app = appmod.App()
box["app"] = app
threading.Thread(target=driver, daemon=True).start()
app.run()
for k in ("normal", "laptop", "style_defaults", "per_app"):
    print(f"{k}: {box.get(k)}")
ok = (box["normal"]["fits"] and box["normal"]["save_visible"] and box["laptop"]["fits"] and box["laptop"]["save_visible"]
      and box["laptop"]["formatting_taller_than_window"] and box["laptop"]["scrolls"]
      and box["style_defaults"] == {"chat": "Casual", "email": "Formal", "ai": "Light touch (punctuation only)",
                                    "document": "Same as above"}
      and box["saved"] and box["per_app"] == {"chat": "formal", "email": "formal", "ai": "", "document": ""})
print("SETTINGS OK" if ok else "SETTINGS FAILED")
sys.exit(0 if ok else 1)
