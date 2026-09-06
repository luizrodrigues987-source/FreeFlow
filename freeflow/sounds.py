"""Soft, bell-like start/stop cue sounds, rendered once to small WAV files.

winsound cannot play asynchronously from memory, so the tones are written to
%APPDATA%/FreeFlow/sounds/ the first time they are needed.
"""
from __future__ import annotations

import logging
import os
import wave
import winsound

import numpy as np

from .config import DATA_DIR

log = logging.getLogger(__name__)

RATE = 44100
VERSION = 4
# (frequency Hz, start offset s, duration s) - soft two-note chimes, a perfect fourth apart
SOUNDS = {
    "start": [(392.00, 0.00, 0.26), (523.25, 0.10, 0.30)],   # G4 -> C5, rising
    "stop": [(523.25, 0.00, 0.26), (392.00, 0.10, 0.32)],    # C5 -> G4, falling
    "cancel": [(349.23, 0.00, 0.28)],                        # F4
    "error": [(311.13, 0.00, 0.20), (261.63, 0.12, 0.30)],   # Eb4 -> C4
}
SOUND_DIR = os.path.join(DATA_DIR, "sounds")
_paths: dict[tuple, str] = {}
DEFAULT_VOLUME = 0.16
_volume = DEFAULT_VOLUME


def set_volume(volume: float):
    global _volume
    try:
        _volume = min(1.0, max(0.02, float(volume)))
    except (TypeError, ValueError):
        _volume = DEFAULT_VOLUME


def duration(kind: str) -> float:
    steps = SOUNDS[kind]
    return max(off + dur for _, off, dur in steps) + 0.02


def _render(steps, volume: float) -> np.ndarray:
    total = max(off + dur for _, off, dur in steps) + 0.02
    out = np.zeros(int(RATE * total))
    for freq, off, dur in steps:
        n = int(RATE * dur)
        t = np.arange(n) / RATE
        attack = 0.022
        env = np.where(t < attack,
                       0.5 - 0.5 * np.cos(np.pi * np.minimum(t, attack) / attack),   # rounded onset
                       np.exp(-(t - attack) / (dur * 0.45)))                          # long, soft decay
        env = env * np.clip((dur - t) / 0.045, 0.0, 1.0)                             # gentle fade, no click
        # mostly fundamental, a warm sub-octave, and just a hint of overtones that die away quickly
        tone = (np.sin(2 * np.pi * freq * t)
                + 0.28 * np.sin(2 * np.pi * 0.5 * freq * t)
                + 0.10 * np.sin(2 * np.pi * 2 * freq * t) * np.exp(-t / 0.08)
                + 0.02 * np.sin(2 * np.pi * 3 * freq * t) * np.exp(-t / 0.04))
        i0 = int(RATE * off)
        out[i0:i0 + n] += tone * env
    peak = float(np.max(np.abs(out))) or 1.0
    return out / peak * volume


def sound_path(kind: str) -> str:
    key = (kind, round(_volume, 2))
    path = _paths.get(key)
    if path is None:
        path = os.path.join(SOUND_DIR, f"{kind}_v{VERSION}_{int(round(_volume * 100)):03d}.wav")
        if not os.path.exists(path):
            os.makedirs(SOUND_DIR, exist_ok=True)
            pcm = (_render(SOUNDS[kind], _volume) * 32767).astype("<i2")
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(pcm.tobytes())
        _paths[key] = path
    return path


def play(kind: str) -> bool:
    try:
        path = sound_path(kind)
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return True
    except Exception as e:
        log.debug("sound failed: %s", e)
        return False
