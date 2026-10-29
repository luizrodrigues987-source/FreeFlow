"""Learning the user's own speech patterns from corrections.

A correction can come from
  - the voice command "correction ..." spoken right after a bad dictation, followed by the right words,
  - dictating the same sentence again within a short time, or fixing the inserted words by typing
    (learned automatically, but only once the same fix has been seen twice: a real mishearing comes
    back, while rewording a sentence is a one-off),
  - editing an entry in Settings > History, or the Learning tab.
Automatic learning (and the spoken correction) only keeps fixes that look like a mishearing: not a
change of grammar ("email" -> "emailed"), not added words, not an ordinary English word that would then
change everywhere ("low" -> "lol" once turned "high-low" into "high-lol"), see rule_problem().
The wrong and the right text are aligned word by word; every changed span of one to four words becomes
a rule "wrong phrase -> right phrase" ("loco host" -> "localhost").  Rules are applied to every later
transcript before the rest of the clean-up, and the right words are added to Whisper's prompt so the
model itself gets them right more often.  Rules live in %APPDATA%/FreeFlow/learned.json.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional

from .commonwords import EVERYDAY

log = logging.getLogger(__name__)

MAX_SPAN = 4                 # longer changes are rewrites, not corrections
AUTO_MIN_SIMILARITY = 0.6    # letters of the two spans must be this alike for automatic learning ("John" -> "Jane" is not)
AUTO_SEEN_TWICE = 2          # a re-dictation fix is learned once it has been seen this often
CORRECTION_RE = re.compile(r"^\s*correction\b[\s:,.!\-]*(.*)$", re.IGNORECASE | re.DOTALL)
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*|[^\sA-Za-z0-9]")


def words(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text or "") if re.match(r"[A-Za-z0-9]", t)]


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


def letters_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _skeleton(s: str) -> str:
    """A rough sound skeleton: consonants only (with similar ones merged), vowels dropped except a
    leading one, doubles collapsed - "leukerhost" and "localhost" both become "lk?st"-like strings."""
    s = re.sub(r"[^a-z]", "", s.lower())
    for a, b in (("ph", "f"), ("ck", "k"), ("qu", "k"), ("c", "k"), ("q", "k"), ("z", "s"), ("x", "ks"),
                 ("w", "v"), ("gh", "")):
        s = s.replace(a, b)
    out = s[:1]
    for ch in s[1:]:
        if ch in "aeiouyh":
            continue
        if out and out[-1] == ch:
            continue
        out += ch
    return out


# words whose replacement is a grammar or wording choice, never a mishearing worth a global rule
COMMON_WORDS = set("""a an the and or but if so as of to in on at by for from with about into over after
before between through during without within along across behind beyond under above up down out off
i me my mine we us our you your yours he him his she her hers it its they them their theirs this that
these those there here where when what which who whom whose why how is am are was were be been being
have has had do does did done will would shall should can could may might must not no yes than then
too very just also only even still yet again ever never always often some any all both each every
much many more most few less least own other another such same one two three first next last new old
good bad well like get got go went come came make made take took give gave say said see saw know knew
think thought want need use used try tell told ask let put keep seem feel find found back way thing
things time day year people man woman work right left now today because while though although""".split())
_APOS = re.compile(r"'")


def _all_common(phrase: str) -> bool:
    ws = [_APOS.sub("", w.lower()) for w in words(phrase)]
    return bool(ws) and all(w in COMMON_WORDS or w in ("im", "ive", "ill", "id", "youre", "youve", "theyre",
                                                          "were", "dont", "cant", "wont", "isnt", "its") for w in ws)


def sounds_alike(src: str, dst: str) -> bool:
    """Could dst be what the speaker said when the recogniser wrote src?  Letters and sound skeletons
    must be reasonably alike; short words need a closer letter match ("John" -> "Jane" is not accepted)."""
    a, b = _norm(src), _norm(dst)
    if not a or not b:
        return False
    letters = SequenceMatcher(None, a, b).ratio()
    if len(a) <= 4 or len(b) <= 4:
        return letters >= 0.6
    skeleton = SequenceMatcher(None, _skeleton(a), _skeleton(b)).ratio()
    return letters > 0.4 and skeleton >= 0.6


def sentence_similarity(a: str, b: str) -> float:
    """How alike two sentences are: by words, or by letters ("start loco host" / "start localhost" share
    only one word of three but nearly all their letters)."""
    wa, wb = [_norm(w) for w in words(a)], [_norm(w) for w in words(b)]
    if not wa or not wb:
        return 0.0
    by_words = SequenceMatcher(None, wa, wb, autojunk=False).ratio()
    by_letters = SequenceMatcher(None, "".join(wa), "".join(wb), autojunk=False).ratio()
    return max(by_words, by_letters)


def extract_corrections(wrong: str, right: str, strict: bool = False) -> list[tuple[str, str]]:
    """Changed spans between the two texts as [(wrong phrase, right phrase)].
    strict: only spans that sound alike and are not just a change of case (automatic learning)."""
    a, b = words(wrong), words(right)
    if not a or not b:
        return []
    sm = SequenceMatcher(None, [_norm(w) for w in a], [_norm(w) for w in b], autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace" or i2 - i1 > MAX_SPAN or j2 - j1 > MAX_SPAN:
            continue
        src, dst = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if src == dst:
            continue
        if strict and (_norm(src) == _norm(dst) or not sounds_alike(src, dst) or _all_common(src)):
            continue
        out.append((src, dst))
    # a change of case / spacing only ("free flow" -> "FreeFlow") counts when the user asked explicitly
    return out


_INFLECTIONS = ("s", "es", "ed", "d", "ing", "er", "ers", "ly", "n", "ns")
# a change of number, day or month is a change of fact, never a mishearing to fix everywhere
_FACT_WORDS = set("""zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen
sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million
billion first second third fourth fifth sixth seventh eighth ninth tenth half quarter dozen monday tuesday wednesday
thursday friday saturday sunday january february march april may june july august september october november
december today tomorrow yesterday tonight morning afternoon evening noon midnight am pm""".split())


def _stems(w: str):
    """The word and what it may be an inflection of (worries -> worry, stopped -> stop)."""
    yield w
    for suffix, repl in (("ies", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("ing", ""), ("ing", "e"),
                         ("ly", ""), ("er", ""), ("er", "e")):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            stem = w[:len(w) - len(suffix)] + repl
            yield stem
            if len(stem) >= 4 and stem[-1] == stem[-2]:
                yield stem[:-1]


def everyday_word(w: str) -> bool:
    """An ordinary English word (one of the 5,000 most common, or an inflection of one)."""
    w = re.sub(r"[^a-z]", "", w.lower())
    return bool(w) and any(s in EVERYDAY or s in COMMON_WORDS for s in _stems(w))


def rule_problem(src: str, dst: str, explicit: bool = False) -> Optional[str]:
    """Why "src -> dst" should NOT become a rule applied to every later dictation (None = it may).
    A learned rule is global, so only a mishearing qualifies; explicit (the spoken "correction ...")
    may also replace a single ordinary word, because the user asked for exactly that."""
    a, b = words(src), words(dst)
    if not a or not b:
        return "empty"
    if len(b) > len(a) or len(a) - len(b) > 1:
        return "words added or dropped (an edit, not a mishearing)"
    na, nb = _norm(src), _norm(dst)
    if na == nb:
        return None                          # spelling / spacing of the same letters ("free flow" -> "FreeFlow")
    if re.search(r"\d", src + dst) or any(w.lower() in _FACT_WORDS for w in a + b):
        return "a number, day or time (a change of fact)"
    for x, y in ((na, nb), (nb, na)):
        if y.startswith(x) and y[len(x):] in _INFLECTIONS:
            return "a change of grammar"
    if (re.search(r"\d", dst) and not re.search(r"\d", src)) or re.search(r"\b(com|www|https?)\b", dst, re.I):
        return "typed text (a number or a link)"
    names = all(w[:1].isupper() for w in a + b)
    if all(everyday_word(w) for w in a) and all(everyday_word(w) for w in b) and not (
            explicit and names and sounds_alike(src, dst)):
        # ("correction: Marc" after "Mark" is a name's spelling, which the user asked for by name)
        return "ordinary words on both sides (a change of wording)"
    if not explicit and len(a) == 1 and everyday_word(a[0]):
        return "an ordinary English word (it would change everywhere)"
    return None


def splice_correction(previous: str, fix: str, max_words: int = 4) -> Optional[str]:
    """"correction: enforced" right after "... are very loosely informed.": a short fix replaces the part
    of the previous text that sounds like it, not the whole text.  Returns the repaired text, or None
    when the fix is long or nothing in the previous text sounds like it."""
    fw = words(fix)
    if not fw or len(fw) > max_words or not previous:
        return None
    toks = list(re.finditer(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", previous))
    best = None
    for n in range(max(1, len(fw) - 1), len(fw) + 2):
        for i in range(0, len(toks) - n + 1):
            a, b = toks[i].start(), toks[i + n - 1].end()
            span = previous[a:b]
            if _norm(span) == _norm(fix):
                continue
            sim = letters_similarity(span, fix)
            if sim >= 0.6 and sounds_alike(span, fix) and (best is None or sim > best[0]):
                best = (sim, a, b)
    if best is None:
        return None
    _sim, a, b = best
    repl = fix.strip().strip(".,;:!?").strip()
    if previous[a:a + 1].isupper() and repl[:1].islower():
        repl = repl[:1].upper() + repl[1:]
    return previous[:a] + repl + previous[b:]


def split_correction(text: str) -> Optional[str]:
    """The corrected words when the text is a spoken correction ("correction: start localhost"), else None."""
    m = CORRECTION_RE.match(text or "")
    if not m:
        return None
    return m.group(1).strip()


class Learner:
    """Learned rules + candidates, saved as JSON."""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "learned.json")
        self.rules: list[dict] = []          # {"from", "to", "learned", "source", "hits"}
        self.candidates: dict[str, dict] = {}   # key -> {"from", "to", "seen", "last"}
        self.retired: list[dict] = []          # rules taken out of use, with the reason
        self._lock = threading.RLock()
        self._dirty = False
        self.load()

    # ------------------------------------------------------------------
    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self.rules = [r for r in data.get("rules", []) if r.get("from") and r.get("to")]
                self.candidates = data.get("candidates", {}) or {}
                self.retired = list(data.get("retired") or [])
            if not data.get("gate_v2"):
                self._retire_bad_rules()
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("learned.json unreadable (%s); starting empty", e)

    def _retire_bad_rules(self):
        """Once: rules learned automatically before rule_problem() existed that would not be learned now
        are taken out of use (kept under "retired" in the file, and logged, so nothing is lost)."""
        keep, gone = [], []
        with self._lock:
            for r in self.rules:
                src = r.get("source", "")
                why = None
                if src in ("typing", "re-dictation"):
                    why = rule_problem(r["from"], r["to"])
                elif src == "voice":
                    why = rule_problem(r["from"], r["to"], explicit=True)
                if why:
                    gone.append(dict(r, retired=time.time(), reason=why))
                else:
                    keep.append(r)
            self.rules = keep
            self.retired = self.retired + gone
            # the chains the old apply() produced ("Karen -> Kern", "Kern -> Keryn") collapse to one step
            froms = {r["from"].lower(): r for r in self.rules}
            for r in self.rules:
                seen = {r["from"].lower()}
                nxt = froms.get(r["to"].lower())
                while nxt is not None and nxt["from"].lower() not in seen:
                    seen.add(nxt["from"].lower())
                    r["to"] = nxt["to"]
                    nxt = froms.get(r["to"].lower())
        for r in gone:
            log.info("Retired learned rule %r -> %r (%s; hits %s)", r["from"], r["to"], r["reason"], r.get("hits", 0))
        self.save()

    def save(self):
        with self._lock:                      # one writer at a time (apply() and learn() may overlap)
            data = {"rules": self.rules, "candidates": self.candidates, "retired": self.retired, "gate_v2": True}
            self._dirty = False
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=1, ensure_ascii=False)
                os.replace(tmp, self.path)
            except Exception as e:
                log.warning("could not save learned.json: %s", e)

    # ------------------------------------------------------------------
    def add_rule(self, src: str, dst: str, source: str = "manual") -> bool:
        src, dst = " ".join(src.split()), " ".join(dst.split())
        if not src or not dst or src == dst:
            return False
        with self._lock:
            key = src.lower()
            # the user just declared dst right: a rule that changes dst (the opposite rule, an earlier
            # mistake) goes away; one that produced src now produces dst ("Karen -> Kern" + "Kern -> Keryn"
            # = "Karen -> Keryn", never a chain)
            self.rules = [r for r in self.rules if r["from"].lower() != dst.lower()]
            for r in self.rules:
                if r["to"].lower() == key and r["from"].lower() != dst.lower():
                    r["to"] = dst
            for r in self.rules:
                if r["from"].lower() == key:
                    r.update(to=dst, learned=time.time(), source=source)
                    break
            else:
                self.rules.append({"from": src, "to": dst, "learned": time.time(), "source": source, "hits": 0})
            self.candidates.pop(key, None)
        self.save()
        log.info("Learned: %r -> %r (%s)", src, dst, source)
        return True

    def remove(self, src: str):
        with self._lock:
            self.rules = [r for r in self.rules if r["from"].lower() != src.lower()]
        self.save()

    def clear(self):
        with self._lock:
            self.rules = []
            self.candidates = {}
        self.save()

    def learn(self, wrong: str, right: str, source: str) -> list[tuple[str, str]]:
        """Explicit correction: every changed span that looks like a mishearing becomes a rule at once
        (a reworded sentence is still inserted as said, it just teaches nothing)."""
        added = []
        for s, d in extract_corrections(wrong, right, strict=False):
            why = rule_problem(s, d, explicit=True)
            if why:
                log.info("Not learned: %r -> %r (%s)", s, d, why)
            elif len(words(s)) == 1 and len(words(d)) == 1 and s.islower() and d.islower():
                # one ordinary-looking word for another ("informed" -> "enforced"): a rule would change it
                # everywhere, so it waits until the same fix comes again
                self._note([(s, d)], source)
            elif self.add_rule(s, d, source):
                added.append((s, d))
        return added

    def learn_auto(self, wrong: str, right: str, source: str = "re-dictation"
                   ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """Automatic learning (re-dictation, typed fix).  Returns (rules learned now, fixes noted for later)."""
        pairs = [(s, d) for s, d in extract_corrections(wrong, right, strict=True) if not rule_problem(s, d)]
        return self._note(pairs, source)

    def learn_typed(self, src: str, dst: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """One span the user retyped in the inserted text."""
        why = rule_problem(src, dst)
        if why:
            log.info("Typed edit not learned: %r -> %r (%s)", src, dst, why)
            return [], []
        return self._note([(src, dst)], "typing")

    def _note(self, pairs, source: str):
        if not pairs or len(pairs) > 2:
            return [], []
        learned, noted = [], []
        now = time.time()
        with self._lock:
            for src, dst in pairs:
                key = src.lower()
                if any(r["from"].lower() == key and r["to"].lower() == dst.lower() for r in self.rules):
                    continue
                c = self.candidates.get(key)
                if c and c.get("to", "").lower() == dst.lower():
                    c["seen"] = int(c.get("seen", 1)) + 1
                    c["last"] = now
                else:
                    c = {"from": src, "to": dst, "seen": 1, "last": now}
                    self.candidates[key] = c
                if c["seen"] >= AUTO_SEEN_TWICE:
                    learned.append((src, dst))
                else:
                    noted.append((src, dst))
            # keep the candidate list small
            if len(self.candidates) > 200:
                oldest = sorted(self.candidates, key=lambda k: self.candidates[k].get("last", 0))[:-200]
                for k in oldest:
                    self.candidates.pop(k, None)
        for src, dst in learned:
            self.add_rule(src, dst, source)
        if noted:
            self.save()
        return learned, noted

    # ------------------------------------------------------------------
    def apply(self, text: str) -> str:
        """Replace learned wrong phrases (whole words, any case; a capitalised match keeps its capital)."""
        if not text:
            return text
        with self._lock:
            rules = sorted(self.rules, key=lambda r: len(r["from"]), reverse=True)
        if not rules:
            return text
        by_key = {}
        for r in rules:
            by_key.setdefault(" ".join(r["from"].lower().split()), r)
        # one pass over the text with all rules (longest first): a replacement is never replaced again,
        # and a word inside a compound ("high-low") or a contraction is not a match for "low"
        alternatives = "|".join(re.escape(r["from"]).replace(r"\ ", r"\s+") for r in rules)
        pattern = re.compile(r"(?<![A-Za-z0-9'’\-])(?:" + alternatives + r")(?=['’]s\b|(?![A-Za-z0-9'’]|-[A-Za-z0-9]))",
                             re.IGNORECASE)
        hit: list = []

        def sub(m):
            r = by_key.get(" ".join(m.group(0).lower().split()))
            if r is None:
                return m.group(0)
            dst = r["to"]
            if m.group(0)[:1].isupper() and dst[:1].islower():
                dst = dst[:1].upper() + dst[1:]
            hit.append(r)
            return dst

        new = pattern.sub(sub, text)
        if hit:
            with self._lock:
                for r in hit:
                    r["hits"] = int(r.get("hits", 0)) + 1
            self.save()                       # a few kilobytes; the worker thread is not time-critical here
        return new

    def vocabulary(self, limit: int = 30) -> list[str]:
        """The learned right phrases for Whisper's prompt, most recently learned first."""
        with self._lock:
            rules = sorted(self.rules, key=lambda r: r.get("learned", 0), reverse=True)
        out: list[str] = []
        for r in rules:
            dst = r["to"]
            if len(dst.split()) <= 3 and dst not in out:
                out.append(dst)
            if len(out) >= limit:
                break
        return out
