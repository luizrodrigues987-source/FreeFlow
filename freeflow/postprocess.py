"""Rule based clean-up of the raw transcript: fillers, voice commands, replacements."""
from __future__ import annotations

import re

FILLER_RE = re.compile(r"(?i)(?<![\w'\-])(?:um+|uh+|uhm+|umm+|hmm+|erm+|ah+|mhm+)(?![\w'\-])[,.!?;:]?[ \t]*")

# "new line" / "new paragraph" only when they stand alone (preceded or followed by punctuation / edges)
_CMD_BEFORE = r"(?i)(?:(?<=[.,!?;:\n])[ \t]*|^[ \t]*)"
_CMD_AFTER = r"(?![\w\-])(?:[.,!?;:]+|(?=[ \t]*$)|(?=[ \t]*\n))?[ \t]*"
NEW_PARAGRAPH_RE = re.compile(_CMD_BEFORE + r"new\s+paragraph" + _CMD_AFTER)
NEW_LINE_RE = re.compile(_CMD_BEFORE + r"new\s+line" + _CMD_AFTER)
NEW_PARAGRAPH_END_RE = re.compile(r"(?i)(?<![\w\-])new\s+paragraph(?=[.,!?;:]|[ \t]*$|[ \t]*\n)[.,!?;:]*[ \t]*")
NEW_LINE_END_RE = re.compile(r"(?i)(?<![\w\-])new\s+line(?=[.,!?;:]|[ \t]*$|[ \t]*\n)[.,!?;:]*[ \t]*")

HALLUCINATIONS = {
    "thank you", "thanks for watching", "thank you for watching", "you", "bye", "so", "okay", "thank you very much",
    "subtitles by the amara org community", "i'm sorry", "please subscribe", "thanks", "the end", "hmm",
}


def normalize_spaces(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return text.strip()


def remove_fillers(text: str) -> str:
    def _mark(m: "re.Match") -> str:
        before = m.string[:m.start()].rstrip()
        matched = m.group(0).rstrip()
        # "can you, um, send" -> the comma before the filler is redundant too
        if before.endswith(",") and matched.endswith(","):
            return "\x01"
        return "\x00"

    marked = FILLER_RE.sub(_mark, text)
    if "\x00" not in marked and "\x01" not in marked:
        return text
    out: list[str] = []
    cap_next = False
    for ch in marked:
        if ch in ("\x00", "\x01"):
            if ch == "\x01":
                while out and out[-1] in " \t":
                    out.pop()
                if out and out[-1] == ",":
                    out.pop()
                out.append(" ")
            prev = "".join(out).rstrip()
            if not prev or prev[-1] in ".!?\n":
                cap_next = True
            continue
        if cap_next:
            if ch.isalpha():
                ch = ch.upper()
                cap_next = False
            elif not ch.isspace():
                cap_next = False
        out.append(ch)
    result = "".join(out)
    result = re.sub(r"\s+([,.!?;:])", r"\1", result)   # "hello , world" -> "hello, world"
    result = re.sub(r"([,;:])\s*([,;:])", r"\2", result)  # ", ," -> ","
    result = re.sub(r"([.!?])\s*[,;:]", r"\1", result)
    return normalize_spaces(result)


BULLET_RE = re.compile(_CMD_BEFORE + r"(?:new\s+)?bullet(?:\s+point)?" + _CMD_AFTER)
BULLET_END_RE = re.compile(r"(?i)(?<![\w\-])(?:new\s+)?bullet(?:\s+point)?(?=[.,!?;:]|[ \t]*$|[ \t]*\n)[.,!?;:]*[ \t]*")


def apply_voice_commands(text: str) -> str:
    for rx in (NEW_PARAGRAPH_RE, NEW_PARAGRAPH_END_RE):
        text = rx.sub("\n\n", text)
    for rx in (NEW_LINE_RE, NEW_LINE_END_RE):
        text = rx.sub("\n", text)
    for rx in (BULLET_RE, BULLET_END_RE):
        text = rx.sub("\n- ", text)
    text = re.sub(r"\n- \s*\n", "\n", text)   # a stray "bullet point" with nothing after it
    text = re.sub(r"[ \t]*,[ \t]*\n", "\n", text)          # drop a comma left before a line break
    text = re.sub(r"\n[ \t]+(?!- )", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(\n+(?:- )?)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return text.strip()


# --------------------------------------------------------------------------
# Spoken enumerations -> numbered lists
# --------------------------------------------------------------------------
_CARDINALS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
             "eighth": 8, "ninth": 9, "tenth": 10, "firstly": 1, "secondly": 2, "thirdly": 3, "lastly": -1}
_MARKER_RE = re.compile(
    r"(?i)(?<![\w'\-])(?:(?P<pre>number|point|step|item|part)\s+)?"
    r"(?P<w>one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|firstly|secondly|thirdly|lastly|\d{1,2})"
    r"(?P<punct>[,.:;)\-]*)(?=\s)")
_BOUNDARY_RE = re.compile(r"(?i)(?:^|[,.;:!?]\s*|\b(?:and|then|also|next)\s+|\n\s*)$")
_TRAIL_RE = re.compile(r"(?i)[\s,;:\-]*(?:\band\b|\bthen\b|\balso\b|\band then\b)?[\s,;:\-]*$")
_LEAD_RE = re.compile(r"(?i)^(?:of all|off)?[\s,;:\-]*")
_BULLET_LINE_RE = re.compile(r"^(\s*(?:\d{1,2}[.)]|[-*•])\s+)(.*)$")


# words around a number that mean it is a quantity or a name, not a list marker
_NOT_BEFORE = {"the", "a", "an", "than", "to", "about", "only", "just", "top", "any", "every", "another", "these",
               "those", "of", "or", "all", "last", "my", "your", "his", "her", "their", "our", "its", "at", "by",
               "for", "with", "in", "on", "from", "into", "over", "under", "between", "around",
               "plus", "minus", "times", "half", "past", "nearly", "almost",
               "like", "maybe", "roughly", "least", "most", "have", "has", "had", "are", "is", "was", "were", "be",
               "been", "being", "take", "took", "takes", "need", "needs", "got", "get", "gets", "buy", "bought"}
# "chapter one", "page three", "version two": a name, unless the number continues a list that is already running
_NAME_BEFORE = {"phase", "chapter", "level", "page", "version", "round", "week", "day", "year", "month", "grade",
                "season", "episode", "step", "part", "number", "point", "item", "table", "figure", "room", "line"}
_NOT_AFTER = {"of", "or", "more", "other", "others", "hundred", "thousand", "million", "billion", "percent",
              "o'clock", "pm", "am", "p.m.", "a.m.", "half", "quarter", "time", "times", "day", "days", "week",
              "weeks", "month", "months", "year", "years", "hour", "hours", "minute", "minutes", "second", "seconds",
              "people", "person", "thing", "things", "point", "points", "step", "steps", "item", "items",
              "changes", "options", "reasons", "ways", "kids", "dogs", "cats", "guys", "questions", "more", "less",
              "each", "per", "dollars", "bucks", "euros", "cents", "meters", "miles", "inches", "feet", "pounds",
              "kilos", "grams", "liters", "gallons", "degrees", "weeks", "issues", "problems", "tasks", "parts",
              "pieces", "sets", "pairs", "copies", "versions", "attempts", "tries", "rounds", "levels", "pages"}


def _marker_value(m: "re.Match") -> tuple[int, str]:
    w = m.group("w").lower()
    if w.isdigit():
        return int(w), "digit"
    if w in _CARDINALS:
        return _CARDINALS[w], "cardinal"
    return _ORDINALS.get(w, 0), "ordinal"


def _is_quantity(text: str, m: "re.Match") -> int:
    """0 = looks like a list marker; 2 = clearly a quantity or a name ('one of', 'the two options',
    'three dogs', '$1'); 1 = probably a name ('page three') unless it continues a running list."""
    before = text[:m.start()]
    after = text[m.end():]
    prev = re.findall(r"[\w'.]+", before[-40:])
    prev_word = prev[-1].lower() if prev else ""
    if not m.group("pre") and prev_word in _NOT_BEFORE:
        return 2
    if m.group("w").isdigit() and before and not before[-1].isspace() and before[-1] not in "(":
        return 2
    nxt = re.findall(r"[\w'.]+", after[:40])
    next_word = nxt[0].lower() if nxt else ""
    if next_word in _NOT_AFTER:
        if m.group("w").lower() == "first" and len(nxt) > 1 and next_word == "of" and nxt[1].lower() == "all":
            return 0   # "first of all" is a list opener
        return 2
    if not m.group("pre") and prev_word in _NAME_BEFORE:
        return 1
    return 0


def _clean_item(text: str) -> str:
    text = _TRAIL_RE.sub("", text.strip())
    text = _LEAD_RE.sub("", text)
    return text.strip()


def format_enumerations(text: str) -> str:
    """'I need three changes. One, fix A. Two, fix B. Three, fix C.'  ->  intro line + numbered list.

    Only sequences that start at one/first, continue in order and have at least two items are
    converted; without punctuation around the markers at least three items are needed."""
    if "\n" in text:
        return "\n".join(format_enumerations(part) for part in text.split("\n"))
    cands = []
    for m in _MARKER_RE.finditer(text):
        value, family = _marker_value(m)
        level = _is_quantity(text, m)
        if value == 0 or level == 2:
            continue
        if family == "ordinal" and value == -1:      # "lastly" closes whatever list is open
            value = None
        cands.append((m.start(), m.end(), value, family, bool(_BOUNDARY_RE.search(text[:m.start()])), level))
    for i, c in enumerate(cands):
        if c[2] != 1 or c[5] != 0:
            continue
        chain = [c]
        want = 2
        for d in cands[i + 1:]:
            if d[3] != c[3] and not (d[2] is None and c[3] == "ordinal"):
                continue
            if d[2] == want or (d[2] is None and want >= 3):
                chain.append(d)
                want += 1
                if d[2] is None:
                    break
            elif d[2] == 1 and d[5] == 0:
                break
        if len(chain) < 2:
            continue
        items = []
        for k, cur in enumerate(chain):
            end = chain[k + 1][0] if k + 1 < len(chain) else len(text)
            items.append(_clean_item(text[cur[1]:end]))
        if any(not it for it in items):
            continue
        all_bounded = all(x[4] for x in chain)
        enough_words = all(len(it.split()) >= 2 for it in items)
        if not ((all_bounded and len(chain) >= 2) or (len(chain) >= 3 and enough_words)):
            continue
        intro = _clean_item(text[:chain[0][0]])
        # the last item may run into unrelated text: split it at the first sentence end
        rest = ""
        mm = re.search(r"[.!?]\s+(?=[A-Z])", items[-1])
        if mm and len(items[-1][mm.end():].split()) >= 4:
            rest = items[-1][mm.end():].strip()
            items[-1] = items[-1][:mm.start() + 1].strip()
        lines = []
        if intro:
            intro = re.sub(r"[.!?,;:]+$", "", intro).strip() + ":"
            lines.append(capitalize_first(intro))
        for k, it in enumerate(items, 1):
            it = re.sub(r"[,;:]+$", "", it).strip()
            lines.append(f"{k}. {capitalize_first(it)}")
        out = "\n".join(lines)
        if rest:
            out += "\n\n" + capitalize_first(rest)
        return out
    return text


def split_list_line(line: str) -> tuple[str, str]:
    """'2. fix the login page' -> ('2. ', 'fix the login page'); plain lines -> ('', line)."""
    m = _BULLET_LINE_RE.match(line)
    if m:
        return m.group(1), m.group(2)
    return "", line


def apply_replacements(text: str, replacements: dict) -> str:
    if not replacements:
        return text
    for phrase in sorted(replacements, key=len, reverse=True):
        repl = replacements[phrase]
        if not phrase.strip():
            continue
        rx = re.compile(r"(?i)(?<!\w)" + re.escape(phrase.strip()) + r"(?!\w)[.!?]?" if False else
                        r"(?i)(?<!\w)" + re.escape(phrase.strip()) + r"(?!\w)")
        text = rx.sub(lambda m: repl, text)
    return text


def capitalize_first(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1:]
        if not ch.isspace() and ch not in "\"'(“‘[":
            return text
    return text


def looks_like_hallucination(text: str, rms: float, duration: float = 99.0) -> bool:
    """Whisper's stock phrases for (near) silence, e.g. 'Thank you.' on a 0.4 s accidental tap."""
    norm = re.sub(r"[^\w\s']", "", text.lower()).strip()
    norm = re.sub(r"\s+", " ", norm)
    return norm in HALLUCINATIONS and (rms < 0.02 or duration < 2.0)


def clean_transcript(text: str, cfg, rms: float = 1.0, duration: float = 99.0) -> str:
    """Apply the rule based pipeline according to the config."""
    text = normalize_spaces(text or "")
    if not text:
        return ""
    if looks_like_hallucination(text, rms, duration):
        return ""
    if cfg.get("remove_fillers", True):
        text = remove_fillers(text)
    if cfg.get("voice_commands", True):
        text = apply_voice_commands(text)
    text = apply_replacements(text, cfg.get("replacements") or {})
    if cfg.get("auto_lists", True):
        text = format_enumerations(text)
    if cfg.get("capitalize_first", True):
        text = capitalize_first(text)
    return text


def finalize_for_injection(text: str, cfg) -> str:
    if not text:
        return ""
    if cfg.get("append_space", True) and not text.endswith(("\n", " ")):
        text += " "
    return text
