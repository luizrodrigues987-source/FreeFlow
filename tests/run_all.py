"""Run FreeFlow's tests.

    .venv\\Scripts\\python.exe tests\\run_all.py          unit tests (seconds; no windows, keys, audio or model)
    .venv\\Scripts\\python.exe tests\\run_all.py --all    also the end-to-end checks: the app with a scripted engine,
                                                       the update restart, the island on two monitors, the real
                                                       text model, real speech, and the packaged FreeFlow.exe
                                                       (run these before publishing an update)

The end-to-end checks use their own config (hotkey Ctrl+Alt+Shift+F9, no muting) and never touch the
running FreeFlow; the island check briefly shows a test pill, and e2e_app opens the microphone.
"""
import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
E2E = ["e2e_app.py", "e2e_settings.py", "e2e_update_restart.py", "e2e_overlay.py", "e2e_ollama.py", "e2e_audio.py", "exe_checks.py"]


def main():
    tests = sorted(os.path.basename(p) for p in glob.glob(os.path.join(HERE, "test_*.py")))
    if "--all" in sys.argv:
        tests += E2E
    failed = []
    for name in tests:
        t0 = time.time()
        p = subprocess.run([PY, os.path.join(HERE, name)], cwd=HERE, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        last = (p.stdout.strip().splitlines() or [""])[-1]
        print("%-4s %-24s %5.1f s  %s" % ("ok" if p.returncode == 0 else "FAIL", name, time.time() - t0, last[:90]))
        if p.returncode != 0:
            failed.append(name)
            print("\n".join("      " + ln for ln in (p.stdout + p.stderr).strip().splitlines()[-25:]))
    print("\nall passed" if not failed else f"\nFAILED: {', '.join(failed)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
