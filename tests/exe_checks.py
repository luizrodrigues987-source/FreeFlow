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


def selftest(label: str) -> tuple:
    cfg = os.path.join(tempfile.mkdtemp(prefix="ff-exe-"), "config.json")
    json.dump({"first_run_done": True, "notifications": False, "sounds": False, "polish": "off", "auto_update": False,
               "hotkey": "ctrl+alt+shift+f9", "mute_output": False, "mute_mic_mode": "off"}, open(cfg, "w"))
    os.makedirs(DATA, exist_ok=True)
    start = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    p = subprocess.run([EXE, "--selftest", spoken_clip()], env=dict(os.environ, FREEFLOW_CONFIG=cfg),
                       cwd=os.path.dirname(EXE), timeout=300)
    with open(LOG, encoding="utf-8", errors="replace") as f:
        f.seek(start)
        seen = f.read()
    print(f"-- {label}")
    for line in seen.splitlines():
        if any(k in line for k in ("Self-test", "word timing", "Traceback", "ERROR")):
            print("  " + line[24:300])
    return p.returncode, seen


# 1) the package's own (bundled) code, with no update staged - if it is new enough to have --selftest
shutil.rmtree(UPD, ignore_errors=True)
bundled_app = open(os.path.join(ROOT, "dist", "FreeFlow", "source", "freeflow", "app.py"), encoding="utf-8").read()
if "def selftest(" in bundled_app:
    code, seen = selftest("bundled code")
    bundled_ok = code == 0 and "from bundled" in seen and "word timing failed" not in seen
else:
    print("-- bundled code: built before --selftest existed; only the update path is checked")
    bundled_ok = True
bundled = open(os.path.join(ROOT, "dist", "FreeFlow", "source", "freeflow", "__init__.py"), encoding="utf-8").read()
rev = int(re.search(r"CODE_REVISION\s*=\s*(\d+)", bundled).group(1)) + 9
shutil.rmtree(UPD, ignore_errors=True)
os.makedirs(os.path.join(UPD, "freeflow"))
for py in glob.glob(os.path.join(ROOT, "freeflow", "*.py")):
    data = open(py, encoding="utf-8").read()
    if os.path.basename(py) == "__init__.py":
        data = re.sub(r"CODE_REVISION\s*=\s*\d+", f"CODE_REVISION = {rev}", data)
    open(os.path.join(UPD, "freeflow", os.path.basename(py)), "w", encoding="utf-8", newline="\n").write(data)
# 2) the current code as a (newer) update
t0 = time.time()
code, seen = selftest("current code as an update")
staged_used = f"Self-test: code r{rev} from update folder" in seen
shutil.rmtree(UPD, ignore_errors=True)
for d in glob.glob(UPD + ".broken-*"):
    shutil.rmtree(d, ignore_errors=True)
ok = bundled_ok and code == 0 and staged_used and "word timing failed" not in seen


def stage(rev_, patch_app=None):
    shutil.rmtree(UPD, ignore_errors=True)
    os.makedirs(os.path.join(UPD, "freeflow"))
    for py in glob.glob(os.path.join(ROOT, "freeflow", "*.py")):
        text = open(py, encoding="utf-8").read()
        name = os.path.basename(py)
        if name == "__init__.py":
            text = re.sub(r"CODE_REVISION\s*=\s*\d+", f"CODE_REVISION = {rev_}", text)
        if name == "app.py" and patch_app:
            text = patch_app(text)
        open(os.path.join(UPD, "freeflow", name), "w", encoding="utf-8", newline="\n").write(text)


# 3) a package with the 2026-10-29 loader: a broken update - failing to import, or crashing right after the
#    start - must fall back to the built-in code, leave the reason in update-failure.log and be set aside
new_loader = "broken_revisions" in open(os.path.join(ROOT, "dist", "FreeFlow", "source", "freeflow", "updater.py"),
                                        encoding="utf-8").read()
if new_loader:
    fail_log = os.path.join(DATA, "update-failure.log")
    for label, patch_app in (
            ("update that does not import", lambda t: t.replace("from .tray import Tray", "from .tray import NoSuchName")),
            ("update that crashes at start", lambda t: t.replace('    """--selftest [file.wav]:',
                                                                  '    raise RuntimeError("broken on purpose")\n    """--selftest [file.wav]:', 1))):
        if os.path.exists(fail_log):
            os.remove(fail_log)
        for d in glob.glob(UPD + ".broken-*"):
            shutil.rmtree(d, ignore_errors=True)
        stage(rev + 1, patch_app)
        code3, seen3 = selftest(label)
        fell_back = (code3 == 0 and "from bundled" in seen3 and os.path.exists(fail_log)
                     and bool(glob.glob(UPD + ".broken-*")) and not os.path.exists(UPD))
        print(f"   fell back to the built-in code: {fell_back}")
        ok = ok and fell_back
    shutil.rmtree(UPD, ignore_errors=True)
    for d in glob.glob(UPD + ".broken-*"):
        shutil.rmtree(d, ignore_errors=True)
else:
    print("-- loader fallback: this package has the old loader; skipped")
print("bundled code ok: %s; update: exit code %s after %.0f s, update code used: %s" % (
    bundled_ok, code, time.time() - t0, staged_used))
print("EXE CHECKS OK" if ok else "EXE CHECKS FAILED")
sys.exit(0 if ok else 1)
