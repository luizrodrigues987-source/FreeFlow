"""A tiny test runner (no pytest needed): @case registers a test, run() runs them and exits 1 on failure."""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_CASES = []


def case(fn):
    _CASES.append(fn)
    return fn


def run():
    failed = 0
    for fn in _CASES:
        try:
            fn()
            print(f"  ok    {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    name = os.path.basename(sys.modules["__main__"].__file__)
    print(f"{name}: {len(_CASES) - failed}/{len(_CASES)} passed")
    sys.exit(1 if failed else 0)
