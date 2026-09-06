"""Start-with-Windows registration (HKCU Run key) and desktop shortcut creation."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import winreg

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "FreeFlow"


FROZEN = bool(getattr(sys, "frozen", False))   # running from the packaged FreeFlow.exe


def project_root() -> str:
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def launcher_path() -> str:
    if FROZEN:
        return os.path.abspath(sys.executable)
    return os.path.join(project_root(), "FreeFlow.pyw")


def pythonw_path() -> str:
    if FROZEN:
        return os.path.abspath(sys.executable)
    candidates = [os.path.join(sys.prefix, "pythonw.exe"),
                  os.path.join(os.path.dirname(sys.executable), "pythonw.exe")]
    for c in candidates:
        if os.path.exists(c):
            return c
    return sys.executable


def command() -> str:
    if FROZEN:
        return f'"{launcher_path()}"'
    return f'"{pythonw_path()}" "{launcher_path()}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except OSError:
        return False


def set_enabled(flag: bool):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if flag:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
            log.info("Autostart enabled: %s", command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
                log.info("Autostart disabled")
            except FileNotFoundError:
                pass


def icon_path() -> str:
    bundled = os.path.join(getattr(sys, "_MEIPASS", project_root()), "assets", "freeflow.ico")
    if FROZEN and os.path.exists(bundled):
        return bundled
    path = os.path.join(project_root(), "assets", "freeflow.ico")
    if FROZEN and not os.access(project_root(), os.W_OK):
        from .config import DATA_DIR
        path = os.path.join(DATA_DIR, "freeflow.ico")
    return path


def create_desktop_shortcut() -> str:
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.isdir(desktop):
        desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
    link = os.path.join(desktop, "FreeFlow.lnk")
    icon = icon_path() if os.path.exists(icon_path()) else ""
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
        "$s.TargetPath='{target}';$s.Arguments='{args}';$s.WorkingDirectory='{wd}';"
        "$s.Description='FreeFlow voice dictation';{icon}$s.Save()"
    ).format(link=link.replace("'", "''"), target=pythonw_path().replace("'", "''"),
             args="" if FROZEN else '"' + launcher_path().replace("'", "''") + '"',
             wd=project_root().replace("'", "''"),
             icon=f"$s.IconLocation='{icon}';" if icon else "")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return link
