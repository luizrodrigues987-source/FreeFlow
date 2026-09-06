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


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.path.insert(0, ROOT)
    update_dir = _use_update_if_newer()
    try:
        main = _import_main()
    except Exception:
        if not update_dir:
            raise
        import traceback
        traceback.print_exc()
        try:
            shutil.move(update_dir, update_dir + ".broken-" + time.strftime("%Y%m%d-%H%M%S"))
        except Exception:
            pass
        sys.path.remove(update_dir)
        for name in [n for n in sys.modules if n == "freeflow" or n.startswith("freeflow.")]:
            del sys.modules[name]
        main = _import_main()
    main()
