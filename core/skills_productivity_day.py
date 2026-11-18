"""
Productivity III skills pack for VYUK — the day-awareness
shelf: a focus report, a meeting prep card, a deadline radar, a
habit streak log, water and stand-up nudges on the scheduler, two
inbox-triage readers, a bill calendar, a spoken shopping list, a
weather-aware packing list and a birthday radar.

Where each tool's truth lives — and what it honestly can't know:

- The Pomodoro keeps NO history: its rounds are reminders on the
  in-memory ProactiveScheduler (core/scheduler.py), gone when
  VYUK closes. focus_timer_report reads that live list,
  counts today's fired focus rounds, and reconstructs minutes from
  the scheduled gaps — labelled an estimate, never a measurement.
- Calendar and mail tools ride core/google_services.py and return
  its own not-connected / sign-in-expired sentences verbatim when
  Google isn't available. Event titles, senders and subjects are
  external content: they are shown inside the injection envelope
  (core/injection.py), exactly like the sibling mail/calendar
  tools, with this pack's own framing outside it.
- Plans (core/brain/planner.py) carry no due times, so deadline_radar
  ranks only calendar events and reminders, and says plans can't
  be ranked instead of guessing.
- Habits, bills and the shopping list persist as memory facts
  (core/memory/memory.py) — small JSON payloads under fixed categories —
  so they survive restarts and show up in the user's memory like
  anything else VYUK remembers.
- Water/stand-up nudges are chains of one-shot scheduler
  reminders covering the next ~8 hours: the scheduler's only
  repeat mode is daily, and the results say the chain is finite
  and in-memory. The nudges cannot pause themselves during calls
  or fullscreen apps — the watchers' fullscreen probe runs on its
  own cadence and a fired reminder can't consult it — so
  stand_up_nudge states that limit instead of implying otherwise.

Same rules as the packs before it: plain functions returning
human-readable strings, never raising; external calls sit behind
module-level helpers so tests can monkeypatch them; stdlib only.
Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

# Phase A batch A29 part 2 (skills split): the life-admin family
# moved verbatim to skills_productivity3_life_admin (the focus-day
# family moved to skills_productivity3_focus_day in part 1, and
# the shared _coerce_int to skills_productivity3_helpers). This
# file is now a pure facade: register_all verbatim plus
# re-exports with identity, so existing importers keep working.
import datetime
import json
import os
import re
import urllib.parse
import urllib.request
from email.utils import parseaddr
from pathlib import Path

# INTENTIONAL (split A29 part 2): the core imports below are
# re-exports, not live uses in this file — the original module's
# namespace carried these names (leaked imports), and the facade
# must keep them bound so the dir() diff vs the original is
# empty in both directions (plan P2).
from . import google_services, injection
from .brain import planner  # Phase 3 B2-remainder: planner moved to core/brain/
from .memory.memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
from .scheduler import scheduler

# Phase A batch A29 part 1 (skills split): the focus-day family
# moved verbatim to skills_productivity3_focus_day, and the shared
# _coerce_int to skills_productivity3_helpers. The names below are
# re-exports with identity, so existing importers keep working.
# INTENTIONAL: _coerce_int is imported from helpers (not redefined)
# so the facade namespace still carries it and the focus-day and
# life-admin modules import the same object (plan P3(c)).
from .skills_productivity3_focus_day import (_coerce_bool, _when_words, _until_words, _reminders, _upcoming_events, _calendar_service, _fetch_events, _open_plan_steps, _FILE_SCAN_ROOTS, _FILE_SCAN_CAP, _TITLE_STOPWORDS, _recent_files_matching, _POMODORO_LABEL_RE, focus_timer_report, _next_event_detail, _event_when_text, _event_link, meeting_prep_card, deadline_radar, _HABITS_CATEGORY, _MAX_HABIT_CHARS, _load_habit, _streak_ending, habit_checkin, _CHAIN_HOURS, _WATER_PREFIX, _WATER_MESSAGE, _STAND_PREFIX, _STAND_MESSAGE, _chain_pending, _chain_cancel, _chain_start, _chain_snooze, _chain_status, _MEMORY_NOTE, water_break, stand_up_nudge)
from .skills_productivity3_helpers import _coerce_int

# INTENTIONAL (split A29 part 2): life-admin names are
# re-exports with identity from skills_productivity3_life_admin,
# so existing importers keep working and register_all below
# resolves its handlers by bare name.
from .skills_productivity3_life_admin import (
    _ordinal,
    _all_facts,
    _gmail_service,
    _mail_headers,
    _WMO_CODES,
    _RAIN_CODES,
    _SNOW_CODES,
    _weather_now,
    _BULK_SENDERS,
    _MARKETING_WORDS,
    _REPLY_PHRASES,
    _bucket_of,
    _UNSUB_URL_RE,
    _UNSUB_MAILTO_RE,
    _unsub_mechanism,
    _BILLS_CATEGORY,
    _load_bills,
    _LISTS_CATEGORY,
    _SHOPPING_KEY,
    _MAX_SHOPPING_ITEMS,
    _MAX_ITEM_CHARS,
    _load_shopping,
    _save_shopping,
    _MONTH_NAMES,
    _BIRTHDAY_WINDOW_DAYS,
    _parse_birthday,
    _birthday_name,
    inbox_zero_assist,
    unsubscribe_finder,
    bill_calendar,
    shopping_list,
    packing_list,
    birthday_radar,
)


def register_all(registry) -> None:
    """Register every Productivity-3 pack tool on a
    ToolRegistry-like object."""
    registry.register(
        name="focus_timer_report",
        description="Summarise today's focus work: how many Pomodoro rounds finished, about how many minutes they add up to, and what's still scheduled. The Pomodoro keeps no history file — it lives in VYUK's reminder list — so the report covers today only, only while the app stays open, and the minutes are reconstructed from the schedule and labelled an estimate. Use when the user asks how much they focused today.",
        parameters={"type": "object", "properties": {},
                    "required": []},
        handler=focus_timer_report,
    )
    registry.register(
        name="meeting_prep_card",
        description="Build a prep card for the next calendar event: its title, when it runs, who's attending, the location and join link, plus files modified in the last day whose names match the meeting's title. Needs Google connected — otherwise it returns the connect-Google line. Use when the user asks what's next or wants to get ready for a meeting.",
        parameters={"type": "object", "properties": {},
                    "required": []},
        handler=meeting_prep_card,
    )
    registry.register(
        name="deadline_radar",
        description="Name the single most urgent dated thing due in the next 48 hours, scanning the calendar and the reminder list, and say when it's due. Plans are checked too, but they carry no due times, so the result notes they can't be ranked instead of guessing. When Google isn't connected the calendar can't be checked and the result says so. Use for 'what's my most urgent deadline?'. Pass hours to scan a different window (default 48, clamped 1–720).",
        parameters={
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "description": "How far ahead to scan, in hours (default 48, clamped 1–720)"},
            },
            "required": [],
        },
        handler=deadline_radar,
    )
    registry.register(
        name="habit_checkin",
        description="Log today's check-in for a habit and report the streak: pass done=true on a day the habit happened, done=false for a miss. The log is stored as a memory fact, the streak counts consecutive done days ending today, and a miss that ends a streak says how long it was. Use when the user reports doing (or skipping) a daily habit.",
        parameters={
            "type": "object",
            "properties": {
                "habit": {"type": "string", "description": "The habit's name, e.g. 'exercise' or 'read 20 pages'"},
                "done": {"type": "boolean", "description": "True (default) when the habit was done today, false for a miss"},
            },
            "required": ["habit"],
        },
        handler=habit_checkin,
    )
    registry.register(
        name="water_break",
        description="Gentle recurring hydration reminders on VYUK's scheduler: 'start' schedules a nudge every interval_minutes (default 30) for the next 8 hours, 'snooze' pushes the next one back by snooze_minutes, 'stop' cancels them, 'status' reports what's left. They live in the app's memory only — closing VYUK stops them — and every result says so. Use when the user wants water or hydration reminders.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "stop", "status", "snooze"], "description": "'start', 'stop', 'status' (default) or 'snooze'"},
                "interval_minutes": {"type": "integer", "description": "Minutes between nudges when starting (default 30, clamped 5–240)"},
                "snooze_minutes": {"type": "integer", "description": "Minutes to push the next nudge back when snoozing (default 10)"},
            },
            "required": [],
        },
        handler=water_break,
    )
    registry.register(
        name="stand_up_nudge",
        description="Hourly movement nudges on VYUK's scheduler: 'start' schedules a stand-up nudge every interval_minutes (default 60) for the next 8 hours, 'stop' cancels, 'status' reports. In-memory only, and the nudges can't yet pause themselves during calls or fullscreen apps — the start result states that limit plainly. Use when the user wants reminders to stand up and move.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "stop", "status"], "description": "'start', 'stop' or 'status' (default)"},
                "interval_minutes": {"type": "integer", "description": "Minutes between nudges when starting (default 60, clamped 10–240)"},
            },
            "required": [],
        },
        handler=stand_up_nudge,
    )
    registry.register(
        name="inbox_zero_assist",
        description="Give the unread inbox a rough first sort into three spoken buckets — needs a reply, for your information, likely noise — by sender and subject patterns, with counts and a couple of examples per bucket. It's a heuristic triage, labelled as such, and changes nothing in the mailbox. Needs Google connected. Use when the user wants help facing a pile of unread mail.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many unread emails to sort (default 20, clamped 1–50)"},
            },
            "required": [],
        },
        handler=inbox_zero_assist,
    )
    registry.register(
        name="unsubscribe_finder",
        description="Scan recent inbox mail for List-Unsubscribe headers and list each sender you could unsubscribe from, with the mechanism (a one-click link or an unsubscribe email address). Read-only: it never unsubscribes anyone — it only lists where the exits are. Needs Google connected. Use when the user wants to cut down junk mail.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many recent inbox emails to scan (default 25, clamped 1–50)"},
            },
            "required": [],
        },
        handler=unsubscribe_finder,
    )
    registry.register(
        name="bill_calendar",
        description="Keep the recurring monthly bills: 'add' stores a named expense with an amount and the day of the month it lands, 'list' shows every bill sorted by day with the monthly total, 'remove' deletes one by name. Bills are stored as memory facts, so they survive restarts. Use when the user talks about rent, subscriptions or other monthly expenses.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "list", "remove"], "description": "'add', 'list' or 'remove'"},
                "name": {"type": "string", "description": "The bill's name, e.g. 'rent' (needed for add and remove)"},
                "amount": {"type": "number", "description": "The monthly amount (needed for add)"},
                "day_of_month": {"type": "integer", "description": "The day of the month it lands, 1–31 (for add; default 1)"},
            },
            "required": ["action"],
        },
        handler=bill_calendar,
    )
    registry.register(
        name="shopping_list",
        description="Work with the persistent spoken shopping list: 'add' puts an item on it, 'remove' takes one off, 'read' (the default) reads the whole list back numbered, 'clear' empties it. The list is stored as a memory fact, so it survives restarts and duplicates are refused politely. Use whenever the user mentions the shopping list.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "remove", "read", "clear"], "description": "'add', 'remove', 'read' (default) or 'clear'"},
                "item": {"type": "string", "description": "The item to add or remove, e.g. 'oat milk'"},
            },
            "required": [],
        },
        handler=shopping_list,
    )
    registry.register(
        name="packing_list",
        description="Generate a trip packing list for a destination and trip length: clothing counts scale with the days, and the list is tuned to the live weather there right now (jacket and layers when cold, umbrella when it's raining, light fabrics when hot). When the weather can't be fetched the list says so and stays general. Use when the user is packing for a trip.",
        parameters={
            "type": "object",
            "properties": {
                "destination": {"type": "string", "description": "Where they're going, e.g. 'Goa' or 'London'"},
                "days": {"type": "integer", "description": "Trip length in days (default 3, clamped 1–30)"},
            },
            "required": ["destination"],
        },
        handler=packing_list,
    )
    registry.register(
        name="birthday_radar",
        description="Report the birthdays stored in memory that fall in the next 14 days, soonest first, with the day each lands. Birthdays whose stored dates can't be read are disclosed rather than dropped silently, and when none are stored at all it says how to add one. Use when the user asks about upcoming birthdays. Pass days to look further ahead or narrow the window (default 14, clamped 1–365).",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "How many days ahead to look (default 14, clamped 1–365)"},
            },
            "required": [],
        },
        handler=birthday_radar,
    )
