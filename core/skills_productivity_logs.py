"""
Productivity V skills pack for VYUK — the money-and-decisions
shelf: an expense log with month totals, a decision log with
revisit dates, a reading list, one combined "what's pending"
view across reminders, the stopwatch and the Pomodoro, a
meeting cost calculator, and — Stage 0 features batch 4
(Group G, 2026-11-22) — the owner's own lists: notes, a
wishlist, a movie/show watchlist and a goal tracker.

Where each tool's truth lives — and what it honestly can't know:

- Expenses, decisions, the reading list, notes, the wishlist,
  the watchlist and goals persist as memory facts
  (core/memory/memory.py) — small JSON payloads under the fixed
  categories `expenses`, `decisions`, `reading`, `notes`,
  `wishlist`, `watchlist` and `goals` — so they survive restarts
  and show up in the user's memory like anything else VYUK
  remembers. Amounts are stored as plain numbers exactly as
  given: this pack never assumes a currency, and its sentences
  never print one.
- whats_pending invents nothing. Reminders and the Pomodoro are
  read live from the in-memory ProactiveScheduler
  (core/scheduler.py) — the same list the Pomodoro tool itself
  rides — so they cover only what is scheduled right now, while
  the app stays open. The stopwatch is read from its saved state
  in skills_productivity_tools (a JSON file in the app data dir), so
  it is reported even across restarts, exactly as that tool left
  it. A section whose source can't be read says so instead of
  guessing.
- meeting_cost is pure arithmetic on the numbers it is handed —
  no calendar lookup, no stored rates.

Same rules as the packs before it: plain functions returning
human-readable strings, never raising; external reads sit behind
module-level helpers so tests can monkeypatch them; stdlib only.
Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import datetime
import json
import re
import time

from .scheduler import scheduler  # INTENTIONAL: old-namespace re-export

from .skills_productivity5_logs import (
    _coerce_int,
    _coerce_float,
    _fmt_amount,
    _fmt_duration,
    _fmt_hours,
    _when_words,
    _store,
    _facts,
    _reminders,
    _stopwatch_state,
    _POMODORO_PREFIX,
    _pomodoro_state,
    _EXPENSES_CATEGORY,
    _MONTH_RE,
    _MAX_NOTE_CHARS,
    _load_expenses,
    _month_label,
    expense_log,
    _DECISIONS_CATEGORY,
    _MAX_DECISION_CHARS,
    _load_decisions,
    decision_log,
    _READING_CATEGORY,
    _MAX_TITLE_CHARS,
    _load_reading,
    _reading_line,
    reading_list,
    whats_pending,
    meeting_cost,
)

from .skills_notes import (  # Stage 0 features batch 4 (Group G)
    goal_tracker,
    notes,
    watchlist,
    wishlist,
)

def register_all(registry) -> None:
    """Register every Productivity-5 pack tool on a
    ToolRegistry-like object."""
    registry.register(
        name="expense_log",
        description="Log an expense and see what a month adds up to. Actions: 'add' stores an amount with an optional note under today's date, 'list' shows one month's entries (month as YYYY-MM, blank for this month), 'total' sums a month. Amounts are kept as plain numbers exactly as given — no currency is assumed. Entries persist as memory facts. Use when the user mentions spending money or asks what they spent.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'list' or 'total'"},
                "amount": {"type": "number", "description": "The amount spent, for 'add' — a plain number, no currency symbol"},
                "note": {"type": "string", "description": "What the expense was for, e.g. 'train tickets'"},
                "month": {"type": "string", "description": "Month to list or total, as YYYY-MM — blank means this month"},
            },
            "required": ["action"],
        },
        handler=expense_log,
    )
    registry.register(
        name="decision_log",
        description="Record decisions so they can be revisited on purpose. Actions: 'add' stores the decision text with an optional revisit date (YYYY-MM-DD), 'list' shows the log with any decision whose revisit date is today or past leading, flagged as due for a second look. Decisions persist as memory facts. Use when the user settles something and wants it remembered, or asks what decisions are pending review.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add' or 'list'"},
                "text": {"type": "string", "description": "The decision itself, for 'add'"},
                "revisit": {"type": "string", "description": "Optional date to reconsider it, as YYYY-MM-DD"},
            },
            "required": ["action"],
        },
        handler=decision_log,
    )
    registry.register(
        name="reading_list",
        description="Keep a reading list: books queued against books finished. Actions: 'add' queues a title (author optional), 'list' shows the to-read shelf and the finished shelf separately, 'mark_read' moves a title to finished. The list persists as memory facts. Use when the user mentions a book they want to read, asks what's on their list, or reports finishing one.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'list' or 'mark_read' (default 'list')"},
                "title": {"type": "string", "description": "The book's title"},
                "author": {"type": "string", "description": "The author, optional, for 'add'"},
            },
            "required": [],
        },
        handler=reading_list,
    )
    registry.register(
        name="notes",
        description="Take a note, list your notes, read one back, or remove one. Actions: 'add' stores the text (title optional — without one the first line is the title); 'list' shows them newest first, numbered; 'read' reads one back by its number from the list; 'remove' drops one by its number. Notes persist as memory facts. Use when the user says 'take a note', 'remember this', or asks what their notes say.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'list', 'read' or 'remove'"},
                "text": {"type": "string", "description": "The note's text, for 'add'"},
                "title": {"type": "string", "description": "Optional title for the note, for 'add'"},
                "index": {"type": "integer", "description": "The note's number from the list, for 'read' or 'remove'"},
            },
            "required": [],
        },
        handler=notes,
    )
    registry.register(
        name="wishlist",
        description="Keep a wishlist of things you want. Actions: 'add' records one (price and a note are optional — prices are plain numbers, no currency assumed), 'list' shows it, 'remove' drops one by name. The list persists as memory facts. Use when the user says they want something, or asks what's on their wishlist.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'list' or 'remove'"},
                "item": {"type": "string", "description": "The thing you want"},
                "price": {"type": "number", "description": "Optional price noted for the item, for 'add'"},
                "note": {"type": "string", "description": "Optional note about the item, for 'add'"},
            },
            "required": [],
        },
        handler=wishlist,
    )
    registry.register(
        name="watchlist",
        description="Keep a watchlist of films and shows to watch. Actions: 'add' queues a title (kind 'movie' or 'show', optional), 'list' shows the to-watch shelf and the watched shelf separately, 'mark_watched' moves a title to watched, 'remove' drops one by title. The list persists as memory facts. Use when the user says they want to watch something, or asks what's on their watchlist.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'list', 'mark_watched' or 'remove'"},
                "title": {"type": "string", "description": "The film or show's title"},
                "kind": {"type": "string", "description": "'movie' or 'show', optional, for 'add'"},
                "note": {"type": "string", "description": "Optional note about the title, for 'add'"},
            },
            "required": [],
        },
        handler=watchlist,
    )
    registry.register(
        name="goal_tracker",
        description="Track your own goals with a target and progress — read a book, run a distance, save an amount. Actions: 'add' records one with a target (unit optional, e.g. 'pages', 'km'), 'list' shows each with its progress against the target, 'update' sets the current progress, 'remove' drops one by name. Targets and progress are plain numbers, no currency assumed. Goals persist as memory facts. Use when the user sets a personal goal or asks how one is going.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'list', 'update' or 'remove'"},
                "name": {"type": "string", "description": "The goal's name"},
                "target": {"type": "number", "description": "The target to reach, for 'add'"},
                "unit": {"type": "string", "description": "Optional unit for the target and progress, e.g. 'pages', for 'add'"},
                "progress": {"type": "number", "description": "The current progress, for 'update'"},
            },
            "required": [],
        },
        handler=goal_tracker,
    )
    registry.register(
        name="whats_pending",
        description="Show everything live right now in one view: reminders still waiting (what and when), whether the stopwatch is running and at what time, and the Pomodoro's round state. Each section reports only what its source actually holds — reminders and the Pomodoro live in the app's memory, the stopwatch's state is read from its saved file. Use when the user asks what's on, what's running or what they're waiting for. Pass days to window the reminders to those due within that many days — the result then counts how many more wait beyond the window.",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "number", "description": "Optional horizon in days: only reminders due within this many days are counted (overdue ones stay in view); leave out to count everything waiting"},
            },
            "required": [],
        },
        handler=whats_pending,
    )
    registry.register(
        name="meeting_cost",
        description="Work out what a meeting costs in people's time. Give the attendee count and the length in minutes: the result is attendee-minutes and person-hours, and with an optional hourly_rate per person, the money figure too. Pure arithmetic on the numbers given — nothing is looked up or stored. Use when the user wonders whether a meeting is worth its size.",
        parameters={
            "type": "object",
            "properties": {
                "attendees": {"type": "integer", "description": "How many people attend (whole number, at least 1)"},
                "minutes": {"type": "number", "description": "The meeting's length in minutes"},
                "hourly_rate": {"type": "number", "description": "Optional cost per person per hour — 0 (default) skips pricing"},
            },
            "required": ["attendees", "minutes"],
        },
        handler=meeting_cost,
    )
