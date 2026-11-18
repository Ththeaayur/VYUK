import datetime
import json
import re
import time

from .scheduler import scheduler

# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _coerce_int(value, default: int, lo: int, hi: int) -> int:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _coerce_float(value):
    """A float, or None when the value isn't numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_amount(value: float) -> str:
    """'1,299.5' / '100' — plain number, no currency symbol."""
    text = f"{value:,.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _fmt_duration(seconds: float) -> str:
    """'1h 23m 45s' / '12m 05s' / '45s'."""
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _fmt_hours(hours: float) -> str:
    """'7.5' / '8' — person-hours without a trailing '.0'."""
    if hours == int(hours):
        return str(int(hours))
    return f"{hours:.1f}"


def _when_words(when: datetime.datetime, now: datetime.datetime) -> str:
    """'today at 14:30', 'tomorrow at 09:00', 'Fri 24 Oct at 09:00'."""
    day = when.date()
    if day == now.date():
        return f"today at {when:%H:%M}"
    if day == now.date() + datetime.timedelta(days=1):
        return f"tomorrow at {when:%H:%M}"
    return f"{when:%a} {when.day} {when:%b} at {when:%H:%M}"


# ---------------------------------------------------------------------------
# Seams — persistence and every external read sit behind one of
# these so tests can monkeypatch a single name.
# ---------------------------------------------------------------------------


def _store():
    """The memory store singleton — this pack's only persistence
    seam. Tests substitute a temp MemoryStore by replacing this
    function; handlers never touch the singleton directly."""
    from .memory.memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
    return memory_store


def _facts(category: str) -> list:
    """Every stored fact in one category ({id, key, value, ...})."""
    return [f for f in _store().get_all_facts()
            if f.get("category") == category]


def _reminders() -> list:
    """The scheduler's reminder records (label/fire_at/message/
    repeat/fired/persistent dicts)."""
    return scheduler.get_all_reminders()


def _stopwatch_state():
    """The stopwatch's saved state dict ({banked, running,
    started_at, laps}) via skills_productivity_tools's own loader, or
    None when no stopwatch exists."""
    from . import skills_productivity_tools
    return skills_productivity_tools._load_stopwatch()


_POMODORO_PREFIX = "Pomodoro"


def _pomodoro_state() -> dict:
    """The Pomodoro's round state, read off the reminder list the
    Pomodoro tool itself schedules on: pending Pomodoro reminder
    records plus how many Pomodoro reminders have fired today."""
    records = _reminders()
    pending = []
    fired_today = 0
    today = datetime.date.today().isoformat()
    for record in records:
        if not str(record.get("label", "")).startswith(
                _POMODORO_PREFIX):
            continue
        if record.get("fired"):
            if str(record.get("fire_at", "")).startswith(today):
                fired_today += 1
        else:
            pending.append(record)
    pending.sort(key=lambda r: str(r.get("fire_at", "")))
    return {"pending": pending, "fired_today": fired_today}


# ---------------------------------------------------------------------------
# EXPENSE LOG — dated amounts as memory facts, month views + totals
# ---------------------------------------------------------------------------

_EXPENSES_CATEGORY = "expenses"
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_MAX_NOTE_CHARS = 80


def _load_expenses() -> tuple[list, int]:
    """(entries, broken_count): {amount, note, date} dicts parsed
    from the expenses facts; unparseable ones are counted."""
    entries = []
    broken = 0
    for fact in _facts(_EXPENSES_CATEGORY):
        try:
            stored = json.loads(fact.get("value") or "")
            amount = float(stored["amount"])
            day = str(stored["date"])
            datetime.date.fromisoformat(day)
            entries.append({
                "amount": amount,
                "note": str(stored.get("note") or ""),
                "date": day,
            })
        except Exception:  # noqa: BLE001
            broken += 1
    entries.sort(key=lambda e: (e["date"], e["note"]))
    return entries, broken


def _month_label(month: str) -> str:
    """'October 2026' for a 'YYYY-MM' string."""
    try:
        parsed = datetime.date.fromisoformat(month + "-01")
    except ValueError:
        return month
    return f"{parsed:%B %Y}"


def expense_log(action: str, amount=0, note: str = "",
                month: str = "") -> str:
    """Log an expense, list a month's entries, or total a month."""
    action = str(action or "").strip().lower()
    if action not in ("add", "list", "total"):
        return ("Expense actions are 'add', 'list' and 'total' — "
                "e.g. expense_log with action 'add', an amount "
                "and a note like 'train tickets'.")
    if action == "add":
        value = _coerce_float(amount)
        if value is None:
            return ("An expense needs a numeric amount — e.g. "
                    "250 or 1299.50. Nothing was logged.")
        if value <= 0:
            return ("An expense amount must be more than zero. "
                    "Nothing was logged.")
        clean_note = " ".join(str(note or "").split())
        if len(clean_note) > _MAX_NOTE_CHARS:
            return (f"That note is {len(clean_note)} characters — "
                    f"expense notes are capped at "
                    f"{_MAX_NOTE_CHARS}. Shorten it and I'll log "
                    "the expense.")
        today = datetime.date.today().isoformat()
        slug = clean_note.lower() or "expense"
        key = f"{today} {slug}"
        try:
            suffix = 1
            candidate = key
            while _store().get_fact(_EXPENSES_CATEGORY,
                                    candidate) is not None:
                suffix += 1
                candidate = f"{key} ({suffix})"
            _store().save_fact(
                _EXPENSES_CATEGORY, candidate,
                json.dumps({"amount": value, "note": clean_note,
                            "date": today}))
        except Exception as e:  # noqa: BLE001
            return f"Couldn't save the expense: {e}"
        label = f" '{clean_note}'" if clean_note else ""
        return (f"Logged an expense of {_fmt_amount(value)}"
                f"{label} for today ({today}).")
    # list / total share the month resolution.
    month_text = str(month or "").strip()
    if month_text:
        if not _MONTH_RE.match(month_text):
            return ("The month must look like YYYY-MM — e.g. "
                    "'2026-10'. Leave it blank for this month.")
        wanted = month_text
    else:
        wanted = datetime.date.today().strftime("%Y-%m")
    try:
        entries, broken = _load_expenses()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the expense log: {e}"
    mine = [e for e in entries if e["date"].startswith(wanted)]
    broken_note = ""
    if broken:
        broken_note = (f" ({broken} stored expense"
                       f"{'s' if broken != 1 else ''} couldn't be "
                       "read and were skipped.)")
    total = sum(e["amount"] for e in mine)
    if action == "total":
        if not mine:
            return (f"No expenses logged for "
                    f"{_month_label(wanted)} yet.{broken_note}")
        return (f"Expenses for {_month_label(wanted)} add up to "
                f"{_fmt_amount(total)} across {len(mine)} "
                f"entr{'ies' if len(mine) != 1 else 'y'}."
                f"{broken_note}")
    if not mine:
        return (f"No expenses logged for {_month_label(wanted)} "
                f"yet. Add one with expense_log, action 'add', "
                f"an amount and a note.{broken_note}")
    lines = [f"Expenses for {_month_label(wanted)} "
             f"({len(mine)}):"]
    for entry in mine:
        line = (f"• {entry['date']} — "
                f"{_fmt_amount(entry['amount'])}")
        if entry["note"]:
            line += f" — {entry['note']}"
        lines.append(line)
    lines.append(f"Total: {_fmt_amount(total)}.")
    if broken:
        lines.append(broken_note.strip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DECISION LOG — decisions with revisit dates, due ones lead
# ---------------------------------------------------------------------------

_DECISIONS_CATEGORY = "decisions"
_MAX_DECISION_CHARS = 200


def _load_decisions() -> tuple[list, int]:
    """(entries, broken_count): {text, revisit, added} dicts
    parsed from the decisions facts."""
    entries = []
    broken = 0
    for fact in _facts(_DECISIONS_CATEGORY):
        try:
            stored = json.loads(fact.get("value") or "")
            text = str(stored["text"]).strip()
            if not text:
                raise ValueError
            revisit = str(stored.get("revisit") or "")
            if revisit:
                datetime.date.fromisoformat(revisit)
            entries.append({
                "text": text,
                "revisit": revisit,
                "added": str(stored.get("added") or ""),
            })
        except Exception:  # noqa: BLE001
            broken += 1
    return entries, broken


def decision_log(action: str, text: str = "",
                 revisit: str = "") -> str:
    """Record a decision (optionally with a revisit date) or list
    the log, due-for-review decisions first."""
    action = str(action or "").strip().lower()
    if action not in ("add", "list"):
        return ("Decision actions are 'add' and 'list' — e.g. "
                "decision_log with action 'add', the decision as "
                "text, and optionally a revisit date.")
    if action == "add":
        clean = " ".join(str(text or "").split())
        if not clean:
            return "A decision log entry needs the decision itself."
        if len(clean) > _MAX_DECISION_CHARS:
            return (f"That decision is {len(clean)} characters — "
                    f"entries are capped at {_MAX_DECISION_CHARS}. "
                    "Summarise it and I'll record it.")
        revisit_text = str(revisit or "").strip()
        revisit_day = None
        if revisit_text:
            try:
                revisit_day = datetime.date.fromisoformat(
                    revisit_text)
            except ValueError:
                return ("The revisit date must look like "
                        "YYYY-MM-DD — e.g. '2026-12-01'. Nothing "
                        "was recorded.")
        payload = {"text": clean,
                   "revisit": revisit_day.isoformat()
                   if revisit_day else "",
                   "added": datetime.date.today().isoformat()}
        key = clean.lower()
        try:
            existed = _store().get_fact(
                _DECISIONS_CATEGORY, key) is not None
            _store().save_fact(_DECISIONS_CATEGORY, key,
                               json.dumps(payload))
        except Exception as e:  # noqa: BLE001
            return f"Couldn't save the decision: {e}"
        result = (f"{'Updated' if existed else 'Recorded'} the "
                  f"decision: '{clean}'.")
        if revisit_day:
            result += (f" I'll flag it for a second look on "
                       f"{revisit_day.isoformat()}.")
        return result
    # action == "list"
    try:
        entries, broken = _load_decisions()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the decision log: {e}"
    if not entries:
        base = ("No decisions recorded yet. Add one with "
                "decision_log, action 'add' and the decision as "
                "text — a revisit date is optional.")
        if broken:
            base += (f" ({broken} stored decision"
                     f"{'s' if broken != 1 else ''} couldn't be "
                     "read and were skipped.)")
        return base
    today = datetime.date.today()
    due, upcoming, undated = [], [], []
    for entry in entries:
        if not entry["revisit"]:
            undated.append(entry)
        elif datetime.date.fromisoformat(
                entry["revisit"]) <= today:
            due.append(entry)
        else:
            upcoming.append(entry)
    due.sort(key=lambda e: e["revisit"])
    upcoming.sort(key=lambda e: e["revisit"])
    lines = [f"Decision log ({len(entries)}):"]
    if due:
        lines.append("Due for a second look:")
        for entry in due:
            lines.append(f"• {entry['text']} (revisit date "
                         f"{entry['revisit']})")
    if upcoming:
        lines.append("Coming up:")
        for entry in upcoming:
            lines.append(f"• {entry['text']} (revisit on "
                         f"{entry['revisit']})")
    if undated:
        lines.append("No revisit date set:")
        for entry in undated:
            lines.append(f"• {entry['text']}")
    if broken:
        lines.append(f"({broken} stored decision"
                     f"{'s' if broken != 1 else ''} couldn't be "
                     "read and were skipped.)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# READING LIST — to-read vs finished, as memory facts
# ---------------------------------------------------------------------------

_READING_CATEGORY = "reading"
_MAX_TITLE_CHARS = 120


def _load_reading() -> tuple[list, int]:
    """(entries, broken_count): {title, author, status, added,
    finished} dicts parsed from the reading facts."""
    entries = []
    broken = 0
    for fact in _facts(_READING_CATEGORY):
        try:
            stored = json.loads(fact.get("value") or "")
            title = str(stored["title"]).strip()
            if not title:
                raise ValueError
            status = str(stored.get("status") or "to_read")
            if status not in ("to_read", "read"):
                status = "to_read"
            entries.append({
                "title": title,
                "author": str(stored.get("author") or ""),
                "status": status,
                "added": str(stored.get("added") or ""),
                "finished": str(stored.get("finished") or ""),
            })
        except Exception:  # noqa: BLE001
            broken += 1
    entries.sort(key=lambda e: (e["status"] != "to_read",
                                e["title"].lower()))
    return entries, broken


def _reading_line(entry: dict) -> str:
    line = f"• {entry['title']}"
    if entry["author"]:
        line += f" — {entry['author']}"
    return line


def reading_list(action: str = "list", title: str = "",
                 author: str = "") -> str:
    """Add a book to the reading list, list what's queued and
    finished, or mark a book read."""
    action = str(action or "list").strip().lower()
    if action not in ("add", "list", "mark_read"):
        return ("Reading list actions are 'add', 'list' and "
                "'mark_read'.")
    if action == "list":
        try:
            entries, broken = _load_reading()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the reading list: {e}"
        if not entries:
            base = ("Your reading list is empty. Add a book "
                    "with reading_list, action 'add' and a "
                    "title.")
            if broken:
                base += (f" ({broken} stored entr"
                         f"{'ies' if broken != 1 else 'y'} "
                         "couldn't be read and were skipped.)")
            return base
        queued = [e for e in entries if e["status"] == "to_read"]
        finished = [e for e in entries if e["status"] == "read"]
        lines = [f"Reading list ({len(entries)}):"]
        if queued:
            lines.append("To read:")
            lines += [_reading_line(e) for e in queued]
        if finished:
            lines.append("Finished:")
            for entry in finished:
                line = _reading_line(entry)
                if entry["finished"]:
                    line += f" (finished {entry['finished']})"
                lines.append(line)
        if broken:
            lines.append(f"({broken} stored entr"
                         f"{'ies' if broken != 1 else 'y'} couldn't "
                         "be read and were skipped.)")
        return "\n".join(lines)
    clean = " ".join(str(title or "").split())
    if not clean:
        return "That needs the book's title."
    if len(clean) > _MAX_TITLE_CHARS:
        return (f"That title is {len(clean)} characters — "
                f"titles are capped at {_MAX_TITLE_CHARS}.")
    key = clean.lower()
    try:
        existing = None
        for entry in _load_reading()[0]:
            if entry["title"].lower() == key:
                existing = entry
                break
        if action == "mark_read":
            if existing is None:
                entries, _broken = _load_reading()
                known = (", ".join(e["title"] for e in entries)
                         if entries else "")
                extra = (f" On the list: {known}." if known
                         else " The list is empty.")
                return (f"'{clean}' isn't on your reading list."
                        f"{extra}")
            if existing["status"] == "read":
                return (f"'{existing['title']}' is already "
                        "marked as finished.")
            payload = {"title": existing["title"],
                       "author": existing["author"],
                       "status": "read",
                       "added": existing["added"],
                       "finished": datetime.date.today().isoformat()}
            _store().save_fact(_READING_CATEGORY,
                               existing["title"].lower(),
                               json.dumps(payload))
            return (f"Marked '{existing['title']}' as finished. "
                    "Nice one.")
        # action == "add"
        if existing is not None:
            if existing["status"] == "read":
                return (f"'{existing['title']}' is already on "
                        "the list — and already marked finished.")
            return (f"'{existing['title']}' is already on your "
                    "reading list.")
        payload = {"title": clean,
                   "author": " ".join(str(author or "").split()),
                   "status": "to_read",
                   "added": datetime.date.today().isoformat(),
                   "finished": ""}
        _store().save_fact(_READING_CATEGORY, key,
                           json.dumps(payload))
    except Exception as e:  # noqa: BLE001
        return f"Couldn't update the reading list: {e}"
    by = f" by {payload['author']}" if payload["author"] else ""
    return (f"Added '{clean}'{by} to your reading list.")


# ---------------------------------------------------------------------------
# WHAT'S PENDING — reminders + stopwatch + Pomodoro in one view
# ---------------------------------------------------------------------------


def whats_pending(days=None) -> str:
    """One combined view of what's live right now: scheduled
    reminders, the stopwatch and the Pomodoro — each section
    reporting only what its source actually holds.

    `days` (optional) windows the reminders section to reminders
    due within that many days from now — overdue ones and ones
    whose fire time can't be read stay in view (they're waiting
    now), and the result says how many more wait beyond the
    window. Without it, every waiting reminder is counted,
    exactly as before."""
    horizon = None
    if days is not None and str(days).strip() != "":
        horizon = _coerce_float(days)
        if horizon is None or horizon < 0:
            return ("The horizon needs to be a number of days, "
                    "zero or more — e.g. whats_pending with "
                    "days 7 — or leave it out to see everything "
                    "waiting.")
    sections = []
    # Reminders (the Pomodoro rides the same list; its records get
    # their own section below so nothing is listed twice).
    try:
        records = _reminders()
        plain = []
        for record in records:
            if record.get("fired"):
                continue
            if str(record.get("label", "")).startswith(
                    _POMODORO_PREFIX):
                continue
            plain.append(record)
        plain.sort(key=lambda r: str(r.get("fire_at", "")))
        beyond = 0
        window = ""
        if horizon is not None:
            now = datetime.datetime.now()
            cutoff = now + datetime.timedelta(days=horizon)
            window = (f"{horizon:g} day"
                      f"{'s' if horizon != 1 else ''}")
            within = []
            for record in plain:
                try:
                    fire = datetime.datetime.strptime(
                        str(record.get("fire_at", "")),
                        "%Y-%m-%d %H:%M")
                except ValueError:
                    within.append(record)
                    continue
                if fire <= cutoff:
                    within.append(record)
                else:
                    beyond += 1
            plain = within
        if plain:
            now = datetime.datetime.now()
            shown = plain[:5]
            if horizon is None:
                lines = [f"Reminders: {len(plain)} waiting."]
            else:
                lines = [f"Reminders: {len(plain)} waiting in "
                         f"the next {window}."]
            for record in shown:
                message = (str(record.get("message") or "").strip()
                           or str(record.get("label") or "Reminder"))
                when_text = str(record.get("fire_at", ""))
                try:
                    fire = datetime.datetime.strptime(
                        when_text, "%Y-%m-%d %H:%M")
                    when_text = _when_words(fire, now)
                except ValueError:
                    pass
                lines.append(f"• '{message}' — {when_text}")
            if len(plain) > len(shown):
                lines.append(f"(and {len(plain) - len(shown)} "
                             "more)")
            if beyond:
                lines.append(f"({beyond} more waiting beyond "
                             "that window.)")
            sections.append("\n".join(lines))
        elif horizon is not None:
            line = (f"Reminders: none waiting in the next "
                    f"{window}.")
            if beyond:
                line += (f"\n({beyond} more waiting beyond that "
                         "window.)")
            sections.append(line)
        else:
            sections.append("Reminders: none waiting.")
    except Exception as e:  # noqa: BLE001
        sections.append(f"Reminders: couldn't be read ({e}).")
    # Stopwatch.
    try:
        state = _stopwatch_state()
        if state is None:
            sections.append("Stopwatch: not started — no "
                            "stopwatch state is saved.")
        else:
            banked = float(state.get("banked", 0.0))
            laps = state.get("laps") or []
            if state.get("running") and \
                    state.get("started_at") is not None:
                elapsed = banked + (time.time()
                                    - float(state["started_at"]))
                line = (f"Stopwatch: running — "
                        f"{_fmt_duration(elapsed)} so far")
            else:
                line = (f"Stopwatch: stopped at "
                        f"{_fmt_duration(banked)}")
            if laps:
                line += (f", {len(laps)} lap"
                         f"{'s' if len(laps) != 1 else ''} "
                         "recorded")
            sections.append(line + ".")
    except Exception as e:  # noqa: BLE001
        sections.append(f"Stopwatch: couldn't be read ({e}).")
    # Pomodoro.
    try:
        pomo = _pomodoro_state()
        pending = pomo["pending"]
        fired = pomo["fired_today"]
        if not pending and not fired:
            sections.append("Pomodoro: nothing running or "
                            "scheduled.")
        else:
            bits = []
            if fired:
                bits.append(f"{fired} reminder"
                            f"{'s' if fired != 1 else ''} already "
                            "fired today")
            if pending:
                nxt = pending[0]
                bits.append(f"{len(pending)} still to come — "
                            f"next is '{nxt.get('label', '')}' at "
                            f"{str(nxt.get('fire_at', ''))[-5:]}")
            sections.append("Pomodoro: " + "; ".join(bits) + ".")
    except Exception as e:  # noqa: BLE001
        sections.append(f"Pomodoro: couldn't be read ({e}).")
    sections.append("Reminders and the Pomodoro live in "
                    "VYUK's memory only — closing the app "
                    "clears them; the stopwatch's state is saved "
                    "on disk.")
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# MEETING COST — attendees × time, priced when a rate is given
# ---------------------------------------------------------------------------


def meeting_cost(attendees, minutes, hourly_rate=0) -> str:
    """The person-time a meeting uses, and its cost at a rate."""
    people = _coerce_float(attendees)
    if people is None or people < 1 or people != int(people):
        return ("A meeting needs a whole number of attendees, "
                "at least 1.")
    length = _coerce_float(minutes)
    if length is None or length <= 0:
        return "A meeting needs a length in minutes, more than zero."
    rate = _coerce_float(hourly_rate)
    if rate is None or rate < 0:
        return ("The hourly rate must be zero or more — leave it "
                "at zero to skip pricing.")
    people = int(people)
    attendee_minutes = people * length
    person_hours = attendee_minutes / 60.0
    base = (f"A {length:g}-minute meeting with {people} "
            f"attendee{'s' if people != 1 else ''} uses "
            f"{_fmt_amount(attendee_minutes)} attendee-minutes — "
            f"{_fmt_hours(person_hours)} person-hour"
            f"{'s' if person_hours != 1 else ''} of people's time.")
    if rate and rate > 0:
        cost = person_hours * rate
        return (base + f" At {_fmt_amount(rate)} per person-hour, "
                f"that's a cost of {_fmt_amount(cost)}.")
    return (base + " Give me an hourly rate per person and I'll "
            "put a price on it.")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
