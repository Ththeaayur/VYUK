"""Focus-day tools of the Productivity-3 pack.

Phase A batch A29 part 1 (skills split): moved verbatim from
core/skills_productivity_day.py — focus_timer_report, meeting_prep_card,
deadline_radar, habit_checkin, water_break and stand_up_nudge, plus
the helpers only they use. Life-admin tools stay in
core/skills_productivity_day.py until batch 2. Behaviour unchanged.
"""

import datetime
import json
import os
import re
from pathlib import Path

from . import google_services, injection
from .brain import planner  # Phase 3 B2-remainder: planner moved to core/brain/
from .memory.memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
from .scheduler import scheduler
# INTENTIONAL (split A29 part 1): _coerce_int is shared by both
# families, so it lives in skills_productivity3_helpers (plan P3(c));
# this module imports it rather than duplicating it.
from .skills_productivity3_helpers import _coerce_int

def _coerce_bool(value, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "y", "1", "done", "did", "on"):
            return True
        if text in ("false", "no", "n", "0", "missed", "off"):
            return False
    return default

def _when_words(when: datetime.datetime, now: datetime.datetime) -> str:
    """'today at 14:30', 'tomorrow at 09:00', 'Fri 24 Oct at 09:00'."""
    day = when.date()
    if day == now.date():
        return f"today at {when:%H:%M}"
    if day == now.date() + datetime.timedelta(days=1):
        return f"tomorrow at {when:%H:%M}"
    return f"{when:%a} {when.day} {when:%b} at {when:%H:%M}"

def _until_words(delta: datetime.timedelta) -> str:
    """'in 3 hours 20 minutes' / 'in 25 minutes' / 'in 2 days'."""
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "now"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes and not days:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return "in " + (" ".join(parts) if parts else "under a minute")

def _reminders() -> list:
    """The scheduler's reminder records (label/fire_at/message/
    repeat/fired/persistent dicts)."""
    return scheduler.get_all_reminders()

def _upcoming_events(within_hours: int) -> list:
    """Structured upcoming calendar events (google_services'
    next_events: {id, title, start, location, all_day} dicts,
    empty list when Google is unavailable)."""
    return google_services.next_events(within_hours=within_hours,
                                       limit=20)

def _calendar_service():
    """(service, failure_message) for the calendar — exactly one of
    the pair is meaningful (google_services' own seam)."""
    return google_services._service_or_failure("calendar",
                                               "your calendar")

def _fetch_events(svc, start, end):
    """Raw calendar event dicts between two datetimes."""
    items, _complete = google_services._fetch_events(
        svc, start, end, max_per_calendar=10)
    return sorted(items, key=google_services._event_sort_key)

def _open_plan_steps() -> tuple[int, int]:
    """(open_step_count, plan_count) across all stored plans."""
    plans, _damaged = planner._load_plans()
    open_steps = 0
    for plan in plans:
        for step in plan.get("steps", []):
            if isinstance(step, dict) and step.get("status") != "done":
                open_steps += 1
    return open_steps, len(plans)

_FILE_SCAN_ROOTS = ("Documents", "Desktop", "Downloads")

_FILE_SCAN_CAP = 30_000

_TITLE_STOPWORDS = {
    "meeting", "call", "sync", "weekly", "daily", "standup",
    "review", "discussion", "session", "intro", "introduction",
    "with", "about", "from", "team", "project", "update",
}

def _recent_files_matching(title: str, hours: int = 24) -> list:
    """Files modified in the last `hours` hours under the home
    folder's Documents/Desktop/Downloads (plus the home folder
    itself) whose names share a significant word with `title`.
    Bounded walk — returns up to 5 display names."""
    words = {
        w for w in re.split(r"[^a-z0-9]+", (title or "").lower())
        if len(w) >= 4 and w not in _TITLE_STOPWORDS
    }
    if not words:
        return []
    home = Path.home()
    roots = [home] + [home / name for name in _FILE_SCAN_ROOTS]
    cutoff = datetime.datetime.now().timestamp() - hours * 3600
    found = []
    scanned = 0
    for root in roots:
        if not root.is_dir():
            continue
        depth = 0 if root == home else 6
        for dirpath, dirnames, filenames in os.walk(root):
            if root == home:
                # The home folder itself: top level only — its
                # subfolders are the named roots above (or app data
                # this scan has no business crawling).
                dirnames[:] = []
            elif depth and len(Path(dirpath).relative_to(root).parts) >= depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                scanned += 1
                if scanned > _FILE_SCAN_CAP:
                    return found[:5]
                lowered = name.lower()
                if not any(w in lowered for w in words):
                    continue
                try:
                    mtime = os.path.getmtime(
                        os.path.join(dirpath, name))
                except OSError:
                    continue
                if mtime >= cutoff:
                    found.append(name)
                    if len(found) >= 5:
                        return found
    return found

# ---------------------------------------------------------------------------
# FOCUS TIMER REPORT — today's Pomodoros, read off the reminder list
# ---------------------------------------------------------------------------

_POMODORO_LABEL_RE = re.compile(r"^Pomodoro (focus|break) (\d+)")

def focus_timer_report() -> str:
    """Today's focus summary from the live Pomodoro schedule."""
    try:
        records = _reminders()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the Pomodoro schedule: {e}"
    today = datetime.date.today()
    entries = []
    for record in records:
        match = _POMODORO_LABEL_RE.match(str(record.get("label", "")))
        if not match:
            continue
        try:
            fire = datetime.datetime.strptime(
                str(record.get("fire_at", "")), "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if fire.date() != today:
            continue
        entries.append({
            "kind": match.group(1),
            "cycle": int(match.group(2)),
            "fire": fire,
            "fired": bool(record.get("fired")),
        })
    honesty = (
        "The Pomodoro keeps no history file — it lives in "
        "VYUK's reminder list, so this covers today only, "
        "only while the app stays open, and the minutes are worked "
        "out from the schedule rather than measured."
    )
    if not entries:
        return ("No Pomodoro rounds on today's schedule — nothing "
                "has run and nothing is waiting. " + honesty)
    entries.sort(key=lambda e: e["fire"])
    # Segment runs: a focus round numbered 1 opens a new run.
    runs: list[list] = []
    current: list = []
    for entry in entries:
        if (entry["kind"] == "focus" and entry["cycle"] == 1
                and current):
            runs.append(current)
            current = []
        current.append(entry)
    if current:
        runs.append(current)
    done_rounds = 0
    pending_rounds = 0
    minutes = 0.0
    untimed_rounds = 0
    next_end = None
    for run in runs:
        by_key = {(e["kind"], e["cycle"]): e for e in run}
        gaps = []
        for entry in run:
            if entry["kind"] == "focus" and entry["cycle"] >= 2:
                prev = by_key.get(("break", entry["cycle"] - 1))
                if prev is not None:
                    gaps.append(
                        (entry["fire"] - prev["fire"]).total_seconds()
                        / 60.0)
        # Every round in a run shares one focus length by
        # construction, so any measurable gap prices the whole run.
        run_len = None
        if gaps:
            gaps.sort()
            run_len = gaps[len(gaps) // 2]
        for entry in run:
            if entry["kind"] != "focus":
                continue
            if entry["fired"]:
                done_rounds += 1
                if run_len:
                    minutes += run_len
                else:
                    untimed_rounds += 1
            else:
                pending_rounds += 1
                if next_end is None or entry["fire"] < next_end:
                    next_end = entry["fire"]
    parts = []
    if done_rounds:
        line = (f"Today's focus: {done_rounds} Pomodoro round"
                f"{'s' if done_rounds != 1 else ''} finished")
        if minutes:
            line += f" — about {minutes:g} minutes of focus"
        line += "."
        parts.append(line)
        if untimed_rounds == 1:
            parts.append(
                "1 of those rounds was a single-round Pomodoro "
                "whose length can't be reconstructed, so the "
                "minutes above don't include it.")
        elif untimed_rounds > 1:
            parts.append(
                f"{untimed_rounds} of those rounds were "
                "single-round Pomodoros whose lengths can't be "
                "reconstructed, so the minutes above don't "
                "include them.")
    else:
        parts.append("No focus rounds have finished yet today.")
    if pending_rounds:
        line = (f"{pending_rounds} round"
                f"{'s' if pending_rounds != 1 else ''} still "
                "scheduled")
        if next_end is not None:
            line += f", the current one ending at {next_end:%H:%M}"
        parts.append(line + ".")
    parts.append(honesty)
    return " ".join(parts)

# ---------------------------------------------------------------------------
# MEETING PREP CARD — the next calendar event, plus matching files
# ---------------------------------------------------------------------------

def _next_event_detail():
    """(raw_event, failure): the soonest calendar event starting
    within the last half hour or the next 7 days. Exactly one of
    the pair is meaningful; (None, "") means nothing is coming."""
    svc, failure = _calendar_service()
    if svc is None:
        return None, failure
    now = datetime.datetime.now().astimezone()
    try:
        items = _fetch_events(svc, now - datetime.timedelta(minutes=30),
                              now + datetime.timedelta(days=7))
    except Exception as e:  # noqa: BLE001
        return None, f"Couldn't read your calendar just now ({e})."
    for event in items:
        parsed = google_services._event_start(event)
        if parsed is None:
            continue
        kind, begin, _finish = parsed
        if kind == "allday":
            start = datetime.datetime(begin.year, begin.month,
                                      begin.day,
                                      tzinfo=google_services._local_tz())
        else:
            start = begin
        if start >= now - datetime.timedelta(minutes=30):
            return event, ""
    return None, ""

def _event_when_text(event, now) -> str:
    parsed = google_services._event_start(event)
    if parsed is None:
        return "time not stated"
    kind, begin, finish = parsed
    if kind == "allday":
        day = begin
        if day == now.date():
            return "all day today"
        if day == now.date() + datetime.timedelta(days=1):
            return "all day tomorrow"
        return f"all day {day:%a} {day.day} {day:%b}"
    text = _when_words(begin, now)
    if finish is not None:
        text += f"–{finish:%H:%M}"
    return text

def _event_link(event) -> str:
    link = (event.get("hangoutLink") or "").strip()
    if link:
        return link
    conf = event.get("conferenceData") or {}
    for point in conf.get("entryPoints") or []:
        if (point.get("entryPointType") == "video"
                and point.get("uri")):
            return str(point["uri"]).strip()
    location = (event.get("location") or "").strip()
    if location.startswith(("http://", "https://")):
        return location
    return ""

def meeting_prep_card() -> str:
    """A prep card for the next calendar event."""
    event, failure = _next_event_detail()
    if event is None:
        if failure:
            return failure
        return ("Nothing coming up on your calendar in the next "
                "7 days — no prep needed.")
    now = datetime.datetime.now().astimezone()
    title = (event.get("summary") or "(untitled event)").strip()
    detail = [f"Next up: {title} — {_event_when_text(event, now)}."]
    attendees = []
    for person in event.get("attendees") or []:
        if not isinstance(person, dict) or person.get("self"):
            continue
        name = (person.get("displayName")
                or person.get("email") or "").strip()
        if name:
            attendees.append(name)
    if attendees:
        shown = ", ".join(attendees[:8])
        if len(attendees) > 8:
            shown += f" and {len(attendees) - 8} more"
        detail.append(f"With: {shown}.")
    location = (event.get("location") or "").strip()
    link = _event_link(event)
    if location and location != link:
        detail.append(f"Where: {location}.")
    if link:
        detail.append(f"Join link: {link}.")
    card = injection.wrap_untrusted("calendar", "\n".join(detail))
    try:
        files = _recent_files_matching(title)
    except Exception:  # noqa: BLE001 — files are colour, never fatal
        files = []
    if files:
        card += ("\nFiles you touched in the last day whose names "
                 "match the meeting: " + ", ".join(files) + ".")
    else:
        card += ("\nNo files modified in the last day match the "
                 "meeting's title.")
    return card

# ---------------------------------------------------------------------------
# DEADLINE RADAR — the single most urgent dated thing in 48 hours
# ---------------------------------------------------------------------------

def deadline_radar(hours: int = 48) -> str:
    """The most urgent dated commitment in the next `hours`
    hours (48 by default — the window the result words use)."""
    win = _coerce_int(hours, 48, 1, 720)
    span = f"{win} hour{'s' if win != 1 else ''}"
    now = datetime.datetime.now().astimezone()
    horizon = now + datetime.timedelta(hours=win)
    candidates = []  # (when, title, source)
    notes = []
    if google_services.is_connected():
        try:
            for event in _upcoming_events(win):
                start = event.get("start")
                if isinstance(start, datetime.datetime):
                    if start.tzinfo is None:
                        start = start.replace(tzinfo=now.tzinfo)
                    candidates.append(
                        (start, str(event.get("title") or
                                    "(untitled event)"), "calendar"))
        except Exception:  # noqa: BLE001
            notes.append("The calendar couldn't be read just now.")
    else:
        notes.append("Google isn't connected, so the calendar "
                     "couldn't be checked.")
    try:
        for record in _reminders():
            if record.get("fired"):
                continue
            try:
                fire = datetime.datetime.strptime(
                    str(record.get("fire_at", "")),
                    "%Y-%m-%d %H:%M").astimezone()
            except ValueError:
                continue
            if now <= fire <= horizon:
                label = (str(record.get("message") or "").strip()
                         or str(record.get("label") or "Reminder"))
                candidates.append((fire, label, "reminder"))
    except Exception:  # noqa: BLE001
        pass
    try:
        open_steps, plan_count = _open_plan_steps()
    except Exception:  # noqa: BLE001
        open_steps, plan_count = 0, 0
    if open_steps:
        notes.append(
            f"Your plans hold {open_steps} open step"
            f"{'s' if open_steps != 1 else ''} across {plan_count} "
            "plan(s), but plans don't carry due times, so they "
            "can't be ranked here.")
    if not candidates:
        base = (f"Nothing is due in the next {span} — no calendar "
                "events and no reminders fall in that window.")
        return " ".join([base] + notes)
    candidates.sort(key=lambda c: c[0])
    when, title, source = candidates[0]
    when_text = _when_words(when, now)
    until = _until_words(when - now)
    if source == "calendar":
        head = (f"The most urgent thing in the next {span} is "
                f"on your calendar, due {when_text} ({until}):")
        result = head + "\n" + injection.wrap_untrusted(
            "calendar", title)
    else:
        result = (f"The most urgent thing in the next {span}: "
                  f"'{title}' — a reminder due {when_text} "
                  f"({until}).")
    if len(candidates) > 1:
        others = len(candidates) - 1
        result += (f" ({others} other dated thing"
                   f"{'s' if others != 1 else ''} also "
                   f"{'falls' if others == 1 else 'fall'} in "
                   "the window.)")
    if notes:
        result += " " + " ".join(notes)
    return result

# ---------------------------------------------------------------------------
# HABIT CHECK-IN — daily log + streak, as a memory fact
# ---------------------------------------------------------------------------

_HABITS_CATEGORY = "habits"

_MAX_HABIT_CHARS = 60

def _load_habit(key: str) -> dict:
    fact = memory_store.get_fact(_HABITS_CATEGORY, key)
    data = {"done": [], "missed": []}
    if fact:
        try:
            stored = json.loads(fact.get("value") or "")
        except Exception:  # noqa: BLE001
            stored = None
        if isinstance(stored, dict):
            for field in ("done", "missed"):
                values = stored.get(field)
                if isinstance(values, list):
                    data[field] = sorted(
                        {str(v) for v in values
                         if isinstance(v, str)})
    return data

def _streak_ending(done_days: set, end: datetime.date) -> int:
    streak = 0
    cursor = end
    while cursor.isoformat() in done_days:
        streak += 1
        cursor -= datetime.timedelta(days=1)
    return streak

def habit_checkin(habit: str, done: bool = True) -> str:
    """Log today's habit check-in and report the streak."""
    name = str(habit or "").strip()
    if not name:
        return ("Tell me the habit — e.g. habit_checkin with the "
                "habit 'exercise' and done true.")
    if len(name) > _MAX_HABIT_CHARS:
        return (f"That habit name is {len(name)} characters — "
                f"habit names are capped at {_MAX_HABIT_CHARS}. "
                "Shorten it and I'll log it.")
    did = _coerce_bool(done, True)
    key = name.lower()
    try:
        data = _load_habit(key)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the habit log: {e}"
    today = datetime.date.today()
    iso = today.isoformat()
    done_set = set(data["done"])
    if iso in done_set:
        prev_streak = _streak_ending(done_set, today)
    else:
        prev_streak = _streak_ending(
            done_set, today - datetime.timedelta(days=1))
    if did:
        done_set.add(iso)
        data["done"] = sorted(done_set)
        data["missed"] = [d for d in data["missed"] if d != iso]
    else:
        done_set.discard(iso)
        data["done"] = sorted(done_set)
        if iso not in data["missed"]:
            data["missed"] = sorted(data["missed"] + [iso])
    try:
        memory_store.save_fact(_HABITS_CATEGORY, key,
                               json.dumps(data))
    except Exception as e:  # noqa: BLE001
        return f"Couldn't save the habit log: {e}"
    if did:
        streak = _streak_ending(done_set, today)
        total = len(done_set)
        if streak > 1:
            return (f"Logged '{name}' as done for today — that's "
                    f"a {streak}-day streak. ({total} day"
                    f"{'s' if total != 1 else ''} logged in all.)")
        return (f"Logged '{name}' as done for today — day one "
                f"of a new streak. ({total} day"
                f"{'s' if total != 1 else ''} logged in all.)")
    if prev_streak > 0:
        return (f"Marked '{name}' as missed today — that ends "
                f"a {prev_streak}-day streak. Tomorrow is a "
                "fresh start.")
    return f"Marked '{name}' as missed today."

# ---------------------------------------------------------------------------
# NUDGE CHAINS — water & stand-up, as scheduler reminder chains
# ---------------------------------------------------------------------------

_CHAIN_HOURS = 8

_WATER_PREFIX = "Water break"

_WATER_MESSAGE = ("Time for a glass of water — sip slowly, and "
                  "give your eyes a moment off the screen.")

_STAND_PREFIX = "Stand up"

_STAND_MESSAGE = ("Time to stand up — walk a little, roll your "
                  "shoulders, then settle back in.")

def _chain_pending(prefix: str) -> list:
    return [r for r in _reminders()
            if str(r.get("label", "")).startswith(prefix)
            and not r.get("fired")]

def _chain_cancel(prefix: str) -> int:
    cancelled = 0
    for record in _reminders():
        label = str(record.get("label", ""))
        if label.startswith(prefix):
            try:
                if scheduler.cancel_reminder(label):
                    cancelled += 1
            except Exception:  # noqa: BLE001
                pass
    return cancelled

def _chain_start(prefix: str, message: str, interval: int,
                 cap: int) -> tuple[int, datetime.datetime,
                                     datetime.datetime]:
    """Cancel any existing chain, then schedule one-shot nudges
    every `interval` minutes for the next ~_CHAIN_HOURS hours.
    Returns (count, first_fire, last_fire)."""
    _chain_cancel(prefix)
    count = max(1, min(cap, int((_CHAIN_HOURS * 60) // interval)))
    now = datetime.datetime.now()
    cursor = now
    first = last = None
    for _ in range(count):
        cursor += datetime.timedelta(minutes=interval)
        scheduler.add_reminder(label=prefix, fire_at=cursor,
                               message=message, repeat=False)
        if first is None:
            first = cursor
        last = cursor
    return count, first, last

def _chain_snooze(prefix: str, minutes: int, message: str):
    """Push the next pending nudge to now + `minutes`.
    Returns the new fire time, or None when nothing is pending."""
    pending = _chain_pending(prefix)
    if not pending:
        return None
    pending.sort(key=lambda r: str(r.get("fire_at", "")))
    scheduler.cancel_reminder(str(pending[0].get("label", "")))
    new_fire = datetime.datetime.now() + datetime.timedelta(
        minutes=minutes)
    scheduler.add_reminder(label=prefix, fire_at=new_fire,
                           message=message, repeat=False)
    return new_fire

def _chain_status(prefix: str, what: str) -> str:
    pending = _chain_pending(prefix)
    if not pending:
        return f"{what} reminders aren't running."
    pending.sort(key=lambda r: str(r.get("fire_at", "")))
    nxt = str(pending[0].get("fire_at", ""))[-5:]
    return (f"{what} reminders are on: {len(pending)} nudge"
            f"{'s' if len(pending) != 1 else ''} left, the next "
            f"at {nxt}.")

_MEMORY_NOTE = ("They live in VYUK's memory only — closing "
                "the app stops them.")

def water_break(action: str = "status", interval_minutes: int = 30,
                snooze_minutes: int = 10) -> str:
    """Start, stop, check or snooze the hydration nudges."""
    action = str(action or "status").strip().lower()
    if action not in ("start", "stop", "status", "snooze"):
        return ("Water break actions are 'start', 'stop', "
                "'status' and 'snooze'.")
    try:
        if action == "status":
            return _chain_status(_WATER_PREFIX, "Water")
        if action == "stop":
            pending = len(_chain_pending(_WATER_PREFIX))
            _chain_cancel(_WATER_PREFIX)
            if not pending:
                return "Water reminders weren't running."
            return (f"Water reminders off — cancelled {pending} "
                    "upcoming nudge"
                    f"{'s' if pending != 1 else ''}.")
        if action == "snooze":
            minutes = _coerce_int(snooze_minutes, 10, 1, 240)
            new_fire = _chain_snooze(_WATER_PREFIX, minutes,
                                     _WATER_MESSAGE)
            if new_fire is None:
                return ("No water nudge to snooze — the reminders "
                        "aren't running.")
            return (f"Snoozed — your next water nudge is now at "
                    f"{new_fire:%H:%M}.")
        interval = _coerce_int(interval_minutes, 30, 5, 240)
        count, first, last = _chain_start(
            _WATER_PREFIX, _WATER_MESSAGE, interval, cap=24)
        return (f"Water reminders on: a gentle nudge every "
                f"{interval} minutes for the next {_CHAIN_HOURS} "
                f"hours ({count} in all; next at {first:%H:%M}, "
                f"last at {last:%H:%M}). {_MEMORY_NOTE} Snooze "
                "one any time with action 'snooze'.")
    except Exception as e:  # noqa: BLE001
        return f"The water reminders couldn't be updated: {e}"

def stand_up_nudge(action: str = "status",
                   interval_minutes: int = 60) -> str:
    """Start, stop or check the hourly movement nudges."""
    action = str(action or "status").strip().lower()
    if action not in ("start", "stop", "status"):
        return ("Stand-up nudge actions are 'start', 'stop' and "
                "'status'.")
    try:
        if action == "status":
            return _chain_status(_STAND_PREFIX, "Stand-up")
        if action == "stop":
            pending = len(_chain_pending(_STAND_PREFIX))
            _chain_cancel(_STAND_PREFIX)
            if not pending:
                return "Stand-up nudges weren't running."
            return (f"Stand-up nudges off — cancelled {pending} "
                    "upcoming nudge"
                    f"{'s' if pending != 1 else ''}.")
        interval = _coerce_int(interval_minutes, 60, 10, 240)
        count, first, last = _chain_start(
            _STAND_PREFIX, _STAND_MESSAGE, interval, cap=12)
        return (f"Stand-up nudges on: a nudge to move every "
                f"{interval} minutes for the next {_CHAIN_HOURS} "
                f"hours ({count} in all; next at {first:%H:%M}, "
                f"last at {last:%H:%M}). {_MEMORY_NOTE} One "
                "limit: the nudges can't yet pause themselves "
                "during calls or fullscreen apps — stop them "
                "any time with action 'stop'.")
    except Exception as e:  # noqa: BLE001
        return f"The stand-up nudges couldn't be updated: {e}"
