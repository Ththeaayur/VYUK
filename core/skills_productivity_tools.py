"""
Productivity II skills pack for VYUK (v5.21, HANDS) — the
timer-and-calculator shelf the first productivity pack never
stocked: a Pomodoro timer, a stopwatch with laps, a plain
countdown, time-zone conversion, date arithmetic, a password
strength analyst, and a tiny JSON path query.

Pomodoro and countdown ride the app's ProactiveScheduler
(core/scheduler.py) — the same in-memory reminder list the window
announces when reminders fire. That means they are honest about
persistence: scheduled reminders live in RAM and are lost if
VYUK closes, and the results say so. The stopwatch keeps its
own tiny state file under the app data dir (started-at epoch, banked
seconds, laps) so its arithmetic is computed from timestamps, not
from a ticking counter that drifts.

password_strength is an ANALYST, never a vault: it scores the
password it is handed and forgets it. The password is never stored,
never logged, and never echoed back in the result text — not even
in an error message.

Same rules as the packs before it: plain functions returning
human-readable strings, never raising; anything not stdlib is
imported lazily and guarded. Wiring: the parent (core/funnel/tools_registry.py) calls
register_all(registry) — this module never touches the registry
singleton itself.
"""

# INTENTIONAL: re-exports of the old module's import bindings, so
# the facade's namespace matches the old module's exactly
# (dir-diff-empty bar, §2 P2).
import calendar
import datetime
import json
import math
import re
import time
from pathlib import Path

from .config import DATA_DIR
from .scheduler import scheduler

# INTENTIONAL: re-exports of every name moved into the impl modules.
from .skills_productivity2_helpers import (
    _now,
    _fmt_duration,
    _fmt_clock,
    _parse_iso_date,
)
from .skills_productivity2_timers import (
    _POMODORO_PREFIX,
    pomodoro,
    _STOPWATCH_FILE,
    _STOPWATCH_ACTIONS,
    _stopwatch_path,
    _load_stopwatch,
    _save_stopwatch,
    _elapsed,
    stopwatch,
    countdown,
)
from .skills_productivity2_convert_query import (
    _load_zone,
    _offset_text,
    timezone_convert,
    _DATE_WORDS,
    _OP_ALIASES,
    _parse_date_flexible,
    _add_months,
    _humanise_span,
    _weekdays_between,
    _diff_text,
    _add_business_days,
    _age_text,
    date_calculator,
    _COMMON_PASSWORDS,
    _KEYBOARD_WALKS,
    _GUESSES_OFFLINE,
    _GUESSES_ONLINE,
    _humanize_seconds,
    _password_penalties,
    password_strength,
    _JSON_RENDER_CAP,
    json_query,
)


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Productivity II pack tool on a ToolRegistry-like object."""
    registry.register(
        name="pomodoro",
        description="Run a Pomodoro focus timer on VYUK's reminder list: schedules an announcement when each focus round ends ('Pomodoro done — take a break') and, between rounds, when each break ends, for as many cycles as asked. action='status' instead reports the Pomodoro reminders still to come. IMPORTANT: the scheduler's reminders are in-memory only — closing VYUK cancels the Pomodoro, and the result says so. Use when the user wants focused work sprints with timed breaks. Not a plain timer: for a single countdown use countdown, to measure elapsed time use stopwatch, for a spoken reminder use set_reminder.",
        parameters={
            "type": "object",
            "properties": {
                "minutes": {"type": "number", "description": "Focus round length in minutes, 1-240 (default 25)"},
                "break_minutes": {"type": "number", "description": "Break length in minutes, 0-120 (default 5)"},
                "cycles": {"type": "integer", "description": "Number of focus rounds, 1-12 (default 1)"},
                "action": {"type": "string", "enum": ["start", "status"], "description": "'start' (default) schedules a Pomodoro; 'status' reports the rounds still to come"},
            },
            "required": [],
        },
        handler=pomodoro,
    )
    registry.register(
        name="stopwatch",
        description="A stopwatch with laps, driven by timestamps (its state — started-at epoch, banked seconds, lap splits — is kept in a small file in the app data dir, so it survives across calls). action is one of: start (begins, or resumes a stopped stopwatch keeping its laps), lap (records a split and reports it), stop (banks the time and reports the total plus every split), status (reports elapsed and running state, including every lap split so far), reset (clears everything). Times are narrated in plain words ('3 minutes 12 seconds, lap 2 was 58 seconds'). It measures elapsed time and announces nothing at a target — for an alert after N minutes use countdown, for a spoken reminder use set_reminder.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "lap", "stop", "status", "reset"], "description": "What to do with the stopwatch"},
            },
            "required": ["action"],
        },
        handler=stopwatch,
    )
    registry.register(
        name="countdown",
        description="Set a simple countdown: after the given number of minutes (fractions allowed, up to 1440 = 24 hours) VYUK announces 'Countdown finished' through her reminder list, with the label included when one is given ('Countdown finished — pasta.'). Reports when it will fire. In-memory only like all reminders — closing VYUK cancels it, and the result says so. Use for 'remind me in 10 minutes' style asks that aren't calendar events. A plain one-shot timer: for work/break cycles use pomodoro, to measure elapsed time use stopwatch.",
        parameters={
            "type": "object",
            "properties": {
                "minutes": {"type": "number", "description": "Minutes until the countdown finishes (above 0, up to 1440; fractions allowed)"},
                "label": {"type": "string", "description": "Optional label announced with the finish, e.g. 'pasta'"},
            },
            "required": ["minutes"],
        },
        handler=countdown,
    )
    registry.register(
        name="timezone_convert",
        description="Convert a time between two IANA time zones: give 'HH:MM' (interpreted on today's date in the source zone) or a full ISO datetime, plus the source and destination zones ('Asia/Kolkata', 'America/New_York', 'UTC', ...). Narrates both sides with dates, UTC offsets, and how far ahead or behind the destination is. An unknown zone is reported plainly with examples, never a crash. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "time_text": {"type": "string", "description": "'HH:MM' or an ISO datetime, e.g. '18:00' or '2026-10-21T18:00'"},
                "from_zone": {"type": "string", "description": "Source IANA time zone, e.g. 'Asia/Kolkata'"},
                "to_zone": {"type": "string", "description": "Destination IANA time zone, e.g. 'UTC'"},
            },
            "required": ["time_text", "from_zone", "to_zone"],
        },
        handler=timezone_convert,
    )
    registry.register(
        name="date_calculator",
        description="Do date arithmetic. operation 'days_between' counts the days from date_a to date_b — longer spans also get week, weekday and calendar-month views, and the direction is stated plainly; 'add_days' adds the (possibly negative) days count to date_a and gives the resulting date and its weekday; 'add_business_days' adds only Mon–Fri days to date_a (Saturdays and Sundays skipped, a negative count walks backwards); 'age' treats date_a as a birth date and gives the exact years, months and days from it to today (a future date is answered plainly: it hasn't happened yet); 'weekday' just names the day of the week of date_a. Dates are ISO (2026-10-21) or the words today / tomorrow / yesterday. The words 'diff' and 'add' are accepted for days_between / add_days, and 'action' may be passed instead of 'operation' (this tool absorbed the retired date_math in v5.30 — one date tool, both vocabularies). Bad dates and unknown operations are reported plainly. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["days_between", "add_days", "add_business_days", "age", "weekday"], "description": "Which calculation to run ('diff' and 'add' also accepted)"},
                "action": {"type": "string", "description": "Alias for operation — the retired date_math tool's parameter name"},
                "date_a": {"type": "string", "description": "The first (or only) date: ISO 'YYYY-MM-DD', or today / tomorrow / yesterday"},
                "date_b": {"type": "string", "description": "The second date, for days_between"},
                "days": {"type": "integer", "description": "Days to add, for add_days and add_business_days (may be negative)"},
            },
            "required": ["date_a"],
        },
        handler=date_calculator,
    )
    registry.register(
        name="password_strength",
        description="Analyse a password's strength and report a verdict: an entropy estimate from its length and character pool, minus pattern penalties (common-password list, sequences like 'abc'/'321', repeated characters or chunks, keyboard walks, digits-only), and the average crack time at offline (10 billion guesses/second) and online (10 thousand/second) rates. ANALYSIS ONLY: the password is never stored, logged, sent anywhere, or echoed back in the result. Use when the user asks 'is this password strong?' — never call it unprompted on a password the user hasn't offered for checking.",
        parameters={
            "type": "object",
            "properties": {
                "password": {"type": "string", "description": "The password to score (analysed in place, never kept or repeated back)"},
            },
            "required": ["password"],
        },
        handler=password_strength,
    )
    registry.register(
        name="json_query",
        description="Read one value out of JSON by a tiny dotted path: 'a.b.0.c' walks objects by key and arrays by numeric index, and a query ending in '.keys' lists an object's keys or '.length' gives an array/object/string length (a bare 'keys'/'length' applies to the whole document; an empty query returns the whole document). The source is a JSON file's path when that file exists, otherwise the source text itself parsed as JSON. Values come back JSON-rendered (truncated past 1000 characters, disclosed). A missing key names the keys that DO exist there. Results lead with the verdict (found, or what is actually there). For working with pasted JSON data or JSON files. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Path of a JSON file, or raw JSON text"},
                "query": {"type": "string", "description": "Dotted path like 'a.b.0.c', optionally ending in '.keys' or '.length' (default: the whole document)"},
            },
            "required": ["source"],
        },
        handler=json_query,
    )
