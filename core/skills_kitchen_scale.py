"""Kitchen scale-convert tools (moved verbatim from core/skills_kitchen.py).

recipe_scale, oven_temp_convert, cooking_time_per_weight and their
private tables/helpers. No behaviour change.
"""

import re

from .skills_kitchen_helpers import (  # INTENTIONAL: shared house helpers
    _coerce_float,
    _duration_words,
    _fmt_num,
    _qty_text,
)


_QTY_MIXED = re.compile(r"^(\d+)\s+(\d+)\s*/\s*(\d+)")


_QTY_FRACTION = re.compile(r"^(\d+)\s*/\s*(\d+)")


_QTY_PLAIN = re.compile(r"^(\d+(?:\.\d+)?|\.\d+)")


def _leading_qty(line: str):
    """(value, chars_consumed) for a line's leading amount, or
    None when the line doesn't start with one."""
    match = _QTY_MIXED.match(line)
    if match:
        whole, num, den = (int(match.group(i)) for i in (1, 2, 3))
        if den:
            return whole + num / den, match.end()
    match = _QTY_FRACTION.match(line)
    if match:
        num, den = int(match.group(1)), int(match.group(2))
        if den:
            return num / den, match.end()
    match = _QTY_PLAIN.match(line)
    if match:
        return float(match.group(1)), match.end()
    return None


def recipe_scale(ingredients: str = "", factor=0) -> str:
    """Scale every leading quantity in an ingredients list by the
    same factor; lines without an amount pass through and are
    counted in a closing note."""
    text = str(ingredients or "")
    if not text.strip():
        return ("Give me the ingredients to scale — one per line, "
                "each starting with its amount, like '200 g flour'.")
    scale = _coerce_float(factor)
    if scale is None or scale <= 0:
        return ("The scaling factor has to be a number above "
                "zero — 2 doubles a recipe, 0.5 halves it.")
    out_lines = []
    unscaled = 0
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            out_lines.append(line)
            continue
        found = _leading_qty(line.lstrip())
        if found is None:
            unscaled += 1
            out_lines.append(line)
            continue
        value, consumed = found
        lead = len(line) - len(line.lstrip())
        scaled = _qty_text(value * scale)
        out_lines.append(
            line[:lead] + scaled + line.lstrip()[consumed:])
    header = f"Your recipe, scaled by {_fmt_num(scale)}×:"
    if unscaled:
        out_lines.append(
            f"({unscaled} line{'s' if unscaled != 1 else ''} had "
            "no amount to scale and "
            f"{'were' if unscaled != 1 else 'was'} left as "
            f"{'they were' if unscaled != 1 else 'it was'}.)")
    return "\n".join([header] + out_lines)


_GAS_TABLE = {
    1: (140, 275), 2: (150, 300), 3: (170, 325), 4: (180, 350),
    5: (190, 375), 6: (200, 400), 7: (220, 425), 8: (230, 450),
    9: (240, 475),
}


_UNIT_ALIASES = {
    "c": "celsius", "celsius": "celsius", "centigrade": "celsius",
    "°c": "celsius",
    "f": "fahrenheit", "fahrenheit": "fahrenheit", "°f": "fahrenheit",
    "gas": "gas mark", "gas mark": "gas mark", "gasmark": "gas mark",
    "gm": "gas mark", "mark": "gas mark",
}


def oven_temp_convert(value=0, from_unit: str = "") -> str:
    """Convert an oven temperature between celsius, fahrenheit
    and gas mark, and give all three forms."""
    unit = _UNIT_ALIASES.get(str(from_unit or "").strip().lower())
    if unit is None:
        return ("I can convert between celsius, fahrenheit and "
                "gas mark — tell me which one the number is in.")
    number = _coerce_float(value)
    if number is None:
        return "The temperature has to be a number."
    if unit == "gas mark":
        mark = int(number) if float(number).is_integer() else None
        if mark not in _GAS_TABLE:
            return ("Gas marks here run from 1 to 9 in whole "
                    "steps — give me one of those.")
        celsius, fahrenheit = _GAS_TABLE[mark]
        return (f"Gas mark {mark} = {celsius}°C = "
                f"{fahrenheit}°F.")
    if unit == "celsius":
        celsius = number
        fahrenheit = round(celsius * 9 / 5 + 32)
    else:
        fahrenheit = number
        celsius = (fahrenheit - 32) * 5 / 9
    closest = min(_GAS_TABLE,
                  key=lambda m: abs(_GAS_TABLE[m][0] - celsius))
    celsius_text = _fmt_num(round(celsius * 2) / 2)
    # An exact table temperature names its gas mark plainly (the
    # dial pairing, e.g. GM6 = 200°C/400°F, is the conventional
    # rounded figure and is quoted from the table so the two
    # directions never disagree); anything else is "closest".
    exact = [m for m, pair in _GAS_TABLE.items() if pair[0] == celsius]
    if exact:
        mark = exact[0]
        dial_f = _GAS_TABLE[mark][1]
        if unit == "celsius":
            return (f"{celsius_text}°C = {_fmt_num(fahrenheit)}°F "
                    f"— gas mark {mark} (oven dials pair it with "
                    f"{dial_f}°F).")
        return (f"{_fmt_num(fahrenheit)}°F = {celsius_text}°C "
                f"— gas mark {mark}.")
    if unit == "celsius":
        return (f"{celsius_text}°C = {_fmt_num(fahrenheit)}°F "
                f"— closest gas mark {closest}.")
    return (f"{_fmt_num(fahrenheit)}°F = {celsius_text}°C "
            f"— closest gas mark {closest}.")


_ROAST_TABLE = {
    "chicken": (("chicken", "whole chicken"), {"": (45, 20)}),
    "turkey": (("turkey",), {"": (40, 20)}),
    "beef": (("beef", "beef joint"),
             {"rare": (40, 20), "medium": (50, 20),
              "well": (60, 20), "well done": (60, 20)}),
    "pork": (("pork", "pork joint"), {"": (70, 25)}),
    "lamb": (("lamb", "lamb joint"),
             {"rare": (40, 20), "medium": (50, 20),
              "well": (60, 20), "well done": (60, 20)}),
}


_ROAST_NAMES = ("beef joint, chicken (whole), lamb, pork joint, "
                "turkey")


def cooking_time_per_weight(protein: str = "", weight_kg=0,
                            doneness: str = "") -> str:
    """Rough roast timing for a protein at a weight, from a
    minutes-per-kilo table plus a fixed extra, with the
    thermometer advice stated."""
    wanted = str(protein or "").strip().lower()
    entry = None
    for _name, (aliases, table) in _ROAST_TABLE.items():
        if wanted in aliases:
            entry = table
            break
    if entry is None:
        return (f"I don't have timings for "
                f"'{str(protein or '').strip()}' — I know: "
                f"{_ROAST_NAMES}.")
    weight = _coerce_float(weight_kg)
    if weight is None or weight <= 0:
        return "The weight has to be a number of kilos above zero."
    if weight > 30:
        return (f"{_fmt_num(weight)} kg is beyond the roasts this "
                "table is meant for (it tops out at 30 kg) — a "
                "butcher's timing is the safer bet there.")
    level = str(doneness or "").strip().lower()
    if set(entry) == {""}:
        if level:
            return (f"{wanted.capitalize()} is timed the same "
                    "whatever the doneness — there's no "
                    "rare/medium/well split for it here.")
        per_kg, extra = entry[""]
        level_word = ""
    else:
        if not level:
            level = "medium"
        if level not in entry:
            options = ", ".join(sorted(
                {k for k in entry if k != 'well done'}))
            return (f"For {wanted} the doneness can be: "
                    f"{options}.")
        per_kg, extra = entry[level]
        level_word = f", {level},"
    total = per_kg * weight + extra
    return (f"A {_fmt_num(weight)} kg {wanted}{level_word} takes "
            f"about {_duration_words(total)} in the oven "
            f"({per_kg} minutes per kilo"
            f"{f' plus {extra}' if extra else ''}), then rest it "
            "covered for 15–30 minutes before carving. "
            "That's a rough guide — use a thermometer for safety: "
            "75°C in the thickest part for poultry, 63°C and "
            "rested for whole cuts of beef, lamb and pork.")
