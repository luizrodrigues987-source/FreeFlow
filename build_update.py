"""Create a small code-only update package for people who installed the packaged FreeFlow.

Result: dist/FreeFlow-update-r<revision>.zip (about 100 KB, also copied to the Desktop) containing
the freeflow/*.py code, "Update FreeFlow.bat" and a short read-me.  The recipient unzips it and
double-clicks the .bat: the code is copied to %APPDATA%\\FreeFlow\\update and FreeFlow restarts.
FreeFlow.exe uses that copy whenever its revision is newer than the bundled code.

Run with the project's venv:  .venv\\Scripts\\python.exe build_update.py
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
INIT = os.path.join(ROOT, "freeflow", "__init__.py")

UPDATER_BAT = r'''@echo off
setlocal
title FreeFlow update
echo.
echo  FreeFlow update
echo  ---------------
set "SRC=%~dp0freeflow"
if not exist "%SRC%\app.py" (
  echo  The "freeflow" folder is missing next to this file. Unzip the whole update first.
  pause & exit /b 1
)
rem A source installation (FreeFlow.pyw next to this file)? Update it in place.
if exist "%~dp0FreeFlow.pyw" (
  set "DEST=%~dp0freeflow"
) else (
  set "DEST=%APPDATA%\FreeFlow\update\freeflow"
)
if not exist "%DEST%" mkdir "%DEST%"
copy /y "%SRC%\*.py" "%DEST%\" >nul
if errorlevel 1 (
  echo  Could not copy the files to "%DEST%".
  pause & exit /b 1
)
echo  New code copied to "%DEST%".
if exist "%~dp0FreeFlow.vbs" (
  echo  Restarting FreeFlow...
  wscript "%~dp0FreeFlow.vbs" --restart
  goto done
)
set "EXE="
set "PYW="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "(Get-Process FreeFlow -ErrorAction SilentlyContinue | Select-Object -First 1).Path"`) do set "EXE=%%p"
if not defined EXE (
  rem No running FreeFlow.exe: look at the "start with Windows" entry (packaged exe, or a source install)
  for /f "usebackq tokens=2,*" %%a in (`reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v FreeFlow 2^>nul ^| find "REG_SZ"`) do (
    for %%i in (%%b) do (
      if /i "%%~nxi"=="FreeFlow.exe" set "EXE=%%~fi"
      if /i "%%~nxi"=="FreeFlow.pyw" set "PYW=%%~fi"
    )
  )
)
if not defined PYW goto nopyw
for %%i in ("%PYW%") do set "PYWDIR=%%~dpi"
echo  Restarting the source installation...
wscript "%PYWDIR%FreeFlow.vbs" --restart
goto done
:nopyw
if not defined EXE (
  echo  FreeFlow is not running and no start-up entry was found.
  echo  Start FreeFlow.exe yourself - it picks up the new code automatically.
  pause & exit /b 0
)
echo  Restarting "%EXE%" ...
start "" "%EXE%" --restart
:done
echo  Done. FreeFlow comes back with the new code in a few seconds.
timeout /t 5 >nul
'''

README_UPDATE = """FreeFlow update r{rev}
=======================
1. Unzip this file (keep "Update FreeFlow.bat" and the "freeflow" folder together).
2. Double-click "Update FreeFlow.bat". It copies the new code to %APPDATA%\\FreeFlow\\update and
   restarts FreeFlow. That's it - your settings are untouched.

Needs an installed FreeFlow {ver} package from 5 September 2026 or later (older packages do not look
for updates; install the full package once, then use these small updates).
If FreeFlow ever fails to start after an update, it falls back to its built-in code by itself.
"""


def stamp_revision() -> int:
    """Write the current time as CODE_REVISION into freeflow/__init__.py (newer code wins)."""
    rev = int(time.strftime("%Y%m%d%H%M"))
    with open(INIT, encoding="utf-8") as f:
        s = f.read()
    if "CODE_REVISION" in s:
        s = re.sub(r"CODE_REVISION\s*=\s*\d+", f"CODE_REVISION = {rev}", s)
    else:
        s += f"\nCODE_REVISION = {rev}\n"
    with open(INIT, "w", encoding="utf-8") as f:
        f.write(s)
    return rev


def build_update() -> str:
    rev = stamp_revision()
    sys.path.insert(0, ROOT)
    import importlib
    import freeflow
    importlib.reload(freeflow)
    ver = freeflow.APP_VERSION
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"FreeFlow-update-r{rev}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for py in sorted(glob.glob(os.path.join(ROOT, "freeflow", "*.py"))):
            z.write(py, "freeflow/" + os.path.basename(py))
        z.writestr("Update FreeFlow.bat", UPDATER_BAT.replace("\n", "\r\n"))
        z.writestr("README-update.txt", README_UPDATE.format(rev=rev, ver=ver).replace("\n", "\r\n"))
    size = os.path.getsize(out)
    print(f"update package: {out}  ({size / 1024:.0f} KB, revision {rev})")
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        shutil.copy2(out, desktop)
        print("copied to", os.path.join(desktop, os.path.basename(out)))
    return out


if __name__ == "__main__":
    build_update()
