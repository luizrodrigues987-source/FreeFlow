"""Learning the user's own speech patterns from corrections.

A correction can come from
  - the voice command "correction ..." spoken right after a bad dictation, followed by the right words,
  - dictating the same sentence again within a short time (learned automatically, but only after the
    same fix has been seen twice, because the second attempt could be the wrong one),
  - editing an entry in Settings > History, or the Learning tab.
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

log = logging.getLogger(__name__)

MAX_SPAN = 4                 # longer changes are rewrites, not corrections
AUTO_MIN_SIMILARITY = 0.6    # letters of the two spans must be this alike for automatic learning ("John" -> "Jane" is not)
AUTO_SEEN_TWICE = 2          # a re-dictation fix is learned once it has been seen this often
CORRECTION_RE = re.compile(r"^\s*(?:correction|correct that|fix that)\b[\s:,.!\-]*(.*)$", re.IGNORECASE | re.DOTALL)
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*|[^\sA-Za-z0-9]")


def words(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text or "") if re.match(r"[A-Za-z0-9]", t)]


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


def letters_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


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
        if strict and (_norm(src) == _norm(dst) or letters_similarity(src, dst) < AUTO_MIN_SIMILARITY):
            continue
        out.append((src, dst))
    # a change of case / spacing only ("free flow" -> "FreeFlow") counts when the user asked explicitly
    return out


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
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("learned.json unreadable (%s); starting empty", e)

    def save(self):
        with self._lock:                      # one writer at a time (apply() and learn() may overlap)
            data = {"rules": self.rules, "candidates": self.candidates}
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
            # the opposite rule (an earlier mistake, or the user changed their mind) goes away
            self.rules = [r for r in self.rules if not (r["from"].lower() == dst.lower() and r["to"].lower() == key)]
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
        """Explicit correction: every changed span becomes a rule at once."""
        pairs = extract_corrections(wrong, right, strict=False)
        added = [(s, d) for s, d in pairs if self.add_rule(s, d, source)]
        return added

    def learn_auto(self, wrong: str, right: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """Automatic learning from a re-dictation.  Returns (rules learned now, fixes noted for later)."""
        pairs = extract_corrections(wrong, right, strict=True)
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
            self.add_rule(src, dst, "re-dictation")
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
        changed = False
        for r in rules:
            pattern = r"(?<![A-Za-z0-9'])" + re.escape(r["from"]).replace(r"\ ", r"\s+") + r"(?![A-Za-z0-9'])"

            def sub(m, r=r):
                dst = r["to"]
                if m.group(0)[:1].isupper() and dst[:1].islower():
                    dst = dst[:1].upper() + dst[1:]
                return dst

            new = re.sub(pattern, sub, text, flags=re.IGNORECASE)
            if new != text:
                text = new
                changed = True
                with self._lock:
                    r["hits"] = int(r.get("hits", 0)) + 1
                    self._dirty = True
        if changed and self._dirty:
            self.save()                       # a few kilobytes; the worker thread is not time-critical here
        return text

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
