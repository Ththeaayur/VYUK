"""Forgiving daily count + ONE daily nudge (RET-1).

Plain words: show "days used this week: N/7" and say one useful
thing each morning. Never a streak that breaks, never a shame
message, never more than one nudge a day.

The count is DERIVED from the activity log at read time — a day
counts as used when at least one voice-driven funnel success is
recorded there (a ``voice_task | success`` marker line, written by
the funnel while a voice turn is active — see core/voice_origin.py).
Opening the app alone never marks a day used, and the store holds
no "used" flags, so the count can never disagree with the record.

The store (data/daily_count.json) holds ONLY preferences + nudge
state: opt-in / opt-out, the chosen time, the last nudge date, its
outcome, and the last wording template used. There is NO
reset-to-zero path anywhere: the only thing that starts a week at
0/7 is the Monday rollover, and the display always shows last week
alongside this week, so Monday never reads as lost progress.

The nudge is a job kind in the agent-job engine (like the briefing
job — kind "daily_nudge", manifest-gated, fired by
core/scheduled_agents.run_job), rendered at FIRE time from the
three pinned sources (reminders due today + routines due today +
calendar events today — counts only, never titles or bodies). It is
NOT an add_reminder with a baked message.

SEAM NOTE (RET-1 re-verification, post-arc): the plan names
core/scheduler.py as the scheduling seam, but the shape it
describes — "its own job kind (like the briefing job), rendered at
fire time, never baked" — no longer lives there. ProactiveScheduler
coerces any non-"reminder" kind back to "reminder" on load
(core/scheduler.py, from_record) and fires baked message strings
to the GUI reminder UI; the scheduler-side briefing kind was
retired by GAPS-26 (S-V4) and the briefing job now lives in the
job engine. The nudge therefore rides the same engine as the
briefing job: one scheduling seam, no second QTimer, no new
thread. The validity window (B-101) and claim-then-speak (B-102)
are enforced in the fire function itself, so they hold on either
engine.

What this module never does: no charging logic (ruling 34 is open),
no streaks/badges/points/leaderboards/variable rewards/social, no
change to the scheduler's missed-event policy, the digest's 7am
default, QuietHours defaults, or the voice loop. The nudge is a
caller of the existing machinery, never a second path around it.
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIMEZONE_NAME = "Asia/Kolkata"
TIMEZONE = ZoneInfo(TIMEZONE_NAME)
DEFAULT_NUDGE_TIME = "07:00"
NUDGE_VALIDITY_HOURS = 2
MARKER_EVENT = "voice_task"
MARKER_OUTCOME = "success"
NUDGE_EVENT = "daily_nudge"
STORE_FILENAME = "daily_count.json"
STORE_VERSION = 1
NUDGE_JOB_ID = "daily_nudge"
NUDGE_JOB_NAME = "Daily nudge"
NUDGE_MISFIRE_GRACE_SECONDS = 2 * 60 * 60  # 2h: matches the validity window

# Fixed wording templates. Each has at most one {n} slot; no
# free-text field from data ever enters a spoken nudge.
_TEMPLATES = {
    "all_clear": "Nothing needs you today.",
    "one": "One thing needs you today.",
    "many": "{n} things need you today.",
}

_DEFAULT_STATE = {
    "version": STORE_VERSION,
    "timezone": TIMEZONE_NAME,
    "opted_in": False,
    "opted_out": False,
    "nudge_time": DEFAULT_NUDGE_TIME,
    "last_nudge_date": None,
    "last_nudge_outcome": None,
    "last_template": None,
}

# ---------------------------------------------------------------------------
# Store (atomic temp-file + os.replace, same shape as the scheduler store)
# ---------------------------------------------------------------------------


def default_store_path() -> Path:
    """data/daily_count.json under the tree root."""
    return Path(__file__).resolve().parent.parent / "data" / STORE_FILENAME


def default_activity_path() -> Path:
    """data/activity.log under the tree root."""
    return Path(__file__).resolve().parent.parent / "data" / "activity.log"


class DailyCountStore:
    """The retention store: preferences + nudge state ONLY.

    Never holds "used" flags (the count is derived from the
    activity log). Every write is atomic (mkstemp-unique temp +
    os.replace) and happens under one lock, so the claim-then-speak
    guard in fire_nudge can rely on read-modify-write being one
    locked step. A missing or corrupt file reads as the default
    state, never raises.
    """

    def __init__(self, store_path=None):
        self._path = Path(store_path) if store_path is not None \
            else default_store_path()
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> dict:
        """The stored state (a copy), defaulted for missing keys."""
        with self._lock:
            return self._read_locked()

    def _read_locked(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
        except (FileNotFoundError, ValueError, OSError):
            return dict(_DEFAULT_STATE)
        if not isinstance(raw, dict):
            return dict(_DEFAULT_STATE)
        state = dict(_DEFAULT_STATE)
        for key in _DEFAULT_STATE:
            if key in raw:
                state[key] = raw[key]
        return state

    def _write_locked(self, state: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=self._path.name + ".", dir=str(self._path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=1, sort_keys=True)
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def update(self, **fields) -> dict:
        """Set fields (unknown keys refused) and persist. Returns the state."""
        with self._lock:
            state = self._read_locked()
            for key, value in fields.items():
                if key not in _DEFAULT_STATE:
                    raise ValueError("unknown daily-count field %r" % key)
                state[key] = value
            self._write_locked(state)
            return state

    def claim_nudge_day(self, day: str, outcome: str) -> bool:
        """Atomically claim `day` ("YYYY-MM-DD") for the nudge.

        One locked read-modify-write: when the day is already
        claimed (any outcome), nothing is written and False is
        returned — so two scheduler threads firing at once can
        never both speak. When the day is free, the date + outcome
        are written and True is returned. This is the B-102 guard.
        """
        with self._lock:
            state = self._read_locked()
            if state.get("last_nudge_date") == day:
                return False
            state["last_nudge_date"] = day
            state["last_nudge_outcome"] = outcome
            self._write_locked(state)
            return True

    def set_outcome(self, outcome: str) -> dict:
        """Record the outcome of today's claimed nudge (spoken/failed)."""
        return self.update(last_nudge_outcome=outcome)


def _parse_hhmm(text):
    """(hour, minute) for an "HH:MM" string, else None."""
    from .daily_helpers import _parse_time

    parsed = _parse_time(text) if isinstance(text, str) else None
    if parsed is None:
        return None
    return parsed.hour, parsed.minute


# ---------------------------------------------------------------------------
# Derivation from the activity log (single source of truth)
# ---------------------------------------------------------------------------


def _read_voice_dates(activity_path, start, end):
    """Dates in [start, end] with >=1 voice_task|success marker.

    `start`/`end` are datetime.date (Asia/Kolkata local). Log
    timestamps are naive local wall time (written by
    datetime.now() at the call site), read as Kolkata dates.
    A missing log is an empty set, not an error; a malformed line
    is skipped. Returns (dates, available): available is False
    only when the log file itself cannot be read.
    """
    from .scheduled_agents import _parse_activity_line

    try:
        lines = Path(activity_path).read_text(
            encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, OSError):
        return set(), False
    dates = set()
    for line in lines:
        parsed = _parse_activity_line(line)
        if parsed is None:
            continue
        when, event, outcome = parsed
        if event != MARKER_EVENT or outcome != MARKER_OUTCOME:
            continue
        day = when.date()
        if start <= day <= end:
            dates.add(day)
    return dates, True


def _kolkata_today(now=None) -> datetime.date:
    """Today's date in Asia/Kolkata (naive local `now` or datetime.now())."""
    moment = now if now is not None else datetime.datetime.now()
    if moment.tzinfo is not None:
        return moment.astimezone(TIMEZONE).date()
    return moment.date()


def _monday(day: datetime.date) -> datetime.date:
    """The Monday of the week containing `day`."""
    return day - datetime.timedelta(days=day.weekday())


def week_counts(activity_path=None, now=None) -> dict:
    """{"this_week", "last_week", "week_start", "display", ...}.

    Monday-anchored, Asia/Kolkata local. A missed day leaves the
    count where it is — there is no decrement and no reset except
    the Monday rollover, which starts a new week at 0/7. The
    display ALWAYS shows last week alongside this week, so Monday
    never reads as lost progress (B-105).
    """
    path = activity_path if activity_path is not None \
        else default_activity_path()
    today = _kolkata_today(now)
    monday = _monday(today)
    last_monday = monday - datetime.timedelta(days=7)
    last_sunday = monday - datetime.timedelta(days=1)
    this_dates, available = _read_voice_dates(path, monday, today)
    last_dates, _ = _read_voice_dates(path, last_monday, last_sunday)
    this_week = len(this_dates)
    last_week = len(last_dates)
    return {
        "available": available,
        "this_week": this_week,
        "last_week": last_week,
        "week_start": monday.isoformat(),
        "last_week_start": last_monday.isoformat(),
        "display": ("Days used this week: %d/7 · last week: %d/7"
                    % (this_week, last_week)),
    }


def daily_count_item(now=None, activity_path=None) -> dict:
    """The count as a digest input item (same path as activity /
    calendar in collect_digest_inputs — B-203)."""
    counts = week_counts(activity_path=activity_path, now=now)
    return {
        "available": counts["available"],
        "this_week": counts["this_week"],
        "last_week": counts["last_week"],
        "week_start": counts["week_start"],
        "display": counts["display"],
    }


def first_voice_task(activity_path=None):
    """The date ("YYYY-MM-DD") of the first voice-driven funnel
    success in the log, or None when there is none yet. The
    opt-in marker (B-106)."""
    path = activity_path if activity_path is not None \
        else default_activity_path()
    try:
        lines = Path(path).read_text(
            encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, OSError):
        return None
    from .scheduled_agents import _parse_activity_line

    earliest = None
    for line in lines:
        parsed = _parse_activity_line(line)
        if parsed is None:
            continue
        when, event, outcome = parsed
        if event != MARKER_EVENT or outcome != MARKER_OUTCOME:
            continue
        day = when.date()
        if earliest is None or day < earliest:
            earliest = day
    return earliest.isoformat() if earliest is not None else None


# ---------------------------------------------------------------------------
# Nudge rendering (pinned sources, counts only)
# ---------------------------------------------------------------------------


def _to_kolkata_date(value):
    """The Asia/Kolkata date of a datetime / ISO string, or None."""
    if isinstance(value, datetime.datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is not None:
        return moment.astimezone(TIMEZONE).date()
    return moment.date()


def _count_due_today(now=None, reminders_fn=None, routines_fn=None,
                     calendar_fn=None) -> dict:
    """Counts from exactly the three pinned sources (B-103).

    - reminders due today: the scheduler's reminders whose fire
      time falls on today's Kolkata date (count only — the message
      text is never read, let alone spoken).
    - routines due today: routines whose next_run falls today.
    - calendar events today: events whose start falls today.
    Each source is read through the existing read paths
    (collect_digest_inputs is the model); a source that fails
    reads as zero with its error recorded, never as a crash and
    never as an invented count. Titles, bodies and names are
    never returned — only counts.
    """
    today = _kolkata_today(now)
    counts = {"reminders": 0, "routines": 0, "events": 0, "total": 0}
    errors = {}
    # Reminders due today.
    try:
        if reminders_fn is None:
            from .scheduled_agents import _collect_reminders

            reminders, error = _collect_reminders()
        else:
            reminders, error = reminders_fn()
        if error:
            errors["reminders"] = error
        else:
            due = 0
            for r in reminders or []:
                if not isinstance(r, dict):
                    continue
                if _to_kolkata_date(r.get("fire_at")) == today:
                    due += 1
            counts["reminders"] = due
    except Exception as exc:  # noqa: BLE001 — a dead source reads as zero
        errors["reminders"] = str(exc)
    # Routines due today (next_run is local "YYYY-MM-DD HH:MM").
    try:
        if routines_fn is None:
            from .scheduled_agents import _collect_routines

            routines, error = _collect_routines()
        else:
            routines, error = routines_fn()
        if error:
            errors["routines"] = error
        else:
            due = 0
            for r in routines or []:
                if not isinstance(r, dict):
                    continue
                nxt = r.get("next_run")
                if isinstance(nxt, str) and nxt[:10] == today.isoformat():
                    due += 1
            counts["routines"] = due
    except Exception as exc:  # noqa: BLE001 — a dead source reads as zero
        errors["routines"] = str(exc)
    # Calendar events today.
    try:
        if calendar_fn is None:
            from .scheduled_agents import _read_calendar

            events, _available, error = _read_calendar(None)
        else:
            events, _available, error = calendar_fn()
        if error:
            errors["events"] = error
        else:
            due = 0
            for e in events or []:
                if not isinstance(e, dict):
                    continue
                if _to_kolkata_date(e.get("start")) == today:
                    due += 1
            counts["events"] = due
    except Exception as exc:  # noqa: BLE001 — a dead source reads as zero
        errors["events"] = str(exc)
    counts["total"] = (counts["reminders"] + counts["routines"]
                       + counts["events"])
    counts["errors"] = errors
    return counts


def render_nudge(now=None, reminders_fn=None, routines_fn=None,
                 calendar_fn=None) -> dict:
    """The nudge text for right now, rendered at fire time.

    Fixed templates with a single {n} slot; no free-text field
    from data ever enters the text. Zero → the all-clear wording,
    which still counts as the day's one nudge. Returns
    {"text", "template", "counts"}.
    """
    counts = _count_due_today(
        now=now, reminders_fn=reminders_fn, routines_fn=routines_fn,
        calendar_fn=calendar_fn)
    total = counts["total"]
    if total == 0:
        key, text = "all_clear", _TEMPLATES["all_clear"]
    elif total == 1:
        key, text = "one", _TEMPLATES["one"]
    else:
        key = "many"
        text = _TEMPLATES["many"].format(n=total)
    return {"text": text, "template": key, "counts": counts}


# ---------------------------------------------------------------------------
# Opt-in / opt-out
# ---------------------------------------------------------------------------


def nudge_offer(store=None, activity_path=None, now=None) -> dict:
    """The opt-in offer, or why it is not available.

    Available ONLY after the first voice-driven funnel success in
    the activity log (B-106) and only while the user has neither
    opted in nor opted out. The offer carries a preview of exactly
    what the nudge will say and asks for the time in the same step
    (default 7am). The live offer waits on Stage 1 (the voice
    loop); this function proves the mechanism in the lab, and the
    Settings surface offers it on demand.
    """
    store = store if store is not None else DailyCountStore()
    state = store.read()
    if state.get("opted_out"):
        return {"available": False,
                "reason": "opted out — the nudge is never re-offered",
                "preview": None, "default_time": DEFAULT_NUDGE_TIME}
    if state.get("opted_in"):
        return {"available": False,
                "reason": "already opted in",
                "preview": None, "default_time": DEFAULT_NUDGE_TIME}
    first = first_voice_task(activity_path)
    if first is None:
        return {"available": False,
                "reason": ("no completed voice task yet — the offer "
                           "appears after the first one"),
                "preview": None, "default_time": DEFAULT_NUDGE_TIME}
    preview = render_nudge(now=now)["text"]
    return {"available": True,
            "reason": "first voice task completed %s" % first,
            "preview": preview, "default_time": DEFAULT_NUDGE_TIME}


def accept_offer(store=None, time=DEFAULT_NUDGE_TIME) -> dict:
    """Accept the opt-in with a chosen time ("HH:MM", default 7am).

    Raises ValueError on a bad time. Does not schedule the job —
    call ensure_nudge_job after accepting.
    """
    if _parse_hhmm(time) is None:
        raise ValueError("nudge time needs HH:MM, got %r" % (time,))
    store = store if store is not None else DailyCountStore()
    return store.update(opted_in=True, opted_out=False,
                        nudge_time=str(time))


def decline_offer(store=None) -> dict:
    """Decline = opt out. Durable: the product never re-asks after
    an opt-out, but opt-in stays available on demand in Settings."""
    store = store if store is not None else DailyCountStore()
    return store.update(opted_in=False, opted_out=True)


def set_opt_in(store=None, time=DEFAULT_NUDGE_TIME) -> dict:
    """On-demand opt-in from the Settings surface (same shape as
    accepting the offer)."""
    return accept_offer(store=store, time=time)


def set_opt_out(store=None) -> dict:
    """One-tap opt-out from the Settings surface (same shape as
    declining the offer). Cancels the scheduled job best-effort."""
    store = store if store is not None else DailyCountStore()
    state = store.update(opted_in=False, opted_out=True)
    try:
        cancel_nudge_job()
    except Exception:  # noqa: BLE001 — opt-out never depends on the engine
        pass
    return state


# ---------------------------------------------------------------------------
# Nudge job (setup / cancel / fire)
# ---------------------------------------------------------------------------


def nudge_manifest():
    """The nudge job's manifest: read the three pinned sources,
    speak via speak_text, record in its own store — nothing else."""
    from .scheduled_agents import create_manifest

    return create_manifest(
        NUDGE_JOB_ID, NUDGE_JOB_NAME,
        allowed_tools=["speak_text"],
        allowed_data=["activity_log", "calendar_upcoming",
                      "reminders", "routines"],
        side_effects=["speak_nudge", "store_nudge"],
        confirm_required=True,
        payload={"job_id": NUDGE_JOB_ID, "kind": "daily_nudge"},
    )


def ensure_nudge_job(store=None, timezone=TIMEZONE_NAME):
    """Schedule the daily nudge job at the chosen time (mirror of
    setup_digest). Refuses when not opted in or when the job
    already exists — cancel first, then re-schedule on a time
    change. Starts the engine so the job fires after a restart."""
    from .scheduled_agents import (
        HANDLER_NAME, delete_manifest, store_manifest,
    )
    from .desktop import job_scheduler as _engine

    store = store if store is not None else DailyCountStore()
    state = store.read()
    if not state.get("opted_in"):
        return {"ok": False,
                "reason": "nudge not opted in — opt in first"}
    hour, minute = _parse_hhmm(state.get("nudge_time")
                               or DEFAULT_NUDGE_TIME)
    existing, listed = _find_job(_engine, NUDGE_JOB_ID)
    if isinstance(listed, dict) and not listed.get("ok"):
        return {"ok": False,
                "reason": "could not check existing jobs: %s"
                          % listed.get("reason", "unknown")}
    if existing is not None:
        return {"ok": False,
                "reason": ("daily nudge is already set up (next fire "
                           "%s) — cancel it first"
                           % existing.get("next_fire"))}
    manifest = nudge_manifest()
    ok, reason = store_manifest(manifest)
    if not ok:
        return {"ok": False,
                "reason": "could not store manifest: %s" % reason}
    payload = {"job_id": NUDGE_JOB_ID, "kind": "daily_nudge"}
    scheduled = _engine.schedule_cron(
        job_id=NUDGE_JOB_ID, handler=HANDLER_NAME, timezone=timezone,
        payload=payload,
        misfire_grace_time=NUDGE_MISFIRE_GRACE_SECONDS,
        name=NUDGE_JOB_NAME, hour=hour, minute=minute)
    if not isinstance(scheduled, dict) or not scheduled.get("ok"):
        delete_manifest(NUDGE_JOB_ID)  # roll back: no orphan manifest
        return {"ok": False,
                "reason": "could not schedule the nudge: %s"
                          % (scheduled.get("reason")
                             if isinstance(scheduled, dict)
                             else "unexpected result")}
    from .scheduled_agents import ensure_engine_running

    started = ensure_engine_running()
    result = {"ok": True, "job_id": NUDGE_JOB_ID,
              "timezone": timezone, "hour": hour, "minute": minute,
              "next_fire": scheduled.get("next_fire"),
              "engine_running": started.get("running", False)}
    if not started.get("ok"):
        result["note"] = ("nudge is persisted, but the engine could "
                          "not start (%s) — it will fire when the "
                          "engine starts" % started.get("reason"))
    return result


def _find_job(job_scheduler, job_id):
    """(job, listed) — mirrors scheduled_agents._find_job."""
    listed = job_scheduler.list_jobs()
    if not isinstance(listed, dict) or not listed.get("ok"):
        return None, listed
    for job in listed.get("jobs", []):
        if isinstance(job, dict) and job.get("job_id") == job_id:
            return job, listed
    return None, listed


def cancel_nudge_job():
    """Cancel the nudge job and delete its manifest."""
    from .scheduled_agents import delete_manifest
    from .desktop import job_scheduler as _engine

    cancelled = _engine.cancel(NUDGE_JOB_ID)
    if not isinstance(cancelled, dict) or not cancelled.get("ok"):
        return {"ok": False,
                "reason": "could not cancel job %r: %s"
                          % (NUDGE_JOB_ID, cancelled.get("reason")
                             if isinstance(cancelled, dict)
                             else "unexpected result")}
    ok, reason = delete_manifest(NUDGE_JOB_ID)
    result = {"ok": True, "job_id": NUDGE_JOB_ID,
              "was_next": cancelled.get("next_run_time")}
    if not ok:
        result["manifest_note"] = "manifest not removed: %s" % reason
    return result


def _record_nudge_activity(outcome: str, detail: str = "",
                           activity_path=None) -> None:
    """One activity-log line for the nudge's own outcome. Never
    raises — logging must never break a nudge."""
    try:
        log_path = Path(activity_path) if activity_path is not None \
            else default_activity_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        line = "%s | %s | %s" % (timestamp, NUDGE_EVENT, outcome)
        if detail:
            line += " — %s" % detail
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001 — logging never breaks a nudge
        pass


def fire_nudge(store=None, now=None, speak_fn=None, registry=None,
               activity_path=None, calendar_fn=None, reminders_fn=None,
               routines_fn=None, quiet_store_path=None) -> dict:
    """Fire one daily nudge: validity window, claim-then-speak.

    Order (B-101, B-102): opted-in? → already handled today? →
    inside the 2-hour validity window? → quiet hours allow it? →
    CLAIM the day (last-nudge date written FIRST, under the
    store's write lock) → render at fire time → speak → record
    the outcome. A speak failure still consumes the day and is
    recorded; there is no same-day retry and no double-fire even
    if the scheduler fires twice. Outside the window the fire is
    recorded as missed and never speaks late; in quiet hours it
    is recorded as quiet and stays silent. Never raises.
    """
    try:
        return _fire_nudge(store=store, now=now, speak_fn=speak_fn,
                           registry=registry, activity_path=activity_path,
                           calendar_fn=calendar_fn,
                           reminders_fn=reminders_fn,
                           routines_fn=routines_fn,
                           quiet_store_path=quiet_store_path)
    except Exception as exc:  # noqa: BLE001 — the fire never raises
        return {"ok": False, "reason": "nudge error: %s" % exc,
                "outcome": "failed"}


def _fire_nudge(store, now, speak_fn, registry, activity_path,
                calendar_fn, reminders_fn, routines_fn,
                quiet_store_path) -> dict:
    store = store if store is not None else DailyCountStore()
    moment = now if now is not None else datetime.datetime.now()
    today = _kolkata_today(moment)
    today_s = today.isoformat()
    state = store.read()
    if not state.get("opted_in"):
        return {"ok": False, "reason": "nudge not opted in",
                "outcome": "not_opted_in"}
    if state.get("last_nudge_date") == today_s:
        return {"ok": False,
                "reason": "already handled today (%s)"
                          % (state.get("last_nudge_outcome") or "claimed"),
                "outcome": state.get("last_nudge_outcome") or "claimed"}
    # Validity window: within 2 hours of the chosen time (B-101).
    chosen = _parse_hhmm(state.get("nudge_time") or DEFAULT_NUDGE_TIME)
    start = moment.replace(hour=chosen[0], minute=chosen[1],
                           second=0, microsecond=0)
    end = start + datetime.timedelta(hours=NUDGE_VALIDITY_HOURS)
    if not (start <= moment < end):
        claimed = store.claim_nudge_day(today_s, "missed")
        if not claimed:
            return {"ok": False, "reason": "already handled today",
                    "outcome": "missed"}
        _record_nudge_activity(
            "missed", "outside the 2-hour window (chosen %s)"
                      % (state.get("nudge_time") or DEFAULT_NUDGE_TIME),
            activity_path=activity_path)
        return {"ok": False,
                "reason": ("outside the 2-hour validity window "
                           "(chosen %s)" % (state.get("nudge_time")
                                            or DEFAULT_NUDGE_TIME)),
                "outcome": "missed"}
    # Quiet hours: consult gate_notify, stay silent (B-103's gate).
    from .w7_wiring_daily import notify_allowed

    allowed, gate_reason = notify_allowed(
        moment, store_path=quiet_store_path)
    if not allowed:
        claimed = store.claim_nudge_day(today_s, "quiet")
        if not claimed:
            return {"ok": False, "reason": "already handled today",
                    "outcome": "quiet"}
        _record_nudge_activity("quiet", gate_reason,
                               activity_path=activity_path)
        return {"ok": False, "reason": gate_reason, "outcome": "quiet"}
    # Claim-then-speak (B-102): the date is written FIRST.
    if not store.claim_nudge_day(today_s, "claimed"):
        return {"ok": False, "reason": "already handled today",
                "outcome": "claimed"}
    rendered = render_nudge(now=moment, reminders_fn=reminders_fn,
                            routines_fn=routines_fn,
                            calendar_fn=calendar_fn)
    text = rendered["text"]
    if speak_fn is None:
        from .scheduled_agents import _default_speak_fn

        if registry is None:
            from .funnel.tools_registry import tools as _tools

            registry = _tools
        speak_fn = _default_speak_fn(registry)
    try:
        spoken = speak_fn(text)
    except Exception as exc:  # noqa: BLE001 — a dead speaker is not spoken
        spoken = {"ok": False, "reason": "speak failed: %s" % exc}
    if not isinstance(spoken, dict) or not spoken.get("ok"):
        store.set_outcome("failed")
        _record_nudge_activity(
            "failed", str(spoken.get("reason", "speak failed"))
            if isinstance(spoken, dict) else "speak failed",
            activity_path=activity_path)
        return {"ok": False,
                "reason": (spoken.get("reason", "speak failed")
                           if isinstance(spoken, dict) else "speak failed"),
                "outcome": "failed", "text": text,
                "template": rendered["template"]}
    store.update(last_nudge_outcome="spoken",
                 last_template=rendered["template"])
    _record_nudge_activity("spoken", "template %s"
                           % rendered["template"],
                           activity_path=activity_path)
    return {"ok": True, "outcome": "spoken", "text": text,
            "template": rendered["template"],
            "counts": rendered["counts"]}
