"""
Habits & wellbeing skills pack for VYUK — habit tracking
with streaks, two small pieces of body maths (water, screen
breaks), a daily journal prompt, mood check-ins with a purely
numerical trend, a gratitude log, a screen-time focus score,
and one combined "today so far" review.

Where each tool's truth lives — and what it honestly can't
know:

- Habit definitions persist as memory facts under the category
  `habit_defs` (key = the habit's name, value = the date it was
  added), and completions as facts under `habit_logs` (key =
  "name|YYYY-MM-DD|counter", value = the note or "done"). The
  separate definitions category is deliberate: the older
  habit_checkin tool (skills_productivity_day) already owns the
  category `habits` with a different value shape, and sharing
  it would corrupt that tool's streak data.
- Moods and gratitude entries persist as memory facts under
  `moods` and `gratitude` — small JSON payloads that record
  their own date, so day views never have to guess from storage
  timestamps. Mood wording is the user's own; the trend tool
  reports numbers only and never interprets them.
- focus_score reads the screen-time day counter that
  skills_system_health maintains (screen_time.json in the app data
  dir, core/config.py's DATA_DIR). It is strictly read-only —
  it never updates the counter, so the figure is exactly what
  that counter last recorded; time while VYUK isn't
  running is unknown to it, and the result says so.
- day_review invents nothing: it re-reads the same fact
  categories the other packs write (expenses and decisions
  from skills_productivity_logs, this pack's moods, gratitude and
  habit logs) and counts only entries whose own recorded date
  is today. A category with nothing today is named as empty.

Same rules as the packs before it: plain functions returning
human-readable strings, never raising; persistence and file
reads sit behind module-level seams so tests can monkeypatch
them; stdlib only. Wiring: the parent (core/funnel/tools_registry.py) calls
register_all(registry) — this module never touches the
registry singleton itself.
"""

# INTENTIONAL: re-exports of the old module's import bindings, so
# the facade's namespace matches the old module's exactly
# (dir-diff-empty bar, §2 P2).
import datetime
import json
import math

# INTENTIONAL: re-exports of every name moved into the impl modules.
from .skills_habits_helpers import (
    _MAX_NOTE_CHARS,
    _coerce_float,
    _coerce_int,
    _duration_words,
    _facts,
    _fmt_amount,
    _read_screen_time,
    _screen_time_path,
    _store,
    _today,
    _unique_key,
)
from .skills_habits_tracking import (
    _BREAK_MINUTES,
    _HABIT_DEFS_CATEGORY,
    _HABIT_LOGS_CATEGORY,
    _LOOKBACK_DAYS,
    _MAX_HABIT_CHARS,
    _habit_defs,
    _habit_log_entries,
    _habit_stats,
    _streaks,
    habit_summary,
    habit_track,
    screen_break_plan,
    water_intake_math,
)
from .skills_habits_mind import (
    _GRATITUDE_CATEGORY,
    _JOURNAL_PROMPTS,
    _MAX_GRATITUDE_CHARS,
    _MAX_MOOD_CHARS,
    _MOODS_CATEGORY,
    _load_gratitude,
    _load_moods,
    gratitude_log,
    journal_prompt,
    mood_log,
    mood_trend,
)
from .skills_habits_review import (
    _expense_day,
    day_review,
    focus_score,
)


def register_all(registry) -> None:
    """Register every Habits pack tool on a ToolRegistry-like
    object."""
    registry.register(
        name="habit_track",
        description="Track habits day by day. Actions: 'add' defines a habit by name, 'done' logs today's completion for a defined habit (an optional note is kept), 'list' shows every tracked habit with whether it's done today. Definitions and completions persist as memory facts. This is separate from habit_checkin, which keeps its own one-fact-per-habit streak list. Use when the user starts a new habit, reports doing one, or asks which habits are tracked.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'done' or 'list'"},
                "name": {"type": "string", "description": "The habit's name, for 'add' and 'done'"},
                "note": {"type": "string", "description": "Optional note kept with a 'done' log entry"},
            },
            "required": ["action"],
        },
        handler=habit_track,
    )
    registry.register(
        name="habit_summary",
        description="Report a habit's track record: how many days it's been done, the current streak (a streak stays alive until a day actually passes undone), the best streak, the last done date, and the share of days done since the habit was added (counting at most the last 365 days). Leave the name blank for every tracked habit. The habits are the ones defined with habit_track. Use when the user asks how a habit is going or what their streak is.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The habit to summarise — blank means all tracked habits"},
            },
            "required": [],
        },
        handler=habit_summary,
    )
    registry.register(
        name="water_intake_math",
        description="Work out a daily water target from body weight, using the standard rule of thumb of 35 ml per kilogram plus 350 ml for every 30 minutes of activity. The result is in litres and in 250 ml glasses, with the rule stated so the number can be judged. Pure arithmetic — nothing is stored. Use when the user asks how much water they should drink.",
        parameters={
            "type": "object",
            "properties": {
                "weight_kg": {"type": "number", "description": "Body weight in kilograms"},
                "activity_minutes": {"type": "number", "description": "Minutes of exercise or physical activity today (default 0)"},
            },
            "required": ["weight_kg"],
        },
        handler=water_intake_math,
    )
    registry.register(
        name="screen_break_plan",
        description="Plan breaks for a stretch of screen time: the time is split into work blocks (20 minutes each by default, changeable), every block is followed by a 5-minute break, and with a start time (HH:MM) the result includes the finishing clock time. Pure arithmetic on the numbers given. For learning about the 20-8-2 sitting/standing idea itself, this returns the simpler block plan, not a posture schedule. Use when the user is planning a long screen session.",
        parameters={
            "type": "object",
            "properties": {
                "screen_hours": {"type": "number", "description": "Total screen time in hours"},
                "break_minutes": {"type": "integer", "description": "Length of each work block in minutes (default 20, between 5 and 120)"},
                "start_time": {"type": "string", "description": "Optional start time as HH:MM — gives the finishing time"},
            },
            "required": ["screen_hours"],
        },
        handler=screen_break_plan,
    )
    registry.register(
        name="journal_prompt",
        description="Give today's journal prompt. The prompt is picked from a fixed list by the date, so it's the same all day and different tomorrow. Nothing is stored — for keeping the writing itself, a notes or writing tool is the place. Use when the user wants to journal and asks for a prompt or doesn't know what to write about.",
        parameters={"type": "object", "properties": {},
                    "required": []},
        handler=journal_prompt,
    )
    registry.register(
        name="mood_log",
        description="Record mood check-ins and see recent ones. Actions: 'add' stores a check-in with a score from 1 to 10 and optional mood word and note, 'list' shows the most recent check-ins, newest first. Check-ins persist as memory facts. The words stay the user's own — this log keeps them verbatim and never interprets them. For patterns over time use mood_trend. Use when the user reports how they feel in score terms or asks what they've logged.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add' or 'list'"},
                "mood": {"type": "string", "description": "Optional word for the mood, e.g. 'calm'"},
                "score": {"type": "integer", "description": "The score from 1 to 10, for 'add'"},
                "note": {"type": "string", "description": "Optional short note kept with the check-in"},
            },
            "required": ["action"],
        },
        handler=mood_log,
    )
    registry.register(
        name="mood_trend",
        description="Summarise mood check-ins over a window (14 days by default): how many entries, the average score, the highest and lowest days, and whether the second half of the window averages higher, lower or about the same as the first (offered only with at least 4 check-ins). It reports the recorded numbers only — it does not interpret them or comment on mental health. The check-ins come from mood_log. Use when the user asks how their mood has been trending.",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Window length in days (default 14, between 1 and 90)"},
            },
            "required": [],
        },
        handler=mood_trend,
    )
    registry.register(
        name="gratitude_log",
        description="Keep a gratitude log. Actions: 'add' saves an entry, 'list' shows the total count and the five most recent entries, 'random' resurfaces one past entry (picked by the date, so it's steady through the day). Entries persist as memory facts with their dates. Use when the user notes something they're grateful for or wants to look back at the log.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'list' or 'random' (default 'list')"},
                "text": {"type": "string", "description": "The entry's text, for 'add'"},
            },
            "required": [],
        },
        handler=gratitude_log,
    )
    registry.register(
        name="focus_score",
        description="Report today's tracked screen time and a focus score: 100 minus the percentage of the counted time that was active screen use, starting from when the counter began today. It reads the screen-time counter that screen_time_today maintains and never changes it — the counter only records while VYUK is running, and the result says exactly which stretch is covered. Use when the user asks how long they've been on screen today or how focused the day looks by that measure.",
        parameters={"type": "object", "properties": {},
                    "required": []},
        handler=focus_score,
    )
    registry.register(
        name="day_review",
        description="Assemble a 'today so far' digest from what the other logs actually hold for today: expenses logged and their total (expense_log), decisions recorded (decision_log), the latest mood check-in (mood_log), gratitude entries (gratitude_log), and how many tracked habits are done (habit_track). Anything with nothing today is named as empty rather than skipped or guessed. Use when the user asks how today is looking or wants a quick review of the day.",
        parameters={"type": "object", "properties": {},
                    "required": []},
        handler=day_review,
    )
