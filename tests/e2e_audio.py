"""The window-context backup with the real speech model on spoken clips (Windows' own voices, made on the
first run): which words change, whether a second listen happens, and what it costs."""
import os
import subprocess
import sys
import tempfile
import time
import wave

import numpy as np

from _util import ROOT  # noqa: F401
from freeflow import context as wincontext, transcribe  # noqa: E402
from freeflow.app import App, Job, build_whisper_prompt  # noqa: E402
from freeflow.config import DEFAULTS  # noqa: E402
from freeflow.postprocess import clean_transcript  # noqa: E402

CLIPS = {   # name: (sentence spoken, must appear in the result, second listen allowed)
    "miren": ("Hi Miren, thanks for your time today.", "Miren", True),
    "miren3": ("I just sent the proposal over to Meeren this morning.", "Miren", True),
    "kirsty": ("Hey Kirsty, this was a lead that I just activated not too long ago.", "Kirsty", True),
    "christy": ("Hey Christy, this was a lead that I just activated not too long ago.", "Christy", True),
    "update": ("We need to update the app before Friday.", "update", False),
    "resched": ("You can use the rescheduling link down below to find a new time for us.", "rescheduling", False),
    "slack": ("Anyone else getting these Slack notifications?", "lack", False),
    "pavers": ("Arthur wants the pavers installed by Friday.", "pavers", False),
    "plain": ("Some of these things are taking too long to dictate.", "dictate", False),
}
PAGE = wincontext.WindowContext(names=["Miren", "Garcia", "Kirsty", "Moreau", "Radcliffe", "Slackbot"],
                                terms=["pavers", "updater", "rescheduleid"], source="test page")
DIR = os.path.join(tempfile.gettempdir(), "freeflow-test-clips")


def make_clips():
    os.makedirs(DIR, exist_ok=True)
    todo = [(k, v[0]) for k, v in CLIPS.items() if not os.path.exists(os.path.join(DIR, k + ".wav"))]
    if not todo:
        return
    lines = ["Add-Type -AssemblyName System.Speech",
             "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, "
             "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)",
             "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer"]
    for k, sentence in todo:
        lines.append("$s.SetOutputToWaveFile('%s', $f); $s.Speak('%s')" % (os.path.join(DIR, k + ".wav"), sentence.replace("'", "''")))
    lines.append("$s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", "; ".join(lines)], check=True, timeout=120)


def load(path):
    with wave.open(path, "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0


class Fut:
    def result(self, timeout=0):
        return PAGE


class Host:          # just what App._window_backup needs
    cfg = dict(DEFAULTS)


make_clips()
engine = transcribe.LocalWhisperEngine("large-v3-turbo")
engine.load()
if engine.error:
    print("SKIPPED: speech model unavailable:", engine.error)
    sys.exit(0)
listens = {"n": 0}
real = engine.transcribe
engine.transcribe = lambda audio, language="en", prompt=None, details=None: (
    listens.__setitem__("n", listens["n"] + 1) or real(audio, language, prompt, details))
bad = []
for name, (_sentence, expect, second_ok) in CLIPS.items():
    audio = load(os.path.join(DIR, name + ".wav"))
    details = {}
    raw = engine.transcribe(audio, "en", build_whisper_prompt([], True), details)
    text = clean_transcript(raw, Host.cfg, 0.05, len(audio) / 16000)
    before = listens["n"]
    t0 = time.perf_counter()
    out = App._window_backup(Host, Job(audio, len(audio) / 16000, 0.05, "chrome.exe", "page", True,
                                       {"context": Fut()}), engine, text, [], details)
    n = listens["n"] - before
    ok = expect in out and (n == 0 or second_ok)
    if not ok:
        bad.append(name)
    print("  %-4s %-8s %d second listen (%.2f s)  %r%s" % ("ok" if ok else "FAIL", name, n, time.perf_counter() - t0,
                                                        out, "" if out == text else f"   <- heard {text!r}"))
print("AUDIO OK" if not bad else f"AUDIO FAILED: {bad}")
sys.exit(1 if bad else 0)
