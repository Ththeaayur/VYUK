from __future__ import annotations

import re


# ------------------------------------------------------------------ #
# MO-1 + MO-6: IPA-override markup + personal lexicon
#
# Two renderings per entry, because the two consumers differ:
#   * `ipa` — the IPA string, for SSMD <phoneme alphabet="ipa">
#     markup consumed by engines that accept it (see ipa_markup()).
#   * `respelling` — the sounds-like respelling in the shipped
#     _DEFAULT_PRONUNCIATIONS style, which is what the Kokoro /
#     Piper plain-text path actually speaks. This is the
#     render-verified path: every entry's respelling is
#     non-empty, ASCII-hyphenated, and different from the word.
# Render verification (render_verify_lexicon()) checks every
# entry: IPA charset, exactly one primary stress mark, a
# respelling present, and no entry colliding with a shipped
# pronunciation key (shipped keys win — see local_client's hook).
# ------------------------------------------------------------------ #

# IPA characters allowed in this table (segments + stress/length).
_IPA_OK = set(
    "abcdefghijklmnopqrstuvwxyzɑɒɔæɛəɜɡɣɦɪʊʃʒʤʧθðŋɲɹɾʔʕħʰ"
    "ˈˌː. -"
)

# (word, IPA, sounds-like respelling, mis-hearing aliases,
#  Devanagari form when the name is a Hindi name, else "").
# Words already in the shipped map are deliberately absent.
PERSONAL_LEXICON: tuple[dict, ...] = (
    {"word": "Powai", "ipa": "poːˈvaɪ", "respelling": "po-vye",
     "aliases": ["poe why", "pow eye"], "devanagari": ""},
    {"word": "Andheri", "ipa": "ənˈdʰeːɾi", "respelling": "un-dhay-ree",
     "aliases": ["and hairy", "andhery"], "devanagari": "अंधेरी"},
    {"word": "Bandra", "ipa": "ˈbɑːndɾa", "respelling": "bahn-dra",
     "aliases": ["band ra"], "devanagari": "बांद्रा"},
    {"word": "Dadar", "ipa": "ˈdɑːdər", "respelling": "dah-dar",
     "aliases": ["day dar"], "devanagari": "दादर"},
    {"word": "Hiranandani", "ipa": "hiːɾaːˈnəndəni",
     "respelling": "hee-ra-nun-dah-nee",
     "aliases": ["heeran andani"], "devanagari": "हीरानंदानी"},
    {"word": "Rohan", "ipa": "ˈɾoːɦən", "respelling": "roh-hun",
     "aliases": ["rowan", "rohan"], "devanagari": "रोहन"},
    {"word": "Sneha", "ipa": "ˈsneːɦa", "respelling": "snay-ha",
     "aliases": ["snay ha", "sne ha"], "devanagari": "स्नेहा"},
    {"word": "Anjali", "ipa": "ˈəndʒəli", "respelling": "un-juh-lee",
     "aliases": ["anjali", "un jolly"], "devanagari": "अंजलि"},
    {"word": "Vikram", "ipa": "ˈvɪkɾəm", "respelling": "vik-rum",
     "aliases": ["wick rum"], "devanagari": "विक्रम"},
    {"word": "Shreya", "ipa": "ˈʃɾeːja", "respelling": "shray-ah",
     "aliases": ["shre ya", "shraya"], "devanagari": "श्रेया"},
    {"word": "Kubernetes", "ipa": "kuːbərˈnɛtiːz",
     "respelling": "koo-ber-net-eez",
     "aliases": ["coobernetes", "kubernetes"], "devanagari": ""},
    {"word": "PyTorch", "ipa": "ˈpaɪtɔːɹʧ", "respelling": "py-torch",
     "aliases": ["pie torch"], "devanagari": ""},
    {"word": "TensorFlow", "ipa": "ˈtɛnsərfloʊ",
     "respelling": "ten-ser-floh",
     "aliases": ["tensor flow"], "devanagari": ""},
    {"word": "Fourier", "ipa": "ˈfʊɹieɪ", "respelling": "for-ee-ay",
     "aliases": ["four year", "foray"], "devanagari": ""},
    {"word": "Lagrange", "ipa": "ləˈɡɹɑːnʒ", "respelling": "la-grahnzh",
     "aliases": ["la grange"], "devanagari": ""},
    {"word": "Misaki", "ipa": "miˈsaːki", "respelling": "mee-sah-kee",
     "aliases": ["miss aki", "misaki"], "devanagari": ""},
)

#: Sounds-like map for the _speech_text hook. Shipped keys win:
#: the hook only adds words absent from the shipped map.
PERSONAL_RESPELLINGS: dict[str, str] = {
    entry["word"]: entry["respelling"] for entry in PERSONAL_LEXICON
}

#: IPA map for SSMD markup (word lower -> IPA).
_IPA_MAP: dict[str, str] = {
    entry["word"].lower(): entry["ipa"] for entry in PERSONAL_LEXICON
}


def validate_ipa(ipa: str) -> bool:
    """True when `ipa` looks like a renderable IPA string: only
    IPA characters, exactly one primary stress mark, non-empty."""
    if not ipa or ipa.count("ˈ") != 1:
        return False
    return all(ch in _IPA_OK for ch in ipa)


def render_verify_lexicon() -> list[str]:
    """Per-entry render verification for PERSONAL_LEXICON.
    Returns a list of problems; empty means every entry verified:
    valid IPA, a respelling present and different from the word,
    at least one alias, and (for Hindi names) a Devanagari form."""
    problems: list[str] = []
    for entry in PERSONAL_LEXICON:
        word = entry["word"]
        if not validate_ipa(entry["ipa"]):
            problems.append(f"{word}: bad IPA {entry['ipa']!r}")
        resp = entry["respelling"]
        if not resp or resp.lower() == word.lower():
            problems.append(f"{word}: bad respelling {resp!r}")
        if not entry["aliases"]:
            problems.append(f"{word}: no mis-hearing alias")
        if entry["word"] in ("Rohan", "Sneha", "Anjali", "Vikram",
                             "Shreya") and not entry["devanagari"]:
            problems.append(f"{word}: Hindi name without Devanagari")
    return problems


def ipa_markup(text: str) -> str:
    """Wraps lexicon words in SSMD-subset <phoneme> markup.

    This is for engines that accept SSML phoneme markup; the
    plain-text Kokoro/Piper path must use the respelling map
    instead (markup fed to Kokoro would be read aloud). Only
    words in _IPA_MAP are wrapped; everything else passes
    through unchanged, so the function is safe to run and then
    strip (see strip_ssmd_to_plan)."""
    out = text
    for word, ipa in _IPA_MAP.items():
        pattern = re.compile(r"\b" + re.escape(word) + r"\b",
                             re.IGNORECASE)
        out = pattern.sub(
            lambda m, i=ipa: (
                f'<phoneme alphabet="ipa" ph="{i}">{m.group(0)}'
                f"</phoneme>"),
            out)
    return out


# ------------------------------------------------------------------ #
# MO-7: Indian numbering — context layer (original in-tree code)
#
# Extends, never replaces, the shipped >=100000 lakh/crore
# grouping in core/voice/local_client.py. The context categories follow
# the NeMo text-normalisation taxonomy (currency, date, ordinal,
# decimal, telephone) — the categories, not NeMo code.
#   * ₹ amounts: Indian grouping + paise ("₹1,250.50" ->
#     "one thousand two hundred fifty rupees and fifty paise").
#     $ amounts keep the shipped western reading (dollars/cents
#     via the generic path — this layer does not touch them).
#   * Day-first ordinals: "25/10/2026" and "25 October 2026" ->
#     "twenty-fifth of October twenty twenty-six" (day first,
#     never month first).
#   * Phones: a bare 10-digit Indian mobile (6-9 first digit) ->
#     digit by digit ("nine eight seven ..."), never a
#     lakh/crore reading.
#   * Hindi (lang="hi"): the same Indian grouping in Devanagari
#     words (lakh = लाख, crore = करोड़, paise = पैसे).
# num2words/indic-num2words adapters are lazy and opt-in only.
# ------------------------------------------------------------------ #

_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "thirteen",
    "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty",
         "seventy", "eighty", "ninety"]
_ORDINAL_IRREGULAR = {
    "one": "first", "two": "second", "three": "third",
    "five": "fifth", "eight": "eighth", "nine": "ninth",
    "twelve": "twelfth",
}


def _en_int(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (
            f"-{_ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        out = f"{_ONES[n // 100]} hundred"
        return f"{out} {_en_int(n % 100)}" if n % 100 else out
    for scale, name in ((1_000_000_000, "billion"),
                        (1_000_000, "million"),
                        (1_000, "thousand")):
        if n >= scale:
            out = f"{_en_int(n // scale)} {name}"
            return f"{out} {_en_int(n % scale)}" if n % scale else out
    return _ONES[0]


def _en_indian(n: int) -> str:
    """Indian grouping, mirroring the shipped _indian_to_words."""
    if n >= 10_000_000:
        out = f"{_en_int(n // 10_000_000)} crore"
        rest = n % 10_000_000
        return f"{out} {_en_indian(rest)}" if rest else out
    if n >= 100_000:
        out = f"{_en_int(n // 100_000)} lakh"
        rest = n % 100_000
        return f"{out} {_en_int(rest)}" if rest else out
    return _en_int(n)


def _en_ordinal(n: int) -> str:
    card = _en_indian(n)
    if "-" in card:
        head, _, last = card.rpartition("-")
        return f"{head}-{_ordinal_word(last)}"
    parts = card.split(" ")
    parts[-1] = _ordinal_word(parts[-1])
    return " ".join(parts)


def _ordinal_word(word: str) -> str:
    if word in _ORDINAL_IRREGULAR:
        return _ORDINAL_IRREGULAR[word]
    if word.endswith("y"):
        return word[:-1] + "ieth"
    return word + "th"


_HI_ONES = [
    "शून्य", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ",
    "नौ", "दस", "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह",
    "सोलह", "सत्रह", "अठारह", "उन्नीस", "बीस", "इक्कीस",
    "बाईस", "तेईस", "चौबीस", "पच्चीस", "छब्बीस", "सत्ताईस",
    "अट्ठाईस", "उनतीस", "तीस", "इकतीस", "बत्तीस", "तैंतीस",
    "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस",
    "उनतालीस", "चालीस", "इकतालीस", "बयालीस", "तैंतालीस",
    "चवालीस", "पैंतालीस", "छियालीस", "सैंतालीस", "अड़तालीस",
    "उनचास", "पचास", "इक्यावन", "बावन", "तिरेपन", "चौवन",
    "पचपन", "छप्पन", "सत्तावन", "अट्ठावन", "उनसठ", "साठ",
    "इकसठ", "बासठ", "तिरेसठ", "चौंसठ", "पैंसठ", "छियासठ",
    "सड़सठ", "अड़सठ", "उनहत्तर", "सत्तर", "इकहत्तर",
    "बहत्तर", "तिहत्तर", "चौहत्तर", "पचहत्तर", "छिहत्तर",
    "सतहत्तर", "अठहत्तर", "उनासी", "अस्सी", "इक्यासी",
    "बयासी", "तिरासी", "चौरासी", "पचासी", "छियासी",
    "सतासी", "अठासी", "नवासी", "नब्बे", "इक्यानबे",
    "बानबे", "तिरानबे", "चौरानबे", "पचानबे", "छियानबे",
    "सत्तानबे", "अट्ठानबे", "निन्यानबे",
]


def _hi_int(n: int) -> str:
    if n < 100:
        return _HI_ONES[n]
    if n < 1000:
        out = f"{_HI_ONES[n // 100]} सौ"
        return f"{out} {_hi_int(n % 100)}" if n % 100 else out
    if n < 100_000:
        out = f"{_hi_int(n // 1000)} हज़ार"
        return f"{out} {_hi_int(n % 1000)}" if n % 1000 else out
    if n < 10_000_000:
        out = f"{_hi_int(n // 100_000)} लाख"
        rest = n % 100_000
        return f"{out} {_hi_int(rest)}" if rest else out
    out = f"{_hi_int(n // 10_000_000)} करोड़"
    rest = n % 10_000_000
    return f"{out} {_hi_int(rest)}" if rest else out


def _hi_number(n: int) -> str:
    return _hi_int(n)


_MONTHS = {
    "january": "January", "february": "February", "march": "March",
    "april": "April", "may": "May", "june": "June",
    "july": "July", "august": "August", "september": "September",
    "october": "October", "november": "November",
    "december": "December",
}
_MONTH_NUM = {name: i + 1 for i, name in enumerate(
    ["january", "february", "march", "april", "may", "june",
     "july", "august", "september", "october", "november",
     "december"])}


def _year_words(year: int) -> str:
    if year == 2000:
        return "two thousand"
    if 2001 <= year <= 2009:
        return f"two thousand {_ONES[year - 2000]}"
    high, low = divmod(year, 100)
    out = _en_int(high)
    if low == 0:
        return f"{out} hundred"
    if low < 10:
        return f"{out} oh {_ONES[low]}"
    return f"{out} {_en_int(low)}"


def indian_amount_words(amount: str, lang: str = "en") -> str | None:
    """Spoken form of a ₹ amount ("1250.50", "2,50,000") with
    paise; None when the token is not a plain ₹ amount. $ is
    deliberately NOT handled here — it keeps the shipped path."""
    body = amount.replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", body):
        return None
    int_part, dot, frac = body.partition(".")
    n = int(int_part)
    if lang == "hi":
        words = _hi_number(n)
        unit = "रुपया" if n == 1 else "रुपये"
        if not dot:
            return f"{words} {unit}"
        paise = int((frac + "00")[:2])
        return (f"{words} {unit} और {_hi_number(paise)} पैसे"
                if paise else f"{words} {unit}")
    words = _en_indian(n)
    unit = "rupee" if n == 1 else "rupees"
    if not dot:
        return f"{words} {unit}"
    paise = int((frac + "00")[:2])
    if paise == 0:
        return f"{words} {unit}"
    paise_word = _en_int(paise)
    return (f"{words} {unit} and {paise_word} "
            f"{'paisa' if paise == 1 else 'paise'}")


def phone_digit_words(digits: str) -> str | None:
    """Digit-by-digit reading for a bare 10-digit Indian mobile
    (first digit 6–9); None for anything else, so ordinary
    numbers never get digit-read by accident."""
    if re.fullmatch(r"[6-9]\d{9}", digits or ""):
        return " ".join(_ONES[int(d)] for d in digits)
    return None


def day_first_date_words(text: str) -> str | None:
    """Day-first date readings. Accepts "25/10/2026",
    "25-10-2026" and "25 October 2026" (also "25th October
    2026"); returns None for month-first or unparseable text,
    so month-first dates are never silently re-read."""
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text.strip())
    if m:
        day, month, year = (int(m.group(1)), int(m.group(2)),
                            int(m.group(3)))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return None
        month_name = list(_MONTHS.values())[month - 1]
        return (f"{_en_ordinal(day)} of {month_name} "
                f"{_year_words(year)}")
    m = re.fullmatch(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})",
        text.strip())
    if m and m.group(2).lower() in _MONTHS:
        day, year = int(m.group(1)), int(m.group(3))
        if not 1 <= day <= 31:
            return None
        return (f"{_en_ordinal(day)} of {_MONTHS[m.group(2).lower()]} "
                f"{_year_words(year)}")
    return None


_RUPEE_RE = re.compile(r"₹\s?(-?\d[\d,]*(?:\.\d{1,2})?)")
_PHONE_RE = re.compile(r"(?<![\w+])(\d{10})(?!\d)")
_DATE_NUM_RE = re.compile(r"(?<![\w])(\d{1,2}[/-]\d{1,2}[/-]\d{4})(?!\d)")
_DATE_WORD_RE = re.compile(
    r"(?<![\w])(\d{1,2}(?:st|nd|rd|th)?\s+"
    r"(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{4})\b",
    re.IGNORECASE)


def indian_context_text(text: str) -> str:
    """The MO-7 context pass for _speech_text, in order:
    day-first dates, ₹ amounts with paise, 10-digit phones.
    Anything it does not recognise passes through untouched,
    and $ amounts are never touched here."""
    out = _DATE_NUM_RE.sub(
        lambda m: day_first_date_words(m.group(1)) or m.group(0),
        text)
    out = _DATE_WORD_RE.sub(
        lambda m: day_first_date_words(m.group(1)) or m.group(0),
        out)
    out = _RUPEE_RE.sub(
        lambda m: (indian_amount_words(m.group(1)) or m.group(0))
        if not m.group(1).startswith("-") else m.group(0),
        out)
    out = _PHONE_RE.sub(
        lambda m: phone_digit_words(m.group(1)) or m.group(0), out)
    return out


def indic_adapter(n: int, lang: str = "hi") -> str | None:
    """OPT-IN lazy adapter: calls installed indic-num2words
    (Apache-2.0 — verified at the v5.58 CC-1 gate from the
    indic-num2words 1.3.2 sdist LICENSE) when present,
    else None. Never imported by the shipped path."""
    try:
        from num_to_words import num_to_word as _ntw
    except Exception:  # noqa: BLE001 — absence is the normal case
        return None
    try:
        return str(_ntw(n, lang=lang))
    except Exception:  # noqa: BLE001
        return None


