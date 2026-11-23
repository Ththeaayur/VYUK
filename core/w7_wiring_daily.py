# Part of the core/w7_wiring.py split (skills split B13).
# Family: daily.

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from . import daily  # sibling module used by this family (QuietHours/AlarmStore/gates)

_DAILY_STORE_NAME = "daily_v562.json"

def daily_store_path(store_path=None) -> Path:
    """The daily store file: caller override, else DATA_DIR /
    daily_v562.json (runtime-created; the file ships absent, in
    which case quiet hours are unset and no alarms exist)."""
    if store_path is not None:
        return Path(store_path)
    if _DAILY_STORE_OVERRIDE is not None:
        return Path(_DAILY_STORE_OVERRIDE)
    from .config import DATA_DIR

    return Path(str(DATA_DIR)) / _DAILY_STORE_NAME

_DAILY_STORE_OVERRIDE = None

def configure_daily_store(store_path) -> None:
    """Point the daily store at a caller-chosen file (tests use
    a temp dir; production uses the DATA_DIR default)."""
    global _DAILY_STORE_OVERRIDE
    _DAILY_STORE_OVERRIDE = str(store_path) if store_path else None

def quiet_hours(store_path=None) -> daily.QuietHours:
    """The PF-20 quiet-hours object over the daily store file
    (shared with alarms: QuietHours reads/writes only its own
    "quiet_hours" key, AlarmStore only "alarms"). The shared
    calendar-quiet cache is attached, so the live gates (the
    watcher _emit path, desk_reminder) treat an in-progress
    timed meeting as quiet — Stage 0 features batch 6, item 14.
    The toggle itself is read live from the config (default ON);
    with no calendar attached anywhere else, a directly built
    QuietHours stays fixed-window-only."""
    return daily.QuietHours(
        str(daily_store_path(store_path)),
        calendar_cache=daily.calendar_quiet_cache())

def set_quiet_window(start: str, end: str, store_path=None) -> dict:
    """Persist a quiet window ("HH:MM" pair; end <= start wraps
    midnight). Survives restarts — the window lives in the
    store file, not in memory."""
    return quiet_hours(store_path).set_window(start, end)

def clear_quiet_window(store_path=None) -> None:
    quiet_hours(store_path).clear_window()

def speak_allowed(now=None, *, is_alarm: bool = False,
                  store_path=None) -> tuple[bool, str]:
    """Speak gate: alarms speak even in quiet hours; otherwise
    quiet blocks (held, not dropped, by the caller)."""
    return daily.gate_speak(quiet_hours(store_path), now,
                                 is_alarm=is_alarm)

def notify_allowed(now=None, *, is_alarm: bool = False,
                   store_path=None) -> tuple[bool, str]:
    """Notify gate: same rule as speak — alarms exempt, else
    quiet holds."""
    return daily.gate_notify(quiet_hours(store_path), now,
                                  is_alarm=is_alarm)

def quiet_gate_for_watchers(now=None, store_path=None) -> tuple[bool, str]:
    """The gate WatcherEngine._emit consults on the live
    proactive notify path: (allowed, reason). With no window
    set this is (True, "") — a default install's notices are
    byte-identical. During quiet hours _emit holds the notice
    (returns False without recording a delivery, so the
    watcher's flag stays unset and the event stays pending for
    a later tick) instead of dropping it."""
    return notify_allowed(now, store_path=store_path)

_DAILY_STARTUP_DONE = False

_LAST_DAILY_TICK: dict = {"fired": [], "missed": [], "store": None}

_DAILY_ON_FIRE: Optional[Callable] = None

_DAILY_ON_MISSED: Optional[Callable] = None

def register_daily_alarm_handlers(on_fire=None, on_missed=None) -> None:
    """Register the alarm delivery callbacks: on_fire(alarm)
    for a due alarm, on_missed(alarm) for each missed alarm
    reported by the start-up pass. With none registered, fired
    and missed alarms are still recorded in the last-tick
    report (and missed ones persist as "missed" in the store
    until dismissed, so a later surface re-reports them) —
    they are never dropped, but no speech/notify delivery
    happens without a handler."""
    global _DAILY_ON_FIRE, _DAILY_ON_MISSED
    if on_fire is not None:
        _DAILY_ON_FIRE = on_fire
    if on_missed is not None:
        _DAILY_ON_MISSED = on_missed

def reset_daily_state() -> None:
    """Test seam: re-arm the start-up missed pass and clear the
    last-tick report."""
    global _DAILY_STARTUP_DONE, _LAST_DAILY_TICK
    _DAILY_STARTUP_DONE = False
    _LAST_DAILY_TICK = {"fired": [], "missed": [], "store": None}

def last_daily_tick_report() -> dict:
    """The most recent daily tick's report (fired + missed
    alarm dicts + store path) — the surface a GUI or log sink
    reads to REPORT missed alarms."""
    return {"fired": [dict(a) for a in _LAST_DAILY_TICK["fired"]],
            "missed": [dict(a) for a in _LAST_DAILY_TICK["missed"]],
            "store": _LAST_DAILY_TICK["store"]}

def add_daily_alarm(label: str, fire_at, message: str = "",
                    kind: str = "alarm", store_path=None
                    ) -> daily.Alarm:
    """Persist one alarm. No live add trigger calls this yet
    (DAILY_ADD_TRIGGER_OWED) — the tick below is the live part."""
    return daily.AlarmStore(
        str(daily_store_path(store_path))).add(label, fire_at,
                                                message, kind)

def list_daily_alarms(status: str = None, store_path=None) -> list:
    return daily.AlarmStore(
        str(daily_store_path(store_path))).list(status)

def dismiss_daily_alarm(alarm_id, store_path=None) -> bool:
    return daily.AlarmStore(
        str(daily_store_path(store_path))).dismiss(alarm_id)

def snooze_daily_alarm(alarm_id, minutes: int = 10, now=None,
                       store_path=None):
    return daily.AlarmStore(
        str(daily_store_path(store_path))).snooze(alarm_id, minutes, now)

def cancel_daily_alarm(alarm_id, store_path=None) -> bool:
    return daily.AlarmStore(
        str(daily_store_path(store_path))).cancel(alarm_id)

def daily_tick(now=None, store_path=None) -> dict:
    """One daily tick, called by the live RoutineScheduler tick.

    First tick in a process runs the start-up pass FIRST:
    pending alarms whose time already passed while the
    assistant was down are marked "missed" and REPORTED (in
    this report + to the on_missed handler when registered) —
    never silently fired late, never dropped. Every tick then
    fires due alarms (persisted as fired BEFORE the report, so
    a crash cannot re-fire them) and hands each to the on_fire
    handler when registered. With no store file present this
    is an empty report — a default install pays one missing-
    file read per routine tick and nothing else."""
    global _DAILY_STARTUP_DONE, _LAST_DAILY_TICK
    moment = now or datetime.datetime.now()
    path = str(daily_store_path(store_path))
    store = daily.AlarmStore(path)
    newly_missed = []
    if not _DAILY_STARTUP_DONE:
        _DAILY_STARTUP_DONE = True
        newly_missed = store.check_missed(moment)
        for alarm in newly_missed:
            if _DAILY_ON_MISSED is not None:
                try:
                    _DAILY_ON_MISSED(alarm)
                except Exception:  # noqa: BLE001 — handler never breaks the tick
                    pass
    fired = store.tick(moment)
    for alarm in fired:
        if _DAILY_ON_FIRE is not None:
            try:
                _DAILY_ON_FIRE(alarm)
            except Exception:  # noqa: BLE001
                pass
    # Missed alarms are RE-REPORTED on every tick until
    # dismissed (the store still holds them as "missed"); only
    # newly-missed ones ping the handler, so an undismissed
    # missed alarm does not spam the handler every tick.
    missed_report = store.list("missed")
    _LAST_DAILY_TICK = {
        "fired": [a.to_dict() for a in fired],
        "missed": [a.to_dict() for a in missed_report],
        "store": path}
    return last_daily_tick_report()
