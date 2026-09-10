"""FreeFlow application: wires hotkeys, recording, muting, transcription, injection and the UI.

Threads
  main        tkinter (overlay + settings window)      <- self.ui(fn, ...) queue
  kbd-hook    low-level keyboard hook                  -> self._ctrl_q
  tray        pystray                                  -> self._ctrl_q / self.ui
  controller  recording state machine + audio muting   -> self._job_q
  worker      transcription -> clean-up -> injection
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import APP_NAME, APP_VERSION
from . import audioctl, autostart, focus, sounds, updater
from .config import DATA_DIR, LAST_RECORDING_PATH, Config
from .history import History
from .hotkeys import HotkeyManager, pretty_combo
from . import gamevocab
from .inject import inject_game_chat, inject_text, press_enter, send_backspaces, send_undo, wait_modifiers_released
from .learning import Learner, sentence_similarity, split_correction, words as text_words
from .editwatch import EditWatcher
from .postprocess import capitalize_first
from .llm import LocalLLM, PolishError, polish, resolve_mode, too_short_to_polish
from .postprocess import clean_transcript, finalize_for_injection, normalize_spaces
from .recorder import Recorder, save_wav
from .transcribe import EngineError, create_engine, engine_signature
from .util import acquire_single_instance, foreground_window_info, message_box, setup_logging, window_info

log = logging.getLogger(__name__)
# a hotkey press older than this when it reaches the controller is not acted on (we were stuck)
STALE_PRESS_S = 2.0


@dataclass
class Job:
    audio: np.ndarray
    duration: float
    rms: float
    app_exe: str
    app_title: str
    handsfree: bool
    target: Optional[dict] = None


class App:
    def __init__(self):
        self.cfg = Config()
        self.history = History(max_entries=int(self.cfg.get("history_max") or 1000))
        self.recorder = Recorder()
        self.output_muter = audioctl.OutputMuter()
        self.mic_muter = audioctl.MicSessionMuter()
        self.local_llm = LocalLLM(self.cfg)
        self._last_polish_warning = 0.0
        self.state = "idle"            # idle | recording
        self.handsfree = False
        self.enabled = True
        self.pending = 0               # transcription jobs in flight
        self._rec_started = 0.0
        self._target: dict = {"hwnd": 0, "exe": "", "title": "", "redirected": False, "editable": None}
        self._last_target: Optional[dict] = None   # last window we inserted text into (a real text box)
        self._last_foreign_fg = 0                   # last foreground window that was not ours
        self._ctrl_q: "queue.Queue[tuple]" = queue.Queue()
        self._job_q: "queue.Queue[Job]" = queue.Queue()
        self._ui_q: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()
        self.engine = None
        self._engine_sig = None
        self._provisional = False      # recording started on a press that may still turn out to be a tap
        self._press_t = 0.0            # time.monotonic() of the key press that started the recording
        self._unmute_timer: Optional[threading.Timer] = None
        self.update_status = ""
        self._just_updated = 0
        self.hotkeys = HotkeyManager(
            on_activate=lambda kind, t: self._ctrl_q.put(("activate", kind, t)),
            on_deactivate=lambda t: self._ctrl_q.put(("deactivate", t)),
            on_cancel=lambda: self._ctrl_q.put(("cancel",)),
            on_other_key=lambda vk: self._ctrl_q.put(("other", vk)),
            is_recording=lambda: self.state == "recording" and not self._provisional,
        )
        self.root: Optional[tk.Tk] = None
        self.overlay = None
        self._followed = 0            # window whose monitor shows the indicator
        self.learner = Learner(DATA_DIR)
        self._last_dictation: Optional[dict] = None   # what was inserted last (for corrections)
        self.editwatch = EditWatcher(self._on_typed_edit, lambda: self.hotkeys.presses)
        self.hotkeys.on_key_down = self._key_observed
        self._last_key_t = 0.0            # monotonic time of the last real key press (any app)
        self._last_dictation_t = 0.0      # monotonic time of the last recording start / stop
        self._restart_pending = 0         # revision of a downloaded update waiting for a quiet moment
        self._last_restart_check = 0.0
        self.tray = None
        self.settings_win = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def run(self):
        setup_logging(debug="--debug" in sys.argv)
        if not acquire_single_instance():
            message_box("FreeFlow is already running. Look for its icon in the system tray.")
            return
        import freeflow as _pkg
        code_dir = os.path.dirname(os.path.dirname(os.path.abspath(_pkg.__file__)))
        origin = "update folder" if os.path.basename(code_dir).lower() == "update" else (
            "bundled" if getattr(sys, "frozen", False) else "source")
        log.info("%s %s starting (python %s, code r%s from %s)", APP_NAME, APP_VERSION, sys.version.split()[0],
                 getattr(_pkg, "CODE_REVISION", 0), origin)

        from .overlay import Overlay
        from .tray import Tray, ensure_icon_file

        _set_dpi_awareness()
        previous_fg = focus.user32.GetForegroundWindow()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        try:
            import sv_ttk
            sv_ttk.set_theme("dark")
        except Exception as e:
            log.debug("sv_ttk unavailable: %s", e)
        ensure_icon_file(autostart.icon_path())
        try:
            self.root.iconbitmap(autostart.icon_path())
        except Exception:
            pass

        self.overlay = Overlay(self.root, lambda: self.recorder.level, self.cfg.get("overlay_position", "bottom"),
                               mode=self._overlay_mode(),
                               on_click=lambda: self.request("toggle_handsfree"),
                               on_right_click=lambda: self.ui(self.open_settings))
        self.root.update_idletasks()
        self._release_startup_focus(previous_fg)
        self.root.after(300, lambda: self._release_startup_focus(previous_fg))
        self.tray = Tray(self)
        self.tray.run()

        self._apply_hotkey()
        if not self.hotkeys.start():
            message_box("FreeFlow could not install its keyboard hook, so the hotkey will not work.\n"
                        "Try restarting the app.", flags=0x30)

        threading.Thread(target=self._controller_loop, name="controller", daemon=True).start()
        threading.Thread(target=self._worker_loop, name="worker", daemon=True).start()
        focus.warm_up()
        if self.cfg.get("game_vocab", True):
            gamevocab.load_cached_champions(DATA_DIR)
            gamevocab.refresh_champions(DATA_DIR)
        self._load_engine()
        self._setup_local_llm()
        self.root.after(30, self._pump_ui)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        first_run = not self.cfg.get("first_run_done")
        if first_run:
            self.cfg["first_run_done"] = True
            self.cfg.save()
            self.root.after(600, self.open_settings)
        sounds.set_volume(self.cfg.get("sound_volume", 0.22))
        self.root.after(1500, lambda: self.tray.notify(self.usage_hint(), "FreeFlow is running"))
        self.root.after(4000, self._warn_about_wispr_flow)
        if getattr(self, "_just_updated", 0):
            self.root.after(2500, lambda: self.tray.notify(
                f"FreeFlow was updated to revision {self._just_updated}.", "FreeFlow update"))
        if self.cfg.get("auto_update", True) and updater.is_frozen():
            self.root.after(20000, lambda: self.check_for_updates(auto=True))
            self.root.after(self._update_interval_ms(), self._daily_update_check)
        try:
            self.root.mainloop()
        finally:
            self.shutdown()

    def _overlay_mode(self) -> str:
        if not self.cfg.get("overlay", True):
            return "popup"        # never shown at all (show() is only called when overlay is on)
        return self.cfg.get("overlay_mode", "island")

    def _own_window(self, hwnd: int) -> bool:
        """Is this one of our invisible windows (hidden root, indicator)?  The settings window does not count."""
        if not hwnd:
            return False
        pid = focus.wt.DWORD(0)
        focus.user32.GetWindowThreadProcessId(hwnd, focus.ctypes.byref(pid))
        if pid.value != os.getpid():
            return False
        try:
            if self.settings_win is not None and self.settings_win.winfo_exists():
                root_hwnd = focus.user32.GetAncestor(self.settings_win.winfo_id(), 2)
                if hwnd == root_hwnd:
                    return False
        except Exception:
            pass
        return True

    def _release_startup_focus(self, previous_fg: int):
        """Starting up must not leave one of our hidden windows as the foreground window."""
        try:
            fg = focus.user32.GetForegroundWindow()
            if fg and self._own_window(fg) and previous_fg and previous_fg != fg and focus.window_exists(previous_fg):
                focus.user32.SetForegroundWindow(previous_fg)
                log.info("Gave the foreground back to the previous window after start-up")
        except Exception as e:
            log.debug("release startup focus failed: %s", e)

    def hotkey_label(self) -> str:
        return pretty_combo(self.cfg.get("hotkey", "ctrl+win"), self.cfg.get("hotkey_gesture", "hold"))

    def usage_hint(self) -> str:
        keys = pretty_combo(self.cfg.get("hotkey", "ctrl+win"))
        gesture = self.cfg.get("hotkey_gesture", "both")
        if gesture == "both":
            return f"Hold {keys} and talk, or double-tap it for hands-free. A single press stops; Esc cancels."
        if gesture == "double_tap":
            return (f"Double-tap {keys} and keep it held while you talk, or double-tap and let go "
                    "for hands-free. A single press stops; Esc cancels.")
        if self.cfg.get("hotkey_mode") == "toggle":
            return f"Press {keys} to start talking and again to finish."
        return f"Hold {keys} and talk; release to type. Tap it to go hands-free."

    def _warn_about_wispr_flow(self):
        """The original Wispr Flow also listens for Ctrl+Win; running both gives double dictation."""
        if (self.cfg.get("hotkey", "ctrl+win").replace(" ", "") != "ctrl+win"
                or self.cfg.get("hotkey_gesture", "both") == "double_tap"):
            return
        try:
            import psutil
            running = any((p.info.get("name") or "").lower().startswith("wispr flow")
                          for p in psutil.process_iter(["name"]))
        except Exception:
            running = False
        if running:
            log.warning("Wispr Flow is running and uses the same hotkey")
            self.tray.notify("Wispr Flow is running too and also uses Ctrl+Win. Quit it, or change the hotkey "
                             "in FreeFlow's Settings, to avoid dictating twice.", "FreeFlow")

    def shutdown(self):
        if self._stop.is_set():
            return
        self._stop.set()
        log.info("Shutting down")
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        if self.state == "recording":
            try:
                self.recorder.abort()
            except Exception:
                pass
            self.state = "idle"
        self._restore_mutes()
        try:
            self.tray.stop()
        except Exception:
            pass

    def quit(self):
        def _do():
            try:
                self.shutdown()
            finally:
                try:
                    self.root.quit()
                    self.root.destroy()
                except Exception:
                    pass
        self.ui(_do)

    # ------------------------------------------------------------------
    # cross-thread helpers
    # ------------------------------------------------------------------
    def ui(self, fn, *args):
        """Run fn(*args) on the Tk main thread."""
        self._ui_q.put((fn, args))

    def _pump_ui(self):
        for _ in range(50):
            try:
                fn, args = self._ui_q.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except Exception:
                log.exception("UI callback failed")
        self.root.after(30, self._pump_ui)

    def request(self, *event):
        """Post an event to the controller thread."""
        self._ctrl_q.put(tuple(event))

    def status_text(self) -> str:
        if not self.enabled:
            return "Dictation disabled"
        if self.state == "recording":
            return "Listening (hands-free)" if self.handsfree else "Listening"
        if self.pending:
            return "Transcribing…"
        if self.engine is None:
            return "No engine"
        if self.engine.error:
            return "Engine error - open Settings"
        if not self.engine.ready.is_set():
            return "Loading speech model…"
        ai = ""
        mode = resolve_mode(self.cfg, self.local_llm)
        if mode == "local":
            ai = f" · AI: {self.local_llm.model}"
        elif mode in ("claude", "openai"):
            ai = f" · AI: {mode}"
        return f"Ready · {self.hotkey_label()} · {self.engine.describe()}{ai}"

    def _setup_local_llm(self):
        """Check Ollama in the background (start it / download the model if needed)."""
        if self.cfg.get("polish", "auto") not in ("auto", "local"):
            return

        def done():
            self.ui(self.tray.refresh_title)
            if self.settings_win is not None:
                self.ui(self.settings_win.refresh_engine_status)
            if self.local_llm.ready:
                log.info("Sentence structuring active (%s)", self.local_llm.model)
            else:
                log.info("Sentence structuring unavailable: %s", self.local_llm.status)
        self.local_llm.setup_async(notify=lambda msg: self.ui(self.tray.notify, msg, "FreeFlow"), on_done=done)

    def toggle_enabled(self):
        self.enabled = not self.enabled
        if not self.enabled and self.state == "recording":
            self.request("cancel")
        self.ui(self.tray.set_state, "idle" if self.enabled else "disabled")
        self.ui(self.tray.refresh_title)

    def open_data_folder(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        subprocess.Popen(["explorer", DATA_DIR])

    # ------------------------------------------------------------------
    # updates
    # ------------------------------------------------------------------
    def _update_interval_ms(self) -> int:
        hours = float(self.cfg.get("update_interval_hours", 24) or 24)
        return int(max(1.0, min(hours, 24 * 14)) * 3600 * 1000)

    def _daily_update_check(self):
        """Tk timer: check once a day while running (a found update waits for a quiet moment)."""
        try:
            if self.cfg.get("auto_update", True) and updater.is_frozen():
                self.check_for_updates(auto=True, daily=True)
        finally:
            self.root.after(self._update_interval_ms(), self._daily_update_check)

    def _key_observed(self, vk: int, t: float):
        self._last_key_t = t
        self.editwatch.key_pressed(vk, t)

    def _quiet(self) -> bool:
        """Nothing is going on that a restart would disturb: no dictation for 10 minutes, no typing for
        5 minutes, no settings window, no game in front."""
        now = time.monotonic()
        if self.state != "idle" or self.pending or self.settings_win is not None:
            return False
        if now - self._last_dictation_t < 600 or now - self._last_key_t < 300:
            return False
        try:
            if (foreground_window_info()[0] or "").lower() in self._game_apps():
                return False
        except Exception:
            pass
        return True

    def _restart_for_update(self):
        rev = self._restart_pending
        self._restart_pending = 0
        log.info("Restarting for update r%s", rev)
        self.ui(self.tray.notify, "Installing the update and restarting FreeFlow…", "FreeFlow update")
        time.sleep(1.5)
        updater.relaunch(["--restart"])

    def _maybe_restart_for_update(self):
        """Controller idle tick: a downloaded update is installed once the PC has been quiet a while."""
        if not self._restart_pending:
            return
        now = time.monotonic()
        if now - self._last_restart_check < 30:
            return
        self._last_restart_check = now
        if self._quiet():
            threading.Thread(target=self._restart_for_update, name="update-restart", daemon=True).start()

    def check_for_updates(self, auto: bool = False, daily: bool = False):
        """Download the latest code update from GitHub; stage it and restart if it is newer.
        Runs in a background thread; results go to the tray (and the settings window if open).
        daily: the restart waits for a quiet moment instead of happening right away."""
        if getattr(self, "_update_thread", None) and self._update_thread.is_alive():
            return

        def work():
            res = updater.check_and_apply(self.cfg.get("update_url"), self.cfg.get("update_page"))
            log.info("Update check (%s): %s - %s", "daily" if daily else ("auto" if auto else "manual"),
                     res.status, res.message)
            self.update_status = res.message
            if self.settings_win is not None:
                self.ui(self.settings_win.refresh_update_status)
            if res.status == "updated" and daily:
                self._restart_pending = res.revision
                if self._quiet():
                    self._restart_for_update()
                else:
                    log.info("Update r%s downloaded; restart deferred until the PC is quiet", res.revision)
                    self.ui(self.tray.notify, f"Update r{res.revision} downloaded. FreeFlow restarts the next time "
                                              "it is not being used.", "FreeFlow update")
            elif res.status == "updated":
                if self.state == "recording":
                    self.ui(self.tray.notify, f"{res.message} once you finish dictating.", "FreeFlow update")
                    for _ in range(600):
                        time.sleep(1)
                        if self.state != "recording":
                            break
                self.ui(self.tray.notify, "Installing the update and restarting FreeFlow…", "FreeFlow update")
                time.sleep(1.5)
                updater.relaunch(["--restart"])
            elif res.status == "full_package":
                self.ui(self.tray.notify, res.message, "FreeFlow update")
            elif not auto:
                self.ui(self.tray.notify, res.message, "FreeFlow update")
        self.update_status = "Checking for updates…"
        self._update_thread = threading.Thread(target=work, name="updater", daemon=True)
        self._update_thread.start()

    # ------------------------------------------------------------------
    # engine
    # ------------------------------------------------------------------
    def _load_engine(self):
        sig = engine_signature(self.cfg)
        if self.engine is not None and sig == self._engine_sig:
            return
        self._engine_sig = sig
        engine = create_engine(self.cfg)
        self.engine = engine
        threading.Thread(target=self._engine_loader, args=(engine,), name="engine-load", daemon=True).start()

    def _engine_loader(self, engine):
        self.ui(self.tray.set_state, "loading")
        self.ui(self.tray.refresh_title)
        engine.load()
        if engine is not self.engine:
            return
        if engine.error:
            log.error("Engine failed: %s", engine.error)
            self.ui(self.tray.set_state, "disabled")
            self.ui(self.tray.notify, f"Speech model failed to load: {engine.error[:180]}", "FreeFlow error")
        else:
            log.info("Engine ready: %s", engine.describe())
            self.ui(self.tray.set_state, "idle" if self.enabled else "disabled")
        self.ui(self.tray.refresh_title)
        if self.settings_win is not None:
            self.ui(self.settings_win.refresh_engine_status)

    # ------------------------------------------------------------------
    # controller thread: recording state machine
    # ------------------------------------------------------------------
    def _controller_loop(self):
        audioctl.co_init()
        try:
            restored = audioctl.restore_after_crash()
            if restored:
                self.ui(self.tray.notify, "Restored audio that was left muted by a previous run.", "FreeFlow")
        except Exception:
            log.exception("crash restore failed")
        while not self._stop.is_set():
            try:
                ev = self._ctrl_q.get(timeout=0.25)
            except queue.Empty:
                self._check_max_duration()
                self._track_foreground()
                continue
            try:
                self._handle(ev)
            except Exception:
                log.exception("controller event %s failed", ev)

    def _handle(self, ev: tuple):
        kind = ev[0]
        mode = self.cfg.get("hotkey_mode", "push_to_talk")
        gesture = self.cfg.get("hotkey_gesture", "both")
        if kind == "activate":
            press_kind = ev[1] if len(ev) > 1 else "press"
            t_press = ev[2] if len(ev) > 2 else time.monotonic()
            if not self.enabled:
                return
            if self.state == "recording":
                if self._provisional:
                    return
                if mode == "toggle" or self.handsfree or gesture in ("double_tap", "both"):
                    self._stop_recording()
                return
            age = time.monotonic() - t_press
            if age > STALE_PRESS_S:
                # the press happened while we were stuck (e.g. a window that never answered); recording
                # now would only capture whatever the user has moved on to
                log.warning("Ignoring a hotkey press from %.1f s ago (FreeFlow was busy)", age)
                return
            if press_kind == "double_tap":
                self._start_recording(handsfree=True, t_press=t_press)
            elif gesture == "both":
                self._start_recording(provisional=True, t_press=t_press)
            else:
                self._start_recording(t_press=t_press)
        elif kind == "deactivate":
            t_release = ev[1] if len(ev) > 1 else time.monotonic()
            if self.state != "recording" or self.handsfree or mode == "toggle":
                return
            # measured from the key events themselves, so slow start-up work cannot turn a tap into a hold
            held_ms = (t_release - self._press_t) * 1000
            if self._provisional and held_ms <= float(self.cfg.get("tap_max_hold_ms", 300)):
                self._discard_recording(held_ms)
                return
            if (gesture == "hold" and self.cfg.get("tap_to_toggle", True)
                    and held_ms < float(self.cfg.get("tap_threshold_ms", 350))):
                self.handsfree = True
                log.info("Hands-free mode (tap)")
                self.ui(self.overlay.show, "handsfree", "")
                self.ui(self.tray.refresh_title)
                return
            self._stop_recording()
        elif kind == "commit":
            if self.state == "recording" and self._provisional:
                self._commit_recording()
        elif kind == "mute_output":
            if self.state == "recording" and not self._provisional:
                self.output_muter.mute(self.cfg.get("mute_output_skip_headphones", True),
                                       self.cfg.get("mute_output_only_if_playing", False))
        elif kind == "unmute_mic":
            self._unmute_timer = None
            if self.state != "recording":      # a new dictation may have started meanwhile
                try:
                    self.mic_muter.restore()
                except Exception:
                    log.exception("mic unmute failed")
        elif kind == "cancel":
            if self.state == "recording":
                self._cancel_recording("cancelled")
        elif kind == "other":
            if (self.state == "recording" and not self.handsfree and mode == "push_to_talk"
                    and self.cfg.get("cancel_on_other_key", True)):
                self._cancel_recording("cancelled (another key was pressed)")
        elif kind == "toggle_handsfree":
            if self.state == "recording":
                self._stop_recording()
            elif self.enabled:
                self._start_recording(handsfree=True)
        elif kind == "stop":
            if self.state == "recording":
                self._stop_recording()

    def _start_recording(self, handsfree: bool = False, provisional: bool = False,
                         t_press: Optional[float] = None):
        if self.engine is None or self.engine.error:
            self._fail("Speech engine is not available - open Settings")
            return
        # 1) Before anything else (chime, indicator, even opening our own mic): take the microphone away
        #    from Discord & co., so nobody in a call hears the chime or the first words.
        self._cancel_pending_unmute()
        self.editwatch.stop()                 # a new dictation: stop watching the previous insertion
        self._last_dictation_t = time.monotonic()
        t0 = time.monotonic()
        mic_mode = self.cfg.get("mute_mic_mode", "list")
        if mic_mode in ("list", "all"):
            self.mic_muter.mute(mic_mode, list(self.cfg.get("mute_mic_apps") or []))
        t_mute = time.monotonic()
        target = self._choose_target()
        self._target = target
        if self.overlay and target.get("hwnd") != self._followed:
            self._followed = int(target.get("hwnd") or 0)
            self.ui(self.overlay.follow, self._followed)     # indicator on the monitor that gets the text
        t_target = time.monotonic()
        try:
            self.recorder.start(self.cfg.get("input_device") or None)
        except Exception as e:
            self._restore_mutes()
            self._fail(f"Microphone error: {e}")
            return
        t_rec = time.monotonic()
        if t_rec - t0 > 0.4:
            log.warning("Slow start (%.0f ms): microphone mute %.0f ms, target check %.0f ms, microphone open %.0f ms",
                        (t_rec - t0) * 1000, (t_mute - t0) * 1000, (t_target - t_mute) * 1000,
                        (t_rec - t_target) * 1000)
        self.state = "recording"
        self.handsfree = handsfree
        self._provisional = provisional
        self._press_t = t_press or time.monotonic()
        self._rec_started = time.monotonic()
        if provisional:
            # hold-to-talk in "both" mode: audio is captured from the first instant, but the chime,
            # indicator and muting wait until we know this is a hold and not the first tap of a double tap
            delay = float(self.cfg.get("tap_max_hold_ms", 300)) / 1000 + 0.02
            threading.Timer(delay, lambda: self.request("commit")).start()
            log.debug("Recording started provisionally")
            return
        self._commit_recording()

    def _commit_recording(self):
        target = self._target
        self._provisional = False
        cue = 0.0
        if self.cfg.get("sounds", True):
            sounds.play("start")
            cue = sounds.duration("start") + 0.06
        if self.cfg.get("overlay", True):
            hint = ""
            if target.get("redirected"):
                hint = "→ " + focus.app_label(target["exe"], target["title"])
            self.ui(self.overlay.show, "handsfree" if self.handsfree else "listening", hint)
        self.ui(self.tray.set_state, "recording")
        self.ui(self.tray.refresh_title)
        if self.cfg.get("mute_output", True):
            if cue:
                # mute only once the chime has finished (never cut off) without blocking key handling
                threading.Timer(cue, lambda: self.request("mute_output")).start()
            else:
                self.output_muter.mute(self.cfg.get("mute_output_skip_headphones", True),
                                       self.cfg.get("mute_output_only_if_playing", False))
        log.info("Recording started (target: %s - %s%s)", target["exe"], target["title"][:60],
                 ", hands-free" if self.handsfree else "")

    def _cancel_pending_unmute(self):
        t = getattr(self, "_unmute_timer", None)
        if t is not None:
            t.cancel()
            self._unmute_timer = None

    def _finish_with_chime(self, kind: str):
        """Speakers back on, play the end chime, and only then give the microphone back to other apps
        (so the chime itself is never transmitted through Discord)."""
        try:
            self.output_muter.restore()
        except Exception:
            log.exception("speaker unmute failed")
        delay = 0.0
        if self.cfg.get("sounds", True) and sounds.play(kind):
            delay = sounds.duration(kind) + 0.05
        if delay > 0:
            self._cancel_pending_unmute()
            self._unmute_timer = threading.Timer(delay, lambda: self.request("unmute_mic"))
            self._unmute_timer.start()
        else:
            try:
                self.mic_muter.restore()
            except Exception:
                log.exception("mic unmute failed")

    def _discard_recording(self, held_ms: float = 0.0):
        """A quick tap in 'both' mode: nothing to transcribe and no chime; a second tap may follow."""
        try:
            self.recorder.abort()
        finally:
            self.state = "idle"
            self.handsfree = False
            self._provisional = False
        self._restore_mutes()
        log.info("Quick tap (%.0f ms) - nothing recorded", held_ms)
        self.ui(self.tray.set_state, "idle" if self.enabled else "disabled")
        self.ui(self.tray.refresh_title)

    def _stop_recording(self):
        audio = self.recorder.stop()
        was_handsfree = self.handsfree
        self.state = "idle"
        self.handsfree = False
        self._provisional = False
        self._finish_with_chime("stop")
        duration = len(audio) / 16000.0
        rms = float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0
        log.info("Recording stopped: %.2fs, rms %.4f", duration, rms)
        if duration < float(self.cfg.get("min_audio_seconds", 0.35)):
            self.ui(self.overlay.hide)
            self.ui(self.tray.set_state, "idle")
            self.ui(self.tray.refresh_title)
            return
        if self.cfg.get("save_last_recording"):
            try:
                save_wav(LAST_RECORDING_PATH, audio)
            except Exception as e:
                log.warning("could not save recording: %s", e)
        self.pending += 1
        ready = self.engine is not None and self.engine.ready.is_set()
        if self.cfg.get("overlay", True):
            self.ui(self.overlay.show, "transcribing" if ready else "loading",
                    "Transcribing…" if ready else "Loading speech model…")
        self.ui(self.tray.set_state, "processing")
        self.ui(self.tray.refresh_title)
        self._job_q.put(Job(audio, duration, rms, self._target["exe"], self._target["title"], was_handsfree,
                            dict(self._target)))

    def _track_foreground(self):
        """Remember the last foreground window that is not one of ours (polled while idle)."""
        try:
            self._maybe_restart_for_update()
            fg = focus.user32.GetForegroundWindow()
            if fg and not self._own_window(fg):
                self._last_foreign_fg = int(fg)
                # the indicator lives on the monitor of the window in front (multi-monitor setups)
                if self.state == "idle" and self.pending == 0 and int(fg) != self._followed and self.overlay:
                    self._followed = int(fg)
                    self.ui(self.overlay.follow, int(fg))
        except Exception:
            pass

    def _game_apps(self) -> set:
        apps = list(self.cfg.get("chat_open_apps") or []) + list(self.cfg.get("chat_send_apps") or [])
        return {a.strip().lower() for a in apps if a.strip()}

    def _choose_target(self) -> dict:
        """Which window gets the text: the one in front, unless it clearly has no text box and we
        know a better one (the window we last dictated into)."""
        exe, title, pid, hwnd = foreground_window_info()
        if hwnd and self._own_window(hwnd):
            # our own hidden window is in front (e.g. right after start-up): use the user's real window
            fallback = self._last_foreign_fg if focus.window_exists(self._last_foreign_fg) else 0
            if self._last_target and focus.window_exists(self._last_target["hwnd"]):
                log.info("Foreground is FreeFlow itself; sending the text to the last text box (%s)", self._last_target["exe"])
                return dict(self._last_target, redirected=True, editable=True)
            if fallback:
                exe, title, pid = window_info(fallback)
                log.info("Foreground is FreeFlow itself; using the previous window %s", exe)
                return {"hwnd": fallback, "exe": exe, "title": title, "redirected": True, "editable": None}
        target = {"hwnd": hwnd, "exe": exe, "title": title, "redirected": False, "editable": None}
        if not self.cfg.get("smart_target", True):
            return target
        if (exe or "").lower() in self._game_apps():
            # games get the text through their chat box; never ask them accessibility questions
            log.debug("Smart target: %s is a game; keeping it", exe)
            return target
        extra = {a.strip().lower() for a in (self.cfg.get("smart_target_text_apps") or []) if a.strip()}
        try:
            editable, info = focus.inspect_editable(exe, extra)
        except Exception as e:
            log.debug("focus check failed: %s", e)
            editable, info = None, {}
        target["editable"] = editable
        last = self._last_target
        if editable is False and last and last["hwnd"] != hwnd and focus.window_exists(last["hwnd"]):
            log.info("Smart target: %s has no text box focused (%s %r); sending the text to %s",
                     exe or "?", info.get("control_type", "?"), info.get("class", ""), last["exe"])
            target = dict(last, redirected=True, editable=True)
        elif editable is None:
            log.debug("Smart target: %s gives no accessibility info; keeping it", exe or "?")
        return target

    def _cancel_recording(self, reason: str = "cancelled"):
        quiet = self._provisional
        try:
            self.recorder.abort()
        finally:
            self.state = "idle"
            self.handsfree = False
            self._provisional = False
        if quiet:   # never announced, so nothing to announce the cancel of
            self._restore_mutes()
            log.info("Provisional recording discarded (%s)", reason)
            self.ui(self.tray.set_state, "idle" if self.enabled else "disabled")
            return
        self._finish_with_chime("cancel")
        log.info("Recording %s", reason)
        if self.cfg.get("overlay", True):
            self.ui(self.overlay.show, "info", "Cancelled", 700)
        self.ui(self.tray.set_state, "idle" if self.enabled else "disabled")
        self.ui(self.tray.refresh_title)

    def _restore_mutes(self):
        try:
            self.mic_muter.restore()
        except Exception:
            log.exception("mic unmute failed")
        try:
            self.output_muter.restore()
        except Exception:
            log.exception("speaker unmute failed")

    def _check_max_duration(self):
        if self.state == "recording":
            limit = float(self.cfg.get("max_record_seconds", 300) or 300)
            if time.monotonic() - self._rec_started > limit:
                log.info("Max recording length reached")
                self._stop_recording()

    # ------------------------------------------------------------------
    # worker thread: transcription -> clean-up -> injection
    # ------------------------------------------------------------------
    def _worker_loop(self):
        while not self._stop.is_set():
            try:
                job = self._job_q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process(job)
            except Exception as e:
                log.exception("processing failed")
                self._fail(f"Error: {e}")
            finally:
                self.pending = max(0, self.pending - 1)
                if self.pending == 0 and self.state != "recording":
                    self.ui(self.tray.set_state, "idle" if self.enabled else "disabled")
                self.ui(self.tray.refresh_title)

    def _process(self, job: Job):
        t0 = time.time()
        engine = self.engine
        if engine is None:
            self._fail("No speech engine configured")
            return
        if not engine.ready.is_set():
            self.ui(self.overlay.show, "loading", "Loading speech model…")
            engine.ready.wait()
        vocab = [w.strip() for w in (self.cfg.get("vocabulary") or []) if w.strip()]
        learning = bool(self.cfg.get("learning", True))
        if learning:
            vocab = self.learner.vocabulary() + vocab      # words learned from corrections, then the user's list
        game_vocab = job.app_exe.lower() in self._game_apps() and bool(self.cfg.get("game_vocab", True))
        if game_vocab:
            # League jargon, items and champion names for Whisper; the user's own words come last (they count most)
            vocab = gamevocab.whisper_terms(list(self.cfg.get("game_vocabulary") or []) + vocab)
        prompt = build_whisper_prompt(vocab, bool(self.cfg.get("whisper_punctuation_prompt", True)))
        try:
            raw = engine.transcribe(job.audio, self.cfg.get("language", "en"), prompt)
        except EngineError as e:
            self._fail(str(e))
            return
        log.info("Raw transcript (%.2fs): %r", time.time() - t0, raw[:200])
        text = clean_transcript(raw, self.cfg, job.rms, job.duration)
        undo_last = None
        correction = split_correction(text) if (text and learning) else None
        if correction is not None:
            text, undo_last = self._handle_correction(correction, job)
            if not text:
                return
        elif text and learning:
            text = self.learner.apply(text)
        if text and game_vocab:
            text = gamevocab.correct_game_text(text)
        if not text:
            if self.cfg.get("overlay", True):
                self.ui(self.overlay.show, "info", "No speech detected", 1200)
            return
        app_context = f"{job.app_exe} - {job.app_title}".strip(" -")
        mode = resolve_mode(self.cfg, self.local_llm)
        if mode != "off" and job.app_exe.lower() in self._game_apps() and not self.cfg.get("polish_in_games", False):
            # chat messages are short and casual; the small model has rewritten them ("you" became "I")
            mode = "off"
        if mode != "off" and not too_short_to_polish(text):
            self.ui(self.overlay.show, "transcribing", "Polishing…")
            t1 = time.time()
            try:
                polished = normalize_spaces(polish(text, self.cfg, app_context, mode=mode, local_llm=self.local_llm))
                if polished:
                    text = polished
                log.info("Structured with %s in %.2fs: %r", mode, time.time() - t1, text[:120])
            except PolishError as e:
                log.warning("AI clean-up failed (%s): %s", mode, e)
                if time.time() - self._last_polish_warning > 300:
                    self._last_polish_warning = time.time()
                    self.ui(self.tray.notify, f"AI clean-up skipped: {e}", "FreeFlow")
        if learning and correction is None and self.cfg.get("learn_from_redictation", True):
            self._maybe_learn_redictation(text)
        final = finalize_for_injection(text, self.cfg)
        method = self.cfg.get("inject_method", "paste")
        type_apps = {a.strip().lower() for a in (self.cfg.get("type_method_apps") or []) if a.strip()}
        if job.app_exe.lower() in type_apps:
            method = "type"
        target = job.target or {}
        if self.cfg.get("smart_target", True) and target.get("hwnd") and focus.window_exists(target["hwnd"]):
            if focus.user32.GetForegroundWindow() != target["hwnd"]:
                label = focus.app_label(target.get("exe", ""), target.get("title", ""))
                if focus.activate_window(target["hwnd"]):
                    log.info("Brought %s back to the front for the text", label)
                    time.sleep(0.15)
                else:
                    from .inject import clipboard_set_text
                    clipboard_set_text(final)
                    if self.cfg.get("history_enabled", True):
                        self.history.add(text, raw, job.duration, engine.describe(), job.app_exe, time.time() - t0)
                    self._fail(f"Couldn't switch to {label}; the text is on the clipboard (Ctrl+V)")
                    return
        # game chat: open the chat box first (Enter), put the text in the way the game accepts it, and
        # optionally send afterwards.  A chat message is one line: a line break would send it early.
        exe_l = (target.get("exe") or job.app_exe or "").lower()
        chat_open = {a.strip().lower() for a in (self.cfg.get("chat_open_apps") or []) if a.strip()}
        chat_send = {a.strip().lower() for a in (self.cfg.get("chat_send_apps") or []) if a.strip()}
        if undo_last is not None:
            self._undo_insertion(undo_last)
        if exe_l in chat_open or exe_l in chat_send:
            final = " ".join(part.strip() for part in final.splitlines() if part.strip())
            if self.cfg.get("append_space", True):
                final += " "
            wait_modifiers_released()
            if exe_l in chat_open:
                press_enter()
                time.sleep(max(0.05, float(self.cfg.get("chat_open_delay_ms", 200)) / 1000))
            how = self.cfg.get("chat_insert", "type")
            method = f"chat-{how}"
            ok = inject_game_chat(final, how, self.cfg.get("restore_clipboard", True),
                                  int(self.cfg.get("clipboard_restore_delay_ms", 500)))
        else:
            ok = inject_text(final, method, self.cfg.get("restore_clipboard", True),
                             int(self.cfg.get("clipboard_restore_delay_ms", 500)),
                             int(self.cfg.get("type_chunk_delay_ms", 0)))
        if ok and exe_l in chat_send:
            time.sleep(0.1)
            press_enter()
        if ok and target.get("hwnd") and (target.get("editable") is True or target.get("redirected")):
            self._last_target = {"hwnd": target["hwnd"], "exe": target.get("exe", ""), "title": target.get("title", "")}
        if ok:
            game = exe_l in chat_open or exe_l in chat_send
            self._last_dictation = {"text": text, "final": final, "time": time.time(), "hwnd": int(target.get("hwnd") or 0),
                                    "method": method, "presses": self.hotkeys.presses, "game": game}
            if learning and not game and self.cfg.get("learn_from_edits", True):
                # learn if the user fixes these words by typing
                self.editwatch.start(final, label=focus.app_label(target.get("exe", ""), target.get("title", "")))
        elapsed = time.time() - t0
        if self.cfg.get("history_enabled", True):
            try:
                self.history.add(text, raw, job.duration, engine.describe(), job.app_exe, elapsed)
            except Exception as e:
                log.warning("history write failed: %s", e)
        if self.cfg.get("overlay", True):
            self.ui(self.overlay.show, "done" if ok else "error", text if ok else "Could not insert text", 1000)
        log.info("Dictation: %.1fs audio -> %d chars in %.2fs via %s (%s)", job.duration, len(text), elapsed,
                 method, "ok" if ok else "FAILED")
        if self.settings_win is not None:
            self.ui(self.settings_win.refresh_history)

    # ------------------------------------------------------------------
    # learning from corrections
    # ------------------------------------------------------------------
    def _handle_correction(self, corrected: str, job: Job) -> tuple:
        """Spoken "correction ...": learn the difference to the last dictation; returns
        (text to insert, last dictation to take back first or None)."""
        last = self._last_dictation
        if not corrected:
            if self.cfg.get("overlay", True):
                self.ui(self.overlay.show, "info", 'Say "correction" and then the right words', 2200)
            return "", None
        if self.cfg.get("capitalize_first", True):
            corrected = capitalize_first(corrected)
        if not last:
            log.info("Correction without a previous dictation; inserting %r", corrected)
            return corrected, None
        learned = self.learner.learn(last["text"], corrected, "voice")
        if learned:
            msg = "; ".join(f"{a} → {b}" for a, b in learned)
            log.info("Correction learned: %s", msg)
            self.ui(self.tray.notify, f"Learned: {msg}", "FreeFlow")
        else:
            log.info("Correction with no word-level difference: %r -> %r", last["text"], corrected)
        undo = None
        if self.cfg.get("correction_replaces", True) and self._can_undo(last, job):
            undo = last
        return corrected, undo

    def _can_undo(self, last: dict, job: Job) -> bool:
        """The last insertion can be taken back when it is recent, went to the same window, was not a
        game chat message, and the user has not typed anything since."""
        hwnd = int((job.target or {}).get("hwnd") or 0)
        return bool(time.time() - last["time"] < 180 and last["hwnd"] and last["hwnd"] == hwnd
                    and not last.get("game") and self.hotkeys.presses == last["presses"])

    def _undo_insertion(self, last: dict):
        wait_modifiers_released()
        if last["method"] == "paste":
            send_undo()
        else:
            send_backspaces(len(last["final"]))
        time.sleep(0.15)
        log.info("Took back the previous insertion (%s)", last["method"])

    def _on_typed_edit(self, wrong: str, right: str):
        """The user changed words of the inserted text by typing (edit watcher thread)."""
        if self.learner.add_rule(wrong, right, "typing"):
            log.info("Typed fix learned: %r -> %r", wrong, right)
            self.ui(self.tray.notify, f"Learned from your edit: {wrong} → {right} (Settings > Learning to undo)", "FreeFlow")

    def _maybe_learn_redictation(self, text: str):
        """The same sentence dictated again within a short time: note the changed words; a fix seen
        twice becomes a rule."""
        last = self._last_dictation
        if not last or time.time() - last["time"] > 45 or len(text_words(text)) < 2:
            return
        sim = sentence_similarity(last["text"], text)
        if sim < 0.6 or sim > 0.999:
            return
        learned, noted = self.learner.learn_auto(last["text"], text)
        if learned:
            msg = "; ".join(f"{a} → {b}" for a, b in learned)
            log.info("Learned from re-dictation: %s", msg)
            self.ui(self.tray.notify, f"Learned: {msg} (Settings > Learning to undo)", "FreeFlow")
        elif noted:
            log.info("Possible correction noted, learned when heard again: %s",
                     "; ".join(f"{a} -> {b}" for a, b in noted))

    def _fail(self, msg: str):
        log.error(msg)
        if self.cfg.get("sounds", True):
            sounds.play("error")
        if self.cfg.get("overlay", True):
            self.ui(self.overlay.show, "error", msg, 2500)
        self.ui(self.tray.notify, msg, "FreeFlow error")
        self.ui(self.tray.set_state, "idle" if self.enabled else "disabled")

    # ------------------------------------------------------------------
    # settings
    # ------------------------------------------------------------------
    def _apply_hotkey(self):
        err = self.hotkeys.configure(self.cfg.get("hotkey", "ctrl+win"), self.cfg.get("cancel_key", "esc"),
                                     self.cfg.get("hotkey_gesture", "hold"), self.cfg.get("double_tap_ms", 400),
                                     self.cfg.get("tap_max_hold_ms", 300))
        if err:
            log.error("Bad hotkey %r: %s - falling back to ctrl+win", self.cfg.get("hotkey"), err)
            self.hotkeys.configure("ctrl+win", "esc")
            if self.tray:
                self.tray.notify(f"Hotkey {self.cfg.get('hotkey')!r} is invalid ({err}); using Ctrl+Win.", "FreeFlow")

    def apply_config(self):
        """Called by the settings window (main thread) after saving."""
        self._apply_hotkey()
        sounds.set_volume(self.cfg.get("sound_volume", 0.22))
        self.history.max_entries = int(self.cfg.get("history_max") or 1000)
        self.overlay.position = self.cfg.get("overlay_position", "bottom")
        self.overlay.set_mode(self._overlay_mode())
        if self.overlay.state in ("idle", "hidden"):
            self.overlay.show_idle() if self._overlay_mode() == "island" else self.overlay.hide()
        try:
            if bool(self.cfg.get("autostart")) != autostart.is_enabled():
                autostart.set_enabled(bool(self.cfg.get("autostart")))
        except Exception as e:
            log.warning("autostart update failed: %s", e)
        self._load_engine()
        self._setup_local_llm()
        self.tray.refresh_title()

    def open_settings(self, tab: Optional[str] = None):
        from .settings_ui import SettingsWindow
        if self.settings_win is not None and self.settings_win.winfo_exists():
            self.settings_win.deiconify()
            self.settings_win.lift()
            self.settings_win.focus_force()
            if tab:
                self.settings_win.select_tab(tab)
            return
        self.settings_win = SettingsWindow(self, tab)


PUNCTUATION_PROMPT = "Okay, so here's the plan. We'll finish this today, right? Yes, I think so."


def build_whisper_prompt(vocab: list, punctuate: bool = True) -> Optional[str]:
    """Whisper treats the prompt as preceding text: punctuated text nudges it to punctuate,
    and the vocabulary list teaches it names and jargon."""
    parts = []
    if punctuate:
        parts.append(PUNCTUATION_PROMPT)
    if vocab:
        parts.append(", ".join(vocab) + ".")
    return " ".join(parts) or None


def _set_dpi_awareness():
    """Crisp rendering and real pixel coordinates on scaled displays."""
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def find_running_instances() -> list:
    """Other FreeFlow processes (excluding this process and its launcher chain)."""
    import psutil
    me = psutil.Process(os.getpid())
    skip = {me.pid} | {p.pid for p in me.parents()}
    found = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (p.info.get("name") or "").lower()
            cmd = " ".join(p.info.get("cmdline") or [])
        except Exception:
            continue
        if p.pid in skip or name not in ("python.exe", "pythonw.exe", "freeflow.exe"):
            continue
        if name == "freeflow.exe" or "freeflow.pyw" in cmd.lower() or "freeflow.app" in cmd.lower():
            found.append(p)
    return found


def _main_windows_of(pids: set) -> list:
    """Hidden Tk main windows (title 'FreeFlow', class TkTopLevel, not visible) of the given processes."""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    proc_t = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    out = []

    def cb(hwnd, _):
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            cls = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(hwnd, cls, 64)
            n = u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if cls.value == "TkTopLevel" and buf.value == APP_NAME and not u.IsWindowVisible(hwnd):
                out.append((hwnd, pid.value))
        return True
    u.EnumWindows(proc_t(cb), 0)
    return out


def quit_running_instance(timeout: float = 12.0) -> bool:
    """Ask a running FreeFlow to shut down cleanly (restores any mutes); force-kill after `timeout`."""
    import ctypes
    import psutil
    WM_CLOSE = 0x0010
    targets = find_running_instances()
    if not targets:
        return False
    pids = {p.pid for p in targets}
    parents = {}
    for p in targets:
        try:
            parents[p.pid] = p.parent()
        except Exception:
            pass
    for hwnd, pid in _main_windows_of(pids):
        ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    deadline = time.time() + timeout
    while time.time() < deadline and any(p.is_running() for p in targets):
        time.sleep(0.2)
    for p in targets:
        if p.is_running():
            try:
                p.terminate()
            except Exception:
                pass
    # a console launcher ("Troubleshoot (console).bat") would otherwise sit at "Press any key"
    for p in targets:
        par = parents.get(p.pid)
        try:
            if par and par.is_running() and par.name().lower() == "cmd.exe" and ".bat" in " ".join(par.cmdline()).lower():
                par.terminate()
        except Exception:
            pass
    return True


def set_autostart_setting(enabled: bool):
    cfg = Config()
    cfg["autostart"] = bool(enabled)
    cfg.save()
    autostart.set_enabled(bool(enabled))


def apply_cli_settings(args: list) -> bool:
    """--set key=value (repeatable) writes config values; returns True if anything was set."""
    import json
    from .config import DEFAULTS
    changed = False
    cfg = None
    for i, a in enumerate(args):
        if a != "--set" or i + 1 >= len(args) or "=" not in args[i + 1]:
            continue
        key, _, raw = args[i + 1].partition("=")
        key = key.strip()
        if key not in DEFAULTS:
            log.warning("--set: unknown setting %r", key)
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        cfg = cfg or Config()
        cfg[key] = value
        changed = True
    if cfg is not None:
        cfg.save()
    return changed


def main(argv: Optional[list] = None):
    """Command line:  --quit | --restart | --update | --autostart on|off | --set key=value
    (no arguments = run normally)."""
    args = list(sys.argv[1:] if argv is None else argv)
    only_settings = False
    just_updated = 0
    if "--update" in args:
        # "Update FreeFlow.bat": fetch the latest code, then (re)start with it
        setup_logging()
        quit_running_instance()
        cfg = Config()
        res = updater.check_and_apply(cfg.get("update_url"), cfg.get("update_page"))
        log.info("Update (--update): %s - %s", res.status, res.message)
        if res.status == "updated":
            updater.relaunch(["--updated", str(res.revision)])   # a fresh process picks up the staged code
            return
        if res.status in ("full_package", "error"):
            message_box(res.message, "FreeFlow update", 0x30 if res.status == "error" else 0x40)
        args = [a for a in args if a != "--update"]
    if "--updated" in args:
        i = args.index("--updated")
        try:
            just_updated = int(args[i + 1])
        except (IndexError, ValueError):
            just_updated = 1
    if apply_cli_settings(args):
        only_settings = True
    if "--autostart" in args:
        i = args.index("--autostart")
        value = args[i + 1].lower() if i + 1 < len(args) else "on"
        set_autostart_setting(value in ("on", "1", "true", "yes"))
        only_settings = True
    if "--quit" in args:
        quit_running_instance()
        return
    if "--restart" in args:
        quit_running_instance()
        only_settings = False
    if only_settings:
        return
    app = App()
    app._just_updated = just_updated
    app.run()


if __name__ == "__main__":
    main()
