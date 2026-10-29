"""Helper for e2e_update_restart.py: a FreeFlow that restarts itself "for an update" once it is up.
argv[1] = the test config path (set as FREEFLOW_CONFIG before freeflow is imported)."""
import os
import sys
import threading
import time

os.environ["FREEFLOW_CONFIG"] = sys.argv[1]
from _util import ROOT  # noqa: E402,F401
from freeflow import app as appmod, updater  # noqa: E402


def later(a):
    time.sleep(3.0)
    if a.cfg.get("hotkey") != "ctrl+alt+shift+f9":
        os._exit(3)                                   # never with the real config
    updater.relaunch(["--restart", "--updated", "4242"])


app = appmod.App()
threading.Thread(target=later, args=(app,), daemon=True).start()
app.run()
print("old instance closed cleanly")
