"""
Proactive Scheduler for Voice AI Assistant.
Manages reminders and time-aware proactive check-ins.

Scheduler contract (Scheduler batch — replaces the old in-memory-only
behaviour; the old module docstring said reminders were lost on quit,
which is no longer true):

* TIME. All datetimes are normalized to aware UTC at the scheduler
  boundary and stored/compared as UTC. A naive datetime is assumed
  to be in the scheduler's timezone (system local time by default,
  overridable in the constructor for tests). An aware datetime is
  converted to UTC. Naive and aware values are therefore never
  compared against each other, and an aware datetime can never
  raise TypeError inside the scheduler. Displayed fire times
  (get_all_reminders) are rendered back in the scheduler timezone,
  so what the user sees is still local wall time.
* DST POLICY (repeats advance in local wall time, not by absolute
  24-hour steps, so a daily 08:00 stays 08:00 across a DST change):
  ambiguous local times (fall-back, the hour happens twice) fire
  on the FIRST occurrence (fold=0); nonexistent local times
  (spring-forward, the hour is skipped) shift forward by the DST
  gap (e.g. 02:30 -> 03:30 when 02:00-03:00 does not exist).
  Monthly repeats clamp to the end of short months, and the
  clamped day becomes the schedule going forward (Jan 31 ->
  Feb 28 -> Mar 28, not Mar 31).
* NEXT OCCURRENCE. A repeating reminder that becomes due fires
  AT MOST ONCE for the missed window. Its next occurrence is
  computed arithmetically from the last scheduled occurrence
  (day/week/month arithmetic in wall time), never by stepping
  one interval at a time through the past firing each step.
* BRIEFINGS (GAPS-26, S-V4 — removed from this module). The
  morning briefing used to be implemented twice: this scheduler's
  retired briefing method (a fixed-string kind-briefing reminder)
  and the GUI QTimer _check_briefing (gui/window_memory_routines.py),
  which asks the live session for the real briefing. The single
  implementation is now the QTimer path, driven by the presence
  settings (presence/briefing_enabled + presence/briefing_time);
  this module holds no briefing state. A briefing record
  found in an old store is migrated to those settings at GUI
  startup (migrate_legacy_briefing_records, called from
  _start_background_services before scheduler.start()); any
  record that reaches from_record un-migrated is dropped, so the
  retired fixed string can never fire again.
* PURGE + RETENTION. A fired one-shot reminder is removed from
  the active store as soon as it fires, so get_all_reminders()
  never lists it again. Fired and missed records are kept in
  capped histories (MAX_HISTORY = 100 entries each; the oldest
  entry is dropped when the cap is exceeded) so surfaces can
  still report what fired or was missed today.
* PERSISTENCE + MISSED-EVENT POLICY. Reminders persist to
  data/scheduler_reminders.json (version 1, atomic temp-file +
  os.replace, writes never raise into call paths) and reload
  when the scheduler starts. The first mutation before start
  lazily loads first, so mutating before start() can never
  wipe previously persisted records by writing in-memory
  state alone. On reload, events whose fire time
  passed while the app was down are handled as follows:
  one-shots missed within MISSED_GRACE (24 hours) are left due
  and fire on the first tick after start; one-shots missed
  beyond the grace window are dropped and recorded in the
  missed history (surfaces should report them via
  get_missed_events()); repeating reminders are rescheduled
  to their next future occurrence without firing for the
  missed windows. A load never discards reminders added
  in-memory before start (label collision: the in-memory
  record wins).
* CALLBACKS NEVER HOLD THE LOCK. The tick collects due
  reminders and applies state changes under the lock, releases
  it, persists, and only then invokes on_trigger. A callback
  may freely call back into the scheduler (get_all_reminders,
  cancel_reminder, add_reminder) without deadlocking.
  Persistence writes are best-effort: a failed write is logged
  and the record stays live in memory until the next mutation
  retries the save.
* START/STOP. start() is idempotent (a second call is a no-op
  returning the running state) and loads from disk; stop() is
  idempotent and joins the loop thread (unless called from
  the loop thread itself, where it only flags the stop).
  start-after-stop works and spawns exactly one loop thread.

Scheduled *routines* are a separate, persistent counterpart
(data/routines.json, core/brain/planner.py) — do not blur the two.
"""

import calendar
import datetime
import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = datetime.timezone.utc

# Fired/missed history retention: oldest entries are dropped past this.
MAX_HISTORY = 100
# One-shots missed by more than this while the app was down are
# reported as missed and dropped instead of firing late.
MISSED_GRACE = datetime.timedelta(hours=24)

_STORE_FILENAME = "scheduler_reminders.json"
_STORE_VERSION = 1

_REPEAT_KINDS = ("daily", "weekly", "monthly")


def _default_tz() -> datetime.tzinfo:
    """System local timezone, falling back to UTC if undetectable."""
    try:
        tz = datetime.datetime.now().astimezone().tzinfo
    except Exception:  # noqa: BLE001 — any failure means "unknown local tz"
        tz = None
    return tz if tz is not None else UTC


def _resolve_wall_time(wall_naive: datetime.datetime, tz: datetime.tzinfo) -> datetime.datetime:
    """Naive local wall time -> aware UTC, under the DST policy.

    Ambiguous wall times resolve to the FIRST occurrence (fold=0);
    nonexistent wall times shift forward by the DST gap. Always
    returns an aware UTC datetime.
    """
    dt0 = wall_naive.replace(tzinfo=tz, fold=0)
    utc0 = dt0.astimezone(UTC)
    if utc0.astimezone(tz).replace(tzinfo=None) == wall_naive:
        return utc0
    dt1 = wall_naive.replace(tzinfo=tz, fold=1)
    utc1 = dt1.astimezone(UTC)
    if utc1.astimezone(tz).replace(tzinfo=None) == wall_naive:
        # Ambiguous (fall-back): first occurrence wins.
        return utc0
    # Nonexistent (spring-forward): shift forward by the gap.
    gap = utc0 - utc1
    return _resolve_wall_time(wall_naive + gap, tz)


def _to_utc(dt: datetime.datetime, tz: datetime.tzinfo) -> datetime.datetime:
    """Scheduler-boundary normalization: naive -> scheduler tz wall
    time (DST-resolved), aware -> converted. Always aware UTC."""
    if dt.tzinfo is None:
        return _resolve_wall_time(dt.replace(tzinfo=None), tz)
    return dt.astimezone(UTC)


def _add_months(wall_naive: datetime.datetime, months: int) -> datetime.datetime:
    """Wall-time month arithmetic, clamping the day to month end."""
    total = (wall_naive.year * 12 + (wall_naive.month - 1)) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(wall_naive.day, calendar.monthrange(year, month)[1])
    return wall_naive.replace(year=year, month=month, day=day)


def _advance_wall(wall_naive: datetime.datetime, kind: str) -> datetime.datetime:
    """One repeat interval forward in wall time."""
    if kind == "weekly":
        return wall_naive + datetime.timedelta(weeks=1)
    if kind == "monthly":
        return _add_months(wall_naive, 1)
    return wall_naive + datetime.timedelta(days=1)


def _next_future_occurrence(last_utc: datetime.datetime, now_utc: datetime.datetime,
                            kind: str, tz: datetime.tzinfo) -> datetime.datetime:
    """Next scheduled occurrence strictly after now_utc, computed
    arithmetically from the last scheduled occurrence (never by
    stepping through past firings). Wall-time arithmetic + the
    DST policy; the guard loop below is a safety net for
    pathological DST resolutions only and never runs on the
    normal path (the arithmetic candidate is already future)."""
    if last_utc > now_utc:
        return last_utc
    last_wall = last_utc.astimezone(tz).replace(tzinfo=None)
    now_wall = now_utc.astimezone(tz).replace(tzinfo=None)
    if kind == "weekly":
        weeks = (now_wall.date() - last_wall.date()).days // 7
        cand = last_wall + datetime.timedelta(weeks=weeks)
        if cand <= now_wall:
            cand += datetime.timedelta(weeks=1)
    elif kind == "monthly":
        months = ((now_wall.year - last_wall.year) * 12
                  + (now_wall.month - last_wall.month))
        cand = _add_months(last_wall, months)
        if cand <= now_wall:
            cand = _add_months(cand, 1)
    else:  # daily
        days = (now_wall.date() - last_wall.date()).days
        cand = last_wall + datetime.timedelta(days=days)
        if cand <= now_wall:
            cand += datetime.timedelta(days=1)
    resolved = _resolve_wall_time(cand, tz)
    guard = 0
    while resolved <= now_utc and guard < 3:
        cand = _advance_wall(cand, kind)
        resolved = _resolve_wall_time(cand, tz)
        guard += 1
    return resolved


def _fmt_local(dt_utc: datetime.datetime, tz: datetime.tzinfo) -> str:
    return dt_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def _append_capped(history: list[dict], record: dict) -> None:
    history.append(record)
    while len(history) > MAX_HISTORY:
        del history[0]


class ScheduledReminder:
    def __init__(self, label: str, fire_at: datetime.datetime, message: str, repeat: bool = False,
                 repeat_kind: str | None = None, kind: str = "reminder"):
        # fire_at is ALWAYS aware UTC (normalized by the scheduler).
        self.label = label
        self.fire_at = fire_at
        self.message = message
        self.repeat = repeat
        self.repeat_kind = repeat_kind if repeat else None
        self.kind = kind
        # Kept for compatibility with surfaces that read it; active
        # records are never "fired" (fired one-shots are purged,
        # repeats are rescheduled, so the flag never lingers).
        self.fired = False

    def message_for(self, fire_utc: datetime.datetime, tz: datetime.tzinfo) -> str:
        return self.message

    def to_record(self) -> dict:
        return {
            "label": self.label,
            "fire_at": self.fire_at.isoformat(),
            "message": self.message,
            "repeat": self.repeat,
            "repeat_kind": self.repeat_kind,
            "kind": self.kind,
        }

    @classmethod
    def from_record(cls, rec: dict) -> "ScheduledReminder | None":
        """Defensive parse: any malformed record is skipped (None),
        never raised — a corrupt store must not kill startup."""
        try:
            if not isinstance(rec, dict):
                return None
            label = rec.get("label")
            fire_raw = rec.get("fire_at")
            if not isinstance(label, str) or not label or not isinstance(fire_raw, str):
                return None
            fire_at = datetime.datetime.fromisoformat(fire_raw)
            if fire_at.tzinfo is None:
                # Store format is always aware UTC; a naive value
                # can only come from a hand-edited file — read it
                # as UTC rather than guessing a timezone.
                fire_at = fire_at.replace(tzinfo=UTC)
            else:
                fire_at = fire_at.astimezone(UTC)
            message = rec.get("message", "")
            if not isinstance(message, str):
                return None
            repeat = bool(rec.get("repeat", False))
            repeat_kind = rec.get("repeat_kind")
            if repeat and repeat_kind not in _REPEAT_KINDS:
                repeat_kind = "daily"
            kind = rec.get("kind", "reminder")
            if kind == "briefing":
                # GAPS-26 (S-V4): the scheduler-side briefing is
                # retired — its time is preserved by
                # migrate_legacy_briefing_records at GUI startup
                # (before this loader ever runs). A record that
                # reaches here un-migrated is dropped, never fired:
                # the retired fixed string must never speak again.
                return None
            if kind != "reminder":
                kind = "reminder"
            return cls(
                label=label,
                fire_at=fire_at,
                message=message,
                repeat=repeat,
                repeat_kind=repeat_kind,
                kind=kind,
            )
        except Exception:  # noqa: BLE001 — corrupt record: skip it
            return None


def migrate_legacy_briefing_records(store_path=None):
    """One-time migration for the retired scheduler-side briefing
    (GAPS-26, S-V4): pull briefing records out of the
    reminders store and return the briefing time the user had
    chosen, as (hour, minute) — or None when no such record
    exists. The store is rewritten without the briefing records
    (atomic tmp+replace, same pattern as _save); when nothing
    is found the file is left untouched. Never raises: a missing
    or unreadable store reads as "nothing to migrate", and a
    failed rewrite is logged (the records are still dropped by
    from_record on load, so the retired fixed string can never
    fire — only the preserved time would be lost, and the GUI
    reports that). The caller (GUI startup, which owns the
    QSettings store) writes the returned time into the presence
    keys presence/briefing_enabled + presence/briefing_time.
    """
    path = (Path(store_path) if store_path is not None else
            (Path(__file__).resolve().parent.parent
             / "data" / _STORE_FILENAME))
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 — unreadable store: nothing to migrate
        print(f"[Scheduler] briefing migration skipped (store unreadable): {exc}")
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("reminders", [])
    if not isinstance(raw, list):
        return None
    briefings = [r for r in raw
                 if isinstance(r, dict) and r.get("kind") == "briefing"]
    if not briefings:
        return None
    first = briefings[0]
    try:
        hour = int(first.get("briefing_hour", 8))
        minute = int(first.get("briefing_minute", 0))
    except (TypeError, ValueError):
        hour, minute = 8, 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        hour, minute = 8, 0
    kept = [r for r in raw
            if not (isinstance(r, dict) and r.get("kind") == "briefing")]
    data["reminders"] = kept
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except Exception as exc:  # noqa: BLE001 — best-effort, like _save
        print(f"[Scheduler] briefing migration could not rewrite the store: {exc}")
        return None
    return (hour, minute)


class ProactiveScheduler:
    def __init__(self, store_path: str | Path | None = None, tz: datetime.tzinfo | None = None,
                 now_fn: Callable[[], datetime.datetime] | None = None):
        self._reminders: list[ScheduledReminder] = []
        self._fired_history: list[dict] = []
        self._missed_history: list[dict] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.on_trigger: Callable[[str, str], None] | None = None  # (label, message)
        self._tz = tz if tz is not None else _default_tz()
        self._now_fn = now_fn
        self._loaded = False
        if store_path is not None:
            self._store_path = Path(store_path)
        else:
            self._store_path = (Path(__file__).resolve().parent.parent
                                / "data" / _STORE_FILENAME)

    # -- time ---------------------------------------------------------
    def _now_utc(self) -> datetime.datetime:
        if self._now_fn is not None:
            now = self._now_fn()
            if now.tzinfo is None:
                # Injectable clocks in tests return aware UTC; a
                # naive value is read as UTC (documented).
                return now.replace(tzinfo=UTC)
            return now.astimezone(UTC)
        return datetime.datetime.now(UTC)

    # -- labels -------------------------------------------------------
    def _unique_label(self, label: str) -> str:
        """`label`, or the first free `label-2`, `label-3`, ...
        Caller must hold self._lock.

        Callers build labels from second-precision timestamps
        (e.g. reminder_093015), so two reminders created within
        the same second would otherwise share a label — and
        cancel_reminder(label) would then kill both."""
        taken = {r.label for r in self._reminders}
        if label not in taken:
            return label
        n = 2
        while f"{label}-{n}" in taken:
            n += 1
        return f"{label}-{n}"

    # -- persistence --------------------------------------------------
    def _snapshot_payload(self) -> dict:
        with self._lock:
            return {
                "version": _STORE_VERSION,
                "reminders": [r.to_record() for r in self._reminders],
                "fired_history": list(self._fired_history),
                "missed_history": list(self._missed_history),
            }

    def _save(self) -> None:
        """Atomic tmp+replace save. Never raises into call paths:
        a failed write is logged and retried on the next mutation.
        The first save lazily loads (merging any records written
        by a previous process) so a mutation before start() can
        never wipe the store by writing only in-memory state."""
        try:
            if not self._loaded:
                self._load_and_apply_policy(self._now_utc())
            payload = self._snapshot_payload()
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            # F-01 audit: the temp file is mkstemp-unique, not the
            # old fixed ".tmp" name — the snapshot is taken under
            # the lock, but _save itself runs on the tick thread
            # AND on user-action threads, so two saves could
            # interleave in one shared temp file and promote a
            # torn store. Unique temp names close that interleave;
            # the in-memory list stays the single source of truth,
            # so no stale-snapshot loss is possible in-process.
            fd, tmp_name = tempfile.mkstemp(
                prefix=self._store_path.name + ".",
                dir=str(self._store_path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=1)
                os.replace(tmp_name, self._store_path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except Exception as e:  # noqa: BLE001 — persistence is best-effort
            print(f"[Scheduler] persist failed (records stay live in memory): {e}")

    def _read_store(self) -> dict:
        """Raw store parse, outside the lock. Any failure (missing
        file, bad JSON, wrong shape) reads as an empty store."""
        try:
            with open(self._store_path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            return data
        except Exception as e:  # noqa: BLE001 — missing/corrupt store: empty
            if not isinstance(e, FileNotFoundError):
                print(f"[Scheduler] store unreadable, starting empty: {e}")
            return {}

    def _load_and_apply_policy(self, now_utc: datetime.datetime) -> None:
        """Load persisted records and apply the missed-event policy.
        Only LOADED records are policy-transformed (in-memory
        records added before start keep normal due semantics).
        Label collision: the in-memory record wins, the loaded
        one is skipped. Runs at most once per instance (start()
        or the first lazy load in _save()); a second call is a
        no-op, so histories are never appended twice."""
        if self._loaded:
            return
        self._loaded = True
        data = self._read_store()
        raw = data.get("reminders", [])
        if not isinstance(raw, list):
            raw = []
        loaded: list[ScheduledReminder] = []
        for rec in raw:
            r = ScheduledReminder.from_record(rec)
            if r is not None:
                loaded.append(r)
        fired_hist = data.get("fired_history", [])
        missed_hist = data.get("missed_history", [])
        with self._lock:
            if isinstance(fired_hist, list):
                for rec in fired_hist:
                    if isinstance(rec, dict):
                        _append_capped(self._fired_history, rec)
            if isinstance(missed_hist, list):
                for rec in missed_hist:
                    if isinstance(rec, dict):
                        _append_capped(self._missed_history, rec)
            taken = {r.label for r in self._reminders}
            for r in loaded:
                if r.label in taken:
                    continue
                taken.add(r.label)
                if r.fire_at <= now_utc:
                    if r.repeat:
                        # Missed windows never fire: reschedule
                        # straight to the next future occurrence.
                        r.fire_at = _next_future_occurrence(
                            r.fire_at, now_utc, r.repeat_kind or "daily", self._tz)
                        self._reminders.append(r)
                    elif now_utc - r.fire_at <= MISSED_GRACE:
                        # Within grace: left due, fires on the
                        # first tick after start.
                        self._reminders.append(r)
                    else:
                        # Beyond grace: dropped, reported missed.
                        _append_capped(self._missed_history, {
                            "label": r.label,
                            "fire_at": _fmt_local(r.fire_at, self._tz),
                            "missed_at": _fmt_local(now_utc, self._tz),
                            "reason": "expired while the app was down",
                            "message": r.message_for(r.fire_at, self._tz),
                        })
                else:
                    self._reminders.append(r)

    # -- public API ---------------------------------------------------
    def add_reminder(self, label: str, fire_at: datetime.datetime, message: str,
                     repeat: bool = False, repeat_kind: str = "daily") -> str:
        """Queue a reminder (persisted — survives restarts).
        Returns the label the reminder was stored under.

        Raises ValueError when fire_at is not in the future: a
        reminder for a time that has already passed is a caller
        bug (typically a non-positive duration), and quietly
        queueing it would make it fire on the next tick while the
        caller reports a past time as if it were scheduled.
        Naive fire_at is read as scheduler-local time; aware
        fire_at is converted to UTC (never a TypeError).
        """
        if repeat and repeat_kind not in _REPEAT_KINDS:
            raise ValueError(f"unknown repeat_kind {repeat_kind!r} "
                             f"(expected one of {_REPEAT_KINDS})")
        fire_utc = _to_utc(fire_at, self._tz)
        now_utc = self._now_utc()
        if fire_utc <= now_utc:
            raise ValueError(
                f"reminder '{label}' would fire at "
                f"{_fmt_local(fire_utc, self._tz)}, which is not in "
                "the future — give a positive number of minutes from now."
            )
        with self._lock:
            label = self._unique_label(label)
            self._reminders.append(ScheduledReminder(
                label, fire_utc, message, repeat=repeat,
                repeat_kind=repeat_kind if repeat else None))
        self._save()
        print(f"[Scheduler] Reminder set: '{label}' at {_fmt_local(fire_utc, self._tz)} "
              "(persisted — survives restarts)")
        return label

    def get_all_reminders(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "label": r.label,
                    "fire_at": _fmt_local(r.fire_at, self._tz),
                    "message": r.message_for(r.fire_at, self._tz),
                    "repeat": r.repeat,
                    "repeat_kind": r.repeat_kind,
                    "kind": r.kind,
                    "fired": r.fired,
                    # Persisted to disk on every mutation; a failed
                    # write leaves the record live in memory until
                    # the next mutation retries the save.
                    "persistent": True,
                }
                for r in self._reminders
            ]

    def get_fired_history(self) -> list[dict]:
        """Fired one-shot records (capped at MAX_HISTORY, oldest
        dropped). Fired records live here, not in the active
        store — get_all_reminders() never lists them."""
        with self._lock:
            return list(self._fired_history)

    def get_missed_events(self) -> list[dict]:
        """One-shots dropped as expired-while-down (capped at
        MAX_HISTORY). Surfaces should report these to the user."""
        with self._lock:
            return list(self._missed_history)

    def cancel_reminder(self, label: str) -> bool:
        with self._lock:
            before = len(self._reminders)
            self._reminders = [r for r in self._reminders if r.label != label]
            removed = len(self._reminders) < before
        if removed:
            self._save()
        return removed

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Idempotent: a second call while running is a no-op
        returning True. Loads persisted reminders (with the
        missed-event policy) before spawning the loop thread."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
        self._load_and_apply_policy(self._now_utc())
        self._save()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True,
                                            name="ProactiveScheduler")
            self._thread.start()
            return True

    def stop(self) -> None:
        """Idempotent. Joins the loop thread (up to 5 s) unless
        called from the loop thread itself, where it only flags
        the stop — joining your own thread would deadlock."""
        self._running = False
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() \
                and threading.current_thread() is not thread:
            thread.join(timeout=5.0)
        if thread is not None and not thread.is_alive():
            self._thread = None

    # -- tick ---------------------------------------------------------
    def _tick(self, now_utc: datetime.datetime | None = None) -> None:
        """One scheduling pass. State changes happen under the
        lock; persistence happens next; callbacks run LAST and
        OUTSIDE the lock, so a callback may freely call back
        into the scheduler without deadlocking."""
        if now_utc is None:
            now_utc = self._now_utc()
        due: list[tuple[str, str]] = []
        with self._lock:
            kept: list[ScheduledReminder] = []
            for reminder in self._reminders:
                if reminder.fire_at > now_utc:
                    kept.append(reminder)
                    continue
                # Due: fire at most once for this window.
                message = reminder.message_for(reminder.fire_at, self._tz)
                if reminder.repeat:
                    reminder.fire_at = _next_future_occurrence(
                        reminder.fire_at, now_utc,
                        reminder.repeat_kind or "daily", self._tz)
                    reminder.fired = False
                    kept.append(reminder)
                else:
                    # One-shot: purge from the active store now;
                    # the record lives on in fired history.
                    _append_capped(self._fired_history, {
                        "label": reminder.label,
                        "fire_at": _fmt_local(reminder.fire_at, self._tz),
                        "fired_at": _fmt_local(now_utc, self._tz),
                        "message": message,
                        "fired": True,
                    })
                due.append((reminder.label, message))
            self._reminders = kept
        if due:
            self._save()
        for label, message in due:
            if self.on_trigger:
                # A throwing listener (a dead GUI bridge, say)
                # must NOT kill this thread: one bad callback can
                # never be allowed to silence every future
                # reminder. Log it and carry on.
                try:
                    self.on_trigger(label, message)
                except Exception as e:  # noqa: BLE001
                    print(f"[Scheduler] on_trigger for "
                          f"'{label}' failed: {e}")

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001 — the loop must outlive any single tick
                print(f"[Scheduler] tick failed: {e}")
            self._stop_event.wait(1.0)


scheduler = ProactiveScheduler()
