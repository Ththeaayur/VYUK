"""U7 — Spoken maths (tutor programme, 2026-11-08).

Lab leg only. The real-Windows field leg — a real MA 105 spoken check
on his machine, the tutor speaking "seven over three" and grading his
spoken answer, never marking a mishearing wrong — is OWED, not this
batch.

Gap proved first (audit T7), with live evidence from the tree:
- The TTS speech path mangles maths. ``_speech_text`` (``core/voice/
  local_client_verbal.py:337``) turns "7/3" into "seven/3", "x^2 + 3"
  into "x^two + three", and passes "d/dx of x squared" through
  untouched ("d slash dx"). ``_number_to_words("7/3")`` returns None
  (``core/voice/local_client_verbal.py:110``) — fractions are not
  spoken at all. There is no speech-string table and no spoken-maths
  lexicon anywhere in ``core/`` or ``gui/`` (grep: zero hits for
  "speech string", "seven over", "x squared" as data).
- The grader cannot hear maths. ``grade_answer`` (``core/brain/
  check_loop.py:244``) takes text, normalises it and parses DIGIT
  tokens (``_numbers`` — ``[]`` when a text carries no digits).
  Probed live, no judge: "seven over three", "seven thirds",
  "thirty-two pi" and "x squared" against golds "7/3", "32 pi",
  "x^2" all return ``fail_safe`` / "no_judge" — a spoken correct
  answer grades as "not proven". What exists is general-text
  verbalisation (integers, currency, percent, ordinals, acronyms)
  and digit-based grading; what is missing is the maths layer in
  both directions.
- The written-form gold guard is blind to spoken gold. Probed live:
  ``_contains_gold("seven over three", "7/3")`` is False (no digit
  tokens, no substring) — so a tutor that spoke the gold's canonical
  string would pass every guard U3/U5 have. U7 closes this with
  ``spoken_reveals_gold`` (below); the production tutor session
  driver MUST pass every spoken string through it before speaking
  (owned residual, same shape as the U3 variant residual: the raw
  CheckSession path still relies on the production variant_fn being
  gold-guarded).

LOPHOLE FAMILY — "a tutor that mishears maths and marks it wrong":
a spoken answer arrives as words, and every way of saying the same
maths ("seven over three", "seven thirds", "seven divided by three",
"2.333...") must grade the same; a tutor that grades the words
instead of the maths marks correct answers wrong, and a tutor that
reads notation aloud letter-by-letter ("x caret two") teaches
nothing. ONE design closes the class: SRE speech strings as the
CANONICAL spoken form — one canonical speech string per maths form,
held in a per-language table (English first, the global default);
the tutor SPEAKS the canonical string (never raw notation), and the
grader NORMALISES a spoken answer to the same canonical form before
U3's grading runs (spoken words -> canonical written form -> U3's
exact / numeric / tolerance / judge order, unchanged). All numeric
work is Fraction-exact, never float (U3's rule). The normaliser
translates form, never evaluates: "two squared" becomes "2^2", not
"4" — evaluation is the judge's job, not the normaliser's.

REJECTED alternatives (recorded, not carried forward):
- Grading the raw transcript as text (every paraphrase of the same
  answer grades differently — the loophole itself).
- A prompt that tells the brain to "speak maths nicely"
  (unverifiable, unrepeatable, changes with the model — the
  canonical string is code, not a prompt).
- Hardcoding English speech strings into the grader (the global
  direction breaks the moment a second language arrives — the table
  is data, the grader reads it).
- Reading notation aloud verbatim ("x caret two plus three" — the
  student hears symbols, not maths).
- A second grading path that bypasses U3 (two graders drift apart
  — U7 normalises INTO U3, it does not replace it).
- Reusing the TTS path's number-to-words for the core (it is general
  prose machinery — it returns None for fractions and mangles "7/3"
  — and it is private to the voice package; U7 spells maths from its
  own per-language table, so a second language's number words work
  without touching the voice package).

NAMED LIMITATIONS (not loopholes — stated, tested, owned):
- Unparseable spans pass through unchanged (``speak_expression`` /
  ``speak_text`` return the input when no pilot form matches).
- Derivative / integral PROSE answers are not normalised to a
  symbolic form ("the derivative of x squared" has no canonical
  written form the grader could match) — they go to U3's judge,
  and with no judge they are "not proven".
- An answer carrying two different maths claims ("seven over three
  or maybe eight over three") is ambiguous and normalises to None —
  the tutor must ask, never guess which claim the student meant.
- "a" / "an" as the number one ("a hundred", "a half" without a
  numerator is covered by the bare ordinal rule) is not parsed.
"""

from __future__ import annotations

import copy
import json
import re
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from . import student_model as sm
from .check_loop import _contains_gold, grade_answer

TABLE_FILENAME = "SPOKEN_MATHS_{lang}.md"
TABLE_BLOCK = "spoken-maths"

# The built-in English table — the fallback when no file is present,
# and the shape a second language's file must match. The owner-facing
# copy lives in SPOKEN_MATHS_EN.md at the tree root (same content,
# plus the plain-language reading guide).
DEFAULT_TABLE = {
    "language": "en",
    "ones": {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
        "fourteen": 14, "fifteen": 15, "sixteen": 16,
        "seventeen": 17, "eighteen": 18, "nineteen": 19,
    },
    "tens": {
        "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
        "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    },
    "scales": {
        "hundred": 100, "thousand": 1000, "million": 1000000,
    },
    "point": "point",
    "negative": ["negative", "minus"],
    "fraction_words": ["over", "divided by", "out of"],
    "ordinal_denominators": {
        "half": 2, "halves": 2,
        "third": 3, "thirds": 3,
        "quarter": 4, "quarters": 4,
        "fifth": 5, "fifths": 5,
        "sixth": 6, "sixths": 6,
        "seventh": 7, "sevenths": 7,
        "eighth": 8, "eighths": 8,
        "ninth": 9, "ninths": 9,
        "tenth": 10, "tenths": 10,
    },
    "power_words": {
        "squared": 2, "cubed": 3,
    },
    "power_phrase": "to the power of",
    "constants": {
        "pi": "pi",
    },
    "operators": {
        "plus": "+", "minus": "-", "times": "*", "multiplied by": "*",
    },
    "variables": ["x", "y", "z", "t", "theta"],
    "units": [
        "metre", "metres", "meter", "meters",
        "second", "seconds", "minute", "minutes",
        "radian", "radians", "degree", "degrees",
        "unit", "units",
    ],
    "symbols": {
        "integral": "the integral",
        "double integral": "the double integral",
        "triple integral": "the triple integral",
        "line integral": "the line integral",
        "surface integral": "the surface integral",
        "divergence of": "the divergence of",
        "curl of": "the curl of",
        "square root of": "the square root of",
    },
    "derivative_words": {
        "derivative": "the derivative of",
        "partial": "the partial derivative of",
        "with respect to": "with respect to",
        "partial bare": "partial",
    },
    "integral_words": {
        "integral": "the integral",
        "from": "from",
        "to": "to",
        "of": "of",
        "over": "over",
        "along": "along",
    },
}

# Filler a student puts before the answer — stripped only by the
# bare-number whole-answer rule, never by the distinctive-span scan.
_FILLER_PREFIXES = (
    "the answer is", "it is", "it's", "i think it's",
    "i think it is", "maybe", "probably",
)

_SUPERSCRIPTS = {
    "⁰": "^0", "¹": "^1", "²": "^2", "³": "^3", "⁴": "^4",
    "⁵": "^5", "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9",
    "⁻": "^-",
}


# ------------------------------------------------------------------
# The table (per-language data)
# ------------------------------------------------------------------

def _default_table_path(language):
    # core/brain/spoken_maths.py -> parents[2] is the tree root.
    return Path(__file__).resolve().parents[2] / (
        TABLE_FILENAME.format(lang=str(language or "en").upper()))


def _table_block(text):
    match = re.search(
        r"```" + re.escape(TABLE_BLOCK) + r"\s*\n(.*?)```",
        text, re.DOTALL)
    return match.group(1) if match else None


def _normalise_table(raw):
    """Overlay a file's table on the built-in defaults. Unknown keys
    are ignored; a bad value keeps its default. Never raises."""
    table = copy.deepcopy(DEFAULT_TABLE)
    if not isinstance(raw, dict):
        return table
    for key, default in DEFAULT_TABLE.items():
        value = raw.get(key)
        if value is None:
            continue
        try:
            if isinstance(default, dict):
                if not isinstance(value, dict) or not value:
                    continue
                # A language defines its own words: a valid file
                # dict REPLACES the default wholesale (a partial
                # overlay would mix two languages' number words).
                # Valid means every value matches the default's
                # value type (int for the number maps, str for the
                # word maps); anything else keeps the default.
                sample = next(iter(default.values()), None)
                if isinstance(sample, int):
                    if all(isinstance(k, str) and isinstance(v, int)
                           for k, v in value.items()):
                        table[key] = {k.strip().lower(): v
                                      for k, v in value.items()}
                elif isinstance(sample, str):
                    if all(isinstance(k, str) and isinstance(v, str)
                           and v.strip()
                           for k, v in value.items()):
                        table[key] = {k.strip().lower(): v.strip().lower()
                                      for k, v in value.items()}
            elif isinstance(default, list):
                if isinstance(value, list) and value and all(
                        isinstance(v, str) for v in value):
                    table[key] = [v.strip().lower() for v in value]
            elif isinstance(default, str):
                if isinstance(value, str) and value.strip():
                    table[key] = value.strip().lower()
        except Exception:  # noqa: BLE001 — a bad entry keeps default.
            continue
    return table


def load_table(language="en", path=None):
    """Read the spoken-maths table for a language. Returns
    (table, source): source is "file" when the file was read and its
    ``spoken-maths`` block parsed, else "default" (missing file,
    unreadable file, or no block — the session runs on the built-in
    English table and says so). A second language is a second file
    (SPOKEN_MATHS_HI.md, ...); the grader reads the table, it never
    has English hardcoded into it. Never raises."""
    lang = str(language or "en").strip().lower() or "en"
    target = Path(path) if path is not None else _default_table_path(lang)
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return copy.deepcopy(DEFAULT_TABLE), "default"
    block = _table_block(text)
    if block is None:
        return copy.deepcopy(DEFAULT_TABLE), "default"
    try:
        raw = json.loads(block)
    except (ValueError, TypeError):
        return copy.deepcopy(DEFAULT_TABLE), "default"
    table = _normalise_table(raw)
    table["language"] = lang
    return table, "file"


def _table(table=None):
    if isinstance(table, dict) and table.get("ones"):
        return table
    return load_table()[0]


# ------------------------------------------------------------------
# Number words (from the table — one spelling per language)
# ------------------------------------------------------------------

def _reverse(table, key):
    return {v: k for k, v in table[key].items()}


def _word_for(value, table, key):
    """The table's word for a number value, falling back to the
    built-in English word when a partial table lacks it — a partial
    table never crashes the speaker, it just borrows a word."""
    word = _reverse(table, key).get(value)
    if word is None:
        word = _reverse(DEFAULT_TABLE, key).get(value)
    return word


def _int_to_words(n, table):
    """An integer as words, from the table: 32 -> "thirty-two",
    -7 -> "negative seven", 123 -> "one hundred twenty-three"."""
    if n < 0:
        return table["negative"][0] + " " + _int_to_words(-n, table)
    if n < 20:
        return _word_for(n, table, "ones")
    if n < 100:
        t, r = divmod(n, 10)
        return _word_for(t * 10, table, "tens") + (
            "-" + _word_for(r, table, "ones") if r else "")
    if n < 1000:
        h, r = divmod(n, 100)
        out = _word_for(h, table, "ones") + " " + _word_for(
            100, table, "scales")
        if r:
            out += " " + _int_to_words(r, table)
        return out
    if n < 1000000:
        th, r = divmod(n, 1000)
        out = _int_to_words(th, table) + " " + _word_for(
            1000, table, "scales")
        if r:
            out += " " + _int_to_words(r, table)
        return out
    m, r = divmod(n, 1000000)
    out = _int_to_words(m, table) + " " + _word_for(
        1000000, table, "scales")
    if r:
        out += " " + _int_to_words(r, table)
    return out


def _frac_to_str(value):
    """A Fraction as a canonical written form: 7/3, or "7" whole."""
    if not isinstance(value, Fraction):
        return str(value)
    if value.denominator == 1:
        return str(value.numerator)
    return "%d/%d" % (value.numerator, value.denominator)


def _parse_number_words(tokens, table):
    """A leading number phrase -> (Fraction, consumed) or None.

    Consumes an optional negative word, integer words with scales,
    and an optional "point" + digit words ("two point three three
    three" -> 2.333, Fraction-exact). Consumes nothing when the
    tokens do not start with a number phrase."""
    ones = table["ones"]
    tens = table["tens"]
    scales = table["scales"]
    neg = set(table["negative"])
    point = table["point"]
    i = 0
    sign = 1
    if i < len(tokens) and tokens[i] in neg:
        sign = -1
        i += 1
    current = 0
    result = 0
    seen_int = False
    while i < len(tokens):
        w = tokens[i]
        if w in ones:
            current += ones[w]
            seen_int = True
            i += 1
        elif w in tens:
            current += tens[w]
            seen_int = True
            i += 1
        elif w in scales:
            v = scales[w]
            if v == 100:
                current = (current or 1) * 100
            else:
                result += (current or 1) * v
                current = 0
            seen_int = True
            i += 1
        else:
            break
    if not seen_int:
        return None
    result += current
    if i < len(tokens) and tokens[i] == point:
        j = i + 1
        digits = []
        while j < len(tokens) and tokens[j] in ones \
                and ones[tokens[j]] < 10:
            digits.append(str(ones[tokens[j]]))
            j += 1
        if not digits:
            return None
        try:
            value = Fraction(Decimal(
                "%d.%s" % (sign * result, "".join(digits))))
        except Exception:  # noqa: BLE001 — not a number after all.
            return None
        return value, j
    return Fraction(sign * result, 1), i


# ------------------------------------------------------------------
# Speaker side: written expression -> canonical speech string
# ------------------------------------------------------------------

_FRAC_RE = re.compile(r"^([+-]?\d+)/(\d+)$")
_PI_PROD_RE = re.compile(r"^([+-]?\d+)\s*(?:\*|×)?\s*(?:π|pi)$")
_PI_OVER_RE = re.compile(r"^(?:π|pi)/(\d+)$")
_POW_RE = re.compile(r"^([A-Za-z]+)\^([+-]?\d+)$")
_COEF_RE = re.compile(r"^([+-]?\d+)\s*([A-Za-z])$")
_TIMES_RE = re.compile(r"^([A-Za-z])\s*(?:\*|×)\s*([A-Za-z])$")
_NUM_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)$")
_SQRT_RE = re.compile(r"^√\s*(.+)$")
_DIV_RE = re.compile(r"^∇\s*·\s*(.+)$")
_CURL_RE = re.compile(r"^∇\s*×\s*(.+)$")
_DERIV_RE = re.compile(r"^(d|∂)/(d|∂)([a-zA-Z]+)(?:\s+(.+))?$")
_DYDX_RE = re.compile(r"^d([a-zA-Z]+)/d([a-zA-Z]+)$")


def _speak_number_str(text, table):
    """A digit string as words: "32" -> "thirty-two",
    "2.333" -> "two point three three three"."""
    if "." in text:
        ip, _, fp = text.partition(".")
        sign = ""
        if ip.startswith(("+", "-")):
            sign, ip = ("negative " if ip[0] == "-" else ""), ip[1:]
        head = _int_to_words(int(ip or "0"), table)
        tail = " ".join(
            _int_to_words(int(d), table) for d in fp)
        return (sign + head + " " + table["point"] + " " + tail).strip()
    return _int_to_words(int(text), table)


def _speak_atom(text, table):
    """One atom — a number, a variable, or a small expression — as
    words. Used for limits, regions, and integrand pieces."""
    s = " ".join(str(text or "").split()).strip()
    if not s:
        return ""
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", s):
        return _speak_number_str(s, table)
    if re.fullmatch(r"[A-Za-z]+", s):
        return s
    return speak_expression(s, table)


def _dw(table, section, key):
    """A derivative/integral connector word from the table, falling
    back to built-in English when a partial table lacks it."""
    return table.get(section, {}).get(
        key, DEFAULT_TABLE[section][key])


def _speak_integral(text, table):
    """∫ / ∬ / ∭ / ∮ / ∯ with optional limits and integrand:
    "∫_0^1 x dx" -> "the integral from zero to one of x d x";
    "∬_D f dA" -> "the double integral over D of f d A"."""
    kinds = {
        "∫": table["symbols"].get("integral", "the integral"),
        "∬": table["symbols"].get("double integral",
                                   "the double integral"),
        "∭": table["symbols"].get("triple integral",
                                   "the triple integral"),
        "∮": table["symbols"].get("line integral", "the line integral"),
        "∯": table["symbols"].get("surface integral",
                                   "the surface integral"),
    }
    sym = text[0]
    kind = kinds[sym]
    rest = text[1:].strip()
    lower = upper = None
    if rest.startswith("_"):
        rest = rest[1:]
        if rest.startswith("{"):
            lower, _, rest = rest[1:].partition("}")
            rest = rest.strip()
        elif rest:
            lower, rest = rest[0], rest[1:].strip()
    if rest.startswith("^"):
        rest = rest[1:]
        if rest.startswith("{"):
            upper, _, rest = rest[1:].partition("}")
            rest = rest.strip()
        elif rest:
            upper, rest = rest[0], rest[1:].strip()
    var = None
    m = re.search(r"\bd\s*([A-Za-z]+)\s*$", rest)
    if m:
        var = m.group(1)
        rest = rest[:m.start()].rstrip("·×* ").strip()
    integrand = speak_expression(rest, table) if rest else ""
    iw = lambda k: _dw(table, "integral_words", k)
    if lower is not None and upper is not None:
        head = "%s %s %s %s %s %s" % (
            kind, iw("from"), _speak_atom(lower, table),
            iw("to"), _speak_atom(upper, table), iw("of"))
    elif lower is not None:
        if sym in "∬∭∯":
            head = "%s %s %s %s" % (
                kind, iw("over"), _speak_atom(lower, table), iw("of"))
        elif sym == "∮":
            head = "%s %s %s %s" % (
                kind, iw("along"), _speak_atom(lower, table), iw("of"))
        else:
            head = "%s %s %s %s" % (
                kind, iw("from"), _speak_atom(lower, table), iw("of"))
    else:
        head = "%s %s" % (kind, iw("of"))
    parts = [head]
    if integrand:
        parts.append(integrand)
    if var:
        parts.append("d " + var)
    return " ".join(parts)


def speak_expression(expr, table=None):
    """A written maths expression -> its canonical speech string,
    from the table. Never returns raw notation for a pilot form:
    "7/3" -> "seven over three", "x^2" -> "x squared",
    "32 pi" -> "thirty-two pi", "d/dx" -> "d d x",
    "∫_0^1 x dx" -> "the integral from zero to one of x d x".
    An expression with no pilot form passes through unchanged
    (named limitation — the caller must not mistake pass-through
    for a canonical string)."""
    table = _table(table)
    text = " ".join(str(expr or "").split()).strip()
    if not text:
        return ""
    for src, dst in _SUPERSCRIPTS.items():
        text = text.replace(src, dst)
    if text and text[0] in "∫∬∭∮∯":
        return _speak_integral(text, table)
    m = _DERIV_RE.match(text)
    if m:
        kind, var, rest = m.group(1), m.group(3), m.group(4)
        dw = lambda k: _dw(table, "derivative_words", k)
        if rest:
            rest = re.sub(r"^of\s+", "", rest.strip())
            word = (dw("partial") if kind == "∂"
                    else dw("derivative"))
            return "%s %s %s %s" % (
                word, speak_expression(rest, table),
                dw("with respect to"), var)
        return ("%s d d %s" % (dw("partial bare"), var)) \
            if kind == "∂" else ("d d %s" % var)
    m = _DYDX_RE.match(text)
    if m:
        dw = lambda k: _dw(table, "derivative_words", k)
        return "%s %s %s %s" % (
            dw("derivative"), m.group(1),
            dw("with respect to"), m.group(2))
    m = _DIV_RE.match(text)
    if m:
        return "%s %s" % (table["symbols"].get(
            "divergence of", "the divergence of"),
            speak_expression(m.group(1), table))
    m = _CURL_RE.match(text)
    if m:
        return "%s %s" % (table["symbols"].get(
            "curl of", "the curl of"),
            speak_expression(m.group(1), table))
    m = _SQRT_RE.match(text)
    if m:
        return "%s %s" % (table["symbols"].get(
            "square root of", "the square root of"),
            speak_expression(m.group(1), table))
    m = _FRAC_RE.match(text)
    if m:
        connector = table["fraction_words"][0]
        return "%s %s %s" % (
            _speak_number_str(m.group(1), table),
            connector,
            _int_to_words(int(m.group(2)), table))
    m = _PI_PROD_RE.match(text)
    if m:
        return "%s pi" % _speak_number_str(m.group(1), table)
    m = _PI_OVER_RE.match(text)
    if m:
        return "pi over %s" % _int_to_words(int(m.group(1)), table)
    m = _POW_RE.match(text)
    if m:
        base, exp = m.group(1), int(m.group(2))
        powers = table["power_words"]
        by_value = {v: k for k, v in powers.items()}
        if exp in by_value:
            return "%s %s" % (base, by_value[exp])
        return "%s %s %s" % (
            base, table["power_phrase"],
            _int_to_words(exp, table))
    m = _COEF_RE.match(text)
    if m:
        return "%s %s" % (
            _speak_number_str(m.group(1), table), m.group(2))
    m = _TIMES_RE.match(text)
    if m:
        return "%s times %s" % (m.group(1), m.group(2))
    m = _NUM_RE.match(text)
    if m:
        return _speak_number_str(m.group(1), table)
    if text in ("π", "pi"):
        return "pi"
    return text


# The spans speak_text replaces inside prose, most specific first.
# Each alternative is a maths span; prose between spans is untouched.
_SPAN_RE = re.compile(
    r"[∫∬∭∮∯](?:_\{[^}]*\}|\^\{[^}]*\}|_[A-Za-z0-9]|\^[A-Za-z0-9]|\s)*?"
    r"[^\n]*?\bd\s*[A-Za-z]+"
    r"|[∫∬∭∮∯](?:_\{[^}]*\}|\^\{[^}]*\}|_[A-Za-z0-9]|\^[A-Za-z0-9])*"
    r"|(?:the\s+)?integral\s+from\s+.+?\s+to\s+.+?\s+of\s+.+?\bd\s*[A-Za-z]+"
    r"|(?:the\s+)?integral\s+of\s+.+?\bd\s*[A-Za-z]+"
    r"|(?:d|∂)/(?:d|∂)[a-zA-Z]+(?:\s+(?:of\s+)?[^\s,;.]+"
    r"(?:\s+[^\s,;.]+)?)?"
    r"|d[a-zA-Z]+/d[a-zA-Z]+"
    r"|[+-]?\d+/\d+"
    r"|[A-Za-z]\^[+-]?\d+|[A-Za-z][²³⁴⁵⁶⁷⁸⁹]"
    r"|[+-]?\d+\s*(?:\*|×)?\s*π|[+-]?\d+\s+pi\b|(?<![A-Za-z])π(?![A-Za-z])"
    r"|√\s*[^\s,;.]+"
    r"|∇\s*[·×]\s*[^\s,;.]+"
    r"|[+-]?\d+\s*(?:[xyzt]|theta)\b|[+-]?\d+(?:[xyzt])\b"
    r"|[+-]?\d+(?:\.\d+)?",
    re.IGNORECASE,
)


def _speak_ascii_integral(match_text, table):
    """"integral from 0 to 1 of x dx" (or "the integral from ...")
    -> the canonical string. A leading "the" in the prose is
    consumed, not doubled ("The the integral" is not spoken)."""
    text = re.sub(r"^the\s+", "", match_text.strip(),
                  flags=re.IGNORECASE)
    iw = lambda k: _dw(table, "integral_words", k)
    m = re.match(
        r"integral\s+from\s+(.+?)\s+to\s+(.+?)\s+of\s+(.+?)"
        r"\bd\s*([A-Za-z]+)\s*$",
        text, re.IGNORECASE)
    if m:
        return "%s %s %s %s %s %s %s d %s" % (
            iw("integral"), iw("from"),
            _speak_atom(m.group(1), table),
            iw("to"), _speak_atom(m.group(2), table), iw("of"),
            speak_expression(m.group(3), table), m.group(4))
    m = re.match(
        r"integral\s+of\s+(.+?)\bd\s*([A-Za-z]+)\s*$",
        text, re.IGNORECASE)
    if m:
        return "%s %s %s d %s" % (
            iw("integral"), iw("of"),
            speak_expression(m.group(1), table), m.group(2))
    return match_text


def speak_text(text, table=None):
    """A whole sentence -> its spoken form: every maths span is
    replaced by its canonical speech string, prose is untouched.
    "What is 7/3?" -> "What is seven over three?" An already-spoken
    sentence ("seven over three") has no spans and passes through.
    An unparseable span passes through unchanged."""
    table = _table(table)
    if not isinstance(text, str) or not text:
        return ""

    def _replace(match):
        span = match.group(0)
        if span.strip().lower().startswith(
                ("integral", "the integral")):
            spoken = _speak_ascii_integral(span, table)
            if spoken != span:
                return spoken
        return speak_expression(span, table)

    return _SPAN_RE.sub(_replace, text)


# ------------------------------------------------------------------
# Grading side: spoken answer -> canonical written form
# ------------------------------------------------------------------

def _words(text):
    """Words of a text, for spoken-form matching: lowercase,
    hyphens split ("thirty-two" -> "thirty two"), punctuation
    dropped. Digits survive as tokens."""
    text = str(text or "").lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9' ]", " ", text)
    return text.split()


def _pi_word(table):
    """The table's spoken word for the constant pi ("pi" in
    English). The written form stays "pi" in every language."""
    return table.get("constants", {}).get("pi", "pi")


def _is_binding(word, table):
    """A word that continues a maths expression rather than ending
    it: a number word, an operator, a constant, a power word, or a
    variable. A span with a binding word immediately beside it is
    part of a larger expression, not a standalone answer."""
    if word in table["ones"] or word in table["tens"] \
            or word in table["scales"]:
        return True
    if word in table["negative"] or word == table["point"]:
        return True
    if word in table["ordinal_denominators"] \
            or word in table["power_words"]:
        return True
    if word in (_pi_word(table), "to", "times", "plus", "minus",
                "over", "divided", "by", "out", "of"):
        return True
    if word in table["variables"]:
        return True
    return False


def _number_word_set(table):
    words = set(table["ones"]) | set(table["tens"]) \
        | set(table["scales"]) | set(table["negative"])
    words.add(table["point"])
    return words


def _bare_number(words, table):
    """The whole answer (modulo a filler prefix and a trailing
    unit) is one number phrase: "seven" -> "7", "the answer is
    two point five" -> "2.5". Anything else -> None."""
    ws = list(words)
    changed = True
    while changed:
        changed = False
        for prefix in _FILLER_PREFIXES:
            pw = prefix.split()
            if ws[:len(pw)] == pw:
                ws = ws[len(pw):]
                changed = True
                break
    unit = None
    if ws and ws[-1] in table["units"]:
        unit = ws[-1]
        ws = ws[:-1]
    if not ws:
        return None
    parsed = _parse_number_words(ws, table)
    if parsed is None:
        return None
    value, consumed = parsed
    if consumed != len(ws):
        return None
    canon = _frac_to_str(value)
    if unit:
        canon += " " + unit
    return canon


def _word_spans(words, table):
    """Distinctive maths spans in a word list: (start, end,
    canonical). A bare number phrase is NOT a span here — it is
    handled by the whole-answer rule, so "one more try" never
    normalises to "1"."""
    spans = []
    n = len(words)
    ones = table["ones"]
    ordinals = table["ordinal_denominators"]
    powers = table["power_words"]
    power_phrase = table["power_phrase"].split()
    variables = set(table["variables"])

    def num_at(i):
        return _parse_number_words(words[i:], table)

    for i in range(n):
        # Fraction: "seven over three" / "seven divided by three".
        parsed = num_at(i)
        if parsed is not None:
            value, end = parsed
            j = i + end
            for fw in table["fraction_words"]:
                fwt = fw.split()
                if words[j:j + len(fwt)] == fwt:
                    parsed2 = num_at(j + len(fwt))
                    if parsed2 is not None:
                        value2, end2 = parsed2
                        if value2 == 0:
                            break
                        spans.append((i, j + len(fwt) + end2,
                                      "%s/%s" % (
                                          _frac_to_str(value),
                                          _frac_to_str(value2))))
                        break
        # Ordinal denominator: "seven thirds" / bare "half".
        if parsed is not None:
            _, end = parsed
            j = i + end
            if j < n and words[j] in ordinals:
                denom = ordinals[words[j]]
                spans.append((i, j + 1, _frac_to_str(
                    parsed[0] / denom)))
        if words[i] in ordinals:
            spans.append((i, i + 1, _frac_to_str(
                Fraction(1, ordinals[words[i]]))))
        # Constant: "thirty-two pi" / bare "pi" (the table's
        # spoken word for pi; the written form stays "pi").
        pi_word = _pi_word(table)
        if parsed is not None:
            _, end = parsed
            j = i + end
            if j < n and words[j] == pi_word:
                spans.append((i, j + 1, "%s pi" % _frac_to_str(
                    parsed[0])))
        if words[i] == pi_word:
            spans.append((i, i + 1, "pi"))
        # Power: "x squared" / "two to the power of three".
        base = None
        j = None
        if words[i] in variables:
            base, j = words[i], i + 1
        elif parsed is not None:
            base, j = _frac_to_str(parsed[0]), i + parsed[1]
        if base is not None and j is not None and j < n:
            if words[j] in powers:
                spans.append((i, j + 1, "%s^%d" % (
                    base, powers[words[j]])))
            elif words[j:j + len(power_phrase)] == power_phrase:
                parsed2 = num_at(j + len(power_phrase))
                if parsed2 is not None and parsed2[0].denominator == 1:
                    spans.append((i, j + len(power_phrase)
                                  + parsed2[1], "%s^%d" % (
                                      base, int(parsed2[0]))))
        # Coefficient: "two x" -> "2x".
        if parsed is not None:
            _, end = parsed
            j = i + end
            if j < n and words[j] in variables:
                spans.append((i, j + 1, "%s%s" % (
                    _frac_to_str(parsed[0]), words[j])))
        # Times: "x times y" -> "x*y"; "x times two" -> "2x".
        if words[i] in variables and i + 1 < n \
                and words[i + 1] == "times":
            if i + 2 < n and words[i + 2] in variables:
                spans.append((i, i + 3, "%s*%s" % (
                    words[i], words[i + 2])))
            else:
                parsed2 = num_at(i + 2)
                if parsed2 is not None:
                    spans.append((i, i + 2 + parsed2[1], "%s%s" % (
                        _frac_to_str(parsed2[0]), words[i])))
    return spans


def _take_spans(spans):
    """Non-overlapping spans, longest first: two spans over the
    same words are one claim, not two."""
    taken = []
    for start, end, canon in sorted(
            spans, key=lambda s: (s[0], -(s[1] - s[0]))):
        if all(end <= s or start >= e for s, e, _ in taken):
            taken.append((start, end, canon))
    return taken


_DIGIT_SPAN_RE = re.compile(
    r"[+-]?\d+/\d+"
    r"|[+-]?\d+\s*(?:\*|×)?\s*(?:π|pi)\b"
    r"|[A-Za-z]\^[+-]?\d+"
    r"|[+-]?\d+[A-Za-z]\b"
    r"|[+-]?\d+(?:\.\d+)?"
)


def _digit_canonical(span, table):
    """A digit maths span -> its canonical written form."""
    span = span.strip()
    if "/" in span and re.fullmatch(r"[+-]?\d+/\d+", span):
        num, _, den = span.partition("/")
        return "%d/%d" % (int(num), int(den))
    if "pi" in span.lower() or "π" in span:
        m = re.match(r"([+-]?\d+)", span)
        return "%d pi" % int(m.group(1))
    if "^" in span:
        base, _, exp = span.partition("^")
        return "%s^%d" % (base, int(exp))
    m = re.fullmatch(r"([+-]?\d+)([A-Za-z])", span)
    if m:
        return "%d%s" % (int(m.group(1)), m.group(2))
    return span


def _digit_span(raw, table):
    """The digit maths spans in the raw answer: (canonical, ok).
    ok is False when the answer is ambiguous (two spans, a binding
    neighbour, or leftover maths words) — the caller returns None
    rather than guessing. (None, True) means no digit span."""
    matches = list(_DIGIT_SPAN_RE.finditer(raw))
    if not matches:
        return None, True
    if len(matches) > 1:
        return None, False
    m = matches[0]
    span, start, end = m.group(0), m.start(), m.end()
    before = raw[:start]
    after = raw[end:]
    if before and (before[-1].isalnum() or before[-1] in "^/*"):
        return None, False
    if after and (after[0].isalnum() or after[0] in "^/*"):
        return None, False
    unit = None
    tail_words = _words(after)
    if tail_words and tail_words[0] in table["units"]:
        unit = tail_words[0]
        rest_words = tail_words[1:]
    else:
        rest_words = tail_words
    head_words = _words(before)
    # A leading "minus" / "negative" word belongs to the span's
    # sign ("minus 7/3" is -7/3), not to the leftovers.
    sign = False
    if head_words and head_words[-1] in table["negative"]:
        sign = True
        head_words = head_words[:-1]
    maths = _number_word_set(table) | set(
        table["ordinal_denominators"]) | set(
        table["power_words"]) | {_pi_word(table), "to", "times",
                                 "plus", "minus", "over", "divided",
                                 "by", "out", "of"} | set(
        table["variables"])
    if any(w in maths for w in head_words + rest_words):
        return None, False
    if re.search(r"\d", before) or re.search(r"\d", after):
        return None, False
    canon = _digit_canonical(span, table)
    if sign and not canon.startswith("-"):
        canon = "-" + canon
    if unit:
        canon += " " + unit
    return canon, True


def normalise_spoken(answer, table=None):
    """A spoken (or typed) answer -> its canonical written form,
    or None when it cannot be understood. "seven over three",
    "seven thirds" and "seven divided by three" all become "7/3";
    "thirty-two pi" becomes "32 pi"; "x squared" becomes "x^2";
    "two x" becomes "2x". A typed "7/3" passes through unchanged.
    None means: no maths claim, or two different claims (ambiguous)
    — the caller grades the original through U3, and with no judge
    that is "not proven". Never raises."""
    table = _table(table)
    if not isinstance(answer, str) or not answer.strip():
        return None
    raw = " ".join(answer.split())
    digit_canon, digit_ok = _digit_span(raw.lower(), table)
    words = [w for w in _words(raw) if not any(c.isdigit() for c in w)]
    spans = _take_spans(_word_spans(words, table))
    word_canon = None
    word_ok = True
    if len(spans) > 1:
        word_ok = False
    elif len(spans) == 1:
        start, end, canon = spans[0]
        unit = None
        after = end
        if after < len(words) and words[after] in table["units"]:
            unit = words[after]
            after += 1
        before_word = words[start - 1] if start > 0 else None
        after_word = words[after] if after < len(words) else None
        if (before_word is not None and _is_binding(before_word, table)) \
                or (after_word is not None
                    and _is_binding(after_word, table)):
            word_ok = False
        else:
            maths = _number_word_set(table) | set(
                table["ordinal_denominators"])
            rest = words[:start] + words[after:]
            if any(w in maths for w in rest):
                word_ok = False
            else:
                word_canon = canon
                if unit:
                    word_canon += " " + unit
    else:
        word_canon = _bare_number(words, table)
    if not digit_ok or not word_ok:
        return None
    if digit_canon is not None and word_canon is not None:
        return None
    if digit_canon is not None:
        return digit_canon
    return word_canon


def grade_spoken(answer, gold, rubric="", *, judge=None,
                 confidence=1.0, table=None):
    """Grade a spoken (or typed) answer: the D-T3 confidence gate
    first (a low-confidence hearing grades nothing and is
    "low_confidence" — the caller re-asks), then the normaliser,
    then U3's grade_answer in its own order (exact -> numeric +
    tolerance -> judge -> deterministic fail-safe). The returned
    dict is U3-shaped: {correct, method, reason, deduction}.
    Never raises."""
    table = _table(table)
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < sm.CONFIDENCE_FLOOR:
        return {"correct": False, "method": "fail_safe",
                "reason": "low_confidence", "deduction": ""}
    canon = normalise_spoken(answer, table)
    if canon is not None:
        return grade_answer(canon, gold, rubric, judge=judge)
    return grade_answer(answer, gold, rubric, judge=judge)


def _word_subsequence(needle, haystack):
    if not needle:
        return False
    n = len(needle)
    return any(haystack[i:i + n] == needle
               for i in range(len(haystack) - n + 1))


def spoken_reveals_gold(text, gold, table=None):
    """Would speaking ``text`` reveal the gold? The written-form
    guard (U3's ``_contains_gold``) is blind to spoken gold —
    ``_contains_gold("seven over three", "7/3")`` is False — so
    this checks three ways: the written form, the gold's canonical
    spoken form as a phrase inside the text, and the text's own
    normalisation against the gold. The production tutor session
    driver MUST pass every spoken string through this before
    speaking; a True here means the string must be replaced by the
    generic line, never spoken."""
    table = _table(table)
    if not isinstance(text, str) or not text.strip():
        return False
    if not isinstance(gold, str) or not gold.strip():
        return False
    if _contains_gold(text, gold):
        return True
    spoken_gold = speak_expression(gold, table)
    if spoken_gold and spoken_gold != gold:
        if _word_subsequence(_words(spoken_gold), _words(text)):
            return True
    canon = normalise_spoken(text, table)
    if canon is not None and _contains_gold(canon, gold):
        return True
    return False
