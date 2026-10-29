"""The restart that installs an update: the running FreeFlow starts its successor as a child process, the
successor must close it (before the fix it skipped its own parent and stopped at "FreeFlow is already
running") and take over.  Runs a test copy with its own config; the real FreeFlow is not touched."""
import json
import os
import subprocess
import sys
import tempfile
import time

import psutil

from _util import ROOT

TMP = tempfile.mkdtemp(prefix="ff-restart-")
CFG = os.path.join(TMP, "config.json")
json.dump({"hotkey": "ctrl+alt+shift+f9", "sounds": False, "overlay": False, "notifications": False,
           "history_enabled": False, "mute_output": False, "mute_mic_mode": "off", "polish": "off",
           "first_run_done": True, "auto_update": False, "autostart": False, "engine": "groq", "groq_api_key": "",
           "window_context": False, "learning": False}, open(CFG, "w"))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PYW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")


def freeflow_procs(namespace: str) -> set:
    out = set()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or []).lower()
            if (p.info["name"] or "").lower() in ("python.exe", "pythonw.exe", "freeflow.exe") and \
                    ("freeflow.pyw" in cmd or "_old_instance.py" in cmd) and \
                    p.environ().get("FREEFLOW_CONFIG", "") == namespace:
                out.add(p.pid)
        except Exception:
            pass
    return out


live_before = freeflow_procs("")
env = dict(os.environ, FREEFLOW_CONFIG=CFG)
old = subprocess.Popen([PY, os.path.join(ROOT, "tests", "_old_instance.py"), CFG], env=env,
                       cwd=os.path.join(ROOT, "tests"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    out, _ = old.communicate(timeout=40)
except subprocess.TimeoutExpired:
    old.kill()
    out = old.communicate()[0]
print("old instance exit code:", old.returncode, "|", (out or "").strip().splitlines()[-1:] or "")
time.sleep(3.0)
new = freeflow_procs(CFG)
print("test instances running now:", new)
ok = old.returncode == 0 and "closed cleanly" in (out or "") and len(new) >= 1
# stop the successor the normal way
subprocess.run([PYW, os.path.join(ROOT, "FreeFlow.pyw"), "--quit"], env=env, timeout=60)
time.sleep(2.0)
left = freeflow_procs(CFG)
for pid in left:
    try:
        psutil.Process(pid).kill()
    except Exception:
        pass
live_after = freeflow_procs("")
print("real FreeFlow untouched:", live_before == live_after, live_before)
ok = ok and not left and live_before == live_after
print("UPDATE RESTART OK" if ok else "UPDATE RESTART FAILED")
sys.exit(0 if ok else 1)
