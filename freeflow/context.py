"""Context from the window in front: the names and the topic words on it.

When a dictation starts, the text of the window that will receive it is read through UI Automation
(the same channel screen readers use; nothing leaves the PC and nothing is kept after the dictation).
Two kinds of words are collected:
  - names: capitalised words that look like people or products - the contact on a CRM page, the person
    an e-mail is addressed to, names in the window title, the parts of e-mail addresses;
  - topic words: distinctive words that recur on the page ("pavers", "stucco", "onboarding"), i.e.
    words that are not everyday English (see commonwords.py) and appear at least twice, or in the title.
Both are handed to Whisper as expected vocabulary, so it writes "Miren" and "pavers" in the first place,
and afterwards a transcript word that still sounds like one of them is replaced by it ("Myron" ->
"Miren", "papers" -> "pavers").  The read runs on its own thread with a deadline, in parallel with the
recording, so it never delays anything; when it is not done in time the dictation goes without it.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

from . import focus
from .commonwords import EVERYDAY
from .learning import COMMON_WORDS, letters_similarity, sounds_alike

log = logging.getLogger(__name__)

UIA_TextPatternId = 10014
UIA_ValuePatternId = 10002
UIA_ControlTypePropertyId = 30003
TreeScope_Descendants = 4
MAX_CHARS = 60000
MAX_DOCS = 6
MAX_NAMES = 15
MAX_TERMS = 25
TERM_MIN_LEN = 4
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
free flow freeflow whisper wispr python untitled document page tab window file home end""".split())
# a name right after one of these is very likely a person
CUES = {"dear", "hi", "hello", "hey", "to", "from", "cc", "contact", "name", "owner", "attn", "regards", "thanks",
        "with", "for", "mr", "mrs", "ms", "dr", "and", "by", "at", "via"}
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’\-]*|[.!?:\n]")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
_EMAIL = re.compile(r"([a-z]{2,})[._-]([a-z]{2,})@", re.IGNORECASE)
_ANY_EMAIL = re.compile(r"\S+@\S+")


def _clean(tok: str) -> str:
    return tok.strip("'’-")


def _plain(s: str) -> str:
    return re.sub(r"['’\-]", "", s.lower())


def looks_like_name(tok: str, lenient: bool = False) -> bool:
    """A capitalised word that could be a person or product.  Strict mode also rejects everyday English
    words (Support, Contract); lenient mode, used right after a cue such as "Hi", keeps them (Hi Mark)."""
    t = _clean(tok)
    if len(t) < 2 or not t[:1].isupper() or t.isupper():
        return False
    body = t[1:]
    if not re.sub(r"['’\-]", "", body).isalpha():
        return False
    parts = re.split(r"['’]", t, maxsplit=1)
    if len(parts[0]) < 2 and len(parts) > 1 and parts[1].lower() in ("m", "ll", "d", "ve", "re", "s", "t"):
        return False                         # I'm, I'll, I'd ... (but O'Neil is a name)
    low, plain = t.lower(), _plain(t)
    if low in COMMON_WORDS or plain in COMMON_WORDS or low in STOP_NAMES:
        return False
    return lenient or plain not in EVERYDAY


def looks_like_term(word: str) -> bool:
    """A distinctive lower-case word worth expecting: not everyday English, not interface noise."""
    w = _plain(word)
    return (len(w) >= TERM_MIN_LEN and w.isalpha() and w not in EVERYDAY and w not in COMMON_WORDS
            and w not in STOP_NAMES)


def extract_names(text: str, title: str = "") -> list[str]:
    """Candidate names in the text, best first (cue words, repeats and the title raise the score)."""
    scores: dict[str, float] = {}
    canon: dict[str, str] = {}

    def add(name: str, pts: float):
        key = name.lower()
        canon.setdefault(key, name)
        scores[key] = scores.get(key, 0.0) + pts

    toks = _TOKEN.findall(_ANY_EMAIL.sub(" ", text or ""))     # addresses are handled separately below
    # a word that also occurs in lower case on the page is an ordinary word, not a name
    lowercase_words = {t.lower() for t in toks if t[:1].islower()}
    for i, tok in enumerate(toks):
        prev = toks[i - 1] if i else "\n"
        cue = prev.lower() in CUES
        if not looks_like_name(tok, lenient=cue):
            continue
        name = _clean(tok)
        if name.lower() in lowercase_words:
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else "\n"
        if nxt == ":":
            continue                         # "Amount:", "Stage:" - a field label
        pts = 1.0
        if cue:
            pts += 2.0
        if prev in (".", "!", "?", "\n", ":"):
            pts -= 0.6                       # sentence starters and field values are often ordinary words
        if looks_like_name(nxt) or (looks_like_name(prev) and not cue):
            pts += 0.8                       # "Miren Garcia": part of a full name
        if scores.get(name.lower(), 0) >= 4:
            pts = 0.2                        # repeats count, but only up to a point
        add(name, pts)
    # e-mail addresses and the window title are strong signals: everyday first names (Arthur, Mark) count there
    for first, last in _EMAIL.findall(text or ""):
        for part in (first, last):
            if len(part) >= 3 and part.lower() not in COMMON_WORDS and part.lower() not in STOP_NAMES:
                add(part[:1].upper() + part[1:].lower(), 2.0)
    for tok in _TOKEN.findall(title or ""):
        if looks_like_name(tok, lenient=True):
            add(_clean(tok), 3.0)
    ranked = sorted(scores, key=lambda k: scores[k], reverse=True)
    return [canon[k] for k in ranked if scores[k] >= 1.0][:MAX_NAMES]


def extract_terms(text: str, title: str = "") -> list[str]:
    """Distinctive words that recur on the page (or appear in its title), most frequent first."""
    counts: dict[str, int] = {}
    for tok in _WORD.findall(_ANY_EMAIL.sub(" ", text or "")):
        if not tok[:1].islower():
            continue                         # capitalised words are names (or sentence starters), not topics
        w = _plain(tok)
        if looks_like_term(w):
            counts[w] = counts.get(w, 0) + 1
    title_words = {_plain(t) for t in _WORD.findall(title or "") if t[:1].islower()}
    scores = {w: min(c, 5) + (2 if w in title_words else 0) for w, c in counts.items()}
    for w in title_words:
        if looks_like_term(w) and w not in scores:
            scores[w] = 2
    ranked = sorted(scores, key=lambda w: (-scores[w], w))
    return [w for w in ranked if scores[w] >= 2][:MAX_TERMS]


def _variants(term: str) -> set[str]:
    """Simple singular / plural forms, so "paver" and "pavers" both count."""
    out = {term}
    if term.endswith("ies"):
        out.add(term[:-3] + "y")
    elif term.endswith("es") and len(term) > 4:
        out.add(term[:-2])
        out.add(term[:-1])
    elif term.endswith("s") and not term.endswith("ss"):
        out.add(term[:-1])
    else:
        out.add(term + "s")
    return {v for v in out if len(v) >= TERM_MIN_LEN}


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
    def __init__(self, names: list[str], terms: Optional[list[str]] = None, source: str = "",
                 elapsed: float = 0.0, chars: int = 0):
        self.names = names
        self.terms = list(terms or [])
        self.source = source
        self.elapsed = elapsed
        self.chars = chars
        self.fixed: list[tuple[str, str]] = []
        self._term_forms: dict[str, str] = {}
        for t in self.terms:
            for v in _variants(t):
                self._term_forms.setdefault(v, v)

    def vocabulary(self) -> list[str]:
        """Words for Whisper's prompt: topic words, then the names (the end of the prompt weighs most)."""
        return list(self.terms) + [n for n in self.names if n.lower() not in self.terms]

    def correct(self, text: str) -> str:
        """Words that sound like a name or topic word from the window become that word."""
        if not text or not (self.names or self.terms):
            return text
        known = {n.lower() for n in self.names} | set(self._term_forms)

        def fix(m):
            word = m.group(0)
            core = _clean(word)
            low = core.lower()
            if len(core) < 3 or low in known or low in COMMON_WORDS:
                return word
            before = text[:m.start()].rstrip()
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
            if best is not None and best_sim >= need:
                self.fixed.append((core, best))
                return word.replace(core, best, 1)
            if len(low) >= TERM_MIN_LEN:
                best, best_sim = None, 0.0
                for form in self._term_forms:
                    if sounds_alike(low, form):
                        sim = letters_similarity(low, form)
                        if sim > best_sim:
                            best, best_sim = form, sim
                # topic words are ordinary words too ("papers" -> "pavers" yes, "paper" -> "paver" no):
                # only a very close match counts
                if best is not None and best_sim > 0.8:
                    repl = best[:1].upper() + best[1:] if capitalised else best
                    self.fixed.append((core, repl))
                    return word.replace(core, repl, 1)
            return word

        return _WORD.sub(fix, text)


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
        terms = extract_terms(text or "", title)
        fut.value = WindowContext(names, terms, focus.app_label(exe, title), time.perf_counter() - t0, len(text or ""))
        log.debug("Window context: %d names, %d topic words from %s in %.2f s (%d chars)", len(names), len(terms),
                  fut.value.source, fut.value.elapsed, fut.value.chars)
        fut._done.set()

    threading.Thread(target=work, name="window-context", daemon=True).start()
    return fut
