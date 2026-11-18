"""Mind shelf for the skills_habits pack: journal prompts, mood
check-ins with a numbers-only trend, and the gratitude log.
"""

import datetime
import json

# INTENTIONAL: names moved into skills_habits_helpers with their units.
from .skills_habits_helpers import (
    _coerce_int,
    _MAX_NOTE_CHARS,
    _store,
    _facts,
    _today,
    _unique_key,
)


_JOURNAL_PROMPTS = (
    "What is one small thing that went better than expected today?",
    "What would you do with one completely free hour tomorrow?",
    "Which conversation from this week is still on your mind, and why?",
    "What is something you used to find hard that feels easier now?",
    "Name one thing you can see, hear and feel right now.",
    "What is a mistake you learned something useful from?",
    "Who made your life easier recently, and how?",
    "What would you tell yourself one year ago?",
    "What are you putting off, and what is the smallest first step?",
    "Describe a place where you feel completely at ease.",
    "What is one belief you have changed your mind about?",
    "Which song or sound sums up this week?",
    "What did you enjoy learning recently, however small?",
    "What does a good ordinary day look like for you?",
    "Name one thing your future self will thank you for starting now.",
    "What is taking up more headspace than it deserves?",
    "Write about a meal you would happily eat once a week forever.",
    "What is something kind you could do in under five minutes?",
    "Which object in your home has the best story behind it?",
    "What felt hard today, and what did you do anyway?",
    "If today had a title, what would it be?",
    "What is one question you wish someone would ask you?",
    "Describe a sound from your childhood home.",
    "What are you curious about right now?",
    "What would 'enough' look like for the thing you're chasing?",
    "Name a small luxury you already have.",
    "What did your hands make, fix or build most recently?",
    "Which part of your routine actually works well?",
    "What is something you want to remember about today?",
    "Write a one-line letter to tomorrow.",
)


def journal_prompt() -> str:
    """Today's journal prompt — the same all day, rotating by
    date through a fixed list."""
    today = _today()
    prompt = _JOURNAL_PROMPTS[today.toordinal()
                              % len(_JOURNAL_PROMPTS)]
    return (f"Today's journal prompt ({today.isoformat()}): "
            f"{prompt}")


_MOODS_CATEGORY = "moods"


_MAX_MOOD_CHARS = 30


def _load_moods() -> tuple[list, int]:
    """(entries, broken_count): {score, mood, note, date, id}
    dicts from the mood facts, oldest first."""
    entries = []
    broken = 0
    for fact in _facts(_MOODS_CATEGORY):
        try:
            stored = json.loads(fact.get("value") or "")
            score = int(stored["score"])
            day = str(stored["date"])
            datetime.date.fromisoformat(day)
            entries.append({
                "score": score,
                "mood": str(stored.get("mood") or ""),
                "note": str(stored.get("note") or ""),
                "date": day,
                "id": int(fact.get("id") or 0),
            })
        except Exception:  # noqa: BLE001
            broken += 1
    entries.sort(key=lambda e: e["id"])
    return entries, broken


def mood_log(action: str, mood: str = "", score=0,
             note: str = "") -> str:
    """Record a mood check-in (score 1–10) or list recent ones."""
    action = str(action or "").strip().lower()
    if action not in ("add", "list"):
        return ("Mood actions are 'add' (record a check-in "
                "with a score from 1 to 10) and 'list' (the "
                "most recent check-ins).")
    if action == "list":
        try:
            entries, broken = _load_moods()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the mood log: {e}"
        if not entries:
            base = ("No mood check-ins yet. Add one with "
                    "mood_log, action 'add' and a score from "
                    "1 to 10.")
            if broken:
                base += (f" ({broken} stored check-in"
                         f"{'s' if broken != 1 else ''} couldn't "
                         "be read and were skipped.)")
            return base
        recent = list(reversed(entries))[:7]
        lines = [f"Recent mood check-ins (newest first, "
                 f"{len(recent)} of {len(entries)}):"]
        for entry in recent:
            line = f"• {entry['date']} — {entry['score']}/10"
            if entry["mood"]:
                line += f" — {entry['mood']}"
            if entry["note"]:
                line += f" ({entry['note']})"
            lines.append(line)
        if broken:
            lines.append(f"({broken} stored check-in"
                         f"{'s' if broken != 1 else ''} couldn't "
                         "be read and were skipped.)")
        return "\n".join(lines)
    value = _coerce_int(score, 0, -1000, 1000)
    if not 1 <= value <= 10:
        return ("A check-in needs a score from 1 to 10. "
                "Nothing was logged.")
    clean_mood = " ".join(str(mood or "").split())
    if len(clean_mood) > _MAX_MOOD_CHARS:
        return (f"That mood word is {len(clean_mood)} "
                f"characters — mood words are capped at "
                f"{_MAX_MOOD_CHARS}. Nothing was logged.")
    clean_note = " ".join(str(note or "").split())
    if len(clean_note) > _MAX_NOTE_CHARS:
        return (f"That note is {len(clean_note)} characters — "
                f"notes are capped at {_MAX_NOTE_CHARS}. "
                "Nothing was logged.")
    now = datetime.datetime.now()
    payload = {"score": value, "mood": clean_mood,
               "note": clean_note,
               "date": now.date().isoformat(),
               "ts": now.isoformat(timespec="seconds")}
    try:
        key = _unique_key(
            _MOODS_CATEGORY,
            now.isoformat(timespec="seconds"))
        _store().save_fact(_MOODS_CATEGORY, key,
                           json.dumps(payload))
    except Exception as e:  # noqa: BLE001
        return f"Couldn't save the check-in: {e}"
    word = f" ('{clean_mood}')" if clean_mood else ""
    return (f"Logged a mood check-in of {value}/10{word} for "
            f"today ({payload['date']}).")


def mood_trend(days: int = 14) -> str:
    """Numbers only: window average, best/worst day, and the
    direction between the window's two halves."""
    window = _coerce_int(days, 14, 1, 90)
    try:
        entries, _broken = _load_moods()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the mood log: {e}"
    today = _today()
    start = today - datetime.timedelta(days=window - 1)
    mine = [e for e in entries
            if start.isoformat() <= e["date"]
            <= today.isoformat()]
    if not mine:
        return (f"No mood check-ins in the last {window} day"
                f"{'s' if window != 1 else ''} — the trend "
                "needs logged check-ins (mood_log, action "
                "'add') before it can say anything.")
    scores = [e["score"] for e in mine]
    average = sum(scores) / len(scores)
    by_day: dict[str, list] = {}
    for entry in mine:
        by_day.setdefault(entry["date"], []).append(
            entry["score"])
    day_avg = {d: sum(v) / len(v) for d, v in by_day.items()}
    best_day = max(day_avg, key=lambda d: day_avg[d])
    worst_day = min(day_avg, key=lambda d: day_avg[d])
    lines = [
        f"Mood check-ins for the last {window} day"
        f"{'s' if window != 1 else ''}: {len(mine)} entr"
        f"{'ies' if len(mine) != 1 else 'y'}, averaging "
        f"{average:.1f} out of 10.",
        f"Highest day: {best_day} (averaged "
        f"{day_avg[best_day]:.1f}); lowest day: {worst_day} "
        f"(averaged {day_avg[worst_day]:.1f}).",
    ]
    if len(mine) < 4:
        lines.append("That's fewer than 4 check-ins, so no "
                     "direction is offered — there isn't enough "
                     "to compare yet.")
    else:
        half = len(mine) // 2
        first = sum(scores[:half]) / half
        second = sum(scores[half:]) / (len(scores) - half)
        if second - first > 0.5:
            direction = "rising"
        elif first - second > 0.5:
            direction = "falling"
        else:
            direction = "steady"
        lines.append(
            f"Direction: {direction} — the first half of the "
            f"window averaged {first:.1f}, the second half "
            f"{second:.1f}.")
    lines.append("That's just the numbers from your "
                 "check-ins, as recorded.")
    return "\n".join(lines)


_GRATITUDE_CATEGORY = "gratitude"


_MAX_GRATITUDE_CHARS = 200


def _load_gratitude() -> tuple[list, int]:
    """(entries, broken_count): {text, date, id} dicts from the
    gratitude facts, oldest first."""
    entries = []
    broken = 0
    for fact in _facts(_GRATITUDE_CATEGORY):
        try:
            stored = json.loads(fact.get("value") or "")
            text = str(stored["text"]).strip()
            if not text:
                raise ValueError
            day = str(stored["date"])
            datetime.date.fromisoformat(day)
            entries.append({"text": text, "date": day,
                            "id": int(fact.get("id") or 0)})
        except Exception:  # noqa: BLE001
            broken += 1
    entries.sort(key=lambda e: e["id"])
    return entries, broken


def gratitude_log(action: str = "list", text: str = "") -> str:
    """Add a gratitude entry, list recent ones, or resurface a
    random past one."""
    action = str(action or "list").strip().lower()
    if action not in ("add", "list", "random"):
        return ("Gratitude actions are 'add' (with the text), "
                "'list' (recent entries) and 'random' (one "
                "past entry).")
    try:
        entries, broken = _load_gratitude()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the gratitude log: {e}"
    broken_note = (
        f" ({broken} stored entr"
        f"{'ies' if broken != 1 else 'y'} couldn't be read "
        "and were skipped.)" if broken else "")
    if action == "add":
        clean = " ".join(str(text or "").split())
        if not clean:
            return ("A gratitude entry needs the text — the "
                    "thing you're grateful for.")
        if len(clean) > _MAX_GRATITUDE_CHARS:
            return (f"That entry is {len(clean)} characters — "
                    f"entries are capped at "
                    f"{_MAX_GRATITUDE_CHARS}. Nothing was "
                    "saved.")
        now = datetime.datetime.now()
        payload = {"text": clean,
                   "date": now.date().isoformat(),
                   "ts": now.isoformat(timespec="seconds")}
        try:
            key = _unique_key(
                _GRATITUDE_CATEGORY,
                now.isoformat(timespec="seconds"))
            _store().save_fact(_GRATITUDE_CATEGORY, key,
                               json.dumps(payload))
        except Exception as e:  # noqa: BLE001
            return f"Couldn't save the entry: {e}"
        return (f"Saved to your gratitude log: '{clean}' "
                f"({len(entries) + 1} entr"
                f"{'ies' if len(entries) + 1 != 1 else 'y'} "
                f"in all).")
    if action == "random":
        if not entries:
            return ("The gratitude log is empty — there's no "
                    "past entry to resurface yet. Add one with "
                    "gratitude_log, action 'add'." + broken_note)
        pick = entries[_today().toordinal() % len(entries)]
        return (f"From your gratitude log ({pick['date']}): "
                f"'{pick['text']}'" + broken_note)
    # action == "list"
    if not entries:
        return ("The gratitude log is empty. Add an entry "
                "with gratitude_log, action 'add' and the "
                "text." + broken_note)
    recent = list(reversed(entries))[:5]
    lines = [f"Gratitude log — {len(entries)} entr"
             f"{'ies' if len(entries) != 1 else 'y'} in all, "
             "most recent first:"]
    for entry in recent:
        lines.append(f"• {entry['date']} — {entry['text']}")
    if broken:
        lines.append(broken_note.strip())
    return "\n".join(lines)
