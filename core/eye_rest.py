"""20-20-20 eye rest, guided deep breathing, and the water-glass counter.

R8-4 screen-break PORT (turqoisehex/screen-break, MIT, single tkinter
file, 1 star, no PyPI distribution — VYUK is PySide6, so the behaviour
is ported as VYUK's own code, not the tkinter file; no new dependency,
no THIRD_PARTY row, requirements.txt untouched).

WHAT IS BUILT (the gap, proven on the backup before building)
-------------------------------------------------------------
* 20-20-20 eye-rest timer: the tree had break-plan *descriptors*
  (screen_break_plan, well_break_plan) and reminder *descriptors*
  (well_reminder) but no live timer that fires an eye-rest notice.
  Greps for 20-20-20 / eye rest returned 0 hits.
* Guided breathing: the tree had no inhale/exhale-timed exercise.
  Greps for inhale / exhale / box breathing / 4-7-8 returned 0 hits;
  well.py's wind-down list only names "take a few slow breaths" as a
  step — it is not a guided exercise.
* Water-glass counter: water_break already reminds (start / snooze /
  stop / status on the scheduler) and water_intake_math computes a
  daily target in 250 ml glasses — but nothing logs glasses drunk.
  This module adds the "+1 Glass" counter.

WHAT IS NOT BUILT (the scouting overclaimed)
--------------------------------------------
* Hydration reminders — water_break already exists.
* Tray icon — QSystemTrayIcon already exists in the shell.
* Micro-pauses as a separate timer — subsumed by the 20-20-20 chain
  (a 20-minute recurring nudge IS the micro-pause cadence).

BOUNDARY (well.py's lane — track / notice / organise only)
----------------------------------------------------------
No diagnosis, no scoring, no coaching, no biometric input or output,
no enforcement: the timer NOTICES, it never locks input or forces a
break. No health-benefit claims anywhere in copy or code. Copy says
"focus session" and "guided deep breathing" — never the trademarked
names. No per-app polling (the Wave 9 'sensing-free' contract).
"""

from __future__ import annotations

import datetime
import json

from .memory.memory import memory_store as _memory_store
from .skills_productivity3_focus_day import (
    _CHAIN_HOURS,
    _MEMORY_NOTE,
    _chain_cancel,
    _chain_pending,
    _chain_snooze,
    _chain_start,
    _chain_status,
)
from .skills_productivity3_helpers import _coerce_int

from core.funnel.tools_registry_core import _funnel_only_fn

# ---------------------------------------------------------------------------
# 20-20-20 eye-rest timer — a third nudge chain, same shape as
# water_break / stand_up_nudge (core/skills_productivity3_focus_day.py).
# ---------------------------------------------------------------------------

_EYE_REST_PREFIX = "Eye rest"

_EYE_REST_MESSAGE = (
    "20-20-20 — look at something about 20 feet away for 20 seconds, "
    "then carry on."
)


def eye_rest_timer(action: str = "status", interval_minutes: int = 20,
                   snooze_minutes: int = 5) -> str:
    """Start, stop, check or snooze the 20-20-20 eye-rest nudges."""
    action = str(action or "status").strip().lower()
    if action not in ("start", "stop", "status", "snooze"):
        return ("Eye-rest timer actions are 'start', 'stop', "
                "'status' and 'snooze'.")
    try:
        if action == "status":
            return _chain_status(_EYE_REST_PREFIX, "Eye-rest")
        if action == "stop":
            pending = len(_chain_pending(_EYE_REST_PREFIX))
            _chain_cancel(_EYE_REST_PREFIX)
            if not pending:
                return "Eye-rest reminders weren't running."
            return (f"Eye-rest reminders off — cancelled {pending} "
                    f"upcoming nudge{'s' if pending != 1 else ''}.")
        if action == "snooze":
            minutes = _coerce_int(snooze_minutes, 5, 1, 240)
            new_fire = _chain_snooze(_EYE_REST_PREFIX, minutes,
                                     _EYE_REST_MESSAGE)
            if new_fire is None:
                return ("No eye-rest nudge to snooze — the reminders "
                        "aren't running.")
            return (f"Snoozed — your next eye-rest nudge is now at "
                    f"{new_fire:%H:%M}.")
        interval = _coerce_int(interval_minutes, 20, 5, 120)
        count, first, last = _chain_start(
            _EYE_REST_PREFIX, _EYE_REST_MESSAGE, interval, cap=24)
        return (f"Eye-rest reminders on: a gentle nudge every "
                f"{interval} minutes for the next {_CHAIN_HOURS} "
                f"hours ({count} in all; next at {first:%H:%M}, "
                f"last at {last:%H:%M}). {_MEMORY_NOTE} Snooze "
                f"one any time with action 'snooze'.")
    except Exception as e:  # noqa: BLE001
        return f"The eye-rest reminders couldn't be updated: {e}"


# ---------------------------------------------------------------------------
# Guided deep breathing — a pure timed script, no timer started, nothing
# measured. Patterns are phase tables; the guide is a descriptor in
# well.py's lane, not a treatment.
# ---------------------------------------------------------------------------

_BREATHING_PATTERNS = {
    "box": {
        "label": "Box breathing",
        "blurb": ("Box breathing: four slow counts in, four held, "
                  "four out, four held."),
        "phases": (("inhale", 4), ("hold", 4),
                   ("exhale", 4), ("hold", 4)),
    },
    "relaxing": {
        "label": "Relaxing breathing",
        "blurb": ("Relaxing breathing: four counts in, seven held, "
                  "eight out."),
        "phases": (("inhale", 4), ("hold", 7), ("exhale", 8)),
    },
}

_PHASE_CUES = {
    "inhale": "Breathe in slowly",
    "hold": "Hold",
    "exhale": "Breathe out slowly",
}

_BREATHING_NOTE = ("A timed script to follow — a suggestion, not a "
                   "treatment. Stop any time.")


def breathing_guide(pattern: str = "box", cycles: int = 4) -> dict:
    """Return the phase script for a guided-deep-breathing pattern.

    Pure computation: no timer is started, nothing is measured, nothing
    is stored. ``cycles`` is clamped to 1-10 (default 4). An unknown
    pattern refuses honestly, naming the two patterns.
    """
    key = str(pattern or "box").strip().lower()
    spec = _BREATHING_PATTERNS.get(key)
    if spec is None:
        return {"ok": False,
                "error": "Breathing patterns are 'box' and 'relaxing'."}
    n = _coerce_int(cycles, 4, 1, 10)
    phases = []
    total = 0
    for cycle in range(1, n + 1):
        for phase, seconds in spec["phases"]:
            phases.append({"cycle": cycle, "phase": phase,
                           "seconds": seconds, "starts_at_s": total,
                           "cue": _PHASE_CUES[phase]})
            total += seconds
    return {"ok": True, "pattern": key, "label": spec["label"],
            "cycles": n, "phases": phases, "total_seconds": total,
            "script": spec["blurb"], "note": _BREATHING_NOTE}


# ---------------------------------------------------------------------------
# Water-glass counter — today's glasses, logged in VYUK's own memory
# (the habit_checkin pattern: a JSON fact keyed by date). One glass is
# 250 ml, the same glass water_intake_math reports its target in.
# ---------------------------------------------------------------------------

_WATER_CATEGORY = "water"
_GLASS_ML = 250


def _load_glasses(today: str) -> int:
    fact = _memory_store.get_fact(_WATER_CATEGORY, today)
    if not fact:
        return 0
    try:
        stored = json.loads(fact.get("value") or "")
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(stored, dict):
        return 0
    try:
        return max(0, int(stored.get("glasses") or 0))
    except (TypeError, ValueError):
        return 0


def _save_glasses(today: str, count: int) -> bool:
    return bool(_memory_store.save_fact(
        _WATER_CATEGORY, today,
        json.dumps({"glasses": count, "ml": count * _GLASS_ML})))


def log_glass(action: str = "log") -> str:
    """Log a glass of water, check today's count, or reset it.

    The reported count is re-read from the store after the write, so
    what is claimed is what is stored (A4-filestat).
    """
    action = str(action or "log").strip().lower()
    if action not in ("log", "status", "reset"):
        return "Glass actions are 'log', 'status' and 'reset'."
    today = datetime.date.today().isoformat()
    try:
        if action == "status":
            count = _load_glasses(today)
            if not count:
                return "No glasses logged today yet."
            return (f"{count} glass{'es' if count != 1 else ''} today "
                    f"({count * _GLASS_ML} ml).")
        if action == "reset":
            if not _save_glasses(today, 0):
                return "Couldn't reset the glass count."
            return "Today's glass count is back to zero."
        count = _load_glasses(today) + 1
        if not _save_glasses(today, count):
            return "Couldn't save the glass count."
        stored = _load_glasses(today)
        return (f"Logged one glass — that's {stored} today "
                f"({stored * _GLASS_ML} ml).")
    except Exception as e:  # noqa: BLE001
        return f"Couldn't update the glass count: {e}"


# ---------------------------------------------------------------------------
# Funnel handlers + registration (R8-4, screen-break port).
# ---------------------------------------------------------------------------

@_funnel_only_fn
def _tool_eye_rest_timer(action: str = "status", interval_minutes: int = 20,
                         snooze_minutes: int = 5) -> str:
    """Funnel handler: the 20-20-20 eye-rest timer, in plain words."""
    return eye_rest_timer(action=action, interval_minutes=interval_minutes,
                          snooze_minutes=snooze_minutes)


@_funnel_only_fn
def _tool_breathing_guide(pattern: str = "box", cycles: int = 4) -> str:
    """Funnel handler: the breathing script, in plain words."""
    result = breathing_guide(pattern=pattern, cycles=cycles)
    if not result.get("ok"):
        return result.get("error", "No breathing pattern given.")
    lines = [f"{result['label']} — {result['cycles']} cycles, "
             f"{result['total_seconds']} seconds in all:"]
    for phase in result["phases"]:
        lines.append(f"Cycle {phase['cycle']} — {phase['cue']} "
                     f"({phase['seconds']}s)")
    lines.append(result["note"])
    return "\n".join(lines)


@_funnel_only_fn
def _tool_log_glass(action: str = "log") -> str:
    """Funnel handler: log a glass of water, in plain words."""
    return log_glass(action=action)


def register_all(registry) -> None:
    """Register the eye-rest pack (R8-4, screen-break port)."""
    registry.register(
        name="eye_rest_timer",
        description="20-20-20 eye-rest nudges on VYUK's scheduler: 'start' schedules a gentle notice every 20 minutes (look at something about 20 feet away for 20 seconds), 'snooze' pushes the next one back, 'stop' cancels them, 'status' reports what's left. They live in the app's memory only — closing VYUK stops them — and every result says so. Use when the user wants a reminder to rest their eyes from the screen.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "What to do: 'start', 'stop', "
                           "'status' or 'snooze' (default 'status')"},
                "interval_minutes": {"type": "integer",
                                     "description": "Minutes between "
                                     "nudges when starting (5-120, "
                                     "default 20)"},
                "snooze_minutes": {"type": "integer",
                                   "description": "Minutes to push the "
                                   "next nudge back when snoozing (1-240, "
                                   "default 5)"},
            },
            "required": [],
        },
        handler=_tool_eye_rest_timer,
    )
    registry.register(
        name="breathing_guide",
        description="A timed guided-deep-breathing script: 'box' (four counts in, four held, four out, four held) or 'relaxing' (four in, seven held, eight out), repeated for a few cycles. Pure computation — no timer is started, nothing is measured, nothing is stored. Use when the user asks for a breathing exercise or a moment to slow down.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string",
                            "description": "Which breathing pattern: "
                            "'box' or 'relaxing' (default 'box')"},
                "cycles": {"type": "integer",
                           "description": "How many times to repeat the "
                           "pattern (1-10, default 4)"},
            },
            "required": [],
        },
        handler=_tool_breathing_guide,
    )
    registry.register(
        name="log_glass",
        description="Log a glass of water drunk (250 ml each) and count today's total: 'log' adds one glass, 'status' reports today's count, 'reset' clears today. The count is stored in VYUK's own memory only. Use when the user says they drank a glass of water.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "What to do: 'log', 'status' or "
                           "'reset' (default 'log')"},
            },
            "required": [],
        },
        handler=_tool_log_glass,
    )
