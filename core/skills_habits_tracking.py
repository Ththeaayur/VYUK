"""Habit tracking + body maths for the skills_habits pack.

Habit definitions/logs/streaks, water intake and the screen-break
plan. Imports from helpers travel with the units (§2 P3).
"""

import datetime
import math

# INTENTIONAL: names moved into skills_habits_helpers with their units.
from .skills_habits_helpers import (
    _coerce_float,
    _coerce_int,
    _fmt_amount,
    _MAX_NOTE_CHARS,
    _store,
    _facts,
    _today,
    _unique_key,
)


_HABIT_DEFS_CATEGORY = "habit_defs"


_HABIT_LOGS_CATEGORY = "habit_logs"


_MAX_HABIT_CHARS = 60


_LOOKBACK_DAYS = 365


def _habit_defs() -> dict:
    """{lowercased name: {"name": display name, "created": date}}
    from the habit_defs facts."""
    defs = {}
    for fact in _facts(_HABIT_DEFS_CATEGORY):
        name = str(fact.get("key") or "").strip()
        if not name:
            continue
        created = None
        try:
            created = datetime.date.fromisoformat(
                str(fact.get("value") or "").strip())
        except ValueError:
            created = None
        defs[name.lower()] = {"name": name, "created": created}
    return defs


def _habit_log_entries() -> list:
    """Completion entries as {name, name_key, date, note} dicts,
    parsed out of the "name|YYYY-MM-DD|counter" keys."""
    entries = []
    for fact in _facts(_HABIT_LOGS_CATEGORY):
        parts = str(fact.get("key") or "").split("|")
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        try:
            day = datetime.date.fromisoformat(parts[1].strip())
        except ValueError:
            continue
        entries.append({
            "name": name,
            "name_key": name.lower(),
            "date": day,
            "note": str(fact.get("value") or ""),
        })
    return entries


def _streaks(done_days: set, today: datetime.date) -> tuple[int, int]:
    """(current, best) streak lengths over a set of done dates.

    Current: consecutive days ending today — or ending yesterday
    when today isn't logged yet (the day isn't over, so the
    streak is still alive). Best: the longest run anywhere."""
    current = 0
    if done_days:
        cursor = today
        if cursor not in done_days:
            cursor = today - datetime.timedelta(days=1)
        while cursor in done_days:
            current += 1
            cursor -= datetime.timedelta(days=1)
    best = 0
    run = 0
    previous = None
    for day in sorted(done_days):
        if previous is not None and \
                (day - previous).days == 1:
            run += 1
        else:
            run = 1
        best = max(best, run)
        previous = day
    return current, best


def _habit_stats(name_key: str, created, entries: list,
                 today: datetime.date) -> dict:
    """The stat set habit_summary reports for one habit."""
    days = sorted({e["date"] for e in entries
                   if e["name_key"] == name_key})
    day_set = set(days)
    current, best = _streaks(day_set, today)
    span_start = created
    if span_start is None:
        span_start = days[0] if days else today
    if (today - span_start).days + 1 > _LOOKBACK_DAYS:
        span_start = today - datetime.timedelta(
            days=_LOOKBACK_DAYS - 1)
    span_days = (today - span_start).days + 1
    done_in_span = sum(1 for d in days if d >= span_start)
    return {
        "days": days,
        "entries": sum(1 for e in entries
                       if e["name_key"] == name_key),
        "current": current,
        "best": best,
        "last": days[-1] if days else None,
        "pct": (100.0 * done_in_span / span_days)
        if span_days else 0.0,
        "capped": created is not None
        and (today - created).days + 1 > _LOOKBACK_DAYS,
    }


def habit_track(action: str, name: str = "", note: str = "") -> str:
    """Define a habit, log today's completion, or list habits
    with today's state."""
    action = str(action or "").strip().lower()
    if action not in ("add", "done", "list"):
        return ("Habit actions are 'add' (define a habit), "
                "'done' (log today's completion) and 'list' "
                "(show habits with today's state).")
    if action == "list":
        try:
            defs = _habit_defs()
            entries = _habit_log_entries()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the habit list: {e}"
        if not defs:
            return ("No habits are being tracked yet. Define "
                    "one with habit_track, action 'add' and a "
                    "name — the older habit_checkin tool keeps "
                    "its own separate list.")
        today = _today()
        done_today = {e["name_key"] for e in entries
                      if e["date"] == today}
        lines = [f"Tracked habits ({len(defs)}):"]
        for key in sorted(defs):
            mark = ("done today" if key in done_today
                    else "not yet today")
            lines.append(f"• {defs[key]['name']} — {mark}")
        return "\n".join(lines)
    clean = " ".join(str(name or "").split())
    if not clean:
        return ("That needs the habit's name — e.g. "
                "habit_track with action 'add' and name "
                "'meditate'.")
    if len(clean) > _MAX_HABIT_CHARS:
        return (f"That habit name is {len(clean)} characters — "
                f"habit names are capped at {_MAX_HABIT_CHARS}. "
                "Shorten it and I'll track it.")
    try:
        defs = _habit_defs()
        existing = defs.get(clean.lower())
        if action == "add":
            if existing is not None:
                since = (f" (added {existing['created']})"
                         if existing["created"] else "")
                return (f"'{existing['name']}' is already "
                        f"being tracked{since}.")
            _store().save_fact(_HABIT_DEFS_CATEGORY, clean,
                               _today().isoformat())
            return (f"Now tracking '{clean}'. Log a completion "
                    "with habit_track, action 'done'.")
        # action == "done"
        if existing is None:
            known = (", ".join(d["name"] for d in
                               defs.values())
                     if defs else "")
            extra = (f" Being tracked: {known}." if known
                     else " None are being tracked yet.")
            return (f"'{clean}' isn't a tracked habit yet, so "
                    f"nothing was logged. Add it first with "
                    f"habit_track, action 'add'.{extra}")
        clean_note = " ".join(str(note or "").split())
        if len(clean_note) > _MAX_NOTE_CHARS:
            return (f"That note is {len(clean_note)} characters "
                    f"— notes are capped at {_MAX_NOTE_CHARS}. "
                    "Nothing was logged.")
        today = _today()
        entries = _habit_log_entries()
        already = sum(1 for e in entries
                      if e["name_key"] == clean.lower()
                      and e["date"] == today)
        counter = already + 1
        key = _unique_key(
            _HABIT_LOGS_CATEGORY,
            f"{existing['name']}|{today.isoformat()}|{counter}")
        _store().save_fact(_HABIT_LOGS_CATEGORY, key,
                           clean_note or "done")
        current, _best = _streaks(
            {e["date"] for e in entries
             if e["name_key"] == clean.lower()} | {today}, today)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't update the habit log: {e}"
    again = (f" That's log number {counter} for today."
             if already else "")
    streak = (f" — a {current}-day streak" if current > 1
              else " — day one of a streak" if current == 1
              else "")
    return (f"Logged '{existing['name']}' as done for today"
            f"{streak}.{again}")


def habit_summary(name: str = "") -> str:
    """Streaks, totals and consistency for one habit or all."""
    try:
        defs = _habit_defs()
        entries = _habit_log_entries()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the habit log: {e}"
    if not defs:
        return ("No habits are being tracked yet — habit "
                "summaries need a habit defined with "
                "habit_track, action 'add' first.")
    today = _today()
    wanted = " ".join(str(name or "").split()).lower()
    if wanted:
        if wanted not in defs:
            known = ", ".join(d["name"] for d in defs.values())
            return (f"'{name}' isn't a tracked habit. Being "
                    f"tracked: {known}.")
        keys = [wanted]
    else:
        keys = sorted(defs)
    shown = keys[:10]
    lines = []
    for key in shown:
        info = defs[key]
        stats = _habit_stats(key, info["created"], entries,
                              today)
        total = len(stats["days"])
        head = (f"{info['name']}: done on {total} day"
                f"{'s' if total != 1 else ''}")
        if stats["entries"] != total:
            head += f" ({stats['entries']} log entries)"
        lines.append(head + ".")
        lines.append(
            f"  Current streak: {stats['current']} day"
            f"{'s' if stats['current'] != 1 else ''}; best "
            f"streak: {stats['best']} day"
            f"{'s' if stats['best'] != 1 else ''}.")
        if stats["last"] is not None:
            lines.append(f"  Last done: "
                         f"{stats['last'].isoformat()}.")
        else:
            lines.append("  Never logged as done yet.")
        cap_note = (f" (the last {_LOOKBACK_DAYS} days — the "
                    "habit is older than that)" if stats["capped"]
                    else "")
        lines.append(
            f"  Done on {stats['pct']:.0f}% of days since it "
            f"was added{cap_note}.")
    if len(keys) > len(shown):
        lines.append(f"(Showing {len(shown)} of {len(keys)} "
                     "habits — ask about one by name for the "
                     "rest.)")
    return "\n".join(lines)


def water_intake_math(weight_kg, activity_minutes=0) -> str:
    """A daily water target from body weight plus activity."""
    weight = _coerce_float(weight_kg)
    if weight is None or weight <= 0:
        return ("Water maths needs a body weight in kilograms "
                "— a number above zero.")
    if weight > 500:
        return ("That weight is over 500 kg, which doesn't "
                "look like a body weight — check the number "
                "and try again.")
    activity = _coerce_float(activity_minutes)
    if activity is None or activity < 0:
        return ("Activity minutes must be zero or more — "
                "leave it at zero for a quiet day.")
    base_ml = 35.0 * weight
    extra_ml = 350.0 * (activity / 30.0)
    total_ml = base_ml + extra_ml
    litres = total_ml / 1000.0
    glasses = math.ceil(total_ml / 250.0)
    result = (f"Daily water target: about {litres:.2f} litres "
              f"— roughly {glasses} glasses of 250 ml.")
    detail = (f"That's the standard rule of thumb used here: "
              f"35 ml per kg of body weight ({weight:g} kg "
              f"makes {_fmt_amount(base_ml)} ml)")
    if activity:
        detail += (f", plus 350 ml for every 30 minutes of "
                   f"activity ({activity:g} minutes adds "
                   f"{_fmt_amount(extra_ml)} ml)")
    return result + " " + detail + "."


_BREAK_MINUTES = 5


def screen_break_plan(screen_hours, break_minutes=20,
                      start_time: str = "") -> str:
    """Split screen time into work blocks, each followed by a
    5-minute break (the plan's stated constant)."""
    hours = _coerce_float(screen_hours)
    if hours is None or hours <= 0:
        return ("A break plan needs the screen time in hours "
                "— a number above zero.")
    if hours > 24:
        return ("That's more screen time than a day holds — "
                "give me 24 hours or less.")
    block = _coerce_int(break_minutes, 20, 5, 120)
    total_min = int(round(hours * 60))
    full_blocks, remainder = divmod(total_min, block)
    segments = full_blocks + (1 if remainder else 0)
    breaks = max(0, segments - 1)
    break_total = breaks * _BREAK_MINUTES
    lines = [
        f"Plan for {_fmt_amount(hours)} hours of screen time: "
        f"work in blocks of {block} minutes, with a "
        f"{_BREAK_MINUTES}-minute break after each block "
        f"(the last block needs none)."]
    if remainder:
        lines.append(f"That's {full_blocks} full block"
                     f"{'s' if full_blocks != 1 else ''} plus a "
                     f"final short block of {remainder} minutes.")
    else:
        lines.append(f"That's {full_blocks} block"
                     f"{'s' if full_blocks != 1 else ''}.")
    if breaks:
        lines.append(f"Breaks: {breaks}, totalling "
                     f"{break_total} minutes away from the "
                     "screen.")
    else:
        lines.append("The screen time fits in a single block, "
                     "so no break falls inside it.")
    start_text = str(start_time or "").strip()
    if start_text:
        try:
            start = datetime.datetime.strptime(start_text,
                                               "%H:%M")
        except ValueError:
            lines.append(f"('{start_text}' doesn't look like a "
                         "start time — use HH:MM, like 09:30, "
                         "and I'll give the finishing time.)")
            return "\n".join(lines)
        finish = start + datetime.timedelta(
            minutes=total_min + break_total)
        suffix = " the next day" if finish.day != start.day \
            else ""
        lines.append(f"Starting at {start_text}, you'd finish "
                     f"at {finish:%H:%M}{suffix} (screen time "
                     "plus breaks).")
    return "\n".join(lines)
