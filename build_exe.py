"""Build a self-contained FreeFlow package (no Python needed on the target PC).

Result:  dist/FreeFlow/FreeFlow.exe  and  dist/FreeFlow-<version>-win64.zip  (also copied to the Desktop).
Run with the project's venv:  .venv\\Scripts\\python.exe build_exe.py   (or build_exe.bat)
Options:  --no-zip   skip the archive     --cpu-only   leave the CUDA libraries out (much smaller, slower)
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from freeflow import APP_VERSION  # noqa: E402

NAME = "FreeFlow"
SITE = os.path.join(ROOT, ".venv", "Lib", "site-packages")
DIST = os.path.join(ROOT, "dist", NAME)
# CUDA libraries Whisper never touches (saves ~200 MB)
SKIP_DLLS = ("nvblas", "cudnn_adv")

FRIEND_README = f"""FreeFlow {APP_VERSION} - private voice dictation for Windows
=========================================================

Getting started
1. Unzip this folder anywhere you like (for example C:\\FreeFlow). Keep all the files together.
2. Run FreeFlow.exe. A purple microphone icon appears in the system tray and the Settings window opens.
   The very first start downloads the speech model (about 1.6 GB); the tray icon is grey while it loads
   and turns purple when FreeFlow is ready. Later starts take a few seconds.
3. Hold Ctrl+Win and talk, then release: the text is typed where your cursor is.
   Double-tap Ctrl+Win for hands-free dictation; a single press of Ctrl+Win (or Esc) ends it.
   Say "new line" or "new paragraph" to format. Change the keys under Settings > General.
4. Recommended: install Ollama (https://ollama.com, free) so FreeFlow can turn your speech into proper
   sentences and paragraphs. Start Ollama once, then restart FreeFlow: it downloads the small text
   model (about 2 GB) by itself and switches sentence structuring on.
5. Settings > General > "Start FreeFlow when I sign in" keeps it running in the background all the time.

In games such as League of Legends, FreeFlow opens the chat for you (Enter), inserts the text and leaves you to
press Enter to send (Settings > Formatting > "Game chat").

While you dictate, FreeFlow mutes your speakers (not headphones) and mutes your microphone for Discord,
so people in your call do not hear you dictating. Both are restored the moment you stop.

Requirements
- Windows 10 or 11, 64-bit. About 4 GB of free disk space including the models.
- An NVIDIA GPU (GTX 10-series or newer, 4 GB+) makes transcription near-instant. Without one, FreeFlow
  runs on the CPU: pick a smaller model such as "small.en" under Settings > Transcription.

Privacy
Everything runs on your own PC. Nothing is sent anywhere unless you deliberately enable a cloud option
(OpenAI / Groq / Claude) in Settings.

Updates
FreeFlow checks GitHub for a newer version each time it starts and installs it by itself (switch this off
in Settings > About). You can also double-click "Update FreeFlow.bat" in this folder, or use the tray
icon's "Check for updates". Updates are small (about 70 KB); a new major version is announced with a link.

Notes
- Windows SmartScreen or an antivirus may warn about an unsigned app the first time; choose "Run anyway".
- Settings and logs live in %APPDATA%\\FreeFlow. The source code is in the "source" folder.
- Quit from the tray icon (right-click > Quit FreeFlow).
"""


def nvidia_binaries() -> list[str]:
    args: list[str] = []
    for lib in ("cublas", "cudnn"):
        for dll in sorted(glob.glob(os.path.join(SITE, "nvidia", lib, "bin", "*.dll"))):
            if any(s in os.path.basename(dll).lower() for s in SKIP_DLLS):
                continue
            args += ["--add-binary", f"{dll}{os.pathsep}nvidia/{lib}/bin"]
    return args


def build(cpu_only: bool = False):
    from build_update import stamp_revision
    rev = stamp_revision()          # the bundled code carries the same revision scheme as updates
    print("code revision", rev)
    icon = os.path.join(ROOT, "assets", "freeflow.ico")
    if not os.path.exists(icon):
        from freeflow.tray import ensure_icon_file
        ensure_icon_file(icon)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", NAME, "--windowed",
           "--icon", icon,
           "--add-data", f"{os.path.join(ROOT, 'assets')}{os.pathsep}assets",
           "--collect-all", "ctranslate2", "--collect-all", "faster_whisper", "--collect-all", "onnxruntime",
           "--collect-all", "sounddevice", "--collect-all", "sv_ttk", "--collect-all", "comtypes",
           "--collect-all", "pycaw", "--collect-submodules", "anthropic", "--collect-data", "anthropic",
           "--collect-data", "certifi",
           "--hidden-import", "pystray._win32", "--hidden-import", "PIL._tkinter_finder",
           "--hidden-import", "tokenizers", "--hidden-import", "huggingface_hub",
           "--exclude-module", "torch", "--exclude-module", "matplotlib", "--exclude-module", "PyInstaller"]
    if not cpu_only:
        cmd += nvidia_binaries()
    cmd.append(os.path.join(ROOT, "freeflow_main.py"))
    print("PyInstaller:", " ".join(a if " " not in a else f'"{a}"' for a in cmd[2:8]), "...")
    t0 = time.time()
    subprocess.check_call(cmd, cwd=ROOT)
    print(f"PyInstaller finished in {time.time() - t0:.0f}s")

    # extras shipped next to the exe
    with open(os.path.join(DIST, "README-FreeFlow.txt"), "w", encoding="utf-8") as f:
        f.write(FRIEND_README)
    with open(os.path.join(DIST, "Update FreeFlow.bat"), "w", encoding="ascii", newline="\r\n") as f:
        f.write("@echo off\r\n"
                "rem Downloads the latest FreeFlow code update from GitHub, installs it and restarts FreeFlow.\r\n"
                "start \"\" \"%~dp0FreeFlow.exe\" --update\r\n")
    src = os.path.join(DIST, "source")
    shutil.rmtree(src, ignore_errors=True)
    os.makedirs(os.path.join(src, "freeflow"))
    for py in glob.glob(os.path.join(ROOT, "freeflow", "*.py")):
        shutil.copy2(py, os.path.join(src, "freeflow"))
    for extra in ("README.md", "requirements.txt", "FreeFlow.pyw", "setup.bat", "build_exe.py"):
        if os.path.exists(os.path.join(ROOT, extra)):
            shutil.copy2(os.path.join(ROOT, extra), src)
    total = sum(os.path.getsize(os.path.join(d, fn)) for d, _, fs in os.walk(DIST) for fn in fs)
    print(f"dist folder: {DIST}  ({total / 1e6:.0f} MB)")


def make_zip() -> str:
    out = os.path.join(ROOT, "dist", f"{NAME}-{APP_VERSION}-win64.zip")
    if os.path.exists(out):
        os.remove(out)
    t0 = time.time()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for d, _, files in os.walk(DIST):
            for fn in files:
                full = os.path.join(d, fn)
                z.write(full, os.path.join(NAME, os.path.relpath(full, DIST)))
    print(f"zip: {out}  ({os.path.getsize(out) / 1e6:.0f} MB, {time.time() - t0:.0f}s)")
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        shutil.copy2(out, desktop)
        print("copied to", os.path.join(desktop, os.path.basename(out)))
    return out


if __name__ == "__main__":
    build(cpu_only="--cpu-only" in sys.argv)
    if "--no-zip" not in sys.argv:
        make_zip()
