"""Kitchen plan tools (moved verbatim from core/skills_kitchen.py).

pantry_check, meal_rotation, coffee_ratio, dough_hydration,
egg_timer_math, spice_substitute, grocery_list and their private
helpers/tables. No behaviour change.
"""

import datetime
import difflib
import random

from .skills_kitchen_helpers import (  # INTENTIONAL: shared house helpers
    _coerce_float,
    _fmt_num,
)


def _split_list(text) -> list:
    """Comma-separated text → trimmed, non-empty items."""
    return [part.strip() for part in str(text or "").split(",")
            if part.strip()]


def _normalise(item: str) -> str:
    return " ".join(item.lower().split())


def _same_item(a: str, b: str) -> bool:
    """Equal, or equal once a trailing plural 's' is forgiven on
    either side."""
    if a == b:
        return True
    return a.rstrip("s") == b or a == b.rstrip("s") \
        or a.rstrip("s") == b.rstrip("s")


def pantry_check(have: str = "", need: str = "") -> str:
    """Compare what a recipe needs against what's in the house
    and report only what's missing."""
    have_items = [_normalise(i) for i in _split_list(have)]
    need_items = _split_list(need)
    if not need_items:
        return ("Tell me what the recipe needs — a comma list "
                "like 'flour, eggs, butter' — and what you have, "
                "and I'll say what's missing.")
    missing = []
    for item in need_items:
        if not any(_same_item(_normalise(item), owned)
                   for owned in have_items):
            missing.append(item)
    if not missing:
        return "You can make it — everything's on hand."
    if len(missing) == 1:
        return f"Missing 1 thing: {missing[0]}."
    return (f"Missing {len(missing)} things: "
            f"{', '.join(missing)}.")


def meal_rotation(dishes: str = "", days: int = 7) -> str:
    """Plan dinners for the coming days by rotating the given
    dishes; the rotation is seeded by the ISO week number, so
    the same week always gives the same plan."""
    seen = set()
    dish_list = []
    for item in _split_list(dishes):
        key = _normalise(item)
        if key not in seen:
            seen.add(key)
            dish_list.append(item)
    if not dish_list:
        return ("Give me the dishes to rotate — a comma list "
                "like 'pasta, curry, stir fry, soup'.")
    try:
        count = int(days)
    except (TypeError, ValueError):
        return "The number of days has to be a whole number."
    requested = count
    count = max(1, min(14, count))
    today = datetime.date.today()
    iso = today.isocalendar()
    rng = random.Random(f"visor-meals|{iso[0]}-W{iso[1]:02d}")
    order = dish_list[:]
    rng.shuffle(order)
    lines = [f"Dinner plan for the next {count} "
             f"day{'s' if count != 1 else ''}:"]
    for offset in range(count):
        day = today + datetime.timedelta(days=offset)
        lines.append(f"• {day:%a} {day.day} {day:%b}: "
                     f"{order[offset % len(order)]}")
    if len(dish_list) < count:
        lines.append(
            f"(Only {len(dish_list)} dish"
            f"{'es' if len(dish_list) != 1 else ''} for {count} "
            "days, so some repeat.)")
    if count != requested:
        lines.append(f"(I planned {count} days — the range is "
                     "1 to 14.)")
    return "\n".join(lines)


def _parse_ratio(ratio) -> float | None:
    """'1:15' (or '15' or '1/15') → water grams per coffee gram,
    or None when it doesn't parse."""
    text = str(ratio or "").strip().lower().replace(" ", "")
    if not text:
        return None
    for sep in (":", "/"):
        if sep in text:
            left, _, right = text.partition(sep)
            base = _coerce_float(left)
            water = _coerce_float(right)
            if base and water and base > 0:
                return water / base
            return None
    return _coerce_float(text)


def _strength_word(r: float) -> str:
    if r <= 13:
        return "strong"
    if r < 16.5:
        return "balanced"
    return "mild"


def coffee_ratio(coffee_g=0, water_ml=0, ratio: str = "1:15") -> str:
    """Work out the water for a coffee weight (or the coffee for
    a water volume) at a brew ratio like 1:15, and say how
    strong that ratio is."""
    r = _parse_ratio(ratio)
    if r is None or r <= 0:
        return ("The ratio should look like '1:15' — one part "
                "coffee to fifteen parts water.")
    ratio_text = f"1:{_fmt_num(r)}"
    strength = _strength_word(r)
    strength_note = (f"{ratio_text} is {strength} — 1:12 is "
                     "strong, 1:17 is mild.")
    coffee = _coerce_float(coffee_g) or 0
    water = _coerce_float(water_ml) or 0
    if coffee > 0:
        return (f"{_fmt_num(coffee)} g of coffee at {ratio_text} "
                f"needs {_fmt_num(round(coffee * r))} ml of "
                f"water. {strength_note}")
    if water > 0:
        grams = water / r
        return (f"For {_fmt_num(water)} ml of water at "
                f"{ratio_text}, use about {_fmt_num(grams)} g of "
                f"coffee. {strength_note}")
    return ("Give me either the coffee weight (coffee_g) or "
            "the water volume (water_ml) and I'll work out "
            "the other.")


_HYDRATION_BANDS = (
    (55.0, "firm — bagel and pasta-dough territory"),
    (65.0, "sandwich-loaf territory"),
    (75.0, "rustic, open-crumb bread territory"),
    (float("inf"), "wet and slack — focaccia and ciabatta "
                   "territory"),
)


def dough_hydration(flour_g=0, water_g=0, extra_dry_g=0,
                    extra_wet_g=0) -> str:
    """Baker's hydration: total wet weight as a percentage of
    total dry weight, with the band that percentage sits in."""
    flour = _coerce_float(flour_g)
    water = _coerce_float(water_g)
    dry_extra = _coerce_float(extra_dry_g)
    wet_extra = _coerce_float(extra_wet_g)
    if None in (flour, water, dry_extra, wet_extra):
        return "Flour, water and the extras all need to be numbers."
    if min(flour, water, dry_extra, wet_extra) < 0:
        return "Weights can't be negative."
    dry = flour + dry_extra
    wet = water + wet_extra
    if dry <= 0:
        return ("Hydration needs some flour (or other dry "
                "weight) to divide by — give me flour_g.")
    pct = wet / dry * 100.0
    band = next(wording for bound, wording in _HYDRATION_BANDS
                if pct < bound)
    extras = ""
    if dry_extra or wet_extra:
        extras = (f" (counting {_fmt_num(dry_extra)} g extra "
                  f"dry and {_fmt_num(wet_extra)} g extra wet)")
    return (f"Hydration: {_fmt_num(round(pct, 1))}% — "
            f"{_fmt_num(wet)} g wet against {_fmt_num(dry)} g "
            f"dry{extras}. That's {band}.")


_EGG_TABLE = {
    "small": {"runny": 3, "soft": 4, "medium": 5, "hard": 7},
    "medium": {"runny": 4, "soft": 5, "medium": 7, "hard": 9},
    "large": {"runny": 5, "soft": 6, "medium": 8, "hard": 10},
}


def egg_timer_math(size: str = "", doneness: str = "",
                   from_fridge: bool = True) -> str:
    """How long to boil an egg: a size, a doneness and whether
    it starts fridge-cold, from a minutes table based on eggs
    going into already-boiling water."""
    size_key = str(size or "").strip().lower()
    if size_key not in _EGG_TABLE:
        return ("Egg sizes here are small, medium and large — "
                "tell me which.")
    level = str(doneness or "").strip().lower()
    if level not in _EGG_TABLE[size_key]:
        return ("Doneness can be runny, soft, medium or hard — "
                "runny whites only just set, hard fully firm.")
    minutes = _EGG_TABLE[size_key][level]
    cold = bool(from_fridge)
    if cold:
        minutes += 1
    start = "from the fridge" if cold else "at room temperature"
    return (f"A {size_key} egg with a {level} yolk, {start}: "
            f"{minutes} minutes. The times assume the egg goes "
            "into water that's already boiling; a fridge-cold "
            "egg gets one extra minute, which is included when "
            "it starts cold.")


_SPICE_TABLE = {
    "allspice": ("equal parts cinnamon, cloves and nutmeg",
                 "use the same amount"),
    "cardamom": ("half ginger, half cinnamon",
                 "use the same amount"),
    "cayenne": ("red pepper flakes, or a few drops of hot sauce",
                "flakes: same amount; hot sauce: a few drops "
                "per quarter-teaspoon"),
    "chili powder": ("paprika with a pinch of cayenne",
                     "use the same amount — the cayenne pinch "
                     "brings the heat back"),
    "cinnamon": ("nutmeg or allspice",
                 "use half as much — both run stronger"),
    "cloves": ("allspice or cinnamon",
               "use three-quarters as much — cloves are strong"),
    "coriander": ("cumin, or caraway for a gentler flavour",
                  "use the same amount"),
    "cumin": ("ground coriander or chili powder",
              "use the same amount"),
    "five spice": ("cinnamon with a little clove and a pinch "
                   "of fennel seed",
                   "use the same amount"),
    "garam masala": ("curry powder",
                     "use the same amount — a different "
                     "character, warmer and less sweet"),
    "ginger": ("allspice, cinnamon or mace for ground ginger; "
               "for fresh, ground ginger",
               "ground swaps: same amount; 1 tablespoon fresh "
               "ginger ≈ a quarter-teaspoon ground"),
    "nutmeg": ("mace, or cinnamon",
               "use the same amount"),
    "oregano": ("marjoram or thyme",
                "use the same amount"),
    "paprika": ("chili powder, or a small pinch of cayenne",
                "chili powder: same amount; cayenne: a quarter "
                "as much — it's far hotter"),
    "rosemary": ("thyme or sage",
                 "use the same amount"),
    "smoked paprika": ("regular paprika with a pinch of cumin, "
                       "or chipotle powder",
                       "same amount of paprika; chipotle: half "
                       "as much"),
    "thyme": ("oregano, marjoram or Italian seasoning",
              "use the same amount"),
    "turmeric": ("dry mustard, or ginger",
                 "use the same amount — the colour won't match, "
                 "the warmth will"),
}


_SPICE_ALIASES = {"chilli powder": "chili powder",
                  "ground coriander": "coriander",
                  "ground cumin": "cumin",
                  "ground ginger": "ginger"}


def spice_substitute(spice: str = "") -> str:
    """What to use when a spice has run out, from a written-in
    table of common swaps with ratio notes."""
    wanted = " ".join(str(spice or "").strip().lower().split())
    if not wanted:
        return "Tell me which spice has run out."
    key = _SPICE_ALIASES.get(wanted, wanted)
    if key in _SPICE_TABLE:
        substitute, note = _SPICE_TABLE[key]
        return (f"No {key}? Use {substitute} — {note}.")
    close = difflib.get_close_matches(key, _SPICE_TABLE,
                                      n=3, cutoff=0.5)
    if close:
        return (f"I don't have a swap for '{wanted}'. Closest "
                f"ones I do know: {', '.join(close)} — ask me "
                "about one of those.")
    known = ", ".join(sorted(_SPICE_TABLE)[:6])
    return (f"I don't have a swap for '{wanted}'. My table "
            f"covers {len(_SPICE_TABLE)} spices — {known}, and "
            "more.")


_GROCERY_CATEGORY = "grocery"


def _store():
    """The memory store singleton — this pack's only persistence
    seam. Tests substitute a temp MemoryStore by replacing this
    function; handlers never touch the singleton directly."""
    from .memory.memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
    return memory_store


def _grocery_facts() -> list:
    """Every grocery fact ({id, key, value, ...}), sorted."""
    facts = [f for f in _store().get_all_facts()
             if f.get("category") == _GROCERY_CATEGORY]
    return sorted(facts, key=lambda f: str(f.get("key", "")))


def _count_word(count: int) -> str:
    return f"{count} thing{'s' if count != 1 else ''}"


def grocery_list(action: str = "", item: str = "") -> str:
    """A grocery list kept as memory facts: add an item, show
    the list, remove an item, or clear it."""
    action = str(action or "").strip().lower()
    if action not in ("add", "show", "remove", "clear"):
        return ("Grocery list actions are 'add', 'show', "
                "'remove' and 'clear' — e.g. grocery_list with "
                "action 'add' and an item like 'oat milk'.")
    try:
        if action == "show":
            facts = _grocery_facts()
            if not facts:
                return ("Your grocery list is empty — add "
                        "something with grocery_list, action "
                        "'add' and an item.")
            lines = [f"Grocery list ({_count_word(len(facts))}):"]
            for index, fact in enumerate(facts, start=1):
                lines.append(f"{index}. {fact.get('value') or ''}")
            return "\n".join(lines)
        if action == "clear":
            facts = _grocery_facts()
            if not facts:
                return "The grocery list was already empty."
            for fact in facts:
                _store().delete_fact(fact["id"])
            return (f"Cleared the grocery list — "
                    f"{_count_word(len(facts))} removed.")
        clean = " ".join(str(item or "").split())
        if not clean:
            return (f"That needs the item — grocery_list, action "
                    f"'{action}' and an item.")
        key = _normalise(clean)
        if action == "add":
            if _store().get_fact(_GROCERY_CATEGORY,
                                 key) is not None:
                return (f"'{clean}' is already on your grocery "
                        "list — I didn't add it twice.")
            _store().save_fact(_GROCERY_CATEGORY, key, clean)
            count = len(_grocery_facts())
            return (f"Added '{clean}' to your grocery list — "
                    f"{_count_word(count)} on it now.")
        # action == "remove"
        facts = _grocery_facts()
        target = None
        for fact in facts:
            if _same_item(str(fact.get("key", "")), key):
                target = fact
                break
        if target is None:
            if facts:
                on_it = ", ".join(str(f.get("value") or "")
                                  for f in facts)
                return (f"'{clean}' isn't on your grocery "
                        f"list. On it now: {on_it}.")
            return (f"'{clean}' isn't on your grocery list — "
                    "the list is empty.")
        _store().delete_fact(target["id"])
        remaining = len(_grocery_facts())
        return (f"Removed '{target.get('value') or clean}' — "
                f"{_count_word(remaining)} left on the list.")
    except Exception as e:  # noqa: BLE001
        return f"Couldn't update the grocery list: {e}"
