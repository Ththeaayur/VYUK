"""
SMART skills for VYUK — pack 5: six small, pure, offline
converters and generators: placeholder (lorem) text, Morse code
both ways, the NATO phonetic alphabet, Roman numerals both ways
with strict validation, a bill splitter with tip, and a BMI
calculator that uses the standard reference ranges and says
plainly that they are a rough reference, not medical advice.

Same rules as the other packs: plain functions returning
human-readable strings; handlers never raise — bad input becomes
an honest "I can't…" line, and every cap, clamp and skipped
character is disclosed in the result. No I/O at all here: the
tools work only on their arguments, need no files, no network
and no hardware, so they behave identically everywhere.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) —
this module never touches the registry singleton itself.
"""

import random
import re


# ---------------------------------------------------------------------------
# LOREM TEXT — placeholder paragraphs from the classic vocabulary
# ---------------------------------------------------------------------------

_LOREM_WORDS = (
    "lorem", "ipsum", "dolor", "sit", "amet", "consectetur",
    "adipiscing", "elit", "sed", "do", "eiusmod", "tempor",
    "incididunt", "ut", "labore", "et", "dolore", "magna",
    "aliqua", "enim", "ad", "minim", "veniam", "quis", "nostrud",
    "exercitation", "ullamco", "laboris", "nisi", "aliquip", "ex",
    "ea", "commodo", "consequat", "duis", "aute", "irure", "in",
    "reprehenderit", "voluptate", "velit", "esse", "cillum",
    "fugiat", "nulla", "pariatur", "excepteur", "sint", "occaecat",
    "cupidatat", "non", "proident", "sunt", "culpa", "qui",
    "officia", "deserunt", "mollit", "anim", "id", "est", "laborum",
)

_LOREM_CAP = 1200


def _lorem_sentence(rng: random.Random, first_word: str = "") -> str:
    count = rng.randint(6, 12)
    words = []
    if first_word:
        words.append(first_word)
        count -= 1
    words.extend(rng.choice(_LOREM_WORDS) for _ in range(count))
    text = " ".join(words)
    return text[:1].upper() + text[1:] + "."


def lorem_text(paragraphs: int = 1, seed_word: str = "") -> str:
    """Generate classic lorem-ipsum placeholder text."""
    try:
        requested = int(paragraphs)
    except (TypeError, ValueError):
        return "paragraphs must be a whole number — like 1, 2 or 3."
    count = max(1, min(5, requested))
    seed = (seed_word or "").strip()
    rng = random.Random(f"visor-lorem|{count}|{seed.lower()}")
    paras = []
    for index in range(count):
        sentences = [
            _lorem_sentence(rng, seed if index == 0 and s == 0
                            and seed else "")
            for s in range(3)
        ]
        paras.append(" ".join(sentences))
    text = "\n\n".join(paras)
    notes = []
    if count != requested:
        notes.append(f"(I made {count} paragraph(s) — the range "
                     "is 1 to 5.)")
    if len(text) > _LOREM_CAP:
        cut = text.rfind(". ", 0, _LOREM_CAP)
        text = text[:cut + 1] if cut > 0 else text[:_LOREM_CAP]
        notes.append(f"(Trimmed to about {_LOREM_CAP} characters.)")
    header = (f"Placeholder text ({count} paragraph(s)):"
              if not seed else
              f"Placeholder text ({count} paragraph(s)), starting "
              f"with '{seed.capitalize()}':")
    return "\n\n".join([header, text] + notes)


# ---------------------------------------------------------------------------
# MORSE TRANSLATE — to and from Morse code
# ---------------------------------------------------------------------------

_MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".",
    "F": "..-.", "G": "--.", "H": "....", "I": "..", "J": ".---",
    "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---",
    "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-",
    "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--",
    "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--",
    "4": "....-", "5": ".....", "6": "-....", "7": "--...",
    "8": "---..", "9": "----.",
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "'": ".----.",
    "!": "-.-.--", "/": "-..-.", "(": "-.--.", ")": "-.--.-",
    "&": ".-...", ":": "---...", ";": "-.-.-.", "=": "-...-",
    "+": ".-.-.", "-": "-....-", "_": "..--.-", '"': ".-..-.",
    "$": "...-..-", "@": ".--.-.",
}
_MORSE_REVERSE = {code: char for char, code in _MORSE.items()}


def morse_translate(text: str = "", direction: str = "to_morse") -> str:
    """Translate plain text to Morse code, or Morse back to text."""
    direction = (direction or "to_morse").strip().lower()
    if direction not in ("to_morse", "from_morse"):
        return ("Morse directions are 'to_morse' (text into code) "
                "and 'from_morse' (code back into text).")
    source = (text or "").strip()
    if not source:
        return ("Give me something to translate — like "
                "morse_translate(text='SOS', direction='to_morse'), "
                "or direction='from_morse' with dots and dashes "
                "and ' / ' between words.")
    if direction == "to_morse":
        words_out = []
        skipped = 0
        for word in source.upper().split():
            codes = []
            for char in word:
                code = _MORSE.get(char)
                if code is None:
                    skipped += 1
                else:
                    codes.append(code)
            if codes:
                words_out.append(" ".join(codes))
        if not words_out:
            return ("Nothing in that has a Morse code — I cover "
                    "A–Z, 0–9 and basic punctuation.")
        result = f"Morse for '{source}':\n" + " / ".join(words_out)
        if skipped:
            result += (f"\n({skipped} character(s) had no Morse "
                       "code and were skipped.)")
        return result
    # from_morse: whitespace-separated codes, '/' ends a word
    words_out = []
    letters = []
    unknown = 0
    for token in source.split():
        if token == "/":
            if letters:
                words_out.append("".join(letters))
                letters = []
            continue
        char = _MORSE_REVERSE.get(token)
        if char is None:
            unknown += 1
        else:
            letters.append(char)
    if letters:
        words_out.append("".join(letters))
    if not words_out:
        return ("I couldn't read any Morse in that — codes are "
                "dots and dashes separated by spaces, with ' / ' "
                "between words, like '... --- ...'.")
    plain = " ".join(words_out)
    result = f"That Morse reads: {plain}"
    if unknown:
        result += (f" ({unknown} code group(s) weren't Morse I "
                   "know and were skipped.)")
    return result


# ---------------------------------------------------------------------------
# NATO SPELL — the phonetic alphabet
# ---------------------------------------------------------------------------

_NATO = {
    "A": "Alfa", "B": "Bravo", "C": "Charlie", "D": "Delta",
    "E": "Echo", "F": "Foxtrot", "G": "Golf", "H": "Hotel",
    "I": "India", "J": "Juliett", "K": "Kilo", "L": "Lima",
    "M": "Mike", "N": "November", "O": "Oscar", "P": "Papa",
    "Q": "Quebec", "R": "Romeo", "S": "Sierra", "T": "Tango",
    "U": "Uniform", "V": "Victor", "W": "Whiskey", "X": "X-ray",
    "Y": "Yankee", "Z": "Zulu",
    "0": "Zero", "1": "One", "2": "Two", "3": "Three",
    "4": "Four", "5": "Five", "6": "Six", "7": "Seven",
    "8": "Eight", "9": "Nine",
}


def nato_spell(word: str = "") -> str:
    """Spell a word out in the NATO phonetic alphabet."""
    source = (word or "").strip()
    if not source:
        return ("Give me a word to spell — like nato_spell"
                "(word='visor').")
    parts = []
    others = []
    for char in source:
        code = _NATO.get(char.upper()) if len(char) == 1 else None
        if code is not None:
            parts.append(f"{char.upper()} — {code}")
        else:
            parts.append(f"'{char}' — itself")
            others.append(char)
    result = f"'{source}' in the NATO alphabet: " + ", ".join(parts) + "."
    if others:
        chars = ", ".join(f"'{c}'" for c in others)
        result += (f" (No code word exists for {chars}, so "
                   "those stay as themselves.)")
    return result


# ---------------------------------------------------------------------------
# ROMAN CONVERT — numerals both ways, strictly validated
# ---------------------------------------------------------------------------

_ROMAN_TABLE = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100,
                 "D": 500, "M": 1000}


def _to_roman(number: int) -> str:
    out = []
    rest = number
    for value, glyphs in _ROMAN_TABLE:
        while rest >= value:
            out.append(glyphs)
            rest -= value
    return "".join(out)


def _from_roman(text: str) -> int | None:
    """The value of a Roman numeral, or None if it isn't a valid
    one. Validation is strict: the numeral must re-encode to
    itself, which rejects IIII, VX, IC and friends."""
    cleaned = text.strip().upper()
    if not cleaned or any(c not in _ROMAN_VALUES for c in cleaned):
        return None
    total = 0
    for index, char in enumerate(cleaned):
        value = _ROMAN_VALUES[char]
        following = (_ROMAN_VALUES[cleaned[index + 1]]
                     if index + 1 < len(cleaned) else 0)
        total += -value if value < following else value
    if total < 1 or total > 3999 or _to_roman(total) != cleaned:
        return None
    return total


def roman_convert(value="") -> str:
    """Convert a number 1–3999 to Roman numerals, or a Roman
    numeral back to a number."""
    if isinstance(value, bool):  # True/False are ints in disguise
        return ("Give me a number from 1 to 3999, or a Roman "
                "numeral like 'MMXXVI'.")
    number = None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str):
        cleaned = value.strip()
        if re.fullmatch(r"[0-9]+", cleaned or ""):
            number = int(cleaned)
        else:
            parsed = _from_roman(cleaned)
            if parsed is not None:
                return (f"{cleaned.upper()} in ordinary numbers "
                        f"is {parsed}.")
            return (f"'{value}' isn't a valid Roman numeral — "
                    "valid ones use I, V, X, L, C, D, M with at "
                    "most three repeats and subtraction only in "
                    "pairs like IV, IX, XL, XC, CD and CM (so IIII "
                    "and VX don't count). Numbers run from 1 to "
                    "3999.")
    if number is None:
        return ("Give me a number from 1 to 3999, or a Roman "
                "numeral like 'MMXXVI'.")
    if number < 1 or number > 3999:
        return (f"Roman numerals here run from 1 to 3999 — "
                f"{number} is outside that.")
    return f"{number} in Roman numerals is {_to_roman(number)}."


# ---------------------------------------------------------------------------
# SPLIT BILL — total, tip and people into a fair share
# ---------------------------------------------------------------------------


def _money(amount: float) -> str:
    return f"{amount:,.2f}"


def split_bill(total: float = 0, people: int = 0,
               tip_percent: float = 0) -> str:
    """Split a bill between people, tip included, and say what
    each person owes."""
    try:
        bill = float(total)
        count = int(people)
        tip_rate = float(tip_percent)
    except (TypeError, ValueError):
        return ("I need numbers for this — a bill total, how many "
                "people, and optionally a tip percentage.")
    if bill <= 0:
        return ("The bill total has to be more than zero — give "
                "me the total and I'll split it.")
    if count < 1:
        return ("Tell me how many people are splitting it — at "
                "least one.")
    if tip_rate < 0 or tip_rate > 100:
        return ("The tip percentage needs to be between 0 and "
                "100.")
    tip = round(bill * tip_rate / 100.0, 2)
    grand = round(bill + tip, 2)
    each = round(grand / count, 2)
    lines = [f"Bill: {_money(bill)}"]
    if tip_rate:
        lines.append(f"Tip ({tip_rate:g}%): {_money(tip)}")
        lines.append(f"Grand total: {_money(grand)}")
    lines.append(f"Split {count} way(s): {_money(each)} each.")
    remainder = round(grand - each * count, 2)
    if remainder > 0:
        lines.append(f"(Rounding leaves {_money(remainder)} "
                     "unaccounted — one person pays that much "
                     "less, or it stays as a little extra tip.)")
    elif remainder < 0:
        lines.append(f"(Rounding over-collects "
                     f"{_money(abs(remainder))} — one person pays "
                     "that much less to keep it exact.)")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# BMI CALCULATOR — the standard ranges, honestly framed
# ---------------------------------------------------------------------------

# (upper bound exclusive, category, range wording)
_BMI_BANDS = (
    (18.5, "underweight", "below 18.5"),
    (25.0, "healthy", "18.5 to 24.9"),
    (30.0, "overweight", "25 to 29.9"),
    (float("inf"), "obese", "30 and above"),
)


def bmi_calculator(weight_kg: float = 0, height_cm: float = 0,
                   weight_lb: float = 0,
                   height_in: float = 0) -> str:
    """Work out a body mass index from metric or imperial
    measurements."""
    try:
        kg = float(weight_kg or 0)
        cm = float(height_cm or 0)
        lb = float(weight_lb or 0)
        inches = float(height_in or 0)
    except (TypeError, ValueError):
        return ("I need numbers for weight and height — "
                "kilograms and centimetres, or pounds and inches.")
    imperial = False
    if kg > 0 and cm > 0:
        pass  # metric wins when both are given
    elif lb > 0 and inches > 0:
        kg = lb * 0.45359237
        cm = inches * 2.54
        imperial = True
    elif (kg > 0 or lb > 0) and not (cm > 0 or inches > 0):
        return ("I have a weight but no height — BMI needs both. "
                "Give me centimetres or inches too.")
    elif (cm > 0 or inches > 0) and not (kg > 0 or lb > 0):
        return ("I have a height but no weight — BMI needs both. "
                "Give me kilograms or pounds too.")
    else:
        return ("Give me a weight and a height — kilograms with "
                "centimetres (weight_kg, height_cm) or pounds with "
                "inches (weight_lb, height_in).")
    if not (2.0 <= kg <= 650.0) or not (40.0 <= cm <= 300.0):
        return (f"Those measurements ({kg:.1f} kg, {cm:.0f} cm) "
                "are outside the plausible range I can work with "
                "(2–650 kg, 40–300 cm) — worth a second look at "
                "the numbers.")
    bmi = kg / ((cm / 100.0) ** 2)
    for bound, category, wording in _BMI_BANDS:
        if bmi < bound:
            band, ranges = category, wording
            break
    given = (f"from {lb:g} lb and {inches:g} in "
             f"(about {kg:.1f} kg and {cm:.0f} cm)"
             if imperial else f"from {kg:g} kg and {cm:g} cm")
    return (f"BMI {given} is {bmi:.1f}. On the standard "
            f"reference ranges that falls in the '{band}' band "
            f"({ranges}). Treat it as a rough reference only, "
            "not medical advice — it doesn't account for muscle, "
            "build or age.")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Smart-5 pack tool on a ToolRegistry-like
    object."""
    registry.register(
        name="lorem_text",
        description="Generate placeholder lorem-ipsum text for mock-ups and layouts, from 1 to 5 paragraphs. An optional seed word starts the text off. The text is assembled from the classic Latin vocabulary — it looks like Latin but means nothing, which is the point of placeholder text. Use when a design needs filler copy.",
        parameters={
            "type": "object",
            "properties": {
                "paragraphs": {"type": "integer", "description": "How many paragraphs, 1 to 5 (default 1)"},
                "seed_word": {"type": "string", "description": "Optional word the first sentence starts with"},
            },
            "required": [],
        },
        handler=lorem_text,
    )
    registry.register(
        name="morse_translate",
        description="Translate text into Morse code, or Morse code back into text. Letters are spaced, words are separated by ' / '. Covers A–Z, 0–9 and basic punctuation; anything without a code is counted and reported, not silently dropped. Use for signals practice, puzzles and radio-style fun.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to translate, or the Morse code to read back"},
                "direction": {"type": "string", "enum": ["to_morse", "from_morse"], "description": "'to_morse' (default) or 'from_morse'"},
            },
            "required": ["text"],
        },
        handler=morse_translate,
    )
    registry.register(
        name="nato_spell",
        description="Spell a word out in the NATO phonetic alphabet — Alfa, Bravo, Charlie and so on — so it can be read over a crackly phone line without confusion. Digits get their spoken names too; characters with no code word are spelled as themselves and flagged. Use for names, codes and callsigns.",
        parameters={
            "type": "object",
            "properties": {
                "word": {"type": "string", "description": "The word or code to spell out"},
            },
            "required": ["word"],
        },
        handler=nato_spell,
    )
    registry.register(
        name="roman_convert",
        description="Convert between ordinary numbers and Roman numerals. Give a number from 1 to 3999 to get its numeral, or a numeral like MMXXVI to get its number. Validation is strict — sequences like IIII or VX are refused with an explanation of the rules, not silently reinterpreted. Use for dates, outlines and inscriptions.",
        parameters={
            "type": "object",
            "properties": {
                "value": {"anyOf": [{"type": "integer"}, {"type": "string"}], "description": "A number from 1 to 3999, or a Roman numeral string"},
            },
            "required": ["value"],
        },
        handler=roman_convert,
    )
    registry.register(
        name="split_bill",
        description="Split a bill fairly between people, with an optional tip percentage. Shows the tip amount, the grand total and what each person pays, and says honestly when rounding to the cent leaves a cent over or short. Use after a meal or a shared purchase.",
        parameters={
            "type": "object",
            "properties": {
                "total": {"type": "number", "description": "The bill total before tip"},
                "people": {"type": "integer", "description": "How many people are splitting it"},
                "tip_percent": {"type": "number", "description": "Tip percentage, 0 to 100 (default 0)"},
            },
            "required": ["total", "people"],
        },
        handler=split_bill,
    )
    registry.register(
        name="bmi_calculator",
        description="Work out a body mass index (BMI) from weight and height — kilograms and centimetres, or pounds and inches (metric wins if both are given). Gives the BMI to one decimal and which of the standard reference bands it falls in. It's a rough population reference, not medical advice, and the result says so. Use for a quick health-metric check.",
        parameters={
            "type": "object",
            "properties": {
                "weight_kg": {"type": "number", "description": "Weight in kilograms"},
                "height_cm": {"type": "number", "description": "Height in centimetres"},
                "weight_lb": {"type": "number", "description": "Weight in pounds (if not using kilograms)"},
                "height_in": {"type": "number", "description": "Height in inches (if not using centimetres)"},
            },
            "required": [],
        },
        handler=bmi_calculator,
    )
