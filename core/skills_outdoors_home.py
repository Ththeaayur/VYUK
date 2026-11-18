"""Outdoors home-life tools — split from core/skills_outdoors.py (skills split Phase A, batch A10).
Move only: the code below is verbatim from the original file apart
from the module-level import lines at the top.
"""

import datetime
import json

# INTENTIONAL: home-life tools resolve these bare names in this
# module's globals (bare-name rule); they are defined in
# skills_outdoors_helpers.
from .skills_outdoors_helpers import (
    _PLANTS_CATEGORY,
    _PET_CATEGORY,
    _coerce_float,
    _duration_words,
    _facts,
    _fmt_num,
    _store,
)


# ---------------------------------------------------------------------------
# GARDEN WATERING — typical intervals, adjusted for the pot
# ---------------------------------------------------------------------------

# Typical days between waterings for a mid-size pot.
_WATER_INTERVALS = {
    "tomato": 3,
    "basil": 2,
    "mint": 2,
    "chilli": 3,
    "succulent": 7,
    "fern": 3,
    "lavender": 5,
}
_GENERIC_INTERVAL = 3


def _watering_interval(plant: str, pot_size_cm: float = 20):
    """(interval_days, known): the plant's typical interval with
    the pot adjustment applied — small pots dry out faster,
    large ones hold moisture longer."""
    base = _WATER_INTERVALS.get((plant or "").strip().lower())
    known = base is not None
    if base is None:
        base = _GENERIC_INTERVAL
    interval = base
    if pot_size_cm < 15:
        interval = max(1, interval - 1)
    elif pot_size_cm > 30:
        interval += 1
    return interval, known


def _watering_verdict(plant_label: str, interval: int,
                      days_since) -> str:
    """The due/overdue wording shared by garden_watering and
    the plant log's list view."""
    remaining = interval - days_since
    if remaining > 0:
        return (f"due in {remaining} day"
                f"{'s' if remaining != 1 else ''}")
    if remaining == 0:
        return "due now"
    overdue = -remaining
    return (f"overdue by {overdue} day"
            f"{'s' if overdue != 1 else ''}")


def garden_watering(plant: str = "", days_since_watered=0,
                    pot_size_cm=20) -> str:
    """Whether a plant is due a water, from typical intervals."""
    name = " ".join(str(plant or "").split())
    if not name:
        return ("Tell me the plant — like garden_watering with "
                "plant 'basil' and how many days since it was "
                "last watered.")
    days_value = _coerce_float(days_since_watered)
    if days_value is None or days_value < 0:
        return ("Days since watering needs to be a number, "
                "zero or more.")
    days = int(days_value)
    pot = _coerce_float(pot_size_cm)
    if pot is None or pot <= 0:
        return ("The pot size needs to be a number of "
                "centimetres, more than zero.")
    interval, known = _watering_interval(name, pot)
    verdict = _watering_verdict(name, interval, days)
    label = name[:1].upper() + name[1:]
    table = ", ".join(sorted(_WATER_INTERVALS))
    if not known:
        lead = (f"I don't have {name} in my table (it covers "
                f"{table}), so I've used the generic guidance of "
                f"watering every {_GENERIC_INTERVAL} days.")
    elif pot < 15:
        lead = (f"{label} typically wants water every "
                f"{_WATER_INTERVALS[name.lower()]} days, but a "
                f"small {pot:g} cm pot dries out about a day "
                f"faster — call it every {interval} days.")
    elif pot > 30:
        lead = (f"{label} typically wants water every "
                f"{_WATER_INTERVALS[name.lower()]} days, and a "
                f"large {pot:g} cm pot holds moisture about a "
                f"day longer — call it every {interval} days.")
    else:
        lead = (f"{label} typically wants water every "
                f"{interval} days in a {pot:g} cm pot.")
    ago = ("today" if days == 0 else
           f"{days} day{'s' if days != 1 else ''} ago")
    tail = (f"It was last watered {ago}, so it's {verdict}."
            if verdict == "due now" else
            f"Last watered {ago} — {verdict}.")
    if verdict.startswith("overdue"):
        tail = (f"Last watered {ago} — {verdict}. Water it as "
                "soon as you can.")
    return f"{lead} {tail}"


# ---------------------------------------------------------------------------
# PLANT CARE LOG — plants and their last-watered dates, as facts
# ---------------------------------------------------------------------------


def _load_plants() -> tuple[list, int]:
    """(entries, broken_count): {name, last_watered, note}
    dicts parsed from the plants facts."""
    entries = []
    broken = 0
    for fact in _facts(_PLANTS_CATEGORY):
        try:
            stored = json.loads(fact.get("value") or "")
            name = str(stored["name"]).strip()
            if not name:
                raise ValueError
            last = str(stored["last_watered"])
            datetime.date.fromisoformat(last)
            entries.append({
                "name": name,
                "last_watered": last,
                "note": str(stored.get("note") or ""),
            })
        except Exception:  # noqa: BLE001
            broken += 1
    return entries, broken


def _save_plant(entry: dict) -> None:
    _store().save_fact(_PLANTS_CATEGORY, entry["name"].lower(),
                       json.dumps(entry))


def plant_care_log(action: str, plant: str = "",
                   note: str = "") -> str:
    """Track plants and when each was last watered."""
    action = str(action or "").strip().lower()
    if action not in ("add", "watered", "list"):
        return ("Plant log actions are 'add', 'watered' and "
                "'list' — e.g. plant_care_log with action 'add' "
                "and plant 'basil'.")
    today = datetime.date.today()
    if action == "list":
        try:
            entries, broken = _load_plants()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the plant log: {e}"
        if not entries:
            base = ("No plants in the log yet. Add one with "
                    "plant_care_log, action 'add' and the "
                    "plant's name — it's stamped as watered "
                    "today.")
            if broken:
                base += (f" ({broken} stored entr"
                         f"{'ies' if broken != 1 else 'y'} "
                         "couldn't be read and were skipped.)")
            return base
        rows = []
        for entry in entries:
            last = datetime.date.fromisoformat(
                entry["last_watered"])
            days = (today - last).days
            interval, _known = _watering_interval(entry["name"])
            verdict = _watering_verdict(entry["name"], interval,
                                        days)
            rows.append((interval - days, entry, days, verdict))
        rows.sort(key=lambda r: r[0])  # most overdue first
        lines = [f"Plant log ({len(entries)}):"]
        for _rem, entry, days, verdict in rows:
            ago = ("today" if days <= 0 else
                   f"{days} day{'s' if days != 1 else ''} ago")
            line = (f"• {entry['name']} — last watered {ago} "
                    f"({entry['last_watered']}) — {verdict}")
            if entry["note"]:
                line += f" — {entry['note']}"
            lines.append(line)
        if broken:
            lines.append(f"({broken} stored entr"
                         f"{'ies' if broken != 1 else 'y'} "
                         "couldn't be read and were skipped.)")
        return "\n".join(lines)
    name = " ".join(str(plant or "").split())
    if not name:
        return "That needs the plant's name."
    clean_note = " ".join(str(note or "").split())
    try:
        entries, _broken = _load_plants()
        existing = None
        for entry in entries:
            if entry["name"].lower() == name.lower():
                existing = entry
                break
        if action == "add":
            if existing is not None:
                return (f"'{existing['name']}' is already in "
                        "the plant log. Use action 'watered' "
                        "when you water it.")
            entry = {"name": name,
                     "last_watered": today.isoformat(),
                     "note": clean_note}
            _save_plant(entry)
            result = (f"Added '{name}' to the plant log, "
                      "marked as watered today.")
            if clean_note:
                result += f" Note kept: '{clean_note}'."
            return result
        # action == "watered"
        if existing is None:
            known = (", ".join(e["name"] for e in entries)
                     if entries else "")
            extra = (f" In the log: {known}." if known
                     else " The log is empty — add it first "
                          "with action 'add'.")
            return (f"'{name}' isn't in the plant log.{extra}")
        existing["last_watered"] = today.isoformat()
        if clean_note:
            existing["note"] = clean_note
        _save_plant(existing)
        return (f"Marked '{existing['name']}' as watered "
                "today.")
    except Exception as e:  # noqa: BLE001
        return f"Couldn't update the plant log: {e}"


# ---------------------------------------------------------------------------
# PET FEEDING LOG — feedings as facts, today and recent views
# ---------------------------------------------------------------------------


def _load_feedings() -> tuple[list, int]:
    """(entries, broken_count): {pet, food, grams, date, time}
    dicts parsed from the pet_feeding facts."""
    entries = []
    broken = 0
    for fact in _facts(_PET_CATEGORY):
        try:
            stored = json.loads(fact.get("value") or "")
            pet = str(stored["pet"]).strip()
            grams = float(stored["grams"])
            day = str(stored["date"])
            datetime.date.fromisoformat(day)
            if not pet:
                raise ValueError
            entries.append({
                "pet": pet,
                "food": str(stored.get("food") or ""),
                "grams": grams,
                "date": day,
                "time": str(stored.get("time") or ""),
            })
        except Exception:  # noqa: BLE001
            broken += 1
    entries.sort(key=lambda e: (e["date"], e["time"]))
    return entries, broken


def pet_feeding_log(action: str, pet: str = "", food: str = "",
                    grams=0) -> str:
    """Record pet feedings and see today's or recent ones."""
    action = str(action or "").strip().lower()
    if action not in ("add", "today", "list"):
        return ("Pet feeding actions are 'add', 'today' and "
                "'list' — e.g. pet_feeding_log with action "
                "'add', a pet and the grams fed.")
    today = datetime.date.today().isoformat()
    if action == "add":
        name = " ".join(str(pet or "").split())
        if not name:
            return "A feeding needs the pet's name."
        amount = _coerce_float(grams)
        if amount is None or amount <= 0:
            return ("A feeding needs the amount in grams, "
                    "more than zero. Nothing was logged.")
        clean_food = " ".join(str(food or "").split())
        now = datetime.datetime.now()
        payload = {"pet": name, "food": clean_food,
                   "grams": amount, "date": today,
                   "time": now.strftime("%H:%M:%S")}
        key = f"{today} {name.lower()}"
        try:
            suffix = 1
            candidate = key
            while _store().get_fact(_PET_CATEGORY,
                                    candidate) is not None:
                suffix += 1
                candidate = f"{key} ({suffix})"
            _store().save_fact(_PET_CATEGORY, candidate,
                               json.dumps(payload))
        except Exception as e:  # noqa: BLE001
            return f"Couldn't save the feeding: {e}"
        what = f" of {clean_food}" if clean_food else ""
        return (f"Logged a feeding for {name}: "
                f"{_fmt_num(amount)} g{what}, today at "
                f"{now:%H:%M}.")
    try:
        entries, broken = _load_feedings()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the feeding log: {e}"
    broken_note = ""
    if broken:
        broken_note = (f" ({broken} stored feeding"
                       f"{'s' if broken != 1 else ''} couldn't "
                       "be read and were skipped.)")
    if action == "today":
        mine = [e for e in entries if e["date"] == today]
        if not mine:
            return (f"No feedings logged today yet.{broken_note}")
        by_pet: dict[str, dict] = {}
        for entry in mine:
            slot = by_pet.setdefault(
                entry["pet"], {"total": 0.0, "count": 0})
            slot["total"] += entry["grams"]
            slot["count"] += 1
        lines = ["Feedings today:"]
        for pet_name in sorted(by_pet):
            slot = by_pet[pet_name]
            lines.append(
                f"• {pet_name} — {_fmt_num(slot['total'])} g "
                f"across {slot['count']} feeding"
                f"{'s' if slot['count'] != 1 else ''}")
        if broken:
            lines.append(broken_note.strip())
        return "\n".join(lines)
    # action == "list"
    if not entries:
        return (f"No feedings logged yet. Add one with "
                f"pet_feeding_log, action 'add', a pet and "
                f"the grams fed.{broken_note}")
    recent = entries[-5:]
    lines = [f"Most recent feedings ({len(recent)} of "
             f"{len(entries)}):"]
    for entry in reversed(recent):
        line = (f"• {entry['date']} {entry['time'][:5]} — "
                f"{entry['pet']} — {_fmt_num(entry['grams'])} g")
        if entry["food"]:
            line += f" of {entry['food']}"
        lines.append(line)
    if broken:
        lines.append(broken_note.strip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# WALK TIME + STEP MATHS — a stated stride and pace table
# ---------------------------------------------------------------------------

_PACES = {"stroll": 4.0, "steady": 5.0, "brisk": 6.0, "power": 7.0}
_STRIDE_M = 0.75
_GOAL_STEPS = 10000


def walk_time(distance_km, pace: str = "brisk", pace_kmh=0) -> str:
    """How long a walk takes, with steps and a calorie range."""
    distance = _coerce_float(distance_km)
    if distance is None or distance <= 0:
        return ("I need the distance in kilometres, more "
                "than zero — like walk_time with "
                "distance_km 3.5.")
    speed = _coerce_float(pace_kmh) or 0.0
    pace_word = str(pace or "brisk").strip().lower()
    if speed <= 0:
        numeric = _coerce_float(pace_word)
        if numeric is not None and numeric > 0:
            speed = numeric
        elif pace_word in _PACES:
            speed = _PACES[pace_word]
        else:
            choices = ", ".join(
                f"{name} ({rate:g} km/h)"
                for name, rate in _PACES.items())
            return (f"I don't know the pace '{pace}'. "
                    f"Choices: {choices} — or give pace_kmh "
                    "as a number.")
    minutes = distance / speed * 60.0
    steps = distance * 1000.0 / _STRIDE_M
    low = distance * 55
    high = distance * 75
    return (f"{_fmt_num(distance)} km at {_fmt_num(speed)} km/h "
            f"takes about {_duration_words(minutes)}. "
            f"That's roughly {_fmt_num(round(steps, -1))} "
            f"steps at a {_STRIDE_M:g} m stride, and around "
            f"{_fmt_num(low)}–{_fmt_num(high)} kcal — the "
            "calories are a rough estimate, from the usual "
            "55–75 kcal per kilometre band.")


def step_goal_math(steps) -> str:
    """Turn a step count into distance, walking time and a
    share of the 10,000-step day."""
    value = _coerce_float(steps)
    if value is None or value <= 0:
        return ("I need a step count, more than zero — like "
                "step_goal_math with steps 6500.")
    count = int(value)
    km = count * _STRIDE_M / 1000.0
    brisk = _PACES["brisk"]
    minutes = km / brisk * 60.0
    pct = count / _GOAL_STEPS * 100.0
    return (f"{count:,} steps is about {_fmt_num(km)} km at a "
            f"{_STRIDE_M:g} m stride — that's "
            f"{_duration_words(minutes)} of walking at a brisk "
            f"{brisk:g} km/h, and {_fmt_num(pct)}% of a "
            f"{_GOAL_STEPS:,}-step day.")

