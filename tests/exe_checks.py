"""Before an update is published: does the PACKAGED FreeFlow.exe (dist/FreeFlow) run the current code?

The exe only contains the Python modules bundled when it was built; code that imports anything else
fails on friends' PCs.  This stages the current freeflow/*.py as an update (newer revision) and runs
FreeFlow.exe --selftest on a spoken clip: the loader must pick the update, every module must import and
the speech engine must transcribe with word probabilities.

Deliberately NOT "FreeFlow.exe --update": the bundled (old) code behind that closes every running FreeFlow,
including the real one.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from _util import ROOT

EXE = os.path.join(ROOT, "dist", "FreeFlow", "FreeFlow.exe")
DATA = os.path.join(os.environ["APPDATA"], "FreeFlow")
LOG = os.path.join(DATA, "freeflow.log")
UPD = os.path.join(DATA, "update")


def spoken_clip() -> str:
    """A 16 kHz mono WAV of a short sentence (Windows' own speech synthesis)."""
    wav = os.path.join(tempfile.gettempdir(), "freeflow-selftest.wav")
    if not os.path.exists(wav):
        ps = ("Add-Type -AssemblyName System.Speech; $f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
              "16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono);"
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.SetOutputToWaveFile('%s', $f);"
              "$s.Speak('Hi Myron, thanks for your time today.'); $s.Dispose()" % wav.replace("'", "''"))
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=60)
    return wav


if not os.path.exists(EXE):
    print("SKIPPED: no packaged build in dist/FreeFlow (python build_exe.py)")
    sys.exit(0)
bundled = open(os.path.join(ROOT, "dist", "FreeFlow", "source", "freeflow", "__init__.py"), encoding="utf-8").read()
rev = int(re.search(r"CODE_REVISION\s*=\s*(\d+)", bundled).group(1)) + 9
shutil.rmtree(UPD, ignore_errors=True)
os.makedirs(os.path.join(UPD, "freeflow"))
for py in glob.glob(os.path.join(ROOT, "freeflow", "*.py")):
    data = open(py, encoding="utf-8").read()
    if os.path.basename(py) == "__init__.py":
        data = re.sub(r"CODE_REVISION\s*=\s*\d+", f"CODE_REVISION = {rev}", data)
    open(os.path.join(UPD, "freeflow", os.path.basename(py)), "w", encoding="utf-8", newline="\n").write(data)
cfg = os.path.join(tempfile.mkdtemp(prefix="ff-exe-"), "config.json")
json.dump({"first_run_done": True, "notifications": False, "sounds": False, "polish": "off", "auto_update": False,
           "hotkey": "ctrl+alt+shift+f9", "mute_output": False, "mute_mic_mode": "off"}, open(cfg, "w"))
os.makedirs(DATA, exist_ok=True)
start = os.path.getsize(LOG) if os.path.exists(LOG) else 0
t0 = time.time()
p = subprocess.run([EXE, "--selftest", spoken_clip()], env=dict(os.environ, FREEFLOW_CONFIG=cfg),
                   cwd=os.path.dirname(EXE), timeout=300)
with open(LOG, encoding="utf-8", errors="replace") as f:
    f.seek(start)
    seen = f.read()
for line in seen.splitlines():
    if any(k in line for k in ("starting", "Self-test", "Model ready", "word timing", "Traceback", "ERROR")):
        print("  " + line[24:400])
staged_used = f"Self-test: code r{rev} from update folder" in seen
shutil.rmtree(UPD, ignore_errors=True)
for d in glob.glob(UPD + ".broken-*"):
    shutil.rmtree(d, ignore_errors=True)
ok = p.returncode == 0 and staged_used and "Self-test:" in seen and "word timing failed" not in seen
print("exit code %s after %.0f s; update code used: %s" % (p.returncode, time.time() - t0, staged_used))
print("EXE CHECKS OK" if ok else "EXE CHECKS FAILED")
sys.exit(0 if ok else 1)
