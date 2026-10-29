r"""Entry point for the packaged FreeFlow.exe (PyInstaller).

Small code updates ("Update FreeFlow.bat") are dropped into %APPDATA%\FreeFlow\update.
When that copy of the code is newer than the bundled one it is used instead; if it turns
out to be broken it is put aside and the bundled code runs.
"""
import multiprocessing
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))


def _revision(init_path: str) -> int:
    try:
        with open(init_path, encoding="utf-8") as f:
            m = re.search(r"CODE_REVISION\s*=\s*(\d+)", f.read())
        return int(m.group(1)) if m else 0
    except OSError:
        return -1


def _use_update_if_newer():
    if not getattr(sys, "frozen", False):
        return None
    base = os.path.dirname(os.path.abspath(sys.executable))
    bundled_rev = _revision(os.path.join(base, "source", "freeflow", "__init__.py"))
    update_dir = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "FreeFlow", "update")
    update_rev = _revision(os.path.join(update_dir, "freeflow", "__init__.py"))
    if update_rev > 0 and update_rev > bundled_rev:
        sys.path.insert(0, update_dir)
        return update_dir
    return None


def _import_main():
    from freeflow.app import main
    return main


def _put_aside(update_dir: str):
    """The update failed: note why, move it out of the way, forget its modules."""
    import traceback
    try:
        with open(os.path.join(os.path.dirname(update_dir), "update-failure.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  update failed, using the bundled code\n")
            traceback.print_exc(file=f)
    except Exception:
        pass
    try:
        shutil.move(update_dir, update_dir + ".broken-" + time.strftime("%Y%m%d-%H%M%S"))
    except Exception:
        pass
    if update_dir in sys.path:
        sys.path.remove(update_dir)
    util = sys.modules.get("freeflow.util")      # the failed start may already hold the single-instance lock
    if getattr(util, "_mutex_handle", None):
        try:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(util._mutex_handle)
            util._mutex_handle = None
        except Exception:
            pass
    for name in [n for n in sys.modules if n == "freeflow" or n.startswith("freeflow.")]:
        del sys.modules[name]


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.path.insert(0, ROOT)
    update_dir = _use_update_if_newer()
    started = 0.0
    try:
        main = _import_main()
        started = time.time()
        main()
    except SystemExit:
        raise
    except Exception:
        # an update that breaks on start (also in a module imported later, e.g. the settings window)
        # must not leave FreeFlow dead: fall back to the bundled code - but not after hours of running
        if not update_dir or (started and time.time() - started > 120):
            raise
        _put_aside(update_dir)
        _import_main()()
