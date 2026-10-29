"""The real local text model (Ollama, the configured model) through FreeFlow's prompt and checks:
clean-ups must come back, self-corrections must be applied, and questions must not be answered."""
import logging
import sys
import time

from _util import ROOT  # noqa: F401
from freeflow import llm
from freeflow.config import DEFAULTS

logging.basicConfig(level=logging.WARNING, format="    %(message)s")
cfg = dict(DEFAULTS)
L = llm.LocalLLM(cfg)
if not llm.ollama_version(L.url):
    print("SKIPPED: Ollama is not running")
    sys.exit(0)
L.ready = True
CASES = [
    # (dictated, context, must contain, must not contain)
    ("so um i think we should uh move the meeting to thursday thursday afternoon works better for me thanks",
     "a chat message", ["Thursday", "Thanks"], []),
    ("oh today is wednesday oh i mean it's tuesday actually", "a chat message", ["Tuesday"], ["Wednesday"]),
    ("we closed two deals no wait three deals in august", "an e-mail or a note", ["three"], ["two"]),
    ("why are you asking me to give access to these websites", llm.describe_app("claude.exe", "Claude"),
     ["?"], ["I'm not"]),
    ("what's the best way to follow up with a cold lead", llm.describe_app("claude.exe", "Claude"), ["?"], []),
    ("make the animation like 25 percent slower", llm.describe_app("claude.exe", "Claude"), ["slower"], ["please"]),
    ("i can't i'm sorry i'm going to the rams game tonight", "a chat message", ["sorry"], []),
    ("the total is 40,000 dollars and the next one is 12,500", "an e-mail or a note", ["40,000", "12,500"], []),
    ("tell mark we don't need the extra seats this quarter", "a chat message", ["don't", "Mark"], []),
]
bad = 0
times = []
for text, ctx, must, must_not in CASES:
    t0 = time.time()
    try:
        out = L.polish(text, "clean", ctx)
    except llm.PolishError as e:
        out = f"<error {e}>"
    times.append(time.time() - t0)
    ok = all(m.lower() in out.lower() for m in must) and not any(m.lower() in out.lower() for m in must_not)
    bad += not ok
    print("  %-4s %.2fs %r\n         -> %r" % ("ok" if ok else "FAIL", times[-1], text, out))
print("median %.2f s" % sorted(times)[len(times) // 2])
print("OLLAMA OK" if not bad else f"OLLAMA: {bad} of {len(CASES)} not as expected")
sys.exit(1 if bad else 0)
