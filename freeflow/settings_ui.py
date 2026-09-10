"""Settings / history window (tkinter + sv_ttk)."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from . import APP_VERSION, audioctl, autostart
from .config import DATA_DIR, LOG_PATH
from .hotkeys import parse_combo, pretty_combo
from .llm import CLAUDE_MODELS
from .llm import LOCAL_MODELS as LLM_MODELS
from .recorder import default_input_name, list_input_devices
from .transcribe import LOCAL_MODELS, MODEL_NOTES

log = logging.getLogger(__name__)

LANGUAGES = [("auto", "Auto-detect"), ("en", "English"), ("pt", "Portuguese"), ("es", "Spanish"),
             ("fr", "French"), ("de", "German"), ("it", "Italian"), ("nl", "Dutch"), ("ja", "Japanese"),
             ("ko", "Korean"), ("zh", "Chinese"), ("ru", "Russian"), ("ar", "Arabic"), ("hi", "Hindi"),
             ("tr", "Turkish"), ("pl", "Polish"), ("sv", "Swedish"), ("uk", "Ukrainian")]
DEFAULT_DEVICE_LABEL = "System default"


class SettingsWindow(tk.Toplevel):
    def __init__(self, app, tab: str | None = None):
        super().__init__(app.root)
        self.app = app
        self.cfg = app.cfg
        self.title("FreeFlow Settings")
        self.geometry("860x720")
        self.minsize(760, 600)
        try:
            self.iconbitmap(autostart.icon_path())
        except Exception:
            pass
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.v: dict[str, tk.Variable] = {}
        self._texts: dict[str, tk.Text] = {}
        self._capturing = False

        # button bar first (packed at the bottom) so it can never be pushed out of view by a tall tab
        bottom = ttk.Frame(self, padding=10)
        bottom.pack(side="bottom", fill="x")
        self.status = ttk.Label(bottom, text="Changes are saved when you click Apply or close the window.")
        self.status.pack(side="left")
        try:
            ttk.Button(bottom, text="Save & Close", style="Accent.TButton", command=self.close).pack(side="right", padx=(6, 0))
        except tk.TclError:
            ttk.Button(bottom, text="Save & Close", command=self.close).pack(side="right", padx=(6, 0))
        ttk.Button(bottom, text="Apply", command=self.save).pack(side="right")

        self.nb = ttk.Notebook(self)
        self.nb.pack(side="top", fill="both", expand=True, padx=10, pady=(10, 0))
        self.tabs: dict[str, ttk.Frame] = {}
        for name, builder in [("General", self._build_general), ("Audio & Muting", self._build_audio),
                              ("Transcription", self._build_transcription), ("Formatting", self._build_formatting),
                              ("Learning", self._build_learning), ("History", self._build_history),
                              ("About", self._build_about)]:
            frame = ttk.Frame(self.nb, padding=12)
            self.nb.add(frame, text=name)
            self.tabs[name] = frame
            builder(frame)
        if tab:
            self.select_tab(tab)
        self.lift()
        self.focus_force()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _var(self, key: str, kind=str) -> tk.Variable:
        val = self.cfg.get(key)
        if kind is bool:
            var = tk.BooleanVar(value=bool(val))
        elif kind is int:
            var = tk.IntVar(value=int(val or 0))
        else:
            var = tk.StringVar(value="" if val is None else str(val))
        self.v[key] = var
        return var

    def _check(self, parent, key: str, text: str, row: int, col: int = 0, colspan: int = 2):
        cb = ttk.Checkbutton(parent, text=text, variable=self._var(key, bool))
        cb.grid(row=row, column=col, columnspan=colspan, sticky="w", pady=2)
        return cb

    def _label(self, parent, text: str, row: int, col: int = 0):
        ttk.Label(parent, text=text).grid(row=row, column=col, sticky="w", padx=(0, 10), pady=3)

    def _entry(self, parent, key: str, row: int, col: int = 1, width: int = 28, show: str | None = None):
        e = ttk.Entry(parent, textvariable=self._var(key), width=width, show=show)
        e.grid(row=row, column=col, sticky="w", pady=3)
        return e

    def _combo(self, parent, key: str, values, row: int, col: int = 1, width: int = 26, state="readonly"):
        c = ttk.Combobox(parent, textvariable=self._var(key), values=values, width=width, state=state)
        c.grid(row=row, column=col, sticky="w", pady=3)
        return c

    def _text(self, parent, key: str, initial: str, row: int, col: int = 1, height: int = 4, width: int = 46):
        t = tk.Text(parent, height=height, width=width, wrap="word", undo=True)
        t.insert("1.0", initial)
        t.grid(row=row, column=col, sticky="we", pady=3)
        self._texts[key] = t
        return t

    def select_tab(self, name: str):
        for i, n in enumerate(self.tabs):
            if n.lower() == name.lower():
                self.nb.select(i)
                if n == "History":
                    self.refresh_history()
                elif n == "Learning":
                    self.refresh_rules()

    def close(self):
        """Save (if valid) and close. Invalid input keeps the window open with an explanation."""
        if self._capturing:
            self.app.hotkeys.cancel_capture()
            self._capturing = False
        try:
            if not self.save():
                return
        except tk.TclError:
            pass
        self.app.settings_win = None
        self.destroy()

    # ------------------------------------------------------------------
    # tabs
    # ------------------------------------------------------------------
    def _build_general(self, f):
        f.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(f, text="Hotkey", font=("Segoe UI", 11, "bold")).grid(row=r, column=0, columnspan=3, sticky="w", pady=(0, 6)); r += 1
        self._label(f, "Dictation hotkey", r)
        box = ttk.Frame(f); box.grid(row=r, column=1, sticky="w")
        self.hotkey_entry = ttk.Entry(box, textvariable=self._var("hotkey"), width=22)
        self.hotkey_entry.pack(side="left")
        self.capture_btn = ttk.Button(box, text="Record keys…", command=self._capture_hotkey)
        self.capture_btn.pack(side="left", padx=6)
        ttk.Label(box, text="e.g. ctrl+win, ctrl+shift+space, f8, rctrl, capslock", foreground="#888").pack(side="left")
        r += 1
        self._label(f, "Activate by", r)
        gbox = ttk.Frame(f); gbox.grid(row=r, column=1, sticky="w")
        gvar = self._var("hotkey_gesture")
        ttk.Radiobutton(gbox, text="Hold to talk + double-tap for hands-free (recommended)", variable=gvar, value="both").pack(anchor="w")
        ttk.Radiobutton(gbox, text="Hold to talk only (a quick tap switches to hands-free)", variable=gvar, value="hold").pack(anchor="w")
        ttk.Radiobutton(gbox, text="Double-tap only (keep the 2nd press held to talk, or let go for hands-free)", variable=gvar, value="double_tap").pack(anchor="w")
        r += 1
        ttk.Label(f, text="While dictating hands-free, a single press of the hotkey stops; Esc cancels.", foreground="#888").grid(row=r, column=1, sticky="w"); r += 1
        self._label(f, "Double-tap window (ms)", r); self._entry(f, "double_tap_ms", r, width=8); r += 1
        self._label(f, "Mode", r)
        mode = ttk.Frame(f); mode.grid(row=r, column=1, sticky="w")
        var = self._var("hotkey_mode")
        ttk.Radiobutton(mode, text="Hold to talk (push-to-talk)", variable=var, value="push_to_talk").pack(side="left")
        ttk.Radiobutton(mode, text="Press to start / press to stop", variable=var, value="toggle").pack(side="left", padx=10)
        r += 1
        self._check(f, "tap_to_toggle", "Quick tap of the hotkey starts hands-free mode (tap again or press Esc to finish)", r, 1); r += 1
        self._check(f, "cancel_on_other_key", "While holding the hotkey, pressing any other key cancels the dictation", r, 1); r += 1
        self._label(f, "Tap threshold (ms)", r); self._entry(f, "tap_threshold_ms", r, width=8); r += 1
        self._label(f, "Cancel key", r); self._entry(f, "cancel_key", r, width=12); r += 1

        ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky="we", pady=10); r += 1
        ttk.Label(f, text="Behaviour", font=("Segoe UI", 11, "bold")).grid(row=r, column=0, columnspan=3, sticky="w", pady=(0, 6)); r += 1
        self._label(f, "Language", r)
        self.lang_combo = ttk.Combobox(f, values=[f"{code} - {name}" for code, name in LANGUAGES], width=26, state="readonly")
        cur = self.cfg.get("language", "en")
        self.lang_combo.set(next((f"{c} - {n}" for c, n in LANGUAGES if c == cur), f"{cur} - {cur}"))
        self.lang_combo.grid(row=r, column=1, sticky="w", pady=3); r += 1
        sbox = ttk.Frame(f); sbox.grid(row=r, column=1, sticky="w")
        ttk.Checkbutton(sbox, text="Play start / stop sounds", variable=self._var("sounds", bool)).pack(side="left")
        ttk.Label(sbox, text="   volume (0.05 - 1)").pack(side="left")
        ttk.Entry(sbox, textvariable=self._var("sound_volume"), width=6).pack(side="left", padx=4)
        r += 1
        self._check(f, "overlay", "Show the floating indicator (island)", r, 1); r += 1
        self._label(f, "Indicator style", r)
        ibox = ttk.Frame(f); ibox.grid(row=r, column=1, sticky="w")
        ivar = self._var("overlay_mode")
        ttk.Radiobutton(ibox, text="Island: always visible, expands while dictating (click = hands-free, right-click = settings)",
                        variable=ivar, value="island").pack(anchor="w")
        ttk.Radiobutton(ibox, text="Only while dictating", variable=ivar, value="popup").pack(anchor="w")
        r += 1
        self._label(f, "Indicator position", r); self._combo(f, "overlay_position", ["bottom", "top"], r, width=10); r += 1
        self._check(f, "notifications", "Show tray notifications for errors", r, 1); r += 1
        self._check(f, "history_enabled", "Keep a history of dictations (stored locally)", r, 1); r += 1
        self.v["autostart"] = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(f, text="Start FreeFlow when I sign in to Windows", variable=self.v["autostart"]).grid(row=r, column=1, sticky="w", pady=2); r += 1
        self._label(f, "Max recording (s)", r); self._entry(f, "max_record_seconds", r, width=8); r += 1

    def _build_audio(self, f):
        f.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(f, text="Microphone", font=("Segoe UI", 11, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 6)); r += 1
        self._label(f, "Input device", r)
        names = [DEFAULT_DEVICE_LABEL] + [n for _, n in list_input_devices()]
        self.dev_combo = ttk.Combobox(f, values=names, width=44, state="readonly")
        cur = self.cfg.get("input_device") or ""
        self.dev_combo.set(cur if cur in names else (next((n for n in names if cur and n.lower().startswith(cur.lower())), DEFAULT_DEVICE_LABEL)))
        self.dev_combo.grid(row=r, column=1, sticky="w", pady=3); r += 1
        ttk.Label(f, text=f"Current system default: {default_input_name()}", foreground="#888").grid(row=r, column=1, sticky="w"); r += 1
        self._check(f, "save_last_recording", "Keep the last recording as a WAV file (for troubleshooting)", r, 1); r += 1

        ttk.Separator(f).grid(row=r, column=0, columnspan=2, sticky="we", pady=10); r += 1
        ttk.Label(f, text="Speakers while dictating", font=("Segoe UI", 11, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 6)); r += 1
        self._check(f, "mute_output", "Mute my speakers while I dictate (and unmute afterwards)", r, 0); r += 1
        self._check(f, "mute_output_skip_headphones", "…but not when the default output looks like headphones / a headset", r, 0); r += 1
        self._check(f, "mute_output_only_if_playing", "…and only when something is actually playing", r, 0); r += 1
        box = ttk.Frame(f); box.grid(row=r, column=0, columnspan=2, sticky="w")
        self.out_label = ttk.Label(box, text="", foreground="#888")
        self.out_label.pack(side="left")
        ttk.Button(box, text="Refresh", command=self._refresh_output).pack(side="left", padx=8)
        r += 1
        self._refresh_output()

        ttk.Separator(f).grid(row=r, column=0, columnspan=2, sticky="we", pady=10); r += 1
        ttk.Label(f, text="Microphone in other apps while dictating (e.g. Discord)", font=("Segoe UI", 11, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 6)); r += 1
        var = self._var("mute_mic_mode")
        ttk.Radiobutton(f, text="Mute the microphone for these apps:", variable=var, value="list").grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        apps = "\n".join(self.cfg.get("mute_mic_apps") or [])
        self._text(f, "mute_mic_apps", apps, r, col=0, height=3, width=40).grid(row=r, column=0, columnspan=2, sticky="w", padx=(24, 0)); r += 1
        ttk.Label(f, text="One program name per line, e.g. Discord.exe, Zoom.exe, Teams.exe", foreground="#888").grid(row=r, column=0, columnspan=2, sticky="w", padx=(24, 0)); r += 1
        ttk.Radiobutton(f, text="Mute the microphone for every other app", variable=var, value="all").grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        ttk.Radiobutton(f, text="Do not touch other apps", variable=var, value="off").grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        box2 = ttk.Frame(f); box2.grid(row=r, column=0, columnspan=2, sticky="we", pady=(6, 0))
        ttk.Button(box2, text="Show apps using the microphone now", command=self._scan_mic_apps).pack(side="left")
        r += 1
        self.mic_apps_label = ttk.Label(f, text="", foreground="#888", justify="left")
        self.mic_apps_label.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1

    def _build_transcription(self, f):
        f.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(f, text="Speech recognition engine", font=("Segoe UI", 11, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 6)); r += 1
        var = self._var("engine")
        ttk.Radiobutton(f, text="Local Whisper (private, runs on this PC - recommended)", variable=var, value="local").grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        self._label(f, "   Model", r)
        box = ttk.Frame(f); box.grid(row=r, column=1, sticky="w")
        self.model_combo = ttk.Combobox(box, textvariable=self._var("local_model"), values=LOCAL_MODELS, width=20)
        self.model_combo.pack(side="left")
        self.model_note = ttk.Label(box, text="", foreground="#888")
        self.model_note.pack(side="left", padx=8)
        self.model_combo.bind("<<ComboboxSelected>>", lambda e: self._update_model_note())
        self._update_model_note()
        r += 1
        self._label(f, "   Device", r); self._combo(f, "local_device", ["auto", "cuda", "cpu"], r, width=10); r += 1
        self._label(f, "   Precision", r); self._combo(f, "local_compute_type", ["auto", "float16", "int8_float16", "int8", "float32"], r, width=14); r += 1
        self._label(f, "   Beam size", r); self._entry(f, "beam_size", r, width=6); r += 1
        ttk.Label(f, text="Models are downloaded once (from Hugging Face) the first time they are used.", foreground="#888").grid(row=r, column=0, columnspan=2, sticky="w"); r += 1

        ttk.Radiobutton(f, text="OpenAI (cloud, needs an API key)", variable=var, value="openai").grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0)); r += 1
        self._label(f, "   API key", r); self._entry(f, "openai_api_key", r, width=48, show="•"); r += 1
        self._label(f, "   Model", r); self._combo(f, "openai_model", ["gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"], r, width=24, state="normal"); r += 1
        ttk.Radiobutton(f, text="Groq (cloud, very fast, needs an API key)", variable=var, value="groq").grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0)); r += 1
        self._label(f, "   API key", r); self._entry(f, "groq_api_key", r, width=48, show="•"); r += 1
        self._label(f, "   Model", r); self._combo(f, "groq_model", ["whisper-large-v3-turbo", "whisper-large-v3"], r, width=24, state="normal"); r += 1

        ttk.Separator(f).grid(row=r, column=0, columnspan=2, sticky="we", pady=10); r += 1
        self._label(f, "Vocabulary", r)
        self._text(f, "vocabulary", ", ".join(self.cfg.get("vocabulary") or []), r, height=3); r += 1
        ttk.Label(f, text="Names, jargon and words that are often misheard, separated by commas. They are given to the model as hints.", foreground="#888", wraplength=520).grid(row=r, column=1, sticky="w"); r += 1
        self.engine_status = ttk.Label(f, text="", foreground="#888")
        self.engine_status.grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0)); r += 1
        self.refresh_engine_status()

    def _build_formatting(self, f):
        f.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(f, text="Clean-up", font=("Segoe UI", 11, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 6)); r += 1
        self._check(f, "remove_fillers", "Remove filler sounds (um, uh, hmm…)", r); r += 1
        self._check(f, "voice_commands", "Voice commands: say \"new line\" / \"new paragraph\" / \"bullet point\"", r); r += 1
        self._check(f, "auto_lists", "Turn spoken enumerations (\"one ..., two ..., three ...\") into numbered lists", r); r += 1
        self._check(f, "capitalize_first", "Capitalise the first letter", r); r += 1
        self._check(f, "append_space", "Add a space after the inserted text", r); r += 1
        self._label(f, "Replacements", r)
        repl = "\n".join(f"{k} => {v}" for k, v in (self.cfg.get("replacements") or {}).items())
        self._text(f, "replacements", repl, r, height=4); r += 1
        ttk.Label(f, text="One per line:  spoken phrase => text to insert   (e.g.  my email => you@example.com)", foreground="#888").grid(row=r, column=1, sticky="w"); r += 1

        ttk.Separator(f).grid(row=r, column=0, columnspan=2, sticky="we", pady=10); r += 1
        ttk.Label(f, text="Inserting text", font=("Segoe UI", 11, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 6)); r += 1
        var = self._var("inject_method")
        box = ttk.Frame(f); box.grid(row=r, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(box, text="Paste via clipboard (fast, works everywhere)", variable=var, value="paste").pack(side="left")
        ttk.Radiobutton(box, text="Type keystrokes", variable=var, value="type").pack(side="left", padx=10)
        r += 1
        self._check(f, "restore_clipboard", "Restore my previous clipboard text after pasting", r); r += 1
        self._check(f, "smart_target", "Smart target: insert into the window I started dictating in; if it has no text box, "
                    "use the last window I dictated into (brought to the front)", r); r += 1
        self._label(f, "Treat as text apps", r); self._entry(f, "smart_target_text_apps", r, width=40)
        self.v["smart_target_text_apps"].set(", ".join(self.cfg.get("smart_target_text_apps") or [])); r += 1
        ttk.Label(f, text="Extra program names that always keep the text (editors / terminals are already known)", foreground="#888").grid(row=r, column=1, sticky="w"); r += 1
        self._label(f, "Always type in", r); self._entry(f, "type_method_apps", r, width=40)
        self.v["type_method_apps"].set(", ".join(self.cfg.get("type_method_apps") or [])); r += 1
        ttk.Label(f, text="Program names, comma separated (for apps where Ctrl+V does not paste)", foreground="#888").grid(row=r, column=1, sticky="w"); r += 1
        self._label(f, "Game chat: open first", r); self._entry(f, "chat_open_apps", r, width=40)
        self.v["chat_open_apps"].set(", ".join(self.cfg.get("chat_open_apps") or [])); r += 1
        self._label(f, "Game chat: send after", r); self._entry(f, "chat_send_apps", r, width=40)
        self.v["chat_send_apps"].set(", ".join(self.cfg.get("chat_send_apps") or [])); r += 1
        self._label(f, "Game chat: insert by", r); self._combo(f, "chat_insert", ["type", "paste", "keys"], r, width=10); r += 1
        self._check(f, "game_vocab", "League of Legends vocabulary for game chat (jargon, items, champion names)", r); r += 1
        self._label(f, "Extra game words", r); self._entry(f, "game_vocabulary", r, width=40)
        self.v["game_vocabulary"].set(", ".join(self.cfg.get("game_vocabulary") or [])); r += 1
        ttk.Label(f, text="Games where FreeFlow presses Enter to open the chat before inserting / to send the message "
                          "afterwards. Insert by: type = Unicode characters (safe, never triggers abilities), "
                          "paste = slow Ctrl+V, keys = real keystrokes (only if the others fail).",
                  foreground="#888", wraplength=560).grid(row=r, column=1, sticky="w"); r += 1

        ttk.Separator(f).grid(row=r, column=0, columnspan=2, sticky="we", pady=10); r += 1
        ttk.Label(f, text="AI clean-up: proper sentences, punctuation, paragraphs", font=("Segoe UI", 11, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 6)); r += 1
        pvar = self._var("polish")
        box = ttk.Frame(f); box.grid(row=r, column=0, columnspan=2, sticky="w")
        for val, txt in [("auto", "Auto (local model when available)"), ("off", "Off"), ("local", "Local (Ollama)"),
                         ("claude", "Claude"), ("openai", "OpenAI")]:
            ttk.Radiobutton(box, text=txt, variable=pvar, value=val).pack(side="left", padx=(0, 10))
        r += 1
        self._label(f, "Local model", r)
        box = ttk.Frame(f); box.grid(row=r, column=1, sticky="w")
        self.ollama_combo = ttk.Combobox(box, textvariable=self._var("ollama_model"), values=LLM_MODELS, width=16)
        self.ollama_combo.pack(side="left")
        ttk.Label(box, text="  Ollama URL").pack(side="left")
        ttk.Entry(box, textvariable=self._var("ollama_url"), width=22).pack(side="left", padx=(4, 8))
        ttk.Button(box, text="Check / download", command=self._check_ollama).pack(side="left")
        r += 1
        self.ollama_status = ttk.Label(f, text="", foreground="#888", wraplength=640, justify="left")
        self.ollama_status.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        self._label(f, "Style", r)
        svar = self._var("polish_style")
        box = ttk.Frame(f); box.grid(row=r, column=1, sticky="w")
        for val, txt in [("clean", "As spoken, just cleaned"), ("formal", "Formal"), ("casual", "Casual")]:
            ttk.Radiobutton(box, text=txt, variable=svar, value=val).pack(side="left", padx=(0, 10))
        r += 1
        self._label(f, "Anthropic API key", r); self._entry(f, "anthropic_api_key", r, width=48, show="•"); r += 1
        self._label(f, "Claude model", r); self._combo(f, "claude_model", CLAUDE_MODELS, r, width=22, state="normal"); r += 1
        self._label(f, "OpenAI model", r); self._combo(f, "openai_polish_model", ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"], r, width=22, state="normal"); r += 1
        ttk.Label(f, text="Uses the OpenAI key from the Transcription tab. A refusal or error falls back to the rule-based clean-up.", foreground="#888", wraplength=560).grid(row=r, column=0, columnspan=2, sticky="w"); r += 1

    def _build_history(self, f):
        f.columnconfigure(0, weight=1)
        f.rowconfigure(0, weight=1)
        cols = ("when", "secs", "text")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("when", text="When"); self.tree.column("when", width=130, stretch=False)
        self.tree.heading("secs", text="Audio"); self.tree.column("secs", width=60, stretch=False, anchor="e")
        self.tree.heading("text", text="Text"); self.tree.column("text", width=500)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda e: self._copy_history())
        box = ttk.Frame(f); box.grid(row=1, column=0, columnspan=2, sticky="we", pady=(8, 0))
        ttk.Button(box, text="Copy", command=self._copy_history).pack(side="left")
        ttk.Button(box, text="Copy raw transcript", command=lambda: self._copy_history(raw=True)).pack(side="left", padx=6)
        ttk.Button(box, text="Refresh", command=self.refresh_history).pack(side="left")
        ttk.Button(box, text="Correct…", command=self._correct_history).pack(side="left", padx=6)
        ttk.Button(box, text="Delete all", command=self._clear_history).pack(side="right")
        self._history_entries: list[dict] = []
        self.refresh_history()

    def _build_learning(self, f):
        f.columnconfigure(0, weight=1)
        f.rowconfigure(6, weight=1)
        r = 0
        ttk.Label(f, text="FreeFlow learns the way you say things", font=("Segoe UI", 11, "bold")).grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        for line in ('• Right after a bad dictation, hold the hotkey and say "correction" followed by the right words, '
                     'e.g. "correction: start localhost". The wrong text is taken back and the fix is inserted.',
                     '• Or just fix the words by typing: FreeFlow watches the text box for a couple of minutes '
                     'after each insertion and learns what you changed.',
                     '• Or simply dictate the sentence again: a fix that shows up twice is learned by itself.',
                     '• Or select an entry under History and click "Correct…", or add a rule below.',
                     'Every rule is applied to future transcripts, and the learned words are whispered to the speech '
                     'model so it hears them right more often.'):
            ttk.Label(f, text=line, wraplength=640, justify="left", foreground="#aaa").grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 2)); r += 1
        box = ttk.Frame(f); box.grid(row=r, column=0, columnspan=2, sticky="w", pady=(6, 6)); r += 1
        ttk.Checkbutton(box, text="Learn from corrections", variable=self._var("learning", bool)).pack(side="left")
        ttk.Checkbutton(box, text="Learn from typed fixes", variable=self._var("learn_from_edits", bool)).pack(side="left", padx=12)
        ttk.Checkbutton(box, text="Learn from re-dictations", variable=self._var("learn_from_redictation", bool)).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(box, text='"Correction" takes back the text just inserted', variable=self._var("correction_replaces", bool)).pack(side="left")
        cols = ("from", "to", "learned", "source", "hits")
        self.rules_tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse", height=9)
        for key, title, width, anchor in (("from", "Heard as", 200, "w"), ("to", "Should be", 200, "w"),
                                          ("learned", "Learned", 120, "w"), ("source", "How", 100, "w"), ("hits", "Used", 50, "e")):
            self.rules_tree.heading(key, text=title)
            self.rules_tree.column(key, width=width, anchor=anchor, stretch=key in ("from", "to"))
        self.rules_tree.grid(row=r, column=0, sticky="nsew")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.rules_tree.yview); sb.grid(row=r, column=1, sticky="ns")
        self.rules_tree.configure(yscrollcommand=sb.set); r += 1
        bbox = ttk.Frame(f); bbox.grid(row=r, column=0, columnspan=2, sticky="we", pady=(8, 0))
        ttk.Button(bbox, text="Add rule…", command=self._add_rule).pack(side="left")
        ttk.Button(bbox, text="Remove", command=self._remove_rule).pack(side="left", padx=6)
        ttk.Button(bbox, text="Correct last dictation…", command=self._correct_last).pack(side="left")
        ttk.Button(bbox, text="Delete all", command=self._clear_rules).pack(side="right")
        self.refresh_rules()

    def refresh_rules(self):
        if not hasattr(self, "rules_tree"):
            return
        try:
            self.rules_tree.delete(*self.rules_tree.get_children())
            for r in sorted(self.app.learner.rules, key=lambda x: x.get("learned", 0), reverse=True):
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("learned", 0)))
                self.rules_tree.insert("", "end", iid=r["from"].lower(),
                                       values=(r["from"], r["to"], when, r.get("source", ""), r.get("hits", 0)))
        except tk.TclError:
            pass

    def _add_rule(self):
        """Small dialog: what FreeFlow hears -> what it should write (tkinter.simpledialog is not in the
        packaged build, so this is a plain Toplevel)."""
        win = tk.Toplevel(self)
        win.title("Add rule")
        win.transient(self)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="What FreeFlow hears (the wrong words):").grid(row=0, column=0, sticky="w")
        src_var, dst_var = tk.StringVar(), tk.StringVar()
        e1 = ttk.Entry(frm, textvariable=src_var, width=44); e1.grid(row=1, column=0, sticky="we", pady=(2, 8))
        ttk.Label(frm, text="What it should become:").grid(row=2, column=0, sticky="w")
        e2 = ttk.Entry(frm, textvariable=dst_var, width=44); e2.grid(row=3, column=0, sticky="we", pady=(2, 8))
        msg = ttk.Label(frm, text="", foreground="#888"); msg.grid(row=4, column=0, sticky="w")

        def ok(_event=None):
            src, dst = src_var.get().strip(), dst_var.get().strip()
            if not src or not dst:
                msg.configure(text="Both fields are needed")
                return
            if self.app.learner.add_rule(src, dst, "manual"):
                self.status.configure(text=f"Learned: {src} → {dst}")
            self.refresh_rules()
            win.destroy()
        b = ttk.Frame(frm); b.grid(row=5, column=0, sticky="e", pady=(6, 0))
        ttk.Button(b, text="Learn", command=ok).pack(side="left")
        ttk.Button(b, text="Cancel", command=win.destroy).pack(side="left", padx=6)
        win.bind("<Return>", ok)
        win.bind("<Escape>", lambda e: win.destroy())
        e1.focus_set()
        win.grab_set()

    def _remove_rule(self):
        sel = self.rules_tree.selection()
        if not sel:
            return
        self.app.learner.remove(sel[0])
        self.refresh_rules()

    def _clear_rules(self):
        if messagebox.askyesno("Delete all rules", "Forget everything FreeFlow has learned from corrections?", parent=self):
            self.app.learner.clear()
            self.refresh_rules()

    def _correct_last(self):
        last = getattr(self.app, "_last_dictation", None)
        text = (last or {}).get("text") or ""
        if not text:
            entries = self.app.history.entries(1)
            text = (entries[0].get("text") if entries else "") or ""
        if not text:
            self.status.configure(text="Nothing has been dictated yet")
            return
        self._correct_dialog(text, "history")

    def _correct_history(self):
        sel = self.tree.selection()
        if not sel:
            self.status.configure(text="Select an entry first")
            return
        e = self._history_entries[int(sel[0])]
        self._correct_dialog(e.get("text") or "", "history")

    def _correct_dialog(self, original: str, source: str):
        win = tk.Toplevel(self)
        win.title("Correct the text")
        win.transient(self)
        ttk.Label(win, text="Change the words that were heard wrong. FreeFlow learns from every word you change "
                            "(one to four words at a time) and applies it to future dictations.",
                  wraplength=540, justify="left").pack(padx=12, pady=(12, 6), anchor="w")
        t = tk.Text(win, width=66, height=6, wrap="word", undo=True)
        t.insert("1.0", original)
        t.pack(padx=12, fill="both", expand=True)
        res = ttk.Label(win, text="", foreground="#888", wraplength=540, justify="left")
        res.pack(padx=12, pady=4, anchor="w")

        def learn():
            edited = t.get("1.0", "end").strip()
            pairs = self.app.learner.learn(original, edited, source)
            if pairs:
                res.configure(text="Learned: " + "; ".join(f"{a} → {b}" for a, b in pairs))
                self.status.configure(text="Learned " + "; ".join(f"{a} → {b}" for a, b in pairs))
                self.refresh_rules()
                win.after(1200, win.destroy)
            else:
                res.configure(text="No word-level change found to learn. Change one to four words at a time.")
        b = ttk.Frame(win); b.pack(padx=12, pady=(0, 12), anchor="e")
        ttk.Button(b, text="Learn", command=learn).pack(side="left")
        ttk.Button(b, text="Cancel", command=win.destroy).pack(side="left", padx=6)
        t.focus_set()
        win.grab_set()

    def _build_about(self, f):
        r = 0
        ttk.Label(f, text=f"FreeFlow {APP_VERSION}", font=("Segoe UI", 14, "bold")).grid(row=r, column=0, sticky="w"); r += 1
        ttk.Label(f, text="Private voice dictation for Windows. Hold the hotkey, speak, release - the text is typed where your cursor is.",
                  wraplength=600).grid(row=r, column=0, sticky="w", pady=(2, 10)); r += 1
        self.about_engine = ttk.Label(f, text="")
        self.about_engine.grid(row=r, column=0, sticky="w"); r += 1
        ttk.Label(f, text=f"Data folder: {DATA_DIR}").grid(row=r, column=0, sticky="w", pady=(8, 0)); r += 1
        ttk.Label(f, text=f"Log file: {LOG_PATH}").grid(row=r, column=0, sticky="w"); r += 1
        box = ttk.Frame(f); box.grid(row=r, column=0, sticky="w", pady=10)
        ttk.Button(box, text="Open log", command=lambda: os.startfile(LOG_PATH) if os.path.exists(LOG_PATH) else None).pack(side="left")
        ttk.Button(box, text="Open data folder", command=self.app.open_data_folder).pack(side="left", padx=6)
        ttk.Button(box, text="Create desktop shortcut", command=self._shortcut).pack(side="left")
        r += 1
        ttk.Label(f, text="Updates", font=("Segoe UI", 11, "bold")).grid(row=r, column=0, sticky="w", pady=(8, 4)); r += 1
        from . import CODE_REVISION
        ttk.Label(f, text=f"Installed code revision: {CODE_REVISION}").grid(row=r, column=0, sticky="w"); r += 1
        ubox = ttk.Frame(f); ubox.grid(row=r, column=0, sticky="w", pady=4)
        ttk.Button(ubox, text="Check for updates now", command=lambda: self.app.check_for_updates(auto=False)).pack(side="left")
        ttk.Checkbutton(ubox, text="Check for updates at start and once a day (a restart waits until you are not using the PC)",
                        variable=self._var("auto_update", bool)).pack(side="left", padx=12)
        r += 1
        self.update_label = ttk.Label(f, text=getattr(self.app, "update_status", "") or "", foreground="#888", wraplength=640, justify="left")
        self.update_label.grid(row=r, column=0, sticky="w"); r += 1
        tips = ("Tips\n"
                "• Hold the hotkey and talk; release to insert the text.\n"
                "• Tap the hotkey briefly for hands-free mode; tap again (or press Esc) to finish.\n"
                "• Press Esc while recording to cancel.\n"
                "• Say \"new line\" or \"new paragraph\" to format.\n"
                "• If Wispr Flow is still installed, quit it - both apps use Ctrl+Win by default.")
        ttk.Label(f, text=tips, justify="left").grid(row=r, column=0, sticky="w", pady=(10, 0)); r += 1

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def _capture_hotkey(self):
        if self._capturing:
            self.app.hotkeys.cancel_capture()
            self._capturing = False
            self.capture_btn.configure(text="Record keys…")
            return
        self._capturing = True
        self.capture_btn.configure(text="Press the keys… (click to cancel)")

        def done(combo: str):
            def apply():
                self._capturing = False
                try:
                    self.capture_btn.configure(text="Record keys…")
                    self.v["hotkey"].set(combo)
                except tk.TclError:
                    pass
            self.app.ui(apply)
        self.app.hotkeys.begin_capture(done)

    def _update_model_note(self):
        self.model_note.configure(text=MODEL_NOTES.get(self.v["local_model"].get(), ""))

    def _refresh_output(self):
        info = audioctl.default_output_info()
        kind = "headphones/headset - will NOT be muted" if info["headphones"] else "speakers - will be muted"
        self.out_label.configure(text=f"Default output now: {info['name']}  [{info['form_factor_name']}]  -> {kind}")

    def _scan_mic_apps(self):
        self.mic_apps_label.configure(text=audioctl.describe_capture_sessions())

    def _check_ollama(self):
        from .llm import normalize_ollama_url
        url = normalize_ollama_url(self.v["ollama_url"].get())
        model = self.v["ollama_model"].get().strip() or LLM_MODELS[0]
        self.ollama_status.configure(text="Checking Ollama…")

        def say(text):
            def _apply():
                try:
                    self.ollama_status.configure(text=text)
                except tk.TclError:
                    pass
            self.app.ui(_apply)

        def work():
            from .llm import LocalLLM, _model_matches, find_ollama_exe, ollama_models, ollama_pull, ollama_version
            llm = LocalLLM({"ollama_url": url, "ollama_model": model})
            if not llm.ensure_server():
                if find_ollama_exe():
                    say("Ollama is installed but its server did not start. Start Ollama from the Start menu and try again.")
                else:
                    say("Ollama is not installed. Get it from ollama.com (free), then click Check again.")
                return
            if not _model_matches(model, ollama_models(url)):
                say(f"Downloading {model}…")
                try:
                    ollama_pull(url, model, progress=lambda s: say(f"Downloading {model}: {s}"))
                except Exception as e:
                    say(f"Download failed: {e}")
                    return
            installed = ollama_models(url)
            say(f"Ollama {ollama_version(url)} is running. Model {model} is ready. Installed models: {', '.join(installed) or '-'}")
            self.app.ui(lambda: self.ollama_combo.configure(values=sorted(set(LLM_MODELS) | set(installed))))
        threading.Thread(target=work, name="ollama-check", daemon=True).start()

    def refresh_update_status(self):
        try:
            self.update_label.configure(text=getattr(self.app, "update_status", "") or "")
        except (tk.TclError, AttributeError):
            pass

    def refresh_engine_status(self):
        eng = self.app.engine
        txt = "No engine" if eng is None else ("Engine: " + eng.describe())
        try:
            txt += f"   |   AI clean-up: {self.app.local_llm.status}"
        except Exception:
            pass
        for lbl in (getattr(self, "engine_status", None), getattr(self, "about_engine", None)):
            if lbl is not None:
                try:
                    lbl.configure(text=txt)
                except tk.TclError:
                    pass

    def refresh_history(self):
        if not hasattr(self, "tree"):
            return
        try:
            self._history_entries = self.app.history.entries(500)
            self.tree.delete(*self.tree.get_children())
            for i, e in enumerate(self._history_entries):
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("ts", 0)))
                text = " ".join((e.get("text") or "").split())
                self.tree.insert("", "end", iid=str(i), values=(when, f"{e.get('duration', 0):.1f}s", text[:200]))
        except tk.TclError:
            pass

    def _copy_history(self, raw: bool = False):
        sel = self.tree.selection()
        if not sel:
            return
        e = self._history_entries[int(sel[0])]
        text = e.get("raw") if raw else e.get("text")
        self.clipboard_clear()
        self.clipboard_append(text or "")
        self.status.configure(text="Copied to clipboard")

    def _clear_history(self):
        if messagebox.askyesno("Delete history", "Delete all dictation history?", parent=self):
            self.app.history.clear()
            self.refresh_history()

    def _shortcut(self):
        try:
            link = autostart.create_desktop_shortcut()
            self.status.configure(text=f"Shortcut created: {link}")
        except Exception as e:
            messagebox.showerror("Shortcut", f"Could not create the shortcut:\n{e}", parent=self)

    # ------------------------------------------------------------------
    def _collect(self) -> dict:
        data = {}
        for key, var in self.v.items():
            if key == "autostart":
                continue
            data[key] = var.get()
        for key in ("tap_threshold_ms", "beam_size", "max_record_seconds", "double_tap_ms"):
            try:
                data[key] = int(str(data.get(key, "")).strip() or 0)
            except ValueError:
                raise ValueError(f"{key} must be a whole number")
        try:
            data["sound_volume"] = min(1.0, max(0.05, float(str(data.get("sound_volume", "0.22")).strip() or 0.22)))
        except ValueError:
            raise ValueError("sound volume must be a number between 0.05 and 1")
        data["hotkey"] = data["hotkey"].strip().lower()
        data["cancel_key"] = data["cancel_key"].strip().lower()
        data["language"] = self.lang_combo.get().split(" - ")[0].strip() or "en"
        dev = self.dev_combo.get()
        data["input_device"] = "" if dev == DEFAULT_DEVICE_LABEL else dev
        data["mute_mic_apps"] = [ln.strip() for ln in self._texts["mute_mic_apps"].get("1.0", "end").splitlines() if ln.strip()]
        vocab_raw = self._texts["vocabulary"].get("1.0", "end").replace("\n", ",")
        data["vocabulary"] = [w.strip() for w in vocab_raw.split(",") if w.strip()]
        repl = {}
        for ln in self._texts["replacements"].get("1.0", "end").splitlines():
            if "=>" in ln:
                k, v = ln.split("=>", 1)
                if k.strip():
                    repl[k.strip()] = v.strip()
        data["replacements"] = repl
        data["type_method_apps"] = [a.strip() for a in data.get("type_method_apps", "").split(",") if a.strip()]
        data["smart_target_text_apps"] = [a.strip() for a in data.get("smart_target_text_apps", "").split(",") if a.strip()]
        data["chat_open_apps"] = [a.strip() for a in data.get("chat_open_apps", "").split(",") if a.strip()]
        data["chat_send_apps"] = [a.strip() for a in data.get("chat_send_apps", "").split(",") if a.strip()]
        data["game_vocabulary"] = [a.strip() for a in data.get("game_vocabulary", "").split(",") if a.strip()]
        for key in ("openai_api_key", "groq_api_key", "anthropic_api_key", "local_model", "claude_model",
                    "openai_model", "groq_model", "openai_polish_model", "ollama_model", "ollama_url"):
            data[key] = str(data.get(key, "")).strip()
        from .llm import normalize_ollama_url
        data["ollama_url"] = normalize_ollama_url(data["ollama_url"])
        data["ollama_model"] = data["ollama_model"] or LLM_MODELS[0]
        return data

    def save(self) -> bool:
        """Validate, write the config and apply it. Returns False when the input is invalid."""
        try:
            data = self._collect()
            parse_combo(data["hotkey"])
            if data["cancel_key"]:
                parse_combo(data["cancel_key"])
        except ValueError as e:
            messagebox.showerror("Settings", str(e), parent=self)
            return False
        if data.get("engine") == "openai" and not data.get("openai_api_key"):
            messagebox.showwarning("Settings", "The OpenAI engine needs an API key.", parent=self)
            return False
        if data.get("engine") == "groq" and not data.get("groq_api_key"):
            messagebox.showwarning("Settings", "The Groq engine needs an API key.", parent=self)
            return False
        if data.get("polish") == "claude" and not data.get("anthropic_api_key"):
            messagebox.showwarning("Settings", "Claude polish needs an Anthropic API key.", parent=self)
            return False
        self.cfg.update(data)
        self.cfg["autostart"] = bool(self.v["autostart"].get())
        self.cfg.save()
        self.app.apply_config()
        self.refresh_engine_status()
        self.status.configure(text=f"Saved. Hotkey: {pretty_combo(data['hotkey'], data.get('hotkey_gesture', 'hold'))}")
        return True
