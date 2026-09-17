"""Names from the window in front.

When a dictation starts, the text of the window that will receive it is read through UI Automation
(the same channel screen readers use; nothing leaves the PC and nothing is kept after the dictation).
Capitalised words that look like names - the contact on a CRM page, the person an e-mail is addressed
to, names in the window title, the parts of e-mail addresses - are collected and
  1. handed to Whisper as expected vocabulary, so it writes "Miren" instead of "Myron" in the first place,
  2. used afterwards to fix names it still got wrong: a capitalised word in the transcript that sounds
     like a name on the page becomes that name.
The read runs on its own thread with a deadline, in parallel with the recording, so it never delays
anything; when it is not done in time the dictation simply goes without it.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

from . import focus
from .learning import COMMON_WORDS, letters_similarity, sounds_alike

log = logging.getLogger(__name__)

UIA_TextPatternId = 10014
UIA_ValuePatternId = 10002
UIA_NamePropertyId = 30005
UIA_ControlTypePropertyId = 30003
TreeScope_Descendants = 4
MAX_CHARS = 60000
MAX_DOCS = 6
MAX_NAMES = 25
READ_DEADLINE = 4.0

# capitalised words that are not people: days, months, product and interface words
STOP_NAMES = set("""monday tuesday wednesday thursday friday saturday sunday january february march april may june
july august september october november december today yesterday tomorrow email emails contact contacts company
companies deal deals ticket tickets task tasks meeting meetings call calls note notes activity activities overview
search settings inbox sent draft drafts subject reply forward delete archive home dashboard report reports
marketing sales service workflows sequences sequence templates template snippets documents quotes products
playbooks chat help log view edit add create save cancel send schedule new all more show hide close open next
back yes no ok done google chrome microsoft windows edge outlook gmail teams zoom slack discord claude hubspot
salesforce linkedin youtube facebook instagram twitter amazon apple inc llc ltd corp co the this that these
those your our their his her its you we they he she it and or but with from about after before over under
please thanks thank regards best hello hi hey dear sincerely cheers re fw fwd cc bcc am pm mr mrs ms dr
january free flow freeflow whisper wispr python untitled document page tab window file home end""".split())
# a name right after one of these is very likely a person
CUES = {"dear", "hi", "hello", "hey", "to", "from", "cc", "contact", "name", "owner", "attn", "regards", "thanks",
        "with", "for", "mr", "mrs", "ms", "dr", "and", "by", "at", "via"}
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’\-]*|[.!?:\n]")
_EMAIL = re.compile(r"([a-z]{2,})[._-]([a-z]{2,})@", re.IGNORECASE)


def _clean(tok: str) -> str:
    return tok.strip("'’-")


def looks_like_name(tok: str) -> bool:
    t = _clean(tok)
    if len(t) < 2 or not t[:1].isupper() or t.isupper():
        return False
    body = t[1:]
    if not re.sub(r"['’\-]", "", body).isalpha():
        return False
    parts = re.split(r"['’]", t, maxsplit=1)
    if len(parts[0]) < 2 and len(parts) > 1 and parts[1].lower() in ("m", "ll", "d", "ve", "re", "s", "t"):
        return False                         # I'm, I'll, I'd ... (but O'Neil is a name)
    low = t.lower()
    plain = re.sub(r"['’]", "", low)
    return low not in COMMON_WORDS and plain not in COMMON_WORDS and low not in STOP_NAMES


def extract_names(text: str, title: str = "") -> list[str]:
    """Candidate names in the text, best first (cue words, repeats and the title raise the score)."""
    scores: dict[str, float] = {}
    canon: dict[str, str] = {}

    def add(name: str, pts: float):
        key = name.lower()
        canon.setdefault(key, name)
        scores[key] = scores.get(key, 0.0) + pts

    toks = _TOKEN.findall(text or "")
    # a word that also occurs in lower case on the page is an ordinary word, not a name
    lowercase_words = {t.lower() for t in toks if t[:1].islower()}
    for i, tok in enumerate(toks):
        if not looks_like_name(tok):
            continue
        name = _clean(tok)
        if name.lower() in lowercase_words:
            continue
        prev = toks[i - 1] if i else "\n"
        nxt = toks[i + 1] if i + 1 < len(toks) else "\n"
        if nxt == ":":
            continue                         # "Amount:", "Stage:" - a field label
        pts = 1.0
        if prev.lower() in CUES:
            pts += 2.0
        if prev in (".", "!", "?", "\n", ":"):
            pts -= 0.6                       # sentence starters and field values are often ordinary words
        if looks_like_name(nxt) or (looks_like_name(prev) and prev.lower() not in CUES):
            pts += 0.8                       # "Miren Garcia": part of a full name
        if scores.get(name.lower(), 0) >= 4:
            pts = 0.2                        # repeats count, but only up to a point
        add(name, pts)
    for first, last in _EMAIL.findall(text or ""):
        for part in (first, last):
            if part.lower() not in COMMON_WORDS and part.lower() not in STOP_NAMES:
                add(part[:1].upper() + part[1:].lower(), 2.0)
    for tok in _TOKEN.findall(title or ""):
        if looks_like_name(tok):
            add(_clean(tok), 3.0)
    ranked = sorted(scores, key=lambda k: scores[k], reverse=True)
    return [canon[k] for k in ranked if scores[k] >= 1.0][:MAX_NAMES]


def read_window_text(hwnd: int) -> str:
    """Text of the documents / text boxes in this window (inspection thread only)."""
    uia = focus._automation()
    if uia is None or not hwnd:
        return ""
    from comtypes.gen import UIAutomationClient as UIA  # noqa: N814
    root = uia.ElementFromHandle(hwnd)
    parts: list[str] = []
    total = 0
    cond = uia.CreateOrCondition(uia.CreatePropertyCondition(UIA_ControlTypePropertyId, 50030),   # Document
                                 uia.CreatePropertyCondition(UIA_ControlTypePropertyId, 50004))   # Edit
    found = root.FindAll(TreeScope_Descendants, cond)
    for i in range(min(found.Length, MAX_DOCS)):
        el = found.GetElement(i)
        txt = None
        try:
            p = el.GetCurrentPattern(UIA_TextPatternId)
            if p:
                txt = p.QueryInterface(UIA.IUIAutomationTextPattern).DocumentRange.GetText(MAX_CHARS - total)
        except Exception:
            txt = None
        if not txt:
            try:
                p = el.GetCurrentPattern(UIA_ValuePatternId)
                if p:
                    txt = p.QueryInterface(UIA.IUIAutomationValuePattern).CurrentValue
            except Exception:
                txt = None
        if txt:
            parts.append(txt)
            total += len(txt)
            if total >= MAX_CHARS:
                break
    return "\n".join(parts)


class WindowContext:
    def __init__(self, names: list[str], source: str = "", elapsed: float = 0.0, chars: int = 0):
        self.names = names
        self.source = source
        self.elapsed = elapsed
        self.chars = chars
        self.fixed: list[tuple[str, str]] = []

    def correct(self, text: str) -> str:
        """Capitalised words that sound like a name from the window become that name."""
        if not text or not self.names:
            return text
        lower_names = {n.lower() for n in self.names}

        def fix(m):
            word = m.group(0)
            core = _clean(word)
            if len(core) < 3 or core.lower() in lower_names or core.lower() in COMMON_WORDS:
                return word
            start = m.start()
            before = text[:start].rstrip()
            initial = not before or before[-1] in ".!?\n"
            capitalised = core[:1].isupper()
            best, best_sim = None, 0.0
            for name in self.names:
                if sounds_alike(core, name):
                    sim = letters_similarity(core, name)
                    if sim > best_sim:
                        best, best_sim = name, sim
            # a capitalised word is probably a name already (Whisper capitalises the names it knows);
            # a lower-case word only counts when it is nearly the page name letter for letter
            need = 0.75 if not capitalised else (0.7 if initial else 0.6)
            if best is None or best_sim < need:
                return word
            self.fixed.append((core, best))
            return word.replace(core, best, 1)

        return re.sub(r"[A-Za-z][A-Za-z'’\-]*", fix, text)


class ContextFuture:
    def __init__(self):
        self._done = threading.Event()
        self.value: Optional[WindowContext] = None

    def result(self, timeout: float = 1.0) -> Optional[WindowContext]:
        self._done.wait(timeout)
        return self.value


_inspector = focus._Inspector()          # its own thread: the focus check is never held up by a page read


def capture_async(hwnd: int, title: str = "", exe: str = "") -> ContextFuture:
    """Start reading the window; the future holds a WindowContext (or None) when done."""
    fut = ContextFuture()

    def work():
        t0 = time.perf_counter()
        try:
            text, status = _inspector.call(lambda: read_window_text(hwnd), READ_DEADLINE)
            if status != "ok":
                log.debug("window context: %s", status)
                text = ""
        except Exception as e:
            log.debug("window context failed: %s", e)
            text = ""
        names = extract_names(text or "", title)
        fut.value = WindowContext(names, focus.app_label(exe, title), time.perf_counter() - t0, len(text or ""))
        log.debug("Window context: %d names from %s in %.2f s (%d chars)", len(names), fut.value.source,
                  fut.value.elapsed, fut.value.chars)
        fut._done.set()

    threading.Thread(target=work, name="window-context", daemon=True).start()
    return fut
