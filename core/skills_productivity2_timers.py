from .config import DATA_DIR
import datetime
import json
import math
from pathlib import Path  # INTENTIONAL: _stopwatch_path's -> Path annotation
from .scheduler import scheduler
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_productivity2_helpers import (
    _fmt_clock,
    _fmt_duration,
    _now,
)


# ---------------------------------------------------------------------------
# POMODORO — focus rounds on the scheduler's reminder list
# ---------------------------------------------------------------------------

_POMODORO_PREFIX = "Pomodoro"


def pomodoro(minutes: float = 25, break_minutes: float = 5,
             cycles: int = 1, action: str = "start") -> str:
    """Start a Pomodoro (scheduler reminders), or report what's running."""
    action = str(action or "start").strip().lower()
    if action == "status":
        try:
            active = [r for r in scheduler.get_all_reminders()
                      if str(r.get("label", "")).startswith(_POMODORO_PREFIX)
                      and not r.get("fired")]
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the Pomodoro schedule: {e}"
        if not active:
            return ("No Pomodoro is running — no Pomodoro reminders "
                    "are on the schedule. Start one with "
                    "pomodoro(minutes=25).")
        lines = [f"{len(active)} Pomodoro reminder(s) still to come:"]
        for record in active:
            lines.append(f"• {record['label']} at {record['fire_at']} "
                         f"— {record['message']}")
        lines.append("Reminders live in VYUK's memory only — "
                     "closing the app cancels them.")
        return "\n".join(lines)
    if action != "start":
        return (f"Unknown pomodoro action '{action}' — use 'start' "
                "or 'status'.")
    try:
        focus = float(minutes)
        rest = float(break_minutes)
    except (TypeError, ValueError):
        return "minutes and break_minutes must be numbers."
    try:
        rounds = int(cycles)
    except (TypeError, ValueError):
        return f"cycles must be a whole number (got {cycles!r})."
    if not 1 <= focus <= 240:
        return (f"A focus round should run 1 to 240 minutes "
                f"(got {focus:g}).")
    if not 0 <= rest <= 120:
        return (f"A break should run 0 to 120 minutes (got {rest:g}).")
    if not 1 <= rounds <= 12:
        return f"cycles must be between 1 and 12 (got {rounds})."
    now = datetime.datetime.now()
    cursor = now
    scheduled = []
    try:
        for cycle in range(1, rounds + 1):
            cursor += datetime.timedelta(minutes=focus)
            if cycle < rounds:
                focus_message = ("Pomodoro done — take a break. "
                                 f"Round {cycle} of {rounds} finished.")
            else:
                focus_message = (f"Pomodoro finished — all {rounds} "
                                 "round(s) done. Good work; take a "
                                 "proper rest.")
            label = scheduler.add_reminder(
                label=f"{_POMODORO_PREFIX} focus {cycle}",
                fire_at=cursor, message=focus_message, repeat=False)
            scheduled.append((label, cursor, focus_message))
            if cycle < rounds and rest > 0:
                cursor += datetime.timedelta(minutes=rest)
                break_message = (f"Break over — back to focus. "
                                 f"Round {cycle + 1} of {rounds} starts now.")
                label = scheduler.add_reminder(
                    label=f"{_POMODORO_PREFIX} break {cycle}",
                    fire_at=cursor, message=break_message, repeat=False)
                scheduled.append((label, cursor, break_message))
    except Exception as e:  # noqa: BLE001
        return f"The Pomodoro couldn't be scheduled: {e}"
    first_end = now + datetime.timedelta(minutes=focus)
    last_end = scheduled[-1][1]
    lines = [
        f"Pomodoro started: {rounds} round(s) of {focus:g} minutes"
        + (f" with {rest:g}-minute breaks between them."
           if rounds > 1 and rest > 0 else ".")
        + f" Round 1 ends at {_fmt_clock(first_end)}"
        + (f"; the whole run finishes at {_fmt_clock(last_end)}."
           if rounds > 1 else ".")
    ]
    lines.append("I'll announce each change of pace as it fires. "
                 "One honesty note: these reminders live in VYUK's "
                 "memory — closing the app cancels the Pomodoro.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# STOPWATCH — start / lap / stop / status / reset, timestamp maths
# ---------------------------------------------------------------------------

_STOPWATCH_FILE = "stopwatch_state.json"
_STOPWATCH_ACTIONS = ("start", "lap", "stop", "status", "reset")


def _stopwatch_path() -> Path:
    return DATA_DIR / _STOPWATCH_FILE


def _load_stopwatch() -> dict | None:
    """The saved state dict, or None when no stopwatch exists."""
    try:
        raw = _stopwatch_path().read_text(encoding="utf-8")
        state = json.loads(raw)
        if isinstance(state, dict) and "banked" in state:
            return state
    except (OSError, ValueError):
        pass
    return None


def _save_stopwatch(state: dict) -> str | None:
    """Persist the state; an error string on failure, else None."""
    try:
        _stopwatch_path().write_text(json.dumps(state), encoding="utf-8")
        return None
    except OSError as e:
        return f"Couldn't save the stopwatch state: {e}"


def _elapsed(state: dict, at: float | None = None) -> float:
    """Total elapsed seconds: banked segments + the running segment."""
    total = float(state.get("banked", 0.0))
    if state.get("running") and state.get("started_at") is not None:
        total += (at if at is not None else _now()) - float(state["started_at"])
    return total


def stopwatch(action: str) -> str:
    """A stopwatch with laps; state survives in the app data dir."""
    action = str(action or "").strip().lower()
    if action not in _STOPWATCH_ACTIONS:
        return (f"Unknown stopwatch action '{action}' — use one of: "
                + ", ".join(_STOPWATCH_ACTIONS) + ".")
    state = _load_stopwatch()
    if action == "reset":
        try:
            _stopwatch_path().unlink(missing_ok=True)
        except OSError as e:
            return f"Couldn't clear the stopwatch: {e}"
        return ("Stopwatch cleared — no time banked, no laps kept. "
                "Start a new one whenever you're ready.")
    if action == "start":
        if state and state.get("running"):
            return (f"The stopwatch is already running — it's at "
                    f"{_fmt_duration(_elapsed(state))}. Stop or reset "
                    "it before starting again.")
        if state and float(state.get("banked", 0.0)) > 0:
            # Resume: keep the banked time and the laps.
            state["running"] = True
            state["started_at"] = _now()
            err = _save_stopwatch(state)
            if err:
                return err
            return (f"Stopwatch resumed from "
                    f"{_fmt_duration(float(state['banked']))} — "
                    "the earlier laps are still on record.")
        fresh = {"running": True, "started_at": _now(),
                 "banked": 0.0, "laps": []}
        err = _save_stopwatch(fresh)
        if err:
            return err
        return "Stopwatch started. Say 'lap' at each split and 'stop' when you're done."
    if state is None:
        return ("No stopwatch exists yet — start one with "
                "stopwatch('start').")
    if action == "status":
        elapsed = _elapsed(state)
        laps = state.get("laps") or []
        if state.get("running"):
            line = f"Stopwatch is running: {_fmt_duration(elapsed)} so far"
        else:
            line = f"Stopwatch is stopped at {_fmt_duration(elapsed)}"
        if laps:
            line += f", with {len(laps)} lap(s) recorded (latest: lap " \
                    f"{laps[-1]['n']} was {_fmt_duration(laps[-1]['split'])})."
            if len(laps) > 1:
                splits = ", ".join(
                    f"lap {lap['n']} was {_fmt_duration(lap['split'])}"
                    for lap in laps)
                line += f" Splits: {splits}."
        else:
            line += ", no laps yet."
        return line
    if action == "lap":
        if not state.get("running"):
            return ("The stopwatch isn't running — a lap needs it "
                    "going. Start it first.")
        now = _now()
        elapsed = _elapsed(state, at=now)
        laps = state.get("laps") or []
        previous_total = laps[-1]["total"] if laps else 0.0
        lap = {"n": len(laps) + 1, "total": elapsed,
               "split": elapsed - previous_total}
        laps.append(lap)
        state["laps"] = laps
        err = _save_stopwatch(state)
        if err:
            return err
        return (f"Lap {lap['n']}: {_fmt_duration(lap['split'])} "
                f"(total {_fmt_duration(elapsed)}).")
    # action == "stop"
    if not state.get("running"):
        return (f"The stopwatch is already stopped at "
                f"{_fmt_duration(_elapsed(state))}. Reset it to clear, "
                "or start to resume.")
    state["banked"] = _elapsed(state)
    state["running"] = False
    state["started_at"] = None
    err = _save_stopwatch(state)
    if err:
        return err
    laps = state.get("laps") or []
    line = f"Stopwatch stopped: {_fmt_duration(float(state['banked']))}."
    if laps:
        splits = ", ".join(
            f"lap {lap['n']} was {_fmt_duration(lap['split'])}"
            for lap in laps)
        line += f" Splits: {splits}."
    return line


# ---------------------------------------------------------------------------
# COUNTDOWN — one reminder, minutes from now
# ---------------------------------------------------------------------------


def countdown(minutes: float, label: str = "") -> str:
    """Set a countdown that announces itself when it finishes."""
    try:
        mins = float(minutes)
    except (TypeError, ValueError):
        return f"minutes must be a number (got {minutes!r})."
    if not math.isfinite(mins) or mins <= 0 or mins > 1440:
        return ("A countdown runs from just above 0 up to 1440 "
                f"minutes (24 hours) — got {minutes!r}.")
    label = str(label or "").strip()
    fire_at = datetime.datetime.now() + datetime.timedelta(minutes=mins)
    name = f"Countdown: {label}" if label else "Countdown"
    message = (f"Countdown finished — {label}." if label
               else "Countdown finished.")
    try:
        stored_label = scheduler.add_reminder(
            label=name, fire_at=fire_at, message=message, repeat=False)
    except Exception as e:  # noqa: BLE001
        return f"The countdown couldn't be set: {e}"
    return (f"Countdown set for {_fmt_duration(mins * 60)}"
            + (f" ('{label}')" if label else "")
            + f" — it finishes at {_fmt_clock(fire_at)} and I'll "
            f"announce it then (reminder '{stored_label}'). As with "
            "all reminders, it lives in VYUK's memory — closing "
            "the app cancels it.")
