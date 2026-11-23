from __future__ import annotations  # INTENTIONAL (skills split B21): moved code was written under postponed evaluation.
import datetime
import threading
import time
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

    def __init__(self, store_path, *, calendar_cache=None,
                 calendar_quiet_enabled=None):
        if not store_path:
            raise ValueError("QuietHours requires a caller-provided store_path")
        self.store_path = str(store_path)
        self._lock = threading.Lock()
        # Calendar-aware quiet (Stage 0 features batch 6,
        # item 14): an attached cache makes an in-progress timed
        # meeting quiet, the same as a fixed window. None means
        # NO calendar attached — the feature is off and the
        # gates behave exactly as before (the wiring layer
        # attaches the shared cache; tests attach their own).
        # calendar_quiet_enabled None means "read the toggle
        # live" (core/config calendar_quiet_enabled, default
        # ON); a bool overrides it (tests / explicit wiring).
        self._calendar_cache = calendar_cache
        self._calendar_quiet_enabled = calendar_quiet_enabled

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

    def _calendar_on(self) -> bool:
        """The calendar-quiet toggle: the instance override when
        set, else the live config value (default ON)."""
        if self._calendar_quiet_enabled is not None:
            return bool(self._calendar_quiet_enabled)
        return calendar_quiet_enabled()

    def is_calendar_quiet(self, now: datetime.datetime | datetime.time | None = None) -> bool:
        """True when calendar-quiet applies right now: the toggle
        is on, a calendar cache is attached, and a timed event is
        in progress. Never blocks, never raises — any failure
        reads as "not in a meeting", and a stale cache kicks off
        a background refresh while the gate answers from what it
        already knows."""
        try:
            if not self._calendar_on():
                return False
            cache = self._calendar_cache
            if cache is None:
                return False
            cache.refresh_async()
            return bool(cache.is_in_meeting(now))
        except Exception:  # noqa: BLE001 — a broken calendar
            return False  # check never silences the user

    def current_meeting(self, now: datetime.datetime | datetime.time | None = None):
        """The in-progress timed event dict, or None — the gate
        refusal names it. Same never-raises contract."""
        try:
            if not self._calendar_on():
                return None
            cache = self._calendar_cache
            if cache is None:
                return None
            return cache.current_event(now)
        except Exception:  # noqa: BLE001
            return None


def _is_calendar_quiet(quiet, now) -> bool:
    """The calendar half of the gates, defensive: a QuietHours
    without the calendar methods (older stubs, fakes) reads as
    "no calendar", never a raise."""
    fn = getattr(quiet, "is_calendar_quiet", None)
    if fn is None:
        return False
    try:
        return bool(fn(now))
    except Exception:  # noqa: BLE001 — a broken calendar check
        return False  # never silences the user


def _meeting_refusal(quiet, now, *, speak: bool) -> str:
    """The honest refusal when calendar-quiet is the cause:
    names the meeting and when it ends, when known."""
    meeting = None
    fn = getattr(quiet, "current_meeting", None)
    if fn is not None:
        try:
            meeting = fn(now)
        except Exception:  # noqa: BLE001 — the generic line
            meeting = None  # below still says the truth
    title = ""
    until = ""
    if isinstance(meeting, dict):
        title = str(meeting.get("title") or "").strip()
        end = meeting.get("end")
        if isinstance(end, datetime.datetime):
            until = end.strftime("%H:%M")
    named = f" — you're in a meeting" + (
        f" ({title!r}" + (f", until {until}" if until else "")
        + ")" if title else "")
    if speak:
        return (f"quiet hours are on{named} — I won't speak now, "
                "except for alarms. Your message is held, not "
                "spoken.")
    return (f"quiet hours are on{named} — notifications are held "
            "until it ends, except for alarms.")


def can_speak(quiet: QuietHours, now=None, *, is_alarm: bool = False) -> bool:
    """Speak gate: alarms speak even in quiet hours; else quiet
    blocks — the fixed window OR an in-progress timed meeting
    (calendar-aware quiet, Stage 0 features batch 6)."""
    if is_alarm:
        return True
    if quiet.is_quiet(now):
        return False
    return not _is_calendar_quiet(quiet, now)


def can_notify(quiet: QuietHours, now=None, *, is_alarm: bool = False) -> bool:
    """Notify gate: same rule as speak — alarms exempt, else
    quiet blocks (fixed window or in-progress meeting)."""
    if is_alarm:
        return True
    if quiet.is_quiet(now):
        return False
    return not _is_calendar_quiet(quiet, now)


def can_alarm(quiet: QuietHours, now=None) -> bool:
    """Alarms are never gated by quiet hours."""
    return True


def gate_speak(quiet: QuietHours, now=None, *, is_alarm: bool = False):
    """(allowed, reason): "" when allowed, else the honest refusal."""
    if can_speak(quiet, now, is_alarm=is_alarm):
        return True, ""
    if _is_calendar_quiet(quiet, now):
        return False, _meeting_refusal(quiet, now, speak=True)
    return False, (
        "quiet hours are on — I won't speak now, except for alarms. "
        "Your message is held, not spoken.")


def gate_notify(quiet: QuietHours, now=None, *, is_alarm: bool = False):
    """(allowed, reason) for a notification."""
    if can_notify(quiet, now, is_alarm=is_alarm):
        return True, ""
    if _is_calendar_quiet(quiet, now):
        return False, _meeting_refusal(quiet, now, speak=False)
    return False, (
        "quiet hours are on — notifications are held until quiet "
        "hours end, except for alarms.")


# ===========================================================================
# PF-20 — calendar-aware quiet (Stage 0 features batch 6, item 14)
# ===========================================================================

_CALENDAR_QUIET_TTL_S = 300.0


def calendar_quiet_enabled() -> bool:
    """The calendar-quiet toggle, read live from the app config
    (ConfigSpec calendar_quiet_enabled, default ON — the
    feature's whole point is meetings going quiet on their
    own). An unreadable config reads as OFF: a broken config
    must never silence the user."""
    try:
        from .config import config as _app_config

        return bool(getattr(_app_config, "calendar_quiet_enabled",
                            True))
    except Exception:  # noqa: BLE001 — unreadable config: the
        return False  # feature stays off


def _local_tzinfo():
    return datetime.datetime.now().astimezone().tzinfo


def _as_local_aware(value):
    """A datetime as tz-aware local time, or None. Naive values
    are read as local (the calendar seam returns aware, so this
    is the defensive path, not the live one)."""
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=_local_tzinfo())
        return value.astimezone(_local_tzinfo())
    return None


def _meeting_moment(now) -> datetime.datetime:
    """`now` as a tz-aware local datetime: None means right now;
    a bare time means today at that time."""
    if now is None:
        return datetime.datetime.now().astimezone()
    if isinstance(now, datetime.datetime):
        aware = _as_local_aware(now)
        return aware if aware is not None \
            else datetime.datetime.now().astimezone()
    if isinstance(now, datetime.time):
        today = datetime.datetime.now().astimezone()
        return today.replace(hour=now.hour, minute=now.minute,
                             second=now.second,
                             microsecond=now.microsecond)
    return datetime.datetime.now().astimezone()


def _default_calendar_fetch() -> list:
    """Today's timed events from the calendar seam — the
    refresh's live fetch. Never raises; [] on any failure.

    Owner boundary (L-24): in an unproven live-origin session
    this reads nothing — the fetch is skipped and the gate
    behaves as if no calendar exists. With no live session in
    flight (provenance "unknown") the app itself is asking, so
    the fetch runs; a user-started (proven) session fetches too.
    """
    try:
        from .funnel.tools_gate import (
            session_owner_proven, session_provenance)

        if not session_owner_proven() \
                and session_provenance() != "unknown":
            return []
    except Exception:  # noqa: BLE001 — unreadable owner state:
        return []  # no fetch (fail-closed)
    try:
        from .google_services_calendar import todays_timed_events

        return list(todays_timed_events() or [])
    except Exception:  # noqa: BLE001 — not connected, failed
        return []  # read: no calendar, never a raise


class CalendarQuietCache:
    """Last-known calendar events for the quiet gate.

    The gate consults ONLY this cache — never the network, so
    ``can_notify`` / ``can_speak`` always answer fast. A stale
    cache kicks off a background (daemon-thread) refresh and the
    gate answers from what it already knows; the refresh stores
    whatever the fetch returned, [] on any failure. ``set_events``
    is the test seam (injects events directly, no fetch).
    Events are {"id", "title", "start", "end", "location",
    "all_day"} dicts with tz-aware datetimes; only timed events
    with a parseable end count as in-progress (all-day entries
    are skipped here too, so a stale or hand-built cache can
    never silence a whole day).
    """

    def __init__(self, ttl_seconds: float = _CALENDAR_QUIET_TTL_S):
        self._ttl = float(ttl_seconds)
        self._lock = threading.Lock()
        self._events: list = []
        self._fetched_at = None  # monotonic seconds, None = never
        self._refreshing = False

    def reset(self) -> None:
        """Test seam: forget everything (never fetched)."""
        with self._lock:
            self._events = []
            self._fetched_at = None
            self._refreshing = False

    def set_events(self, events) -> None:
        """Test seam: inject events directly; counts as a fresh
        fetch (no background refresh until the TTL passes)."""
        with self._lock:
            self._events = [dict(e) for e in (events or [])
                            if isinstance(e, dict)]
            self._fetched_at = time.monotonic()

    def refresh(self, fetch_fn=None) -> None:
        """Synchronous refresh via fetch_fn (default: the live
        calendar fetch). Never raises; stores [] on failure."""
        try:
            fn = fetch_fn or _default_calendar_fetch
            events = fn() or []
        except Exception:  # noqa: BLE001 — a failed fetch is
            events = []  # "no calendar", never a raise
        with self._lock:
            self._events = [dict(e) for e in events
                            if isinstance(e, dict)]
            self._fetched_at = time.monotonic()
            self._refreshing = False

    def refresh_async(self, fetch_fn=None) -> None:
        """Kick off a background refresh when the cache is stale
        (never fetched, or older than the TTL) and none is in
        flight. Returns immediately — the gate never waits."""
        with self._lock:
            if self._refreshing:
                return
            if self._fetched_at is not None and \
                    time.monotonic() - self._fetched_at < self._ttl:
                return
            self._refreshing = True
        thread = threading.Thread(
            target=self._refresh_guarded, args=(fetch_fn,),
            daemon=True)
        thread.start()

    def _refresh_guarded(self, fetch_fn) -> None:
        try:
            self.refresh(fetch_fn)
        except Exception:  # noqa: BLE001 — refresh() already
            with self._lock:  # never raises; this only clears
                self._refreshing = False  # the flag on the
            # impossible path so a later consult can retry.

    def current_event(self, now=None):
        """The in-progress timed event, or None. Pure computation
        over the cached list — no fetch, no thread, no raise."""
        try:
            moment = _meeting_moment(now)
        except Exception:  # noqa: BLE001 — an unparseable `now`
            return None  # reads as "not in a meeting"
        try:
            with self._lock:
                events = list(self._events)
        except Exception:  # noqa: BLE001
            return None
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("all_day"):
                continue
            start = _as_local_aware(event.get("start"))
            end = _as_local_aware(event.get("end"))
            if start is None or end is None:
                continue
            if start <= moment < end:
                return event
        return None

    def is_in_meeting(self, now=None) -> bool:
        """True when a cached timed event is in progress now."""
        return self.current_event(now) is not None


_CALENDAR_QUIET_CACHE = CalendarQuietCache()


def calendar_quiet_cache() -> CalendarQuietCache:
    """The shared calendar-quiet cache (the wiring layer
    attaches this one to every live QuietHours)."""
    return _CALENDAR_QUIET_CACHE


def reset_calendar_quiet_cache() -> None:
    """Test seam: forget the shared cache's events."""
    _CALENDAR_QUIET_CACHE.reset()


