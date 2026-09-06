"""Speaker muting and per-application microphone muting (Windows Core Audio via pycaw).

Everything here must be called from a thread that has initialised COM; every
public function calls co_init() to make that painless.  Whatever we mute is
also written to a small state file so that a crash never leaves the speakers
or somebody's Discord microphone muted: restore_after_crash() is run at
start-up.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

import comtypes
import psutil
from pycaw.constants import CLSID_MMDeviceEnumerator
from pycaw.pycaw import (
    DEVICE_STATE,
    AudioUtilities,
    EDataFlow,
    ERole,
    IAudioEndpointVolume,
    IAudioMeterInformation,
    IAudioSessionControl2,
    IAudioSessionManager2,
    IMMDeviceEnumerator,
    ISimpleAudioVolume,
)

from .config import MUTE_STATE_PATH

log = logging.getLogger(__name__)

FORM_FACTOR_KEY = "{1DA5D803-D492-4EDD-8C23-E0C0FFEE7F0E} 0"
FORM_FACTORS = {0: "Remote network device", 1: "Speakers", 2: "Line level", 3: "Headphones",
                4: "Microphone", 5: "Headset", 6: "Handset", 7: "Digital passthrough",
                8: "SPDIF", 9: "HDMI / display audio", 10: "Unknown"}
HEADPHONE_FORM_FACTORS = {3, 5, 6}
HEADPHONE_WORDS = ("headphone", "headset", "earphone", "earbud", "buds", "arctis", "hyperx",
                   "airpods", "wh-1000", "wf-1000", "jabra", "plantronics", "beats", "cloud ii",
                   "cloud iii", "blackshark", "kraken", "void ", "virtuoso", "hs70", "hs80", "g733",
                   "g735", "g935", "g pro x", "a50", "a40", "nari", "barracuda")

_state_lock = threading.Lock()


def co_init():
    try:
        comtypes.CoInitialize()
    except Exception:
        pass


def _enumerator():
    return comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)


def _default_render_device():
    return _enumerator().GetDefaultAudioEndpoint(EDataFlow.eRender.value, ERole.eMultimedia.value)


def _normalize_exe(name: str) -> str:
    name = (name or "").strip().lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def looks_like_headphones(name: str, form_factor: Optional[int]) -> bool:
    if form_factor in HEADPHONE_FORM_FACTORS:
        return True
    n = (name or "").lower()
    return any(w in n for w in HEADPHONE_WORDS)


# --------------------------------------------------------------------------
# state file (crash safety)
# --------------------------------------------------------------------------
def _read_state() -> dict:
    try:
        with open(MUTE_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write_state(update: dict):
    with _state_lock:
        state = _read_state()
        state.update(update)
        state = {k: v for k, v in state.items() if v}
        try:
            if state:
                os.makedirs(os.path.dirname(MUTE_STATE_PATH), exist_ok=True)
                with open(MUTE_STATE_PATH, "w", encoding="utf-8") as f:
                    json.dump(state, f)
            elif os.path.exists(MUTE_STATE_PATH):
                os.remove(MUTE_STATE_PATH)
        except Exception as e:
            log.warning("Could not write mute state: %s", e)


def restore_after_crash() -> list[str]:
    """Undo mutes recorded in the state file (from a previous crashed run)."""
    state = _read_state()
    if not state:
        return []
    co_init()
    done = []
    dev_id = state.get("output_device_id")
    if dev_id:
        try:
            dev = _enumerator().GetDevice(dev_id)
            vol = dev.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)
            if vol.GetMute():
                vol.SetMute(0, None)
                done.append("speakers")
        except Exception as e:
            log.warning("Crash-restore of speakers failed: %s", e)
    for entry in state.get("mic_sessions") or []:
        try:
            for s in capture_sessions():
                if s["device_id"] == entry.get("device_id") and _normalize_exe(s["process"]) == _normalize_exe(entry.get("process", "")):
                    if s["volume"].GetMute():
                        s["volume"].SetMute(0, None)
                        done.append(f"mic:{s['process']}")
        except Exception as e:
            log.warning("Crash-restore of mic session failed: %s", e)
    _write_state({"output_device_id": None, "mic_sessions": None})
    if done:
        log.warning("Restored mutes left over from a previous run: %s", done)
    return done


# --------------------------------------------------------------------------
# Output (speakers)
# --------------------------------------------------------------------------
def default_output_info() -> dict:
    """Name / form factor / headphones-guess / mute state of the default output device."""
    co_init()
    try:
        dev = _default_render_device()
        ad = AudioUtilities.CreateDevice(dev)
        name = ad.FriendlyName or "?"
        ff = ad.properties.get(FORM_FACTOR_KEY)
        ff = int(ff) if isinstance(ff, (int, float)) else None
        muted = bool(ad.EndpointVolume.GetMute())
        return {"name": name, "form_factor": ff, "form_factor_name": FORM_FACTORS.get(ff, "?"),
                "headphones": looks_like_headphones(name, ff), "muted": muted, "id": ad.id}
    except Exception as e:
        return {"name": f"(error: {e})", "form_factor": None, "form_factor_name": "?", "headphones": False,
                "muted": False, "id": ""}


def output_is_playing(dev=None, threshold: float = 0.005, samples: int = 4) -> bool:
    co_init()
    try:
        dev = dev or _default_render_device()
        meter = dev.Activate(IAudioMeterInformation._iid_, comtypes.CLSCTX_ALL, None).QueryInterface(IAudioMeterInformation)
        peak = 0.0
        for _ in range(samples):
            peak = max(peak, float(meter.GetPeakValue()))
            time.sleep(0.02)
        return peak > threshold
    except Exception as e:
        log.debug("meter failed: %s", e)
        return True


class OutputMuter:
    """Mutes the default playback device for the duration of a dictation."""

    def __init__(self):
        self._vol = None
        self._device_id = ""
        self.muted_by_us = False
        self.last_reason = ""

    def mute(self, skip_headphones: bool = True, only_if_playing: bool = False) -> bool:
        co_init()
        if self.muted_by_us:
            return True
        try:
            dev = _default_render_device()
            ad = AudioUtilities.CreateDevice(dev)
            name = ad.FriendlyName or "?"
            ff = ad.properties.get(FORM_FACTOR_KEY)
            ff = int(ff) if isinstance(ff, (int, float)) else None
        except Exception as e:
            self.last_reason = f"no default output device ({e})"
            log.warning("Speaker mute skipped: %s", self.last_reason)
            return False
        if skip_headphones and looks_like_headphones(name, ff):
            self.last_reason = f"'{name}' looks like headphones"
            log.info("Speaker mute skipped: %s", self.last_reason)
            return False
        if only_if_playing and not output_is_playing(dev):
            self.last_reason = "nothing is playing"
            log.info("Speaker mute skipped: %s", self.last_reason)
            return False
        try:
            vol = ad.EndpointVolume
            if vol.GetMute():
                self.last_reason = "already muted"
                return False
            vol.SetMute(1, None)
        except Exception as e:
            self.last_reason = f"mute failed ({e})"
            log.warning("Speaker mute failed: %s", e)
            return False
        self._vol = vol
        self._device_id = ad.id
        self.muted_by_us = True
        self.last_reason = f"muted '{name}'"
        _write_state({"output_device_id": ad.id})
        log.info("Speakers muted: %s", name)
        return True

    def restore(self):
        if not self.muted_by_us:
            return
        co_init()
        ok = False
        try:
            self._vol.SetMute(0, None)
            ok = True
        except Exception as e:
            log.warning("Speaker unmute via cached interface failed (%s); retrying", e)
            try:
                dev = _enumerator().GetDevice(self._device_id)
                vol = dev.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)
                vol.SetMute(0, None)
                ok = True
            except Exception as e2:
                log.error("Speaker unmute failed: %s", e2)
        self.muted_by_us = False
        self._vol = None
        if ok:
            _write_state({"output_device_id": None})
            log.info("Speakers unmuted")


# --------------------------------------------------------------------------
# Capture sessions (per-app microphone)
# --------------------------------------------------------------------------
def capture_sessions() -> list[dict]:
    """All audio sessions on all active capture (microphone) devices."""
    co_init()
    out = []
    enum = _enumerator()
    collection = enum.EnumAudioEndpoints(EDataFlow.eCapture.value, DEVICE_STATE.ACTIVE.value)
    for i in range(collection.GetCount()):
        dev = collection.Item(i)
        try:
            dev_id = dev.GetId()
        except Exception:
            dev_id = f"#{i}"
        dev_name = ""
        try:
            dev_name = AudioUtilities.CreateDevice(dev).FriendlyName or ""
        except Exception:
            pass
        try:
            mgr = dev.Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None).QueryInterface(IAudioSessionManager2)
            sess_enum = mgr.GetSessionEnumerator()
            count = sess_enum.GetCount()
        except Exception as e:
            log.debug("session manager failed for %s: %s", dev_name, e)
            continue
        for j in range(count):
            try:
                ctl = sess_enum.GetSession(j)
                ctl2 = ctl.QueryInterface(IAudioSessionControl2)
                pid = int(ctl2.GetProcessId())
                if pid == 0:
                    continue
                try:
                    state = int(ctl2.GetState())
                except Exception:
                    state = -1
                if state == 2:  # expired
                    continue
                try:
                    process = psutil.Process(pid).name()
                except Exception:
                    process = f"pid {pid}"
                vol = ctl2.QueryInterface(ISimpleAudioVolume)
                out.append({"device_id": dev_id, "device": dev_name, "pid": pid, "process": process,
                            "state": {0: "inactive", 1: "active"}.get(state, "?"),
                            "muted": bool(vol.GetMute()), "volume": vol})
            except Exception as e:
                log.debug("session %d on %s failed: %s", j, dev_name, e)
    return out


class MicSessionMuter:
    """Mutes the microphone *for specific applications* (e.g. Discord) while we dictate."""

    def __init__(self):
        self._muted: list[dict] = []

    def mute(self, mode: str, apps: list[str]) -> list[str]:
        """mode: 'list' (only the given exe names), 'all' (every app except us), 'off'."""
        if mode not in ("list", "all"):
            return []
        co_init()
        wanted = {_normalize_exe(a) for a in apps if a.strip()}
        my_pid = os.getpid()
        names = []
        try:
            sessions = capture_sessions()
        except Exception as e:
            log.warning("Could not enumerate microphone sessions: %s", e)
            return []
        for s in sessions:
            if s["pid"] == my_pid:
                continue
            if mode == "list" and _normalize_exe(s["process"]) not in wanted:
                continue
            if s["muted"]:
                continue  # muted by the user already - leave it alone
            try:
                s["volume"].SetMute(1, None)
            except Exception as e:
                log.warning("Could not mute mic for %s: %s", s["process"], e)
                continue
            self._muted.append(s)
            names.append(s["process"])
        if self._muted:
            _write_state({"mic_sessions": [{"device_id": m["device_id"], "process": m["process"]} for m in self._muted]})
            log.info("Microphone muted for: %s", ", ".join(f"{m['process']} on {m['device']}" for m in self._muted))
        return names

    def restore(self):
        if not self._muted:
            return
        co_init()
        for m in self._muted:
            try:
                m["volume"].SetMute(0, None)
                continue
            except Exception as e:
                log.warning("Unmute via cached session failed for %s (%s); re-scanning", m["process"], e)
            try:
                for s in capture_sessions():
                    if s["device_id"] == m["device_id"] and s["pid"] == m["pid"] and s["muted"]:
                        s["volume"].SetMute(0, None)
            except Exception as e2:
                log.error("Could not unmute mic for %s: %s", m["process"], e2)
        log.info("Microphone unmuted for: %s", ", ".join(m["process"] for m in self._muted))
        self._muted = []
        _write_state({"mic_sessions": None})


def describe_capture_sessions() -> str:
    """Human readable list for the settings window."""
    try:
        sessions = capture_sessions()
    except Exception as e:
        return f"Could not list microphone sessions: {e}"
    if not sessions:
        return "No application is using a microphone right now."
    lines = []
    for s in sessions:
        lines.append(f"{s['process']}  (pid {s['pid']}, {s['state']}{', muted' if s['muted'] else ''})  on  {s['device']}")
    return "\n".join(lines)
