"""League of Legends vocabulary for game chat.

Two things happen for text that goes to a game listed under "Game chat" (Settings > Formatting):
  1. Whisper is primed with the game's jargon and the hardest champion names (its prompt works like
     preceding text, so listed spellings win: "gank", "kite", "Kai'Sa", "Zhonya's", "Atakhan").
  2. The transcript is corrected afterwards: chat acronyms get their usual case (ADC, AoE, gg, wp),
     known mishearings are fixed (Barron -> Baron, Harold -> Herald, gang mid -> gank mid) and champion
     names get their official spelling (kaisa -> Kai'Sa, cho gath -> Cho'Gath, xin zao -> Xin Zhao).

Champion list: Riot Data Dragon 16.17.1 (September 2026); refresh_champions() updates it from the CDN.
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

CHAMPIONS = [
    "Aatrox", "Ahri", "Akali", "Akshan", "Alistar", "Ambessa", "Amumu", "Anivia", "Annie", "Aphelios", "Ashe",
    "Aurelion Sol", "Aurora", "Azir", "Bard", "Bel'Veth", "Blitzcrank", "Brand", "Braum", "Briar", "Caitlyn",
    "Camille", "Cassiopeia", "Cho'Gath", "Corki", "Darius", "Diana", "Dr. Mundo", "Draven", "Ekko", "Elise",
    "Evelynn", "Ezreal", "Fiddlesticks", "Fiora", "Fizz", "Galio", "Gangplank", "Garen", "Gnar", "Gragas",
    "Graves", "Gwen", "Hecarim", "Heimerdinger", "Hwei", "Illaoi", "Irelia", "Ivern", "Janna", "Jarvan IV",
    "Jax", "Jayce", "Jhin", "Jinx", "K'Sante", "Kai'Sa", "Kalista", "Karma", "Karthus", "Kassadin", "Katarina",
    "Kayle", "Kayn", "Kennen", "Kha'Zix", "Kindred", "Kled", "Kog'Maw", "LeBlanc", "Lee Sin", "Leona", "Lillia",
    "Lissandra", "Locke", "Lucian", "Lulu", "Lux", "Malphite", "Malzahar", "Maokai", "Master Yi", "Mel", "Milio",
    "Miss Fortune", "Mordekaiser", "Morgana", "Naafiri", "Nami", "Nasus", "Nautilus", "Neeko", "Nidalee", "Nilah",
    "Nocturne", "Nunu & Willump", "Olaf", "Orianna", "Ornn", "Pantheon", "Poppy", "Pyke", "Qiyana", "Quinn",
    "Rakan", "Rammus", "Rek'Sai", "Rell", "Renata Glasc", "Renekton", "Rengar", "Riven", "Rumble", "Ryze",
    "Samira", "Sejuani", "Senna", "Seraphine", "Sett", "Shaco", "Shen", "Shyvana", "Singed", "Sion", "Sivir",
    "Skarner", "Smolder", "Sona", "Soraka", "Swain", "Sylas", "Syndra", "Tahm Kench", "Taliyah", "Talon", "Taric",
    "Teemo", "Thresh", "Tristana", "Trundle", "Tryndamere", "Twisted Fate", "Twitch", "Udyr", "Urgot", "Varus",
    "Vayne", "Veigar", "Vel'Koz", "Vex", "Vi", "Viego", "Viktor", "Vladimir", "Volibear", "Warwick", "Wukong",
    "Xayah", "Xerath", "Xin Zhao", "Yasuo", "Yone", "Yorick", "Yunara", "Yuumi", "Zaahen", "Zac", "Zed", "Zeri",
    "Ziggs", "Zilean", "Zoe", "Zyra",
]

# names that are also ordinary words or common names: only fixed when Whisper already wrote them capitalised
AMBIGUOUS_NAMES = {"brand", "twitch", "graves", "jinx", "kindred", "karma", "swain", "sion", "vi", "mel", "bard",
                   "rumble", "poppy", "quinn", "annie", "diana", "elise", "fizz", "lux", "olaf", "senna", "vex",
                   "talon", "thresh", "ashe", "gwen", "briar", "aurora", "smolder", "sett", "singed", "riven",
                   "kled", "zed", "jax", "zoe", "zac", "nami", "sona", "taric", "locke", "ryze", "shen", "corki",
                   "ekko", "rell", "nilah", "milio", "hwei", "yone", "sylas", "kayle", "kayn", "jayce", "garen",
                   "darius", "ornn", "pyke", "lulu", "janna", "leona", "camille", "irelia", "fiora", "nasus"}

# what people say -> the official spelling (in addition to the automatic "letters only" matching)
NAME_ALIASES = {
    "j4": "Jarvan IV", "jarvan": "Jarvan IV", "jarvan 4": "Jarvan IV", "jarvan four": "Jarvan IV",
    "mundo": "Dr. Mundo", "doctor mundo": "Dr. Mundo", "dr mundo": "Dr. Mundo",
    "nunu": "Nunu", "nunu and willump": "Nunu", "willump": "Nunu",
    "asol": "Aurelion Sol", "a sol": "Aurelion Sol", "aurelion": "Aurelion Sol",
    "renata": "Renata Glasc", "kench": "Tahm Kench", "tahm": "Tahm Kench", "tam kench": "Tahm Kench",
    "leblanc": "LeBlanc", "le blanc": "LeBlanc", "yi": "Yi", "kaisa": "Kai'Sa", "kai sa": "Kai'Sa",
    "kasa": "Kai'Sa", "kaiser": "Kai'Sa", "chogath": "Cho'Gath", "cho gath": "Cho'Gath", "cho": "Cho'Gath",
    "khazix": "Kha'Zix", "kha zix": "Kha'Zix", "kazix": "Kha'Zix", "kogmaw": "Kog'Maw", "kog maw": "Kog'Maw",
    "kog": "Kog'Maw", "reksai": "Rek'Sai", "rek sai": "Rek'Sai", "velkoz": "Vel'Koz", "vel koz": "Vel'Koz",
    "velcoz": "Vel'Koz", "belveth": "Bel'Veth", "bel veth": "Bel'Veth", "ksante": "K'Sante", "k sante": "K'Sante",
    "ke sante": "K'Sante", "xin": "Xin Zhao", "xin zao": "Xin Zhao", "shin zhao": "Xin Zhao", "shin zao": "Xin Zhao",
    "zin zhao": "Xin Zhao", "yasso": "Yasuo", "yassuo": "Yasuo", "yasou": "Yasuo", "yasuo": "Yasuo",
    "yohne": "Yone", "yoni": "Yone", "yon": "Yone", "yumi": "Yuumi", "yuumi": "Yuumi", "zairi": "Zeri", "ziri": "Zeri",
    "trynd": "Tryndamere", "tryn": "Tryndamere", "trynda": "Tryndamere", "morde": "Mordekaiser",
    "heimer": "Heimerdinger", "blitz": "Blitzcrank", "naut": "Nautilus", "cait": "Caitlyn", "trist": "Tristana",
    "voli": "Volibear", "eve": "Evelynn", "kass": "Kassadin", "malz": "Malzahar", "sera": "Seraphine",
    "vlad": "Vladimir", "renek": "Renekton", "sej": "Sejuani", "liss": "Lissandra", "ori": "Orianna",
    "fiddle": "Fiddlesticks", "cass": "Cassiopeia", "cassio": "Cassiopeia", "kata": "Katarina",
    "nid": "Nidalee", "nida": "Nidalee", "gp": "Gangplank", "mf": "MF", "tf": "TF", "lb": "LeBlanc",
    "ww": "Warwick", "twisted fate": "Twisted Fate", "miss fortune": "Miss Fortune", "master yi": "Master Yi",
    "lee sin": "Lee Sin", "lee": "Lee Sin", "aphelios": "Aphelios", "afelios": "Aphelios", "ilaoi": "Illaoi",
    "illaoi": "Illaoi", "qiyana": "Qiyana", "kiana": "Qiyana", "kiyana": "Qiyana", "naafiri": "Naafiri",
    "nafiri": "Naafiri", "hwei": "Hwei", "wei": "Hwei", "atakan": "Atakhan", "attakhan": "Atakhan",
    "atacan": "Atakhan", "atakhan": "Atakhan",
}

# jargon: how Whisper tends to write it -> how players write it (game chat only, whole words)
TERM_ALIASES = {
    "adc": "ADC", "a d c": "ADC", "a.d.c.": "ADC", "ap": "AP", "ad": "AD", "aoe": "AoE", "a o e": "AoE",
    "cc": "CC", "mr": "MR", "dps": "DPS", "cs": "CS", "xp": "XP", "lp": "LP", "kda": "KDA", "tp": "TP",
    "op": "OP", "afk": "AFK", "mia": "MIA", "aa": "AA", "cdr": "CDR", "ah": "AH", "ms": "MS", "hp": "HP",
    "ie": "IE", "bt": "BT", "ga": "GA", "pd": "PD", "rfc": "RFC", "botrk": "BotRK", "bork": "BotRK",
    "pta": "PTA", "gg": "gg", "g g": "gg", "wp": "wp", "w p": "wp", "ez": "ez", "e z": "ez", "ff": "ff",
    "f f": "ff", "gj": "gj", "nt": "nt", "ns": "ns", "ty": "ty", "np": "np", "brb": "brb", "gl": "gl",
    "hf": "hf", "oom": "oom", "o o m": "oom", "bg": "bg", "bm": "bm", "elo": "elo", "jg": "jg", "jgl": "jgl",
    "supp": "supp", "sup": "supp", "mid": "mid", "bot": "bot", "top": "top",
    "ulti": "ulti", "ulty": "ulti", "ultie": "ulti", "alty": "ulti", "ult": "ult", "alt": "ult",
    "gang": "gank", "ganging": "ganking", "ganged": "ganked", "gangs": "ganks", "gunk": "gank",
    "kyte": "kite", "kyting": "kiting", "peal": "peel", "peeling": "peeling",
    "barron": "Baron", "baren": "Baron", "byron": "Baron", "baron": "Baron", "nashor": "Nashor",
    "nasher": "Nashor", "nash or": "Nashor", "nashore": "Nashor", "nash": "Nash",
    "harold": "Herald", "herold": "Herald", "herald": "Herald", "shelly": "Shelly",
    "drake": "drake", "elder": "Elder", "soul": "Soul", "grubs": "grubs", "scuttle": "scuttle",
    "krugs": "Krugs", "crux": "Krugs", "gromp": "Gromp", "grump": "Gromp", "raptors": "Raptors",
    "in hib": "inhib", "in hip": "inhib", "inhip": "inhib", "inhib": "inhib", "inhibs": "inhibs",
    "nexus": "Nexus", "zhonya": "Zhonya's", "zhonyas": "Zhonya's", "zonias": "Zhonya's", "zonya": "Zhonya's",
    "zonyas": "Zhonya's", "honias": "Zhonya's", "honyas": "Zhonya's", "zhonias": "Zhonya's",
    "rabadon": "Rabadon's", "rabadons": "Rabadon's", "rabadans": "Rabadon's", "rabbadon": "Rabadon's",
    "liandry": "Liandry's", "liandrys": "Liandry's", "ggwp": "gg wp", "gg wp": "gg wp", "ggez": "gg ez",
    "leandry": "Liandry's", "ludens": "Luden's", "luden": "Luden's", "rylai": "Rylai's", "rylais": "Rylai's",
    "morello": "Morello", "mejai": "Mejai's", "mejais": "Mejai's", "dorans": "Doran's", "doran": "Doran's",
    "guinsoo": "Guinsoo's", "guinsoos": "Guinsoo's", "runaan": "Runaan's", "runaans": "Runaan's",
    "statik": "Statikk", "statikk": "Statikk", "sterak": "Sterak's", "steraks": "Sterak's",
    "shojin": "Shojin", "warmog": "Warmog's", "warmogs": "Warmog's", "jaksho": "Jak'Sho", "jak sho": "Jak'Sho",
    "randuin": "Randuin's", "randuins": "Randuin's", "deadmans": "Deadman's", "mercs": "Mercs", "merc": "Mercs",
    "tabis": "Tabis", "tabi": "Tabis", "swifties": "Swifties", "sorcs": "Sorcs", "sorc": "Sorcs",
    "triforce": "Triforce", "tri force": "Triforce", "thornmail": "Thornmail", "heartsteel": "Heartsteel",
    "kraken": "Kraken", "shieldbow": "Shieldbow", "navori": "Navori", "yuntal": "Yun Tal", "yun tal": "Yun Tal",
    "stridebreaker": "Stridebreaker", "hullbreaker": "Hullbreaker", "conqueror": "Conqueror",
    "conq": "Conq", "electrocute": "Electrocute", "aery": "Aery", "comet": "Comet", "grasp": "Grasp",
    "aftershock": "Aftershock", "fleet": "Fleet", "smite": "Smite", "flash": "Flash", "ignite": "Ignite",
    "exhaust": "Exhaust", "cleanse": "Cleanse", "barrier": "Barrier", "ghost": "Ghost", "teleport": "Teleport",
    "1v1": "1v1", "one v one": "1v1", "one vee one": "1v1", "2v2": "2v2", "two v two": "2v2",
    "5v5": "5v5", "five v five": "5v5", "ff15": "ff15", "ff 15": "ff15", "f f 15": "ff15",
}

# Whisper prompt.  faster-whisper keeps only the last 223 tokens of a prompt, so this list is kept to
# about 190 tokens (checked against the model's tokenizer) and the most important terms come LAST.
PROMPT_TERMS = [
    "Doran's", "Liandry's", "Rabadon's", "Zhonya's", "AoE", "CC",
    "K'Sante", "Bel'Veth", "Rek'Sai", "Vel'Koz", "Kog'Maw", "Kha'Zix", "Cho'Gath", "Kai'Sa", "Xin Zhao",
    "Illaoi", "Qiyana", "Yasuo", "Yone", "Ambessa", "Yunara", "Zaahen",
    "Smite", "Ignite", "Flash", "TP", "ulti", "ult", "gg", "wp", "ff", "ez", "OP",
    "int", "tilted", "diff", "CS", "supp", "jungler", "ADC", "Scuttle", "Krugs", "Gromp", "Raptors",
    "Void Grubs", "Atakhan", "Elder", "drake", "Rift Herald", "Baron Nashor", "inhib", "Nexus",
    "peel", "poke", "kite", "gank",
]
# words that must never be "corrected" into a champion name
FUZZY_STOPLIST = {"video", "nexus", "herald", "baron", "elder", "drake", "smite", "ignite", "flash", "cleanse",
                  "exhaust", "barrier", "teleport", "krugs", "gromp", "raptors", "wolves", "scuttle", "grubs",
                  "atakhan", "inhib", "tower", "turret", "minion", "minions", "jungle", "jungler", "support",
                  "mid", "top", "bot", "lane", "ward", "wards", "vision", "shelly", "nashor", "soul", "team",
                  "focus", "group", "push", "back", "recall", "reset", "roam", "gank", "kite", "peel", "poke",
                  "carry", "tank", "mage", "adc", "supp"}

_JARGON = ({re.sub(r"[^a-z0-9]", "", k.lower()) for k in TERM_ALIASES}
           | {re.sub(r"[^a-z0-9]", "", v.lower()) for v in TERM_ALIASES.values()}
           | {re.sub(r"[^a-z0-9]", "", t.lower()) for t in PROMPT_TERMS})

_CACHE_FILE = "champions.json"
_DDRAGON = "https://ddragon.leagueoflegends.com"
_champions: list[str] = list(CHAMPIONS)
_index: dict[str, str] = {}
_max_words = 3
_lock = threading.Lock()


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _build_index():
    global _index, _max_words
    idx = {}
    for name in _champions:
        idx[_key(name)] = name
    for alias, name in NAME_ALIASES.items():
        idx[_key(alias)] = name
    _index = idx
    _max_words = max(len(re.findall(r"[A-Za-z0-9']+", a)) for a in list(NAME_ALIASES) + _champions)


_build_index()


def champions() -> list[str]:
    return list(_champions)


def load_cached_champions(data_dir: str):
    """Use the champion list refreshed earlier (if any)."""
    global _champions
    try:
        with open(os.path.join(data_dir, _CACHE_FILE), encoding="utf-8") as f:
            data = json.load(f)
        names = data.get("names") or []
        if len(names) >= len(CHAMPIONS):
            with _lock:
                _champions = sorted(set(names) | set(CHAMPIONS))
                _build_index()
            log.debug("Champion list from cache: %d names (Data Dragon %s)", len(_champions), data.get("version"))
    except FileNotFoundError:
        pass
    except Exception as e:
        log.debug("champion cache unreadable: %s", e)


def refresh_champions(data_dir: str, max_age_days: float = 7.0):
    """Fetch the current champion list from Riot's Data Dragon in the background (new champions appear
    every few months).  Nothing about the user is sent; skipped while the cache is fresh."""
    path = os.path.join(data_dir, _CACHE_FILE)
    try:
        if time.time() - os.path.getmtime(path) < max_age_days * 86400:
            return
    except OSError:
        pass

    def work():
        try:
            import requests
            s = requests.Session()
            s.trust_env = False
            ver = s.get(f"{_DDRAGON}/api/versions.json", timeout=8).json()[0]
            data = s.get(f"{_DDRAGON}/cdn/{ver}/data/en_US/champion.json", timeout=15).json()["data"]
            names = sorted(c["name"] for c in data.values())
            if len(names) < 150:
                raise ValueError("unexpectedly short list")
            os.makedirs(data_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": ver, "fetched": time.time(), "names": names}, f)
            load_cached_champions(data_dir)
            log.info("Champion list refreshed: %d names (Data Dragon %s)", len(names), ver)
        except Exception as e:
            log.debug("champion list refresh skipped: %s", e)

    threading.Thread(target=work, name="champions-refresh", daemon=True).start()


def whisper_terms(extra: Optional[list] = None) -> list[str]:
    """Vocabulary for the Whisper prompt; the user's own game words go last (they matter most)."""
    terms = list(PROMPT_TERMS)
    for w in extra or []:
        w = w.strip()
        if w and w not in terms:
            terms.append(w)
    return terms


# --------------------------------------------------------------------------
# corrections
# --------------------------------------------------------------------------
_WORD = re.compile(r"[A-Za-z0-9']+|[^A-Za-z0-9']+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text)


def _fix_terms(text: str) -> str:
    """Whole-word / whole-phrase replacements from TERM_ALIASES (case-insensitive), longest first."""
    for phrase in sorted(TERM_ALIASES, key=len, reverse=True):
        repl = TERM_ALIASES[phrase]
        pattern = r"(?<![A-Za-z0-9'])" + re.escape(phrase).replace(r"\ ", r"\s+") + r"(?![A-Za-z0-9'])"
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text


def _fix_names(text: str) -> str:
    """Champion names: exact (letters-only) matches over 1-3 words; a capitalised unknown word that is very
    close to a name is taken as that name (Whisper writes names it does not know phonetically)."""
    toks = _tokens(text)
    out = []
    i = 0
    n = len(toks)
    while i < n:
        tok = toks[i]
        if not re.match(r"[A-Za-z0-9']", tok):
            out.append(tok)
            i += 1
            continue
        matched = False
        # try the longest phrase first (words separated by single spaces)
        for width in range(_max_words, 0, -1):
            j = i + 2 * (width - 1)
            if j >= n:
                continue
            words = toks[i:j + 1:2]
            seps = toks[i + 1:j:2]
            if any(not re.fullmatch(r"\s+", sep) for sep in seps) or any(not re.match(r"[A-Za-z0-9']", w) for w in words):
                continue
            phrase = " ".join(words)
            name = _index.get(_key(phrase))
            if not name:
                continue
            low = phrase.lower()
            if width == 1 and low in AMBIGUOUS_NAMES and not phrase[:1].isupper():
                continue                                   # "brand new", "graves", "vi" ... left alone
            if low == name.lower():
                out.append(name)                           # case only
            else:
                out.append(name)
            i = j + 1
            matched = True
            break
        if matched:
            continue
        key = _key(tok)
        if (tok[:1].isupper() and not tok.isupper() and len(key) >= 4 and key not in _index
                and key not in FUZZY_STOPLIST and key not in _JARGON):
            best, score = None, 0.0
            for name in _champions:
                if " " in name or name.lower() in AMBIGUOUS_NAMES:
                    continue
                r = SequenceMatcher(None, key, _key(name)).ratio()
                if r > score:
                    best, score = name, r
            # a sentence-initial word is capitalised anyway, so it has to be a closer match
            if best and score >= (0.82 if i == 0 else 0.8):
                out.append(best)
                i += 1
                continue
        out.append(tok)
        i += 1
    return "".join(out)


def correct_game_text(text: str) -> str:
    """Spelling of jargon, acronyms and champion names as players write them (game chat only)."""
    if not text:
        return text
    fixed = _fix_names(_fix_terms(text))
    if fixed != text:
        log.debug("Game vocabulary: %r -> %r", text, fixed)
    return fixed
