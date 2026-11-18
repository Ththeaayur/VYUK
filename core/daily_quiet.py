from __future__ import annotations  # INTENTIONAL (skills split B21): moved code was written under postponed evaluation.
import datetime
import threading
# INTENTIONAL (skills split B21): shared store plumbing lives in daily_helpers; QuietHours/AlarmStore methods resolve these by bare name.
from .daily_helpers import _load_json, _save_json_atomic, _parse_time

# ===========================================================================
# PF-20 — quiet hours
# ===========================================================================

class QuietHours:
    """User-settable quiet hours, persisted in the caller's store.

    ``store_path`` is mandatory: this class never picks a path
    itself, so it can never silently write into the tree's
    ``data/``. A window is (start, end) as "HH:MM"; when end <=
    start the window wraps midnight (e.g. 22:00–07:00). With no
    window set, nothing is ever quiet.
    """

    def __init__(self, store_path):
        if not store_path:
            raise ValueError("QuietHours requires a caller-provided store_path")
        self.store_path = str(store_path)
        self._lock = threading.Lock()

    def _read(self) -> dict:
        return _load_json(self.store_path).get("quiet_hours") or {}

    def _write(self, window: dict) -> None:
        with self._lock:
            data = _load_json(self.store_path)
            data["quiet_hours"] = window
            _save_json_atomic(self.store_path, data)

    def set_window(self, start: str, end: str) -> dict:
        """Persist a quiet window. Raises ValueError on bad times."""
        if _parse_time(start) is None or _parse_time(end) is None:
            raise ValueError(f"quiet hours need HH:MM times, got {start!r} / {end!r}")
        window = {"start": str(start), "end": str(end)}
        self._write(window)
        return window

    def clear_window(self) -> None:
        with self._lock:
            data = _load_json(self.store_path)
            data.pop("quiet_hours", None)
            _save_json_atomic(self.store_path, data)

    def window(self) -> dict | None:
        data = self._read()
        if _parse_time(data.get("start", "")) is None:
            return None
        if _parse_time(data.get("end", "")) is None:
            return None
        return {"start": data["start"], "end": data["end"]}

    def is_quiet(self, now: datetime.datetime | datetime.time | None = None) -> bool:
        """True when `now` falls inside the persisted window."""
        window = self.window()
        if window is None:
            return False
        if now is None:
            now = datetime.datetime.now()
        moment = now.time() if isinstance(now, datetime.datetime) else now
        start = _parse_time(window["start"])
        end = _parse_time(window["end"])
        if start <= end:
            return start <= moment < end
        return moment >= start or moment < end


def can_speak(quiet: QuietHours, now=None, *, is_alarm: bool = False) -> bool:
    """Speak gate: alarms speak even in quiet hours; else quiet blocks."""
    if is_alarm:
        return True
    return not quiet.is_quiet(now)


def can_notify(quiet: QuietHours, now=None, *, is_alarm: bool = False) -> bool:
    """Notify gate: same rule as speak — alarms exempt, else quiet blocks."""
    if is_alarm:
        return True
    return not quiet.is_quiet(now)


def can_alarm(quiet: QuietHours, now=None) -> bool:
    """Alarms are never gated by quiet hours."""
    return True


def gate_speak(quiet: QuietHours, now=None, *, is_alarm: bool = False):
    """(allowed, reason): "" when allowed, else the honest refusal."""
    if can_speak(quiet, now, is_alarm=is_alarm):
        return True, ""
    return False, (
        "quiet hours are on — I won't speak now, except for alarms. "
        "Your message is held, not spoken.")


def gate_notify(quiet: QuietHours, now=None, *, is_alarm: bool = False):
    """(allowed, reason) for a notification."""
    if can_notify(quiet, now, is_alarm=is_alarm):
        return True, ""
    return False, (
        "quiet hours are on — notifications are held until quiet "
        "hours end, except for alarms.")


