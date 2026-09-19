"""Context from the window in front: the names and the topic words on it - used as a BACKUP.

When a dictation starts, the text of the window that will receive it is read through UI Automation
(the same channel screen readers use; nothing leaves the PC and nothing is kept after the dictation).
Two kinds of words are collected:
  - names: capitalised words that look like people or products - the contact on a CRM page, the person
    an e-mail is addressed to, names in the window title, the parts of e-mail addresses;
  - topic words: distinctive words that recur on the page ("pavers", "stucco", "onboarding"), i.e.
    words that are not everyday English (see commonwords.py) and appear at least twice, or in the title.
The transcript itself is made WITHOUT them: what was said decides, not what happens to be on the screen.
Only afterwards, and only for words in doubt - the recogniser gave the word a low probability, or it is
a name whose sounds match a name on the page in another spelling ("Myron" / "Miren") - is the window
consulted, and even then the change is made only when a second listen to that bit of audio, with the
window word as a hint, really writes it (see WindowContext).  The page read runs on its own thread with
a deadline, in parallel with the recording, so it never delays anything; when it is not done in time the
dictation goes without it.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

from . import focus
from .commonwords import EVERYDAY
from .learning import COMMON_WORDS, _skeleton, letters_similarity, sounds_alike

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


UNSURE_P = 0.5                 # the recogniser's own probability for a word; below this it was on the fence
MAX_CANDIDATES = 3
WHOLE_AUDIO_S = 14.0           # a dictation up to this long is simply heard again in full ...
CLIP_BEFORE_S, CLIP_AFTER_S = 5.0, 2.5     # ... a longer one only around the doubtful word
CLIP_SPAN_S = 12.0             # doubtful words this close together share one second listen
_POSSESSIVE = re.compile(r"^(.{2,}?)(['’]s)$", re.IGNORECASE)


def _norm_word(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (w or "").lower())


def _split_word(core: str) -> tuple[str, str]:
    """("Arthur", "'s") for "Arthur's"; contractions of everyday words stay whole words of their own."""
    m = _POSSESSIVE.match(core)
    return (m.group(1), m.group(2)) if m else (core, "")


def _stems(w: str):
    """The word and what it may be an inflection of (worries -> worry, stopped -> stop, taking -> take)."""
    yield w
    for suffix, repl in (("ies", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("ing", ""), ("ing", "e"),
                         ("ly", ""), ("er", ""), ("er", "e"), ("est", "")):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            stem = w[:len(w) - len(suffix)] + repl
            yield stem
            if len(stem) >= 4 and stem[-1] == stem[-2]:
                yield stem[:-1]              # stopped -> stopp -> stop


def _everyday(low: str) -> bool:
    """An ordinary English word (or an interface / product word): nothing the window should overrule."""
    plain = _plain(low)
    if low in COMMON_WORDS or low in STOP_NAMES:
        return True
    return any(s in EVERYDAY or s in COMMON_WORDS for s in _stems(plain))


def word_probabilities(words) -> dict:
    """{normalised word: lowest probability seen} from the engine's word list ("details")."""
    out: dict[str, float] = {}
    for w in words or []:
        core = _clean(re.sub(r"[^A-Za-z0-9'’\-]", "", str(w.get("word", ""))))
        for key in {_norm_word(core), _norm_word(_split_word(core)[0])}:
            if key:
                out[key] = min(out.get(key, 1.0), float(w.get("prob", 1.0)))
    return out


def _iter_words(text: str):
    """(match, base word, possessive suffix, sentence-initial?) for every word of the text."""
    for m in _WORD.finditer(text or ""):
        core = _clean(m.group(0))
        if not core:
            continue
        base, suffix = _split_word(core)
        before = text[:m.start()].rstrip(" \t\"'“‘(")
        yield m, base, suffix, (not before or before[-1] in ".!?\n:")


def _prob_of(probs: dict, base: str, suffix: str) -> float:
    # a word the clean-up or a learned rule produced is not in the list: nothing says it was doubtful
    return probs.get(_norm_word(base + suffix), probs.get(_norm_word(base), 1.0))


def worth_a_look(text: str, probs: dict) -> bool:
    """Is there a word the window could help with at all - a name or unusual word, or one the recogniser
    was unsure about?  When not, the dictation does not even wait for the page read."""
    for _m, base, suffix, initial in _iter_words(text):
        low = base.lower()
        if len(base) < 3 or low in COMMON_WORDS:
            continue
        if not _everyday(low):
            return True
        if _prob_of(probs, base, suffix) < UNSURE_P and not initial:
            return True
    return False


class WindowContext:
    """Names and topic words of the window in front - a BACKUP, consulted only for words in doubt.

    The transcript is made without it.  Afterwards a word becomes a candidate for a window word only if
      - the recogniser itself was unsure about it (probability < UNSURE_P) and it sounds like the window
        word, or
      - it is a name / unusual word (never ordinary English) that has exactly the sounds of the window
        word in another spelling ("Myron" / "Miren", "Applebaum" / "Apelbaum").
    A candidate is not applied blindly: that stretch of audio is heard a second time with the window
    word as a hint, and the word changes only when the recogniser then writes it (so "Christy" stays
    "Christy" on Kirsty's page, and "update" never becomes "updater")."""

    def __init__(self, names: list[str], terms: Optional[list[str]] = None, source: str = "",
                 elapsed: float = 0.0, chars: int = 0):
        self.names = names
        self.terms = list(terms or [])
        self.source = source
        self.elapsed = elapsed
        self.chars = chars
        self._term_forms: dict[str, str] = {}
        for t in self.terms:
            for v in _variants(t):
                self._term_forms.setdefault(v, t)

    def vocabulary(self) -> list[str]:
        """All window words (topic words, then names) - for display and tests; not given to Whisper."""
        return list(self.terms) + [n for n in self.names if n.lower() not in self.terms]

    def _best_match(self, base: str, unsure: bool) -> Optional[tuple[str, str]]:
        a = _norm_word(base)
        best, best_sim = None, 0.0
        pool = [(n, "name") for n in self.names] + [(f, "term") for f in self._term_forms]
        for cand, kind in pool:
            b = _norm_word(cand)
            if not b or a == b or a.startswith(b) or b.startswith(a):
                continue                     # update / updater, Slack / Slackbot: other words, not mishearings
            if min(len(a), len(b)) / max(len(a), len(b)) < 0.6 or not sounds_alike(a, b):
                continue
            sim = letters_similarity(a, b)
            if sim < 0.6:
                continue
            if not unsure and _skeleton(a) != _skeleton(b):
                continue                     # sure of the sounds: only another spelling of the same sounds
            if sim > best_sim:
                best, best_sim = (cand, kind), sim
        return best

    def candidates(self, text: str, probs: Optional[dict] = None, protected=()) -> list[dict]:
        """Words of the text that may really be a window word, most doubtful first.
        protected: the user's own vocabulary / learned words - their spelling is wanted as it is."""
        if not text or not (self.names or self.terms):
            return []
        probs = probs or {}
        known = {n.lower() for n in self.names} | set(self._term_forms)
        keep = {_norm_word(w) for p in protected for w in _WORD.findall(p)}
        found, seen = [], set()
        for _m, base, suffix, initial in _iter_words(text):
            low = base.lower()
            if len(base) < 3 or low in known or low in COMMON_WORDS or low in seen or _norm_word(base) in keep:
                continue
            p = _prob_of(probs, base, suffix)
            unsure = p < UNSURE_P
            if _everyday(low) and (not unsure or initial):
                continue                     # ordinary word, confidently heard (first words always score low)
            match = self._best_match(base, unsure)
            if match is None:
                continue
            seen.add(low)
            cand, kind = match
            forms = [cand] if kind == "name" else sorted(_variants(self._term_forms.get(cand, cand)) | {cand})
            found.append({"word": base, "replacement": cand, "kind": kind, "prob": p, "forms": forms,
                          "capitalised": base[:1].isupper()})
        found.sort(key=lambda c: c["prob"])
        return found[:MAX_CANDIDATES]

    @staticmethod
    def confirmed(cands: list[dict], heard_again: str) -> list[dict]:
        """The candidates the second listen really wrote (and whose first word it no longer wrote)."""
        toks = {}
        for t in _WORD.findall(heard_again or ""):
            base = _split_word(_clean(t))[0]
            toks.setdefault(_norm_word(base), base)
        ok = []
        for c in cands:
            if _norm_word(c["word"]) in toks:
                continue
            for form in [c["replacement"]] + list(c["forms"]):
                if _norm_word(form) in toks:
                    repl = form
                    if c["kind"] == "term":
                        repl = form[:1].upper() + form[1:] if c["capitalised"] else form
                    ok.append(dict(c, replacement=repl))
                    break
        return ok

    @staticmethod
    def apply(text: str, accepted: list[dict]) -> str:
        for c in accepted:
            text = re.sub(r"(?<![A-Za-z])" + re.escape(c["word"]) + r"(?![A-Za-z])",
                          lambda _m, r=c["replacement"]: r, text)
        return text


def second_listen_plan(cands: list[dict], words, duration: float) -> tuple[list[dict], Optional[tuple[float, float]]]:
    """Which candidates to check and which stretch of the audio to hear again: (candidates, None) = the
    whole dictation, (candidates, (start, end)) = a clip in seconds, ([], None) = cannot be checked."""
    if not cands:
        return [], None
    if duration <= WHOLE_AUDIO_S:
        return cands, None
    times = {}
    for w in words or []:
        core = _clean(re.sub(r"[^A-Za-z0-9'’\-]", "", str(w.get("word", ""))))
        key = _norm_word(_split_word(core)[0])
        if key and key not in times and w.get("end", 0) > 0:
            times[key] = (float(w.get("start", 0.0)), float(w.get("end", 0.0)))
    timed = [(c, times[_norm_word(c["word"])]) for c in cands if _norm_word(c["word"]) in times]
    if not timed:
        return [], None                      # a long dictation without word times: not worth hearing it all again
    anchor = timed[0][1]                     # the most doubtful word; others ride along when they are close
    chosen = [(c, t) for c, t in timed if abs(t[0] - anchor[0]) <= CLIP_SPAN_S]
    start = max(0.0, min(t[0] for _c, t in chosen) - CLIP_BEFORE_S)
    end = min(duration, max(t[1] for _c, t in chosen) + CLIP_AFTER_S)
    return [c for c, _t in chosen], (start, end)


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
