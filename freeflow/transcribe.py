"""Speech-to-text engines: local faster-whisper (default), OpenAI and Groq (cloud)."""
from __future__ import annotations

import glob
import io
import logging
import os
import sys
import threading
import time
import wave
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

LOCAL_MODELS = ["tiny.en", "base.en", "small.en", "medium.en", "distil-large-v3", "large-v3-turbo", "large-v3"]
MODEL_NOTES = {
    "tiny.en": "fastest, lowest accuracy (75 MB)",
    "base.en": "fast, basic accuracy (150 MB)",
    "small.en": "good balance on CPU (480 MB)",
    "medium.en": "accurate, slow on CPU (1.5 GB)",
    "distil-large-v3": "accurate + fast, English only (1.5 GB)",
    "large-v3-turbo": "best choice on a GPU (1.6 GB)",
    "large-v3": "most accurate, slowest (3 GB)",
}

_cuda_dirs_added = False


def add_cuda_dll_dirs():
    """Make the pip-installed NVIDIA cuBLAS / cuDNN DLLs loadable by CTranslate2."""
    global _cuda_dirs_added
    if _cuda_dirs_added:
        return
    _cuda_dirs_added = True
    seen = set()
    roots = list(sys.path) + [os.path.join(sys.prefix, "Lib", "site-packages")]
    if getattr(sys, "frozen", False):   # packaged build: the DLLs live next to the app's _internal files
        roots = [getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable)] + roots
    for p in roots:
        if not p or not os.path.isdir(p):
            continue
        for d in glob.glob(os.path.join(p, "nvidia", "*", "bin")):
            d = os.path.normpath(d)
            if d in seen:
                continue
            seen.add(d)
            try:
                os.add_dll_directory(d)
            except Exception:
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def audio_to_wav_bytes(audio: np.ndarray, rate: int = 16000) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class EngineError(Exception):
    pass


class Engine:
    name = "engine"

    def __init__(self):
        self.ready = threading.Event()
        self.error: Optional[str] = None

    def load(self):
        self.ready.set()

    def describe(self) -> str:
        return self.name

    def transcribe(self, audio: np.ndarray, language: str = "en", prompt: Optional[str] = None) -> str:
        raise NotImplementedError


class LocalWhisperEngine(Engine):
    name = "local"

    def __init__(self, model_name: str = "large-v3-turbo", device: str = "auto",
                 compute_type: str = "auto", beam_size: int = 5):
        super().__init__()
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.beam_size = max(1, int(beam_size or 5))
        self._model = None
        self.device_used = ""
        self.compute_used = ""
        self._lock = threading.Lock()

    def describe(self) -> str:
        if self.error:
            return f"local {self.model_name}: ERROR {self.error}"
        if not self.ready.is_set():
            return f"local {self.model_name}: loading..."
        return f"local {self.model_name} on {self.device_used} ({self.compute_used})"

    def load(self):
        try:
            add_cuda_dll_dirs()
            import ctranslate2
            from faster_whisper import WhisperModel

            device = self.device
            if device not in ("cuda", "cpu"):
                try:
                    device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                except Exception:
                    device = "cpu"
            compute = self.compute_type
            if compute in ("", "auto"):
                compute = "float16" if device == "cuda" else "int8"
            t0 = time.time()
            log.info("Loading Whisper model %s on %s (%s)...", self.model_name, device, compute)
            try:
                model = WhisperModel(self.model_name, device=device, compute_type=compute)
            except Exception as e:
                if device == "cuda":
                    log.warning("CUDA load failed (%s); falling back to CPU", e)
                    device, compute = "cpu", "int8"
                    model = WhisperModel(self.model_name, device=device, compute_type=compute)
                else:
                    raise
            # warm-up so the first real dictation is fast
            try:
                segs, _ = model.transcribe(np.zeros(16000, dtype=np.float32), beam_size=1,
                                           language=None if self._auto_language() else "en", vad_filter=False)
                list(segs)
            except Exception as e:
                log.debug("warm-up failed: %s", e)
            with self._lock:
                self._model = model
            self.device_used, self.compute_used = device, compute
            log.info("Model ready in %.1fs (%s, %s)", time.time() - t0, device, compute)
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            log.exception("Model load failed")
        finally:
            self.ready.set()

    def _auto_language(self) -> bool:
        return not self.model_name.endswith(".en")

    def transcribe(self, audio: np.ndarray, language: str = "en", prompt: Optional[str] = None) -> str:
        self.ready.wait()
        if self.error or self._model is None:
            raise EngineError(self.error or "model not loaded")
        lang = None if language in ("", "auto") else language
        if self.model_name.endswith(".en"):
            lang = "en"
        with self._lock:
            segments, info = self._model.transcribe(
                audio, language=lang, beam_size=self.beam_size, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 300},
                initial_prompt=prompt or None, condition_on_previous_text=False,
            )
            parts = [s.text.strip() for s in segments]
        text = " ".join(p for p in parts if p)
        return text.strip()


class HttpWhisperEngine(Engine):
    """OpenAI-compatible /audio/transcriptions endpoint (OpenAI, Groq)."""

    def __init__(self, name: str, url: str, api_key: str, model: str):
        super().__init__()
        self.name = name
        self.url = url
        self.api_key = api_key
        self.model = model

    def describe(self) -> str:
        return f"{self.name} {self.model}"

    def transcribe(self, audio: np.ndarray, language: str = "en", prompt: Optional[str] = None) -> str:
        import requests
        if not self.api_key:
            raise EngineError(f"No API key configured for {self.name}")
        data = {"model": self.model, "response_format": "json"}
        if language and language != "auto":
            data["language"] = language
        if prompt:
            data["prompt"] = prompt
        try:
            r = requests.post(self.url, headers={"Authorization": f"Bearer {self.api_key}"},
                              files={"file": ("audio.wav", audio_to_wav_bytes(audio), "audio/wav")},
                              data=data, timeout=60)
        except requests.RequestException as e:
            raise EngineError(f"{self.name}: network error ({e})")
        if r.status_code != 200:
            raise EngineError(f"{self.name} API error {r.status_code}: {r.text[:200]}")
        return (r.json().get("text") or "").strip()


def create_engine(cfg) -> Engine:
    kind = cfg.get("engine", "local")
    if kind == "openai":
        return HttpWhisperEngine("OpenAI", "https://api.openai.com/v1/audio/transcriptions",
                                 cfg.get("openai_api_key", ""), cfg.get("openai_model") or "gpt-4o-mini-transcribe")
    if kind == "groq":
        return HttpWhisperEngine("Groq", "https://api.groq.com/openai/v1/audio/transcriptions",
                                 cfg.get("groq_api_key", ""), cfg.get("groq_model") or "whisper-large-v3-turbo")
    return LocalWhisperEngine(cfg.get("local_model") or "large-v3-turbo", cfg.get("local_device") or "auto",
                              cfg.get("local_compute_type") or "auto", cfg.get("beam_size") or 5)


def engine_signature(cfg) -> tuple:
    """Settings that require re-creating the engine when they change."""
    return (cfg.get("engine"), cfg.get("local_model"), cfg.get("local_device"), cfg.get("local_compute_type"),
            cfg.get("beam_size"), cfg.get("openai_api_key"), cfg.get("openai_model"),
            cfg.get("groq_api_key"), cfg.get("groq_model"))
