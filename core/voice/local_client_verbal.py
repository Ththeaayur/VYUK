"""verbalisation concern extracted from core/voice/local_client.py (refactor Phase 1 step 2). Behaviour identical; every name here stays importable from core.local_client."""

import json
import re
from ..config import DATA_DIR, config  # Phase 3 V1b: moved to core/voice/


_MARKUP_TAG_RE = re.compile(r"<[A-Za-z/][^>]*>")
def _ssmd_display(text: str) -> str:
    """Text with SSMD-subset markup removed (karaoke display /
    verbatim-backend spoken text). GUARDED: text with no
    markup-like tag is returned unchanged, byte for byte, so
    plain replies never pass through the module's tag strip
    (which would also strip any other <...> span, and would
    eat a comparison span if one shared the text with a tag —
    the one documented edge, inherited from the module's own
    strip-everything contract). A lone unknown tag (no subset
    tag present) is stripped too: a tag must never reach an
    engine on any backend."""
    if not text or not _MARKUP_TAG_RE.search(text):
        return text
    try:
        from . import mouth  # Phase 3 V1b: moved to core/voice/

        clean, _segments = mouth.parse_ssmd_tags(text)
        return clean
    except Exception:  # noqa: BLE001 — a strip fault never mutes her
        return text
_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
]
_TENS = [
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety",
]
def _int_to_words(n: int) -> str:
    """English words for 0 <= n < 10**12 (the callers' cap)."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        words = _TENS[n // 10]
        return f"{words}-{_ONES[n % 10]}" if n % 10 else words
    if n < 1000:
        words = f"{_ONES[n // 100]} hundred"
        return f"{words} {_int_to_words(n % 100)}" if n % 100 else words
    for scale, name in ((1_000_000_000, "billion"),
                        (1_000_000, "million"),
                        (1_000, "thousand")):
        if n >= scale:
            words = f"{_int_to_words(n // scale)} {name}"
            return f"{words} {_int_to_words(n % scale)}" if n % scale else words
    return _ONES[0]  # unreachable under the cap
def _indian_to_words(n: int) -> str:
    """Indian grouping for n >= 100_000 (v5.17, M13): crore (10**7)
    blocks, then lakh (10**5) blocks, then the sub-lakh remainder in
    the ordinary style — 250000 is "two lakh fifty thousand",
    15000000 is "one crore fifty lakh". Composed from _int_to_words
    so the sub-blocks read exactly as they always have."""
    if n >= 10_000_000:
        words = f"{_int_to_words(n // 10_000_000)} crore"
        rest = n % 10_000_000
        return f"{words} {_indian_to_words(rest)}" if rest else words
    if n >= 100_000:
        words = f"{_int_to_words(n // 100_000)} lakh"
        rest = n % 100_000
        return f"{words} {_int_to_words(rest)}" if rest else words
    return _int_to_words(n)
def _indian_comma_pattern(int_part: str) -> bool:
    """True when a comma'd integer is grouped the Indian way — the
    last group has 3 digits and every earlier group has 2, except
    the first, which has 1–2: "2,50,000", "12,50,000". Western
    grouping ("1,250,000", groups of 3 throughout) returns False."""
    groups = int_part.split(",")
    if len(groups) < 2 or len(groups[-1]) != 3:
        return False
    if not 1 <= len(groups[0]) <= 2:
        return False
    return all(len(group) == 2 for group in groups[1:-1])
def _grouped_int_words(int_part: str, value: int, has_fraction: bool) -> str:
    """The spoken words for one plain integer token (the M13
    boundary): Indian grouping (lakh/crore) applies to values
    >= 100_000 with NO fractional part that are written bare
    ("250000") or with Indian commas ("2,50,000"). A western-comma'd
    token ("1,250,000") keeps the western reading, and so does every
    decimal — the written grouping is the author's signal, and
    prices/decimals read as they always have."""
    if has_fraction or value < 100_000:
        return _int_to_words(value)
    # A trailing comma is sentence punctuation, not grouping.
    group_part = int_part[:-1] if int_part.endswith(",") else int_part
    if "," not in group_part or _indian_comma_pattern(group_part):
        return _indian_to_words(value)
    return _int_to_words(value)
def _year_to_words(year: int) -> str:
    """The spoken form of a 1900–2099 year: 1985 is "nineteen
    eighty-five", 2000 "two thousand", 2026 "twenty twenty-six"."""
    if year == 2000:
        return "two thousand"
    if 2001 <= year <= 2009:
        return f"two thousand {_ONES[year - 2000]}"
    high, low = divmod(year, 100)
    words = _int_to_words(high)
    if low == 0:
        return f"{words} hundred"
    if low < 10:
        return f"{words} oh {_ONES[low]}"
    return f"{words} {_int_to_words(low)}"
def _number_to_words(text: str) -> str | None:
    """Spoken form of one numeric token ("1,250", "3.5", "-7",
    "1,250.50", ".5"), or None when it isn't a plain number after
    all. Integers read in words up to the trillion cap; beyond it the
    digits are read out one by one (the graceful fallback — a grouped
    reading would need grouping the model may not have written).
    A bare 4-digit 1900–2099 token reads as a year."""
    negative = text.startswith("-")
    body = text[1:] if negative else text
    if not re.fullmatch(r"\d[\d,]*(?:\.\d+)?|\.\d+", body):
        return None
    int_part, dot, frac_part = body.partition(".")
    digits = int_part.replace(",", "")
    # A trailing comma is sentence punctuation ("In 2026, …"), not a
    # grouping separator — it doesn't disqualify the year reading.
    year_core = int_part[:-1] if int_part.endswith(",") else int_part
    if (not dot and "," not in year_core and len(year_core) == 4
            and year_core.isdigit() and 1900 <= int(year_core) <= 2099):
        words = _year_to_words(int(year_core))
    elif len(digits) > 12:
        words = " ".join(_ONES[int(d)] for d in digits)
    elif digits:
        words = _grouped_int_words(int_part, int(digits), has_fraction=bool(dot))
    else:
        words = "zero"
    if dot:
        words += " point " + " ".join(_ONES[int(d)] for d in frac_part)
    return f"minus {words}" if negative else words
_ORDINAL_IRREGULAR = {
    "one": "first", "two": "second", "three": "third", "five": "fifth",
    "eight": "eighth", "nine": "ninth", "twelve": "twelfth",
}
def _ordinal_word(word: str) -> str:
    if word in _ORDINAL_IRREGULAR:
        return _ORDINAL_IRREGULAR[word]
    if word.endswith("y"):
        return word[:-1] + "ieth"
    return word + "th"
def _ordinal_to_words(n: int) -> str:
    card = _int_to_words(n)
    if "-" in card:
        head, _, last = card.rpartition("-")
        return f"{head}-{_ordinal_word(last)}"
    parts = card.split(" ")
    parts[-1] = _ordinal_word(parts[-1])
    return " ".join(parts)
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_FENCE_OPEN_RE = re.compile(r"```.*", re.DOTALL)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
_QUOTE_RE = re.compile(r"(?m)^\s*>\s?")
_BULLET_RE = re.compile(r"(?m)^\s*[-*+•]\s+")
_HR_RE = re.compile(r"(?m)^\s*(?:-{3,}|_{3,}|\*{3,})\s*$")
_BOLD_STAR_RE = re.compile(r"\*\*(\S(?:[^*]*\S)?)\*\*")
_BOLD_UNDER_RE = re.compile(r"__(\S(?:[^_]*\S)?)__")
_STRIKE_RE = re.compile(r"~~(\S(?:[^~]*\S)?)~~")
_ITALIC_STAR_RE = re.compile(r"\*(\S(?:[^*\n]*\S)?)\*")
_ITALIC_UNDER_RE = re.compile(r"(?<![\w])_(\S(?:[^_\n]*\S)?)_(?![\w])")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s)\]]+")
_PATH_RES = (
    re.compile(r"[A-Za-z]:[\\/][^\s]*"),                    # C:\… / C:/…
    re.compile(r"~/[^\s]*"),                                # ~/…
    re.compile(r"(?:/[^\s/]+){2,}/?"),                      # /a/b[/c…]
    re.compile(r"(?:[\w.~-]+/)+[\w.~-]+\.[A-Za-z0-9]{1,5}"),  # a/b/file.ext
)
def _url_spoken(match: re.Match) -> str:
    """A URL speaks as its bare domain — the one part a listener can
    actually use. Falls back to "the link" when no host parses out."""
    host = match.group(0).split("://", 1)[-1]
    host = host.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    host = host.rstrip(".,;:!?")
    return host or "the link"
def _path_spoken(match: re.Match) -> str:
    """A file path speaks as "the file" (sentence punctuation that
    trailed the path is preserved)."""
    raw = match.group(0)
    trail = ""
    while raw and raw[-1] in ".,;:!?":
        trail = raw[-1] + trail
        raw = raw[:-1]
    return "the file" + trail
_CURRENCY_WORDS = {"₹": "rupees", "$": "dollars", "£": "pounds", "€": "euros"}
_CURRENCY_SINGULAR = {"₹": "rupee", "$": "dollar", "£": "pound", "€": "euro"}
_CURRENCY_RE = re.compile(r"([₹$£€])\s?(-?\d[\d,]*(?:\.\d+)?)")
_PERCENT_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s?%")
def _currency_spoken(match: re.Match) -> str:
    words = _number_to_words(match.group(2))
    if words is None:
        return match.group(0)
    unit = (_CURRENCY_SINGULAR[match.group(1)] if words == "one"
            else _CURRENCY_WORDS[match.group(1)])
    return f"{words} {unit}"
def _percent_spoken(match: re.Match) -> str:
    words = _number_to_words(match.group(1))
    return f"{words} percent" if words is not None else match.group(0)
_ABBREVIATIONS = (
    (re.compile(r"\be\.g\.", re.IGNORECASE), "for example"),
    (re.compile(r"\bi\.e\.", re.IGNORECASE), "that is"),
    (re.compile(r"\betc\.", re.IGNORECASE), "et cetera"),
    (re.compile(r"\bvs\.", re.IGNORECASE), "versus"),
    (re.compile(r"\bapprox\.", re.IGNORECASE), "approximately"),
    (re.compile(r"\bDr\."), "Doctor"),
    (re.compile(r"\bMr\."), "Mister"),
    (re.compile(r"\bMrs\."), "Missus"),
    (re.compile(r"\bMs\."), "Miz"),
    (re.compile(r"\bProf\."), "Professor"),
    (re.compile(r"\bFig\."), "Figure"),
    (re.compile(r"\bfig\."), "figure"),
)
_ORDINAL_RE = re.compile(r"(?<![\w.])(\d{1,3})(?:st|nd|rd|th)\b")
_NUMBER_RE = re.compile(
    r"(?<![\w.,/-])(-?(?:\d[\d,]*(?:\.\d+)?|\.\d+))(?![\d,]|\.\d|-\d)")
def _number_spoken(match: re.Match) -> str:
    return _number_to_words(match.group(1)) or match.group(0)
_SPELL_OUT = {
    "US", "UK", "UN", "EU", "FBI", "CIA", "PDF", "HTML", "HTTP",
    "HTTPS", "URL", "API", "CPU", "GPU", "USB", "GPS", "FAQ",
}
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,4}\b")
def _acronym_spoken(match: re.Match) -> str:
    token = match.group(0)
    if token in _SPELL_OUT or not re.search(r"[AEIOU]", token):
        return " ".join(token)
    return token
_DEFAULT_PRONUNCIATIONS: dict[str, str] = {
    # Her name and the user's name.
    # VYUK: the wake grammar's phonetic pair ("via oak") is what
    # the spotter hears for the spoken name — VYE-uk; "vyuck"
    # phonemizes to exactly that (v-aɪ-ʌk) in the measured G2P.
    "VYUK": "vyuck",
    "Aayur": "ah-yur",
    "Gattani": "gat-ah-nee",
    # Tech vocabulary the normaliser/G2P mangles or leaves to luck.
    "GHz": "gigahertz",          # pin: G2P already expands it
    "MHz": "megahertz",          # pin: G2P already expands it
    "kHz": "kilohertz",          # G2P said "kay aitch-zed"
    "SQL": "sequel",             # acronym pass spelled S-Q-L
    "USB-C": "U S B C",          # the hyphen broke the USB spell-out
    "Wi-Fi": "why-fy",           # pin: G2P already reads why-fy
    "Jupyter": "jew-pi-ter",     # G2P said "jup-iter"
    "LaTeX": "lah-tek",          # G2P split it: "la tee eks"
    "sudo": "soo-doh",           # G2P said "syoo-doh"
    "Xiaomi": "shau-mee",        # G2P said "zy-a-roh-mee"
    "IDE": "I D E",              # G2P said the word "ide"
    "LED": "L E D",              # G2P said the word "led"
    "IoT": "eye oh tee",         # G2P said "ee-oh-tee"
    # Indian places.
    "Mumbai": "mum-bye",         # pin
    "Delhi": "del-ee",           # pin
    "Bengaluru": "beng-ga-loo-roo",  # G2P swallowed the vowels
    "Chennai": "chen-nye",       # stress: chen-NYE, not CHEN-eye
    "Kolkata": "kol-kata",       # pin
    "Hyderabad": "hy-dra-bad",   # G2P said "hy-der-a-bad"
    "Pune": "poo-nay",           # G2P said "poo-na"
    "Chandigarh": "chun-dee-garh",   # G2P said "chun-dig-er"
    "Guwahati": "goo-wah-hah-tee",
    "Thiruvananthapuram": "thi-roo-va-nun-ta-poo-ram",
    "Nagpur": "nahg-pur",
    "Kanpur": "kahn-pur",
    # Common Indian first names G2P flattens.
    "Aarav": "ah-ruv",
    "Ananya": "ah-nun-ya",
    "Kavya": "kahv-ya",
    "Meera": "mee-ra",
    "Anika": "uh-nee-ka",
}
def _pronunciation_map() -> dict[str, str]:
    """The sounds-like dictionary (v5.17, M12; grown v5.43, M5):
    the shipped _DEFAULT_PRONUNCIATIONS table, overridden and
    extended by the user's own entries from
    config.pronunciations_json — a JSON object {"word": "sounds
    like"} (Settings' Pronunciations box writes it to
    PRONUNCIATIONS_JSON in the .env file next to main.py; see
    TROUBLESHOOTING.md §39). User entries win case-insensitively.
    Anything malformed (bad JSON, a non-object top level,
    non-string or empty entries) degrades to fewer/no user
    overrides — the shipped defaults always survive, and a broken
    dictionary must never break her voice."""
    out: dict[str, str] = dict(_DEFAULT_PRONUNCIATIONS)
    # v5.58 (Wave 3, MO-1/MO-6): the personal lexicon from
    # core/voice/mouth.py joins the map ADDITIVELY — only words
    # absent from the shipped map (case-insensitively) are
    # added, so no shipped pin is ever overridden here. The
    # user's own config entries below still win over both.
    try:
        from . import mouth  # Phase 3 V1b: moved to core/voice/

        for word, sounds in mouth.PERSONAL_RESPELLINGS.items():
            if not any(existing.lower() == word.lower()
                       for existing in out):
                out[word] = sounds
    except Exception:  # noqa: BLE001 — a lexicon fault never mutes her
        pass
    raw = getattr(config, "pronunciations_json", "") or ""
    if not isinstance(raw, str) or not raw.strip():
        return out
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return out
    if not isinstance(data, dict):
        return out
    for word, sounds in data.items():
        if (isinstance(word, str) and isinstance(sounds, str)
                and word.strip() and sounds.strip()):
            for existing in list(out):
                if existing.lower() == word.lower():
                    del out[existing]
            out[word] = sounds
    return out
def _apply_pronunciations(text: str) -> str:
    """Whole-word, case-insensitive sounds-like replacements (M12).
    Runs before every other normalization step in _speech_text, so
    the replacement text is then treated like any other prose."""
    mapping = _pronunciation_map()
    if not mapping:
        return text
    out = text
    for word, sounds in mapping.items():
        pattern = re.compile(
            r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
        out = pattern.sub(lambda _match, s=sounds: s, out)
    return out
def _speech_text(text: str) -> str:
    """Rewrites reply text into its spoken form (see the section
    header). Speech only — the caller keeps the original for chat."""
    if not text:
        return ""
    # v5.58 (Wave 3, MO-9): SSMD-subset tags are brain markup,
    # never speech — strip them (inner text kept, other tags
    # stripped, per core/voice/mouth.py) BEFORE any number /
    # symbol pass, so a tag's digits are never read aloud as
    # numbers (the pre-v5.58 behaviour spoke <break
    # time="200ms"/> literally, digits expanded). _ssmd_display
    # is guarded: text with no markup-like tag passes through
    # untouched, so plain-text normalization is byte-identical.
    out = _ssmd_display(text)
    out = _FENCE_RE.sub(" ", _apply_pronunciations(out))
    out = _FENCE_OPEN_RE.sub(" ", out)
    out = _IMAGE_RE.sub(r"\1", out)
    out = _LINK_RE.sub(r"\1", out)
    out = _INLINE_CODE_RE.sub(r"\1", out)
    out = _HR_RE.sub(" ", out)
    out = _HEADING_RE.sub("", out)
    out = _QUOTE_RE.sub("", out)
    out = _BULLET_RE.sub("", out)
    out = out.replace(" • ", ", ")
    out = _BOLD_STAR_RE.sub(r"\1", out)
    out = _BOLD_UNDER_RE.sub(r"\1", out)
    out = _STRIKE_RE.sub(r"\1", out)
    out = _ITALIC_STAR_RE.sub(r"\1", out)
    out = _ITALIC_UNDER_RE.sub(r"\1", out)
    # v5.58 (Wave 3, MO-7): Indian-numbering context pass —
    # day-first dates, ₹ amounts with paise, 10-digit phones.
    # It runs BEFORE the URL/path passes (a "25/10/2026" date
    # would otherwise be mangled into "the file" by the path
    # patterns) and before the generic currency/number passes,
    # so its word output is never re-processed by them; $
    # amounts and anything it does not recognise pass through
    # untouched. MOVED STRING (coordinator pre-pin record):
    # "₹1,250.50" used to read "one thousand two hundred fifty
    # point five zero rupees"; it now reads "one thousand two
    # hundred fifty rupees and fifty paise".
    try:
        from . import mouth  # Phase 3 V1b: moved to core/voice/

        out = mouth.indian_context_text(out)
    except Exception:  # noqa: BLE001 — context fault = generic path
        pass
    out = _URL_RE.sub(_url_spoken, out)
    for pattern in _PATH_RES:
        out = pattern.sub(_path_spoken, out)
    out = _CURRENCY_RE.sub(_currency_spoken, out)
    out = _PERCENT_RE.sub(_percent_spoken, out)
    for pattern, spoken in _ABBREVIATIONS:
        out = pattern.sub(spoken, out)
    out = _ORDINAL_RE.sub(
        lambda m: _ordinal_to_words(int(m.group(1))), out)
    out = _NUMBER_RE.sub(_number_spoken, out)
    out = _ACRONYM_RE.sub(_acronym_spoken, out)
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    return out.strip()
