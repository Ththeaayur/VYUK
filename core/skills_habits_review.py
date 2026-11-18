"""Review shelf for the skills_habits pack: today's focus score
(read-only from the screen-time counter) and the combined day review.
"""

import datetime
import json

# INTENTIONAL: names moved into skills_habits_helpers with their units.
from .skills_habits_helpers import (
    _duration_words,
    _facts,
    _fmt_amount,
    _read_screen_time,
    _today,
)
# INTENTIONAL: cross-part names moved with their units (§2 P3(b)) —
# day_review re-reads the tracking and mind groups' categories.
from .skills_habits_tracking import _habit_defs, _habit_log_entries
from .skills_habits_mind import _load_gratitude, _load_moods


def focus_score() -> str:
    """Today's tracked screen time plus the share of the
    counted day that wasn't on screen. Read-only: the counter
    in skills_system_health owns the file and its updates."""
    state = _read_screen_time()
    if state is None:
        return ("No screen time logged yet — there's no "
                "readable screen-time record on this PC. The "
                "counter (kept by the screen_time_today tool) "
                "only records while VYUK is running, so "
                "hours away from it are never counted "
                "anywhere.")
    today = _today().isoformat()
    if state.get("date") != today:
        last = str(state.get("date") or "an earlier day")
        previous = state.get("previous_day")
        extra = ""
        if isinstance(previous, dict) and previous.get("date"):
            extra = (f" The day before that "
                     f"({previous['date']}) totalled "
                     f"{_duration_words(float(previous.get('active_seconds') or 0))}.")
        return (f"No screen time logged yet today — the "
                f"counter's last tracked day was {last}, with "
                f"{_duration_words(float(state.get('active_seconds') or 0))} "
                f"of active use.{extra} It only counts while "
                "VYUK is running.")
    active = float(state.get("active_seconds") or 0)
    base = (f"Screen time today: {_duration_words(active)} of "
            "tracked active use")
    try:
        started_at = float(state.get("started_at") or 0)
    except (TypeError, ValueError):
        started_at = 0
    now = datetime.datetime.now().timestamp()
    elapsed = now - started_at
    if started_at <= 0 or elapsed <= 0:
        return (base + ". The counter's start time isn't "
                "recorded, so no focus score can be worked "
                "out honestly.")
    share = min(1.0, max(0.0, active / elapsed))
    score = round(100.0 * (1.0 - share))
    started_clock = datetime.datetime.fromtimestamp(
        started_at).strftime("%H:%M")
    return (base + f". Focus score: {score} out of 100 — "
            f"that's the share of the time since the counter "
            f"started today ({started_clock}) that wasn't "
            "active screen time. Time while VYUK isn't "
            "running is never counted by the counter, so the "
            "score covers only the stretch it has seen.")


def _expense_day(entries: list, today: str) -> tuple[int, float]:
    """(count, total) of expenses whose own recorded date —
    the value's date field, then the key's date prefix, then
    the fact's updated date — is today. The formats are
    skills_productivity_logs's expense_log formats."""
    count = 0
    total = 0.0
    for fact in entries:
        day = ""
        amount = None
        try:
            stored = json.loads(fact.get("value") or "")
            if isinstance(stored, dict):
                day = str(stored.get("date") or "")
                amount = float(stored["amount"])
        except Exception:  # noqa: BLE001
            day = ""
        if not day:
            key = str(fact.get("key") or "")
            day = key[:10]
        if day != today:
            continue
        count += 1
        if amount is not None:
            total += amount
    return count, total


def day_review() -> str:
    """Today so far, assembled from the fact categories the
    other packs write: expenses, decisions, moods, gratitude
    and habit logs."""
    today = _today()
    today_text = today.isoformat()
    lines = [f"Today so far ({today_text}):"]
    try:
        expense_count, expense_total = _expense_day(
            _facts("expenses"), today_text)
        if expense_count:
            lines.append(
                f"• Expenses: {expense_count} logged, "
                f"totalling {_fmt_amount(expense_total)}.")
        else:
            lines.append("• Expenses: none logged today.")
        decisions_today = 0
        for fact in _facts("decisions"):
            try:
                stored = json.loads(fact.get("value") or "")
                if isinstance(stored, dict) and \
                        str(stored.get("added") or "") \
                        == today_text:
                    decisions_today += 1
            except Exception:  # noqa: BLE001
                continue
        if decisions_today:
            lines.append(f"• Decisions: {decisions_today} "
                         "recorded today.")
        else:
            lines.append("• Decisions: none recorded today.")
        moods, _mb = _load_moods()
        todays_moods = [e for e in moods
                        if e["date"] == today_text]
        if todays_moods:
            latest = todays_moods[-1]
            word = (f" ('{latest['mood']}')"
                    if latest["mood"] else "")
            lines.append(
                f"• Mood: latest check-in {latest['score']}"
                f"/10{word} ({len(todays_moods)} today).")
        else:
            lines.append("• Mood: no check-in today.")
        gratitude, _gb = _load_gratitude()
        g_today = sum(1 for e in gratitude
                      if e["date"] == today_text)
        if g_today:
            lines.append(f"• Gratitude: {g_today} entr"
                         f"{'ies' if g_today != 1 else 'y'} "
                         "today.")
        else:
            lines.append("• Gratitude: nothing noted today.")
        defs = _habit_defs()
        entries = _habit_log_entries()
        done_names = sorted({e["name"] for e in entries
                             if e["date"] == today})
        if defs:
            names = (f" ({', '.join(done_names)})"
                     if done_names else "")
            lines.append(
                f"• Habits: {len(done_names)} of {len(defs)} "
                f"tracked habits done today{names}.")
        else:
            lines.append("• Habits: none being tracked yet.")
    except Exception as e:  # noqa: BLE001
        return f"Couldn't assemble today's review: {e}"
    return "\n".join(lines)
