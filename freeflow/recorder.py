"""Microphone capture with sounddevice (PortAudio). Produces 16 kHz mono float32."""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000


def list_input_devices() -> list[tuple[int, str]]:
    """Input devices of the default host API (MME) -> [(index, name)]."""
    devices = sd.query_devices()
    try:
        default_in = sd.default.device[0]
        hostapi = devices[default_in]["hostapi"] if default_in is not None and default_in >= 0 else 0
    except Exception:
        hostapi = 0
    out = []
    for idx, d in enumerate(devices):
        if d["max_input_channels"] > 0 and d["hostapi"] == hostapi:
            out.append((idx, d["name"]))
    return out


def default_input_name() -> str:
    try:
        idx = sd.default.device[0]
        return sd.query_devices(idx)["name"]
    except Exception:
        return "(unknown)"


def resolve_device(spec) -> Optional[int]:
    """'' / None -> default; int -> index; str -> name match (exact, prefix, then substring)."""
    if spec is None or spec == "" or spec == "default":
        return None
    if isinstance(spec, int):
        return spec
    s = str(spec).strip()
    if s.isdigit():
        return int(s)
    devs = list_input_devices()
    for idx, name in devs:
        if name.lower() == s.lower():
            return idx
    for idx, name in devs:
        if name.lower().startswith(s.lower()) or s.lower().startswith(name.lower()):
            return idx
    for idx, name in devs:
        if s.lower() in name.lower():
            return idx
    log.warning("Input device %r not found, using default", spec)
    return None


class Recorder:
    def __init__(self):
        self._stream: Optional[sd.InputStream] = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._rate = SAMPLE_RATE
        self.level = 0.0          # smoothed, auto-gained level 0..1 for the overlay
        self._floor = None
        self._peak = 0.0
        self.started_at = 0.0
        self.device_name = ""

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self, device_spec=None):
        if self._stream is not None:
            return
        idx = resolve_device(device_spec)
        self._chunks = []
        self.level = 0.0
        self._floor, self._peak = None, 0.0
        last_err = None
        for rate in (SAMPLE_RATE, None):
            try:
                if rate is None:
                    info = sd.query_devices(idx if idx is not None else sd.default.device[0])
                    rate = int(info["default_samplerate"])
                stream = sd.InputStream(device=idx, samplerate=rate, channels=1, dtype="float32",
                                        blocksize=1024, callback=self._callback)
                stream.start()
                self._stream = stream
                self._rate = rate
                break
            except Exception as e:
                last_err = e
                log.warning("Could not open input at %s Hz: %s", rate, e)
        if self._stream is None:
            raise RuntimeError(f"Could not open microphone: {last_err}")
        try:
            self.device_name = sd.query_devices(idx if idx is not None else sd.default.device[0])["name"]
        except Exception:
            self.device_name = "?"
        self.started_at = time.monotonic()
        log.info("Recording from %r at %d Hz", self.device_name, self._rate)

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.debug("audio status: %s", status)
        data = indata[:, 0].copy()
        with self._lock:
            self._chunks.append(data)
        rms = float(np.sqrt(np.mean(data * data))) if len(data) else 0.0
        # Automatic gain for the indicator: track this microphone's noise floor and its recent peak so the
        # bars swing over their full range whether the mic is loud or, like a webcam mic, very quiet.
        if self._floor is None:
            self._floor, self._peak = rms, rms
        self._floor = min(rms, self._floor * 1.03 + 2e-5)     # floor may creep up slowly, drops at once
        self._peak = max(rms, self._peak * 0.992)             # peak decays over a few seconds
        span = max(self._peak - self._floor, 0.0015)
        target = max(0.0, min(1.0, (rms - self._floor) / span)) ** 0.6
        # fast attack, quick but smooth release
        self.level = target if target > self.level else self.level * 0.55 + target * 0.45

    def duration(self) -> float:
        with self._lock:
            n = sum(len(c) for c in self._chunks)
        return n / float(self._rate)

    def stop(self) -> np.ndarray:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as e:
                log.warning("stream close failed: %s", e)
        with self._lock:
            chunks, self._chunks = self._chunks, []
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        if self._rate != SAMPLE_RATE and len(audio):
            audio = resample(audio, self._rate, SAMPLE_RATE)
        self.level = 0.0
        return audio.astype(np.float32, copy=False)

    def abort(self):
        self.stop()


def resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return audio
    n_out = int(round(len(audio) * dst_rate / src_rate))
    x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def save_wav(path: str, audio: np.ndarray, rate: int = SAMPLE_RATE):
    import wave
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
