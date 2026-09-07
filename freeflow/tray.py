"""System tray icon and menu (pystray)."""
from __future__ import annotations

import logging
import os
import threading

import pystray
from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

COLORS = {"idle": "#7B61FF", "recording": "#FF4D4D", "processing": "#F5A623", "loading": "#8A8A9A", "disabled": "#55555F"}


def make_icon_image(state: str = "idle", size: int = 64) -> Image.Image:
    color = COLORS.get(state, COLORS["idle"])
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, size - 3, size - 3), radius=size // 4, fill=color)
    # microphone glyph
    cx = size / 2
    d.rounded_rectangle((cx - 8, 12, cx + 8, 36), radius=8, fill="white")
    d.arc((cx - 15, 20, cx + 15, 46), start=0, end=180, fill="white", width=4)
    d.line((cx, 46, cx, 54), fill="white", width=4)
    d.line((cx - 9, 54, cx + 9, 54), fill="white", width=4)
    if state == "recording":
        d.ellipse((size - 22, size - 22, size - 6, size - 6), fill="#FFFFFF")
        d.ellipse((size - 19, size - 19, size - 9, size - 9), fill="#FF4D4D")
    return img


def ensure_icon_file(path: str):
    if os.path.exists(path):
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        make_icon_image("idle", 256).save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    except Exception as e:
        log.debug("icon file not written: %s", e)


class Tray:
    def __init__(self, app):
        self.app = app
        self.state = "loading"
        self.icon = pystray.Icon("FreeFlow", make_icon_image(self.state), "FreeFlow", menu=self._menu())
        self._thread = None

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem(lambda item: self.app.status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Dictation enabled", self._toggle_enabled, checked=lambda item: self.app.enabled),
            pystray.MenuItem("Start / stop hands-free dictation", self._toggle_handsfree),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings…", self._settings, default=True),
            pystray.MenuItem("History…", self._history),
            pystray.MenuItem("Check for updates…", self._check_updates),
            pystray.MenuItem("Open data folder", self._open_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit FreeFlow", self._quit),
        )

    # menu handlers run on the pystray thread -> hand work to the app
    def _toggle_enabled(self, icon, item):
        self.app.toggle_enabled()

    def _toggle_handsfree(self, icon, item):
        self.app.request("toggle_handsfree")

    def _settings(self, icon, item):
        self.app.ui(self.app.open_settings)

    def _history(self, icon, item):
        self.app.ui(self.app.open_settings, "History")

    def _open_folder(self, icon, item):
        self.app.open_data_folder()

    def _check_updates(self, icon, item):
        self.app.check_for_updates(auto=False)

    def _quit(self, icon, item):
        self.app.quit()

    def run(self):
        self._thread = threading.Thread(target=self.icon.run, name="tray", daemon=True)
        self._thread.start()

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass

    def set_state(self, state: str):
        if state == self.state:
            return
        self.state = state
        try:
            self.icon.icon = make_icon_image(state)
            self.icon.title = f"FreeFlow - {self.app.status_text()}"
        except Exception as e:
            log.debug("tray update failed: %s", e)

    def refresh_title(self):
        try:
            self.icon.title = f"FreeFlow - {self.app.status_text()}"[:127]
        except Exception:
            pass

    def notify(self, message: str, title: str = "FreeFlow"):
        if not self.app.cfg.get("notifications", True):
            return
        try:
            self.icon.notify(message, title)
        except Exception as e:
            log.debug("notify failed: %s", e)
