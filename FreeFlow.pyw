"""FreeFlow launcher. Double-click me (or use FreeFlow.bat)."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_PYW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")

# If we were started with a different Python (e.g. double-click -> Microsoft Store Python),
# re-launch with the project's own environment.  Going through explorer.exe makes the new
# process a child of the shell, not of a sandboxed Store app (whose children get a
# virtualised %APPDATA% and would keep their settings in a different place).
if os.path.exists(VENV_PYW) and os.path.normcase(sys.prefix) != os.path.normcase(os.path.join(ROOT, ".venv")):
    vbs = os.path.join(ROOT, "FreeFlow.vbs")   # windowless launcher
    try:
        if os.path.exists(vbs) and not sys.argv[1:]:
            subprocess.Popen(["explorer.exe", vbs])
        else:
            subprocess.Popen([VENV_PYW, os.path.abspath(__file__)] + sys.argv[1:], cwd=ROOT)
    except Exception:
        subprocess.Popen([VENV_PYW, os.path.abspath(__file__)] + sys.argv[1:], cwd=ROOT)
    sys.exit(0)

sys.path.insert(0, ROOT)
from freeflow.app import main  # noqa: E402

main()
