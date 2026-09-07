"""Configuration storage for FreeFlow (JSON file in %APPDATA%/FreeFlow)."""
from __future__ import annotations

import copy
import json
import logging
import os
import threading

from . import APP_NAME

log = logging.getLogger(__name__)

APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~")
DATA_DIR = os.path.join(APPDATA, APP_NAME)
CONFIG_PATH = os.environ.get("FREEFLOW_CONFIG") or os.path.join(DATA_DIR, "config.json")
MUTE_STATE_PATH = os.path.join(DATA_DIR, "mute_state.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")
LOG_PATH = os.path.join(DATA_DIR, "freeflow.log")
LAST_RECORDING_PATH = os.path.join(DATA_DIR, "last_recording.wav")

DEFAULTS: dict = {
    # --- Hotkey -----------------------------------------------------------
    "hotkey": "ctrl+win",            # e.g. "ctrl+win", "ctrl+shift+space", "f8", "rctrl", "ctrl"
    "hotkey_gesture": "both",        # both (hold to talk + double-tap for hands-free) | hold | double_tap
    "double_tap_ms": 450,            # max gap between the two taps
    "tap_max_hold_ms": 300,          # the first tap must be shorter than this
    "hotkey_mode": "push_to_talk",   # push_to_talk | toggle
    "tap_to_toggle": True,           # quick tap of the push-to-talk key = hands-free mode
    "tap_threshold_ms": 350,
    "cancel_key": "esc",             # press while recording to discard
    "cancel_on_other_key": True,     # PTT: pressing another key while holding the hotkey cancels
    # --- Transcription ----------------------------------------------------
    "language": "en",                # ISO code or "auto"
    "engine": "local",               # local | openai | groq
    "local_model": "large-v3-turbo", # tiny.en, base.en, small.en, medium.en, distil-large-v3, large-v3-turbo, large-v3
    "local_device": "auto",          # auto | cuda | cpu
    "local_compute_type": "auto",    # auto | float16 | int8 | int8_float16 | float32
    "beam_size": 5,
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini-transcribe",
    "groq_api_key": "",
    "groq_model": "whisper-large-v3-turbo",
    "vocabulary": [],                # words / names that should be recognised correctly
    # --- Audio ------------------------------------------------------------
    "input_device": "",              # "" = system default, else device name (substring) or index
    "min_audio_seconds": 0.35,
    "max_record_seconds": 300,
    "save_last_recording": False,
    # --- Speaker muting ---------------------------------------------------
    "mute_output": True,
    "mute_output_skip_headphones": True,   # do not mute if the default output looks like headphones/headset
    "mute_output_only_if_playing": False,  # only mute if something is audibly playing right now
    # --- Per-app microphone muting ----------------------------------------
    "mute_mic_mode": "list",         # list | all | off
    "mute_mic_apps": ["Discord.exe"],
    # --- Output / injection -----------------------------------------------
    "inject_method": "paste",        # paste | type
    "restore_clipboard": True,
    "clipboard_restore_delay_ms": 500,
    "type_chunk_delay_ms": 0,
    "type_method_apps": [],          # exe names that should always use keystroke typing
    "chat_open_apps": ["League of Legends.exe"],   # games: press Enter to open the chat before inserting
    "chat_send_apps": [],            # games: press Enter after inserting to send the message
    "chat_insert": "type",           # how the text gets into a game's chat: type (Unicode characters, never
                                     # triggers abilities) | paste (slow Ctrl+V chord) | keys (real keystrokes)
    "chat_open_delay_ms": 200,       # time for the chat box to open after the Enter
    "smart_target": True,            # insert into the window you started in; fall back to the last text box
    "smart_target_text_apps": [],    # extra exe names that always count as having a text box
    "append_space": True,
    # --- Formatting -------------------------------------------------------
    "remove_fillers": True,
    "voice_commands": True,          # "new line" / "new paragraph" / "bullet point"
    "auto_lists": True,              # "one ..., two ..., three ..." becomes a numbered list
    "capitalize_first": True,
    "replacements": {},              # spoken phrase -> replacement text
    "polish": "auto",                # auto (local when available) | off | local | claude | openai
    "polish_style": "clean",         # clean | formal | casual
    "ollama_url": "http://127.0.0.1:11434",   # 127.0.0.1, not localhost: IPv6 fallback costs 2 s per request
    "ollama_model": "qwen2.5:3b",
    "ollama_keep_alive": "15m",      # how long the text model stays loaded in VRAM after a dictation
    "polish_timeout": 25,
    "whisper_punctuation_prompt": True,   # nudge Whisper to produce punctuation
    "anthropic_api_key": "",
    "claude_model": "claude-opus-5",
    "openai_polish_model": "gpt-4o-mini",
    # --- UI ---------------------------------------------------------------
    "sounds": True,
    "sound_volume": 0.16,            # 0.05 .. 1.0
    "overlay": True,
    "overlay_mode": "island",        # island (always visible, expands while dictating) | popup (only while dictating)
    "overlay_position": "bottom",    # bottom | top
    "notifications": True,
    "history_enabled": True,
    "history_max": 1000,
    "autostart": False,
    "first_run_done": False,
    # --- Updates -----------------------------------------------------------
    "auto_update": True,             # look for a newer code update on GitHub when FreeFlow starts
    "update_url": "https://github.com/luizrodrigues987-source/FreeFlow/releases/latest/download/FreeFlow-update-latest.zip",
    "update_page": "https://github.com/luizrodrigues987-source/FreeFlow/releases/latest",
}


class Config:
    """Thread-safe dict-like config with defaults, persisted as JSON."""

    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    # dict-like ------------------------------------------------------------
    def __getitem__(self, key):
        with self._lock:
            return self.data[key]

    def __setitem__(self, key, value):
        with self._lock:
            self.data[key] = value

    def get(self, key, default=None):
        with self._lock:
            return self.data.get(key, default)

    def update(self, values: dict):
        with self._lock:
            self.data.update(values)

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.data)

    # persistence ----------------------------------------------------------
    def load(self):
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                if isinstance(stored, dict):
                    for k, v in stored.items():
                        self.data[k] = v
                log.info("Config loaded from %s", self.path)
            except FileNotFoundError:
                log.info("No config file yet; using defaults")
            except Exception as e:  # corrupt file - keep defaults, keep a backup
                log.error("Could not read config (%s); using defaults", e)
                try:
                    os.replace(self.path, self.path + ".broken")
                except OSError:
                    pass

    def save(self):
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
            log.info("Config saved")
