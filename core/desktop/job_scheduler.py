"""B-D9 (2026-11-09) apscheduler timed-job engine — the ONE dict-returning
wrapper a driver calls for timed jobs (one-shot reminders, recurring
routines, cron schedules).

THE GAP (proved first, file/line evidence)
------------------------------------------
A scheduler ALREADY exists in the tree, twice over — this batch builds
only the remainder:

* ``core/scheduler.py`` (647 lines) — ProactiveScheduler: one-shot +
  daily/weekly/monthly repeats, JSON persistence
  (``data/scheduler_reminders.json``), a 1-second tick loop, a fixed
  24-hour missed grace, DST policy. It is ALREADY wired to tools
  (``core/funnel/tools_handlers_data_services.py:152,177,183``,
  ``core/skills_productivity2_timers.py:78,86,263`` — pomodoro /
  countdown / focus-day reminders). What it does NOT have:
  1. NO cron expressions — repeats are only daily / weekly / monthly
     (``_REPEAT_KINDS``, scheduler.py) — no "every Mon/Wed/Fri at
     09:30", no "first of the month", no minute-level cron.
  2. NO interval triggers — no "every 90 minutes from 08:00".
  3. NO per-job timezone — ONE scheduler timezone for everything
     (``self._tz``); a job cannot carry its own IANA zone.
  4. NO job-id identity — jobs are identified by LABEL
     (``cancel_reminder(label)``, scheduler.py:545); a duplicate
     label is silently uniquified (``_unique_label``), so the caller
     cannot address a job it scheduled.
  5. NO per-job misfire grace — one fixed 24 h grace for one-shots
     only; a repeating reminder missed while the app was down is
     rescheduled SILENTLY (``_load_and_apply_policy``) — the miss is
     never reported anywhere.
  6. NO timezone refusal — a naive datetime is ASSUMED to be
     scheduler-local time (scheduler.py:460 docstring); a caller
     passing a naive UTC time gets a wrong fire time, silently.
  7. NO database job store — one JSON file rewritten wholesale on
     every mutation (``_save``), no atomicity beyond temp-file +
     os.replace.
* ``core/brain/routine_scheduler.py`` (RoutineScheduler) + ``core/brain/
  planner_routines.py`` (``next_run``) — scheduled ROUTINES: a 20-second
  tick re-reads ``data/routines.json`` and fires named routines
  through ``tools.execute()``. It schedules routines (named action
  lists), not timed jobs, and its next-run math is planner code.

What is genuinely missing, and what this module adds, is the
timed-job primitive: cron / interval / one-shot jobs, each with its
own job id, its own timezone, its own misfire grace, persisted in a
real job store that is the single source of truth. The existing
schedulers are NOT changed by this batch (their behaviour is theirs;
a rewire is a separate decision for the parent). Boundary: B11's
``run_goal`` persistence is for GOAL STEPS (a goal's step ledger);
this wrapper is for TIMED JOBS (a job fires at a wall-clock time).
A timed job may ASK the driver to run a goal, but the wrapper never
runs a goal itself and never touches the routines engine.

LOPHOLE FAMILY — "a reminder that fires twice, or never fires, or
fires at the wrong time"
---------------------------------------------------------------
* A job that fires twice after a restart (the store reloaded + an
  in-memory copy both firing).
* A job that never fires (silently dropped on a restart, or a
  misfire swallowed).
* A job that fires at the wrong time (a timezone-naive time read in
  the wrong zone, or a DST shift).
* A job that fires after it was cancelled (a stale job in the store).
* A recurring job that drifts (each run scheduled from the last
  run's ACTUAL time instead of its intended time).

ONE design closes the class: every timed job goes through this
module; a job is identified by its own job id (never by a bare
name alone); the APScheduler job store is the single source of
truth — the wrapper keeps NO second in-memory copy that can
double-fire (``list_jobs()`` reads the store, always); a misfire
is REPORTED, never silently dropped (a per-job misfire grace time
is set, and a run missed beyond grace is recorded in the persisted
missed log, surfaced by ``get_missed()``); every time is
timezone-aware (a naive time is REFUSED, never guessed — the
opposite of ``core/scheduler.py``'s assume-local rule, deliberately);
a cancelled job is REMOVED from the store (``cancel()`` deletes the
row; a stale job never fires); a recurring job is scheduled from
its INTENDED time (APScheduler's CronTrigger / IntervalTrigger
compute the next fire from the schedule, never from the last run's
actual time) with ``coalesce=True`` (at most one fire per missed
window) and ``max_instances=1`` (a still-running job can never
double-fire).

Rejected alternatives (recorded):
* In-memory-only jobs — lost on restart. (What the old scheduler
  did before its JSON persistence; never again.)
* Double bookkeeping — a second in-memory copy alongside the store
  that double-fires after a restart. (The store is the ONLY copy.)
* Silent misfire drops — a missed job never reported. (What the
  existing routine scheduler does for repeating reminders.)
* Timezone-naive times — interpreted in the wrong zone.
  (``core/scheduler.py`` assumes scheduler-local; this wrapper
  refuses instead, so a driver never has to guess which zone a
  naive time was read in.)
* Cancel-without-store-removal — a stale job fires. (A cancel that
  only hides a job from listings.)
* Drift scheduling — each run from the last run's actual time.
  (A tick loop that adds "now + interval" after every fire.)
* A home-grown timer loop with the apscheduler name on it — the
  engine here IS real APScheduler (BackgroundScheduler, real
  DateTrigger / CronTrigger / IntervalTrigger, real misfire grace,
  real coalescing); only the STORE is in-tree (see DEPENDENCY).

DEPENDENCY (proposed — parent rules)
------------------------------------
* APScheduler 3.11.3 (MIT, Copyright (c) Alex Grönholm — licence
  read from the 3.11.3 PyPI wheel's LICENSE.txt, 2026-11-09) is the
  engine. 4.x is still alpha (4.0.0a6) and async-first with a
  different API; 3.11.3 is the latest stable 3.x line and its
  sync BackgroundScheduler drops straight into a PySide6 app —
  the round-6 record ("BackgroundScheduler drops straight into a
  PySide6 app", DESKTOP_GITHUB_RESEARCH.md:762) holds for 3.x.
  Installed in .venv-qt only (3.11.3); system python3 has no
  apscheduler (PEP 668 blocks pip there; --break-system-packages
  was NOT used) — the wrapper degrades there to engine_unavailable.
* tzlocal 5.4.5 (MIT, Copyright 2011-2017 Lennart Regebro —
  licence read from the installed wheel's LICENSE.txt,
  2026-11-09) is APScheduler's only hard runtime dependency.
* SQLAlchemy is NOT a dependency. APScheduler's shipped persistent
  store is SQLAlchemyJobStore, but SQLAlchemy 2.1.8 measures
  28 MB installed (du -sm, 2026-11-09) for a store that persists
  three columns (id, next_run_time, pickle blob) — more than the
  whole current product zip (21 MB). The store here is
  ``SQLiteJobStore``, an in-tree ``BaseJobStore`` subclass over
  stdlib ``sqlite3`` (0 new bytes shipped): same table name
  (``apscheduler_jobs``), same columns, same pickle format
  (``job.__getstate__()`` / ``Job.__setstate__()``) as
  SQLAlchemyJobStore, so a store file written by one is readable
  by the other. SQLAlchemy itself is MIT (Copyright 2005-2026
  SQLAlchemy authors and contributors — licence read from the
  installed wheel, 2026-11-09), so the rejection is weight, not
  licence; if the parent prefers the shipped store, the swap is
  one line (``SQLAlchemyJobStore(url="sqlite:///…")`` in
  ``_RealBackend``) and the wrapper's public API does not change.
* six 1.17.0 (MIT) rides with the set; already in the tree.
* No GPL anywhere in the set. Weight, measured 2026-11-09 from
  the installed Qt venv: apscheduler 620 KB + tzlocal 128 KB =
  under 1 MB new; SQLAlchemy 28 MB NOT shipped (see above).
* LAZY-imported (function-level static imports, the sympy
  pattern); absence degrades every timed job to an honest
  engine_unavailable, nothing else.

THE FIRE PATH (what runs when a job fires)
------------------------------------------
A job never carries a callable object: APScheduler persists jobs by
pickling them, and a pickled callable is a string reference
("module:function") that must resolve on the next start. So a job
carries a HANDLER NAME (a string) + a plain-data payload (a dict of
str/int/float/bool/None/list/dict, validated at schedule time).
The driver registers the callable once, by name, with
``register_handler(name, callable)``; the job's function is the
module-level ``_fire_job(handler_name, payload)``, which looks the
name up in the registry and calls it. A handler that is not
registered at FIRE time raises — the executor records it as a job
error (never a silent no-op), and a job whose handler is not
registered at SCHEDULE time is refused outright. No string is ever
eval'd or exec'd: the registry lookup is a dict lookup.

MISSED / FAILED fires are recorded in the SAME SQLite file, table
``scheduler_missed`` (capped at 100, oldest dropped), and surfaced
by ``get_missed()`` — so a miss survives a restart and is reported,
never silently dropped. A corrupt job row that cannot be restored
is removed AND recorded there too (upstream SQLAlchemyJobStore
removes it silently; this store records it).
"""

import datetime
import json
import pickle
import sqlite3
import threading
from pathlib import Path

__all__ = [
    "engine_available",
    "engine_status",
    "register_handler",
    "unregister_handler",
    "reset_handlers",
    "schedule_one_shot",
    "schedule_interval",
    "schedule_cron",
    "cancel",
    "list_jobs",
    "get_missed",
    "start",
    "stop",
    "shutdown",
    "is_running",
    "set_backend",
    "reset_backend",
    "set_store_path",
    "JobScheduler",
    "SQLiteJobStore",
]

# -- constants ---------------------------------------------------------
_DEFAULT_MISFIRE_GRACE_SECONDS = 3600  # 1 hour: a job missed by more is reported, not run late
_MAX_MISSED_RECORDS = 100
_CRON_FIELDS = ("year", "month", "day", "week", "day_of_week",
                "hour", "minute", "second")
_INTERVAL_FIELDS = ("weeks", "days", "hours", "minutes", "seconds")
_MISSED_TABLE = "scheduler_missed"
_JOBS_TABLE = "apscheduler_jobs"

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_DEFAULT_STORE_PATH = _DATA_DIR / "scheduled_jobs.sqlite3"


class _EngineUnavailable(Exception):
    """APScheduler (or a part of it) is not importable in this interpreter."""


# -- lazy APScheduler import (sympy pattern) ---------------------------
_APS = None  # cached dict of APScheduler names, or False when unavailable


def _load_apscheduler():
    """Import APScheduler once; return the names this module needs.

    Raises _EngineUnavailable (with the import error as the reason)
    when apscheduler cannot be imported. The SQLiteJobStore class is
    built here too, because it subclasses APScheduler's BaseJobStore
    and so cannot be defined at module import time.
    """
    global _APS
    if _APS is None:
        try:
            from apscheduler.job import Job as _Job
            from apscheduler.jobstores.base import (
                BaseJobStore as _BaseJobStore,
            )
            from apscheduler.jobstores.base import (
                ConflictingIdError as _ConflictingIdError,
            )
            from apscheduler.jobstores.base import (
                JobLookupError as _JobLookupError,
            )
            from apscheduler.schedulers.background import (
                BackgroundScheduler as _BackgroundScheduler,
            )
            from apscheduler.triggers.cron import CronTrigger as _CronTrigger
            from apscheduler.triggers.date import DateTrigger as _DateTrigger
            from apscheduler.triggers.interval import (
                IntervalTrigger as _IntervalTrigger,
            )
            from apscheduler.util import (
                datetime_to_utc_timestamp as _dt_to_ts,
            )
            from apscheduler.util import (
                utc_timestamp_to_datetime as _ts_to_dt,
            )
            import apscheduler as _apscheduler_pkg
        except ImportError as e:  # noqa: BLE001 — the reason IS the report
            _APS = False
            raise _EngineUnavailable(
                "apscheduler is not installed in this interpreter "
                f"({e}); timed jobs degrade to engine_unavailable"
            ) from e
        _APS = {
            "version": getattr(_apscheduler_pkg, "__version__", "unknown"),
            "Job": _Job,
            "BaseJobStore": _BaseJobStore,
            "ConflictingIdError": _ConflictingIdError,
            "JobLookupError": _JobLookupError,
            "BackgroundScheduler": _BackgroundScheduler,
            "CronTrigger": _CronTrigger,
            "DateTrigger": _DateTrigger,
            "IntervalTrigger": _IntervalTrigger,
            "datetime_to_utc_timestamp": _dt_to_ts,
            "utc_timestamp_to_datetime": _ts_to_dt,
            "SQLiteJobStore": _build_sqlite_job_store(_BaseJobStore),
        }
    if _APS is False:
        raise _EngineUnavailable(
            "apscheduler is not installed in this interpreter; "
            "timed jobs degrade to engine_unavailable"
        )
    return _APS


def _build_sqlite_job_store(BaseJobStore):
    """Build the in-tree SQLite job store over stdlib sqlite3.

    Same table name (``apscheduler_jobs``), same columns (id,
    next_run_time, job_state) and same pickle format
    (``job.__getstate__()``) as APScheduler's own SQLAlchemyJobStore
    — a store file written by one is readable by the other. The
    one deliberate difference: a job row that cannot be restored
    (corrupt pickle, unresolvable callable) is REMOVED and RECORDED
    in the ``scheduler_missed`` table of the same file, where
    upstream removes it silently.
    """

    class SQLiteJobStore(BaseJobStore):
        """Persistent APScheduler job store on stdlib sqlite3.

        One connection per operation (plus a lock for writes), so no
        connection is ever shared across threads. ``check_same_thread``
        is False because the scheduler thread and the driver thread
        both touch the store — every operation opens its own
        connection, so no connection object crosses a thread.
        """

        def __init__(self, path, pickle_protocol=pickle.HIGHEST_PROTOCOL):
            self._path = str(path)
            self._protocol = pickle_protocol
            self._lock = threading.RLock()
            self._ensure_tables()

        # -- internals ------------------------------------------------
        def _connect(self):
            conn = sqlite3.connect(self._path, timeout=30.0,
                                    check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            return conn

        def _ensure_tables(self):
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute(
                        f"CREATE TABLE IF NOT EXISTS {_JOBS_TABLE} ("
                        "id TEXT PRIMARY KEY, "
                        "next_run_time REAL, "
                        "job_state BLOB)"
                    )
                    conn.execute(
                        f"CREATE TABLE IF NOT EXISTS {_MISSED_TABLE} ("
                        "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "job_id TEXT, scheduled_at TEXT, recorded_at TEXT, "
                        "kind TEXT, reason TEXT)"
                    )
                    conn.commit()
                finally:
                    conn.close()

        def _reconstitute(self, job_state):
            from apscheduler.job import Job

            state = pickle.loads(job_state)
            state["jobstore"] = self
            job = Job.__new__(Job)
            job.__setstate__(state)
            job._scheduler = self._scheduler
            job._jobstore_alias = self._alias
            return job

        def _get_jobs(self, *conditions):
            jobs = []
            failed_ids = []
            where = ""
            params = []
            if conditions:
                where = " WHERE " + " AND ".join(c[0] for c in conditions)
                params = [c[1] for c in conditions]
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT id, job_state FROM {_JOBS_TABLE}{where} "
                    f"ORDER BY next_run_time IS NULL, next_run_time",
                    params,
                ).fetchall()
                for job_id, job_state in rows:
                    try:
                        jobs.append(self._reconstitute(job_state))
                    except BaseException:  # noqa: BLE001 — a corrupt row must never kill the listing
                        failed_ids.append(job_id)
                if failed_ids:
                    # Removed AND recorded (never silently dropped):
                    # the missed log lives in the same file.
                    now_iso = datetime.datetime.now(
                        datetime.timezone.utc).isoformat()
                    for job_id in failed_ids:
                        conn.execute(
                            f"DELETE FROM {_JOBS_TABLE} WHERE id = ?",
                            (job_id,),
                        )
                        conn.execute(
                            f"INSERT INTO {_MISSED_TABLE} "
                            "(job_id, scheduled_at, recorded_at, kind, reason) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (job_id, "", now_iso, "unrestorable",
                             "job record could not be restored "
                             "(corrupt pickle or unresolvable callable) "
                             "— removed from the store"),
                        )
                    conn.commit()
            finally:
                conn.close()
            self._fix_paused_jobs_sorting(jobs)
            return jobs

        # -- BaseJobStore interface -----------------------------------
        def lookup_job(self, job_id):
            conn = self._connect()
            try:
                row = conn.execute(
                    f"SELECT job_state FROM {_JOBS_TABLE} WHERE id = ?",
                    (job_id,),
                ).fetchone()
            finally:
                conn.close()
            if not row:
                return None
            try:
                return self._reconstitute(row[0])
            except BaseException:  # noqa: BLE001 — corrupt row: gone, recorded
                with self._lock:
                    conn = self._connect()
                    try:
                        conn.execute(
                            f"DELETE FROM {_JOBS_TABLE} WHERE id = ?",
                            (job_id,),
                        )
                        conn.execute(
                            f"INSERT INTO {_MISSED_TABLE} "
                            "(job_id, scheduled_at, recorded_at, kind, reason) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (job_id, "",
                             datetime.datetime.now(
                                 datetime.timezone.utc).isoformat(),
                             "unrestorable",
                             "job record could not be restored "
                             "(corrupt pickle or unresolvable callable) "
                             "— removed from the store"),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                return None

        def get_due_jobs(self, now):
            from apscheduler.util import datetime_to_utc_timestamp

            ts = datetime_to_utc_timestamp(now)
            return self._get_jobs(("next_run_time <= ?", ts))

        def get_next_run_time(self):
            from apscheduler.util import utc_timestamp_to_datetime

            conn = self._connect()
            try:
                row = conn.execute(
                    f"SELECT MIN(next_run_time) FROM {_JOBS_TABLE} "
                    "WHERE next_run_time IS NOT NULL"
                ).fetchone()
            finally:
                conn.close()
            if not row or row[0] is None:
                return None
            return utc_timestamp_to_datetime(row[0])

        def get_all_jobs(self):
            return self._get_jobs()

        def add_job(self, job):
            from apscheduler.jobstores.base import ConflictingIdError
            from apscheduler.util import datetime_to_utc_timestamp

            with self._lock:
                conn = self._connect()
                try:
                    conn.execute(
                        f"INSERT INTO {_JOBS_TABLE} "
                        "(id, next_run_time, job_state) VALUES (?, ?, ?)",
                        (job.id,
                         datetime_to_utc_timestamp(job.next_run_time),
                         pickle.dumps(job.__getstate__(), self._protocol)),
                    )
                    conn.commit()
                except sqlite3.IntegrityError as e:
                    raise ConflictingIdError(job.id) from e
                finally:
                    conn.close()

        def update_job(self, job):
            from apscheduler.jobstores.base import JobLookupError
            from apscheduler.util import datetime_to_utc_timestamp

            with self._lock:
                conn = self._connect()
                try:
                    cur = conn.execute(
                        f"UPDATE {_JOBS_TABLE} SET next_run_time = ?, "
                        "job_state = ? WHERE id = ?",
                        (datetime_to_utc_timestamp(job.next_run_time),
                         pickle.dumps(job.__getstate__(), self._protocol),
                         job.id),
                    )
                    conn.commit()
                    if cur.rowcount == 0:
                        raise JobLookupError(job.id)
                finally:
                    conn.close()

        def remove_job(self, job_id):
            from apscheduler.jobstores.base import JobLookupError

            with self._lock:
                conn = self._connect()
                try:
                    cur = conn.execute(
                        f"DELETE FROM {_JOBS_TABLE} WHERE id = ?",
                        (job_id,),
                    )
                    conn.commit()
                    if cur.rowcount == 0:
                        raise JobLookupError(job_id)
                finally:
                    conn.close()

        def remove_all_jobs(self):
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute(f"DELETE FROM {_JOBS_TABLE}")
                    conn.commit()
                finally:
                    conn.close()

        def shutdown(self):
            pass  # connections are per-operation; nothing to close

        def __repr__(self):
            return f"<SQLiteJobStore (path={self._path})>"

    return SQLiteJobStore


# Backwards alias: the store class is built lazily (it subclasses
# APScheduler's BaseJobStore), so the module-level name resolves on
# first use through _load_apscheduler().
def SQLiteJobStore(*args, **kwargs):  # noqa: N802 — factory alias, mirrors the class name
    """Return the SQLiteJobStore class (building it on first use).

    Kept as a function so ``from core.desktop.job_scheduler import
    SQLiteJobStore`` works without importing apscheduler at module
    import time; callers that need the class call it with no args to
    get the class, then instantiate it.
    """
    aps = _load_apscheduler()
    cls = aps["SQLiteJobStore"]
    if args or kwargs:
        return cls(*args, **kwargs)
    return cls


# -- missed / failed-fire log (stdlib sqlite, same file as the jobs) --
def _ensure_missed_table(conn):
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {_MISSED_TABLE} ("
        "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
        "job_id TEXT, scheduled_at TEXT, recorded_at TEXT, "
        "kind TEXT, reason TEXT)"
    )


def _record_missed(store_path, job_id, scheduled_at, reason, kind="missed"):
    """Persist one missed/failed fire record (capped, oldest dropped).

    Never raises into a scheduler call path: a missed log that cannot
    be written is printed, and the job itself is unaffected.
    """
    try:
        path = Path(store_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30.0,
                                check_same_thread=False)
        try:
            _ensure_missed_table(conn)
            scheduled_iso = (
                scheduled_at.isoformat()
                if isinstance(scheduled_at, datetime.datetime)
                else str(scheduled_at or "")
            )
            conn.execute(
                f"INSERT INTO {_MISSED_TABLE} "
                "(job_id, scheduled_at, recorded_at, kind, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, scheduled_iso,
                 datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 kind, reason),
            )
            conn.execute(
                f"DELETE FROM {_MISSED_TABLE} WHERE seq NOT IN "
                f"(SELECT seq FROM {_MISSED_TABLE} "
                f"ORDER BY seq DESC LIMIT {_MAX_MISSED_RECORDS})"
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — the log must never kill scheduling
        print(f"[JobScheduler] could not record missed fire: {e}")


def _read_missed(store_path):
    """Read the persisted missed/failed-fire records, newest first."""
    try:
        conn = sqlite3.connect(str(store_path), timeout=30.0,
                                check_same_thread=False)
        try:
            _ensure_missed_table(conn)
            rows = conn.execute(
                f"SELECT job_id, scheduled_at, recorded_at, kind, reason "
                f"FROM {_MISSED_TABLE} ORDER BY seq DESC"
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — no store file yet means no misses
        return []
    return [
        {"job_id": r[0], "scheduled_at": r[1], "recorded_at": r[2],
         "kind": r[3], "reason": r[4]}
        for r in rows
    ]


# -- handler registry + fire path --------------------------------------
# A job carries a handler NAME + a plain-data payload. The driver
# registers the callable once, by name. No string is ever eval'd.
_HANDLERS = {}
_HANDLERS_LOCK = threading.RLock()


def register_handler(name, fn):
    """Register the callable a timed job fires. Returns a dict."""
    if not isinstance(name, str) or not name.strip():
        return {"ok": False, "reason": "handler name must be a non-empty string"}
    if not callable(fn):
        return {"ok": False, "reason": f"handler {name!r} is not callable"}
    with _HANDLERS_LOCK:
        _HANDLERS[name] = fn
    return {"ok": True, "handler": name}


def unregister_handler(name):
    """Remove a handler registration. Returns a dict."""
    with _HANDLERS_LOCK:
        removed = _HANDLERS.pop(name, None) is not None
    return {"ok": removed,
            "reason": None if removed else f"no handler {name!r} registered"}


def reset_handlers():
    """Clear every handler registration. Test-only; the driver never calls this."""
    with _HANDLERS_LOCK:
        _HANDLERS.clear()
    return {"ok": True}


def _fire_job(handler_name, payload):
    """The function APScheduler actually calls (module-level, so its
    "module:function" reference survives a restart).

    Looks the handler name up in the registry and calls it with the
    payload. Raises on an unregistered handler or a handler error —
    the executor (real or fake) records the failure; a fire that
    silently does nothing is never an option.
    """
    with _HANDLERS_LOCK:
        fn = _HANDLERS.get(handler_name)
    if fn is None:
        raise LookupError(
            f"no handler {handler_name!r} registered for this timed job "
            "(the driver never registered it, or it was unregistered "
            "before the job fired)")
    return fn(payload)


# -- validation ---------------------------------------------------------
def _refuse(reason):
    return {"ok": False, "reason": reason}


def _is_aware(value):
    return (isinstance(value, datetime.datetime)
            and value.tzinfo is not None
            and value.tzinfo.utcoffset(value) is not None)


def _validate_job_id(job_id):
    if not isinstance(job_id, str) or not job_id.strip():
        return "job id must be a non-empty string"
    return None


def _validate_payload(payload):
    """Payload must be plain data (picklable without side effects)."""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return "payload must be a dict (or None)"
    for key in payload:
        if not isinstance(key, str):
            return "payload keys must be strings"
    stack = list(payload.values())
    while stack:
        value = stack.pop()
        if value is None or isinstance(value, (str, int, float, bool)):
            continue
        if isinstance(value, (list, tuple)):
            stack.extend(value)
            continue
        if isinstance(value, dict):
            if not all(isinstance(k, str) for k in value):
                return "payload dict keys must be strings"
            stack.extend(value.values())
            continue
        return (f"payload value {value!r} is not plain data "
                "(only str/int/float/bool/None/list/dict)")
    return None


def _validate_grace(misfire_grace_time):
    if isinstance(misfire_grace_time, bool) \
            or not isinstance(misfire_grace_time, int):
        return "misfire_grace_time must be an integer number of seconds"
    if misfire_grace_time < 1:
        return ("misfire_grace_time must be >= 1 (APScheduler requires a "
                "positive integer; 0 would mean a job is missed the "
                "instant it is due)")


def _validate_handler_registered(handler):
    if not isinstance(handler, str) or not handler.strip():
        return "handler must be a registered handler name (non-empty string)"
    with _HANDLERS_LOCK:
        if handler not in _HANDLERS:
            return (f"no handler {handler!r} is registered — register it "
                    "with register_handler() before scheduling a job for it")
    return None


# -- triggers ------------------------------------------------------------
def _real_trigger_factory(kind, **params):
    """Build a real APScheduler trigger. Raises _EngineUnavailable
    when apscheduler is missing; ValueError/TypeError from the
    trigger constructor propagate to the caller, which turns them
    into a refusal dict.
    """
    aps = _load_apscheduler()
    if kind == "date":
        return aps["DateTrigger"](run_date=params["run_at"])
    if kind == "interval":
        return aps["IntervalTrigger"](
            weeks=params.get("weeks", 0), days=params.get("days", 0),
            hours=params.get("hours", 0), minutes=params.get("minutes", 0),
            seconds=params.get("seconds", 0),
            start_date=params["start_at"])
    if kind == "cron":
        fields = {k: v for k, v in params["fields"].items()}
        return aps["CronTrigger"](timezone=params["timezone"], **fields)
    raise ValueError(f"unknown trigger kind {kind!r}")


def _trigger_kind(trigger):
    name = type(trigger).__name__
    return {"DateTrigger": "date", "IntervalTrigger": "interval",
            "CronTrigger": "cron"}.get(name, name)


# -- job view ------------------------------------------------------------
class _JobView:
    """The wrapper's own projection of a job: id, handler, payload,
    trigger kind, next fire time, misfire grace. The wrapper never
    keeps job objects of its own — views are built fresh from the
    backend (the store) on every call."""

    def __init__(self, job_id, handler, payload, trigger_kind,
                 next_run_time, misfire_grace_time):
        self.id = job_id
        self.handler = handler
        self.payload = payload
        self.trigger_kind = trigger_kind
        self.next_run_time = next_run_time
        self.misfire_grace_time = misfire_grace_time

    @classmethod
    def from_aps_job(cls, job):
        args = list(getattr(job, "args", []) or [])
        return cls(
            job_id=job.id,
            handler=args[0] if args else None,
            payload=args[1] if len(args) > 1 else None,
            trigger_kind=_trigger_kind(job.trigger),
            # A job added while the scheduler is stopped is PENDING
            # and has no next_run_time yet (APScheduler only computes
            # it in _real_add_job, at start) — getattr, never a crash.
            next_run_time=getattr(job, "next_run_time", None),
            misfire_grace_time=job.misfire_grace_time,
        )

    def as_dict(self):
        return {
            "job_id": self.id,
            "handler": self.handler,
            "trigger": self.trigger_kind,
            "next_fire": (self.next_run_time.isoformat()
                          if self.next_run_time is not None else None),
            "misfire_grace_time": self.misfire_grace_time,
        }


# -- real backend (APScheduler BackgroundScheduler) -----------------------
class _RealBackend:
    """Thin adapter over a real BackgroundScheduler. The scheduler IS
    the store-backed engine: jobs live in the SQLiteJobStore, and
    this adapter never keeps a second copy."""

    def __init__(self, store_path):
        aps = _load_apscheduler()
        store = aps["SQLiteJobStore"](str(store_path))
        self._aps = aps
        self._store = store
        self._sched = aps["BackgroundScheduler"](
            jobstores={"default": store},
            job_defaults={"coalesce": True, "max_instances": 1,
                          "misfire_grace_time": _DEFAULT_MISFIRE_GRACE_SECONDS},
            timezone="UTC",
        )

    def add_job(self, job_id, trigger, handler, payload,
                misfire_grace_time, coalesce, max_instances, name):
        job = self._sched.add_job(
            _fire_job, trigger=trigger, id=job_id,
            args=[handler, payload], name=name or job_id,
            misfire_grace_time=misfire_grace_time,
            coalesce=coalesce, max_instances=max_instances,
            replace_existing=False,
        )
        view = _JobView.from_aps_job(job)
        if view.next_run_time is None:
            # Scheduler stopped: the job is pending and APScheduler
            # has not computed its next run yet — compute it from the
            # trigger, exactly as _real_add_job does at start.
            view.next_run_time = trigger.get_next_fire_time(
                None, datetime.datetime.now(datetime.timezone.utc))
        return view

    def remove_job(self, job_id):
        # Directly on the STORE, not via the scheduler: a stopped
        # scheduler's remove_job only sees pending jobs, never the
        # store. The store is the single source of truth.
        self._store.remove_job(job_id)  # raises JobLookupError when missing

    def get_job(self, job_id):
        # Directly on the STORE: BaseScheduler.get_job() on a stopped
        # scheduler returns only PENDING jobs ("If the scheduler has
        # not been started yet, only pending jobs can be returned
        # because the job stores haven't been started yet either" —
        # get_jobs docstring, schedulers/base.py:641). A list that
        # hides stored jobs after a restart would be a lie.
        job = self._store.lookup_job(job_id)
        return _JobView.from_aps_job(job) if job is not None else None

    def get_jobs(self):
        # Directly on the STORE, for the same reason as get_job.
        return [_JobView.from_aps_job(j)
                for j in self._store.get_all_jobs()]

    def start(self):
        """Start firing. STATE_RUNNING -> no-op; STATE_PAUSED ->
        resume(); STATE_STOPPED -> start the scheduler thread.
        (APScheduler's executor pool does NOT survive a
        shutdown/start cycle — "cannot schedule new futures after
        shutdown" — so stop() below PAUSES instead of shutting down;
        only shutdown() destroys the engine, and it is final.)"""
        from apscheduler.schedulers.base import STATE_PAUSED, STATE_STOPPED

        if self._sched.state == STATE_STOPPED:
            self._sched.start()
        elif self._sched.state == STATE_PAUSED:
            self._sched.resume()

    def pause(self):
        """Stop firing, keep the engine: pause job processing. The
        thread stays alive but idle; start() resumes. Jobs stay in
        the store throughout."""
        from apscheduler.schedulers.base import STATE_RUNNING

        if self._sched.state == STATE_RUNNING:
            self._sched.pause()

    def shutdown(self):
        """Full teardown: shut the scheduler (and its executor pool)
        down. FINAL — a scheduler shut down this way cannot be
        started again (upstream limitation); build a new backend on
        the same store path instead (that IS the restart)."""
        from apscheduler.schedulers.base import SchedulerNotRunningError

        try:
            self._sched.shutdown(wait=True)
        except SchedulerNotRunningError:
            pass

    def is_running(self):
        """True only while FIRING (STATE_RUNNING). A paused scheduler
        is not firing — APScheduler's own ``running`` property would
        say True for a paused scheduler (state != STOPPED), which is
        the wrong answer for a driver asking 'will my job fire?'."""
        from apscheduler.schedulers.base import STATE_RUNNING

        return self._sched.state == STATE_RUNNING

    def add_listener(self, fn, mask):
        self._sched.add_listener(fn, mask)


# -- the wrapper ---------------------------------------------------------
class JobScheduler:
    """One timed-job engine. ``backend=None`` builds the real
    APScheduler backend (lazy import; engine_unavailable when the
    library is missing). A test backend + trigger factory + clock
    may be injected; the wrapper's validation, identity, and
    reporting rules are identical either way.

    ``store_path`` is where the SQLite job store (and the missed
    log) live; default ``data/scheduled_jobs.sqlite3`` under the
    tree root. Tests pass a temp path so they never touch data/.
    """

    def __init__(self, store_path=None, backend=None,
                 trigger_factory=None, now_fn=None):
        self._store_path = Path(store_path) if store_path is not None \
            else _DEFAULT_STORE_PATH
        self._backend = backend  # None -> build the real one lazily
        self._trigger_factory = trigger_factory or _real_trigger_factory
        self._now_fn = now_fn or (
            lambda: datetime.datetime.now(datetime.timezone.utc))
        self._listener_registered = False
        self._started = False

    # -- internals ------------------------------------------------------
    def _ensure_backend(self):
        if self._backend is None:
            self._backend = _RealBackend(self._store_path)
            # Register the miss/error listener HERE, before the backend
            # can ever start (a listener added in start() would miss
            # events from a lazy start on the first schedule).
            if (hasattr(self._backend, "add_listener")
                    and not self._listener_registered):
                try:
                    from apscheduler.events import (
                        EVENT_JOB_ERROR,
                        EVENT_JOB_MAX_INSTANCES,
                        EVENT_JOB_MISSED,
                    )

                    self._backend.add_listener(
                        self._on_event,
                        EVENT_JOB_MISSED | EVENT_JOB_ERROR
                        | EVENT_JOB_MAX_INSTANCES)
                    self._listener_registered = True
                except _EngineUnavailable:
                    pass
        return self._backend

    def _now(self):
        return self._now_fn()

    def _on_event(self, event):
        """APScheduler event listener (real backend only): every
        missed / error / max-instances event is recorded in the
        persisted missed log, never silently dropped."""
        from apscheduler.events import (
            EVENT_JOB_ERROR,
            EVENT_JOB_MAX_INSTANCES,
            EVENT_JOB_MISSED,
        )

        scheduled = getattr(event, "scheduled_run_time", None)
        if event.code == EVENT_JOB_MISSED:
            _record_missed(
                self._store_path, event.job_id, scheduled,
                "missed its fire time by more than the misfire grace "
                "time — not run late, reported here",
                kind="missed")
        elif event.code == EVENT_JOB_ERROR:
            exc = getattr(event, "exception", None)
            _record_missed(
                self._store_path, event.job_id, scheduled,
                f"handler raised {type(exc).__name__}: {exc}"
                if exc is not None else "handler raised an unknown error",
                kind="error")
        elif event.code == EVENT_JOB_MAX_INSTANCES:
            _record_missed(
                self._store_path, event.job_id, scheduled,
                "skipped: the previous instance was still running "
                "(max_instances=1) — not run twice",
                kind="skipped")

    def _schedule(self, kind, trigger_params, job_id, handler, payload,
                  misfire_grace_time, name):
        err = _validate_job_id(job_id)
        if err:
            return _refuse(err)
        err = _validate_handler_registered(handler)
        if err:
            return _refuse(err)
        err = _validate_payload(payload)
        if err:
            return _refuse(err)
        err = _validate_grace(misfire_grace_time)
        if err:
            return _refuse(err)
        try:
            trigger = self._trigger_factory(kind, **trigger_params)
        except _EngineUnavailable as e:
            return {"ok": False, "reason": "engine_unavailable",
                    "detail": str(e)}
        except (ValueError, TypeError) as e:
            return _refuse(f"invalid schedule: {e}")
        try:
            backend = self._ensure_backend()
        except _EngineUnavailable as e:
            return {"ok": False, "reason": "engine_unavailable",
                    "detail": str(e)}
        # The engine starts lazily on the first schedule: APScheduler
        # only persists a job when the scheduler is running (a job
        # added while stopped is PENDING — in memory only, invisible
        # to list_jobs(), and lost if the app dies before start).
        # A scheduled job must be persisted IMMEDIATELY, so schedule
        # starts the engine when it is not running.
        if not self.is_running():
            backend.start()
        existing = backend.get_job(job_id)
        if existing is not None:
            return _refuse(
                f"job id {job_id!r} is already scheduled "
                f"(next fire {existing.next_run_time.isoformat() if existing.next_run_time else None}) "
                "— cancel it first, or pick another id")
        try:
            view = backend.add_job(
                job_id, trigger, handler, payload,
                misfire_grace_time, True, 1, name)
        except Exception as e:  # noqa: BLE001 — ConflictingIdError included
            if type(e).__name__ == "ConflictingIdError":
                return _refuse(f"job id {job_id!r} is already scheduled "
                               "— cancel it first, or pick another id")
            return _refuse(f"could not schedule job {job_id!r}: {e}")
        # Verify the job actually reached the store: a job added while
        # the scheduler is stopped is PENDING (in memory only, lost
        # if the app dies before start) — a schedule that claims
        # success for a job that is not persisted is a lie.
        if backend.get_job(job_id) is None:
            return _refuse(
                f"job {job_id!r} was accepted but is not in the job "
                "store — not scheduled")
        return {
            "ok": True,
            "job_id": view.id,
            "handler": view.handler,
            "trigger": view.trigger_kind,
            "next_fire": (view.next_run_time.isoformat()
                          if view.next_run_time is not None else None),
            "misfire_grace_time": view.misfire_grace_time,
            "engine_running": self.is_running(),
        }

    # -- public API: schedule -------------------------------------------
    def schedule_one_shot(self, job_id, run_at, handler, payload=None,
                          misfire_grace_time=_DEFAULT_MISFIRE_GRACE_SECONDS,
                          name=None):
        """Schedule one fire at an aware datetime. A naive datetime
        is REFUSED, never guessed; a past time is REFUSED."""
        if not _is_aware(run_at):
            return _refuse(
                "run_at must be a timezone-aware datetime — a naive time "
                "is refused, never guessed (pass an aware datetime, e.g. "
                "with zoneinfo.ZoneInfo)")
        if run_at <= self._now():
            return _refuse(
                f"run_at {run_at.isoformat()} is not in the future")
        return self._schedule(
            "date", {"run_at": run_at}, job_id, handler, payload,
            misfire_grace_time, name)

    def schedule_interval(self, job_id, start_at, handler, payload=None,
                          misfire_grace_time=_DEFAULT_MISFIRE_GRACE_SECONDS,
                          name=None, **interval):
        """Schedule a recurring job every interval from ``start_at``
        (an aware datetime). The next fire is computed from the
        INTENDED schedule (start + n * interval), never from the
        last run's actual time — so a late run never drifts the
        schedule. Interval kwargs: weeks/days/hours/minutes/seconds,
        at least one positive, none negative."""
        if not _is_aware(start_at):
            return _refuse(
                "start_at must be a timezone-aware datetime — a naive "
                "time is refused, never guessed")
        if start_at <= self._now():
            return _refuse(
                f"start_at {start_at.isoformat()} is not in the future")
        unknown = sorted(set(interval) - set(_INTERVAL_FIELDS))
        if unknown:
            return _refuse(f"unknown interval field(s): {unknown}")
        if not interval:
            return _refuse("interval needs at least one of "
                           "weeks/days/hours/minutes/seconds")
        for key, value in interval.items():
            if isinstance(value, bool) or not isinstance(value, int):
                return _refuse(f"interval {key} must be an integer")
            if value < 0:
                return _refuse(f"interval {key} must be >= 0")
        if all(v == 0 for v in interval.values()):
            return _refuse("interval must be positive (all fields are 0)")
        params = {"start_at": start_at}
        params.update(interval)
        return self._schedule(
            "interval", params, job_id, handler, payload,
            misfire_grace_time, name)

    def schedule_cron(self, job_id, handler, timezone, payload=None,
                      misfire_grace_time=_DEFAULT_MISFIRE_GRACE_SECONDS,
                      name=None, **fields):
        """Schedule a recurring job on a cron expression. ``fields``
        are CronTrigger fields (year/month/day/week/day_of_week/hour/
        minute/second, e.g. hour=9, minute=30, day_of_week="mon,wed,fri").
        ``timezone`` is a required IANA name — a cron with no zone
        is refused, never guessed. The next fire is computed from the
        cron schedule, never from the last run's actual time."""
        if not isinstance(timezone, str) or not timezone.strip():
            return _refuse("cron needs a timezone (IANA name, e.g. "
                           "'Asia/Kolkata') — a cron with no zone is "
                           "refused, never guessed")
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(timezone)
        except Exception as e:  # noqa: BLE001 — unknown zone name
            return _refuse(f"unknown timezone {timezone!r}: {e}")
        unknown = sorted(set(fields) - set(_CRON_FIELDS))
        if unknown:
            return _refuse(f"unknown cron field(s): {unknown}")
        if not fields:
            return _refuse("cron needs at least one field "
                           "(e.g. hour=9, minute=30)")
        params = {"fields": dict(fields), "timezone": timezone}
        return self._schedule(
            "cron", params, job_id, handler, payload,
            misfire_grace_time, name)

    # -- public API: cancel / list / missed ------------------------------
    def cancel(self, job_id):
        """Remove a job from the store. A cancelled job never fires.
        Unknown id -> honest refusal (never a silent no-op)."""
        err = _validate_job_id(job_id)
        if err:
            return _refuse(err)
        try:
            backend = self._ensure_backend()
        except _EngineUnavailable as e:
            return {"ok": False, "reason": "engine_unavailable",
                    "detail": str(e)}
        existing = backend.get_job(job_id)
        if existing is None:
            return _refuse(f"no job {job_id!r} is scheduled")
        was_next = (existing.next_run_time.isoformat()
                    if existing.next_run_time is not None else None)
        try:
            backend.remove_job(job_id)
        except Exception as e:  # noqa: BLE001 — JobLookupError included
            if type(e).__name__ == "JobLookupError":
                return _refuse(f"no job {job_id!r} is scheduled")
            return _refuse(f"could not cancel job {job_id!r}: {e}")
        # A cancel must be verifiable: the store must no longer list it.
        if backend.get_job(job_id) is not None:
            return _refuse(
                f"cancel of {job_id!r} did not remove it from the store")
        return {"ok": True, "job_id": job_id, "was_next_fire": was_next}

    def list_jobs(self):
        """List scheduled jobs — read from the STORE every time (the
        single source of truth); the wrapper keeps no second copy."""
        try:
            backend = self._ensure_backend()
        except _EngineUnavailable as e:
            return {"ok": False, "reason": "engine_unavailable",
                    "detail": str(e), "jobs": []}
        return {"ok": True,
                "jobs": [v.as_dict() for v in backend.get_jobs()]}

    def get_missed(self):
        """Missed / failed fires, newest first, persisted across
        restarts in the same SQLite file as the jobs."""
        return {"ok": True, "missed": _read_missed(self._store_path)}

    # -- public API: lifecycle -------------------------------------------
    def start(self):
        """Start firing. Idempotent. The engine also starts lazily on
        the first schedule (so a scheduled job is persisted
        immediately); start() is the explicit entry. Jobs missed
        while the engine was down fire if within their grace, else
        are recorded as missed."""
        try:
            backend = self._ensure_backend()
        except _EngineUnavailable as e:
            return {"ok": False, "reason": "engine_unavailable",
                    "detail": str(e)}
        backend.start()
        self._started = True
        return {"ok": True, "running": True}

    def stop(self):
        """Stop firing (pause). Idempotent. Jobs stay in the store
        and survive the stop (and the restart). The engine thread
        stays alive but idle — APScheduler's executor pool does NOT
        survive a shutdown/start cycle ("cannot schedule new futures
        after shutdown"), so stop() pauses instead of destroying;
        start() resumes. For a full teardown (process exit, or
        simulating a restart in tests) use shutdown()."""
        if self._backend is None:
            return {"ok": True, "running": False}
        self._backend.pause()
        self._started = False
        return {"ok": True, "running": False}

    def shutdown(self):
        """Full teardown of the engine. FINAL for this object: a
        scheduler shut down this way cannot be started again, so
        after this call build a new JobScheduler on the same store
        path — that IS the restart, and it picks the jobs up from
        the store. Test/process-exit use only; the driver uses
        stop() (pause) for reversible stops."""
        if self._backend is not None:
            try:
                self._backend.shutdown()
            except Exception:  # noqa: BLE001 — teardown must not raise
                pass
            self._backend = None
        self._started = False
        self._listener_registered = False
        return {"ok": True, "running": False}

    def is_running(self):
        if self._backend is None:
            return False
        try:
            return bool(self._backend.is_running())
        except Exception:  # noqa: BLE001 — a dead backend reads as not running
            return False

    def tick(self, now=None):
        """Drive one firing pass (fake backends only — the real
        backend runs its own thread). Test/driver-driven advance:
        fires due jobs at ``now`` (default: the injected clock),
        records misfires, advances recurring jobs from their
        INTENDED schedule. Refused on the real backend."""
        if self._backend is None:
            return _refuse("tick() needs a backend — start the engine first")
        if not hasattr(self._backend, "fire_due"):
            return _refuse("tick() is only for driven (fake) backends — "
                           "the real backend runs its own thread")
        at = now if now is not None else self._now()
        outcomes = self._backend.fire_due(at)
        for outcome in outcomes:
            if outcome.get("kind") in ("missed", "error", "skipped"):
                _record_missed(
                    self._store_path, outcome["job_id"],
                    outcome.get("scheduled_at"),
                    outcome.get("reason", ""), kind=outcome["kind"])
        return {"ok": True, "fired": [o for o in outcomes
                                      if o.get("kind") == "fired"],
                "reported": [o for o in outcomes
                             if o.get("kind") != "fired"]}


# -- module-level default engine (driver surface) -----------------------
_DEFAULT = None
_BACKEND_OVERRIDE = None
_STORE_PATH_OVERRIDE = None


def _default_engine():
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = JobScheduler(
            store_path=_STORE_PATH_OVERRIDE, backend=_BACKEND_OVERRIDE)
    return _DEFAULT


def engine_available():
    """True when APScheduler imports in this interpreter."""
    try:
        _load_apscheduler()
        return True
    except _EngineUnavailable:
        return False


def engine_status():
    """Honest engine report: availability, version, store, running."""
    try:
        aps = _load_apscheduler()
        version = aps["version"]
        available = True
        reason = None
    except _EngineUnavailable as e:
        version = None
        available = False
        reason = str(e)
    engine = _DEFAULT
    return {
        "ok": available,
        "available": available,
        "version": version,
        "reason": reason,
        "store_path": str(_STORE_PATH_OVERRIDE or _DEFAULT_STORE_PATH),
        "running": engine.is_running() if engine is not None else False,
    }


def set_backend(backend):
    """Test seam (clipboard idiom): replace the backend the default
    engine uses. Resets the default engine so the next call builds
    a fresh one on the new backend."""
    global _DEFAULT, _BACKEND_OVERRIDE
    _BACKEND_OVERRIDE = backend
    _DEFAULT = None
    return {"ok": True}


def reset_backend():
    """Undo set_backend(): the default engine builds the real
    APScheduler backend again on next use."""
    global _DEFAULT, _BACKEND_OVERRIDE
    _BACKEND_OVERRIDE = None
    _DEFAULT = None
    return {"ok": True}


def set_store_path(path):
    """Test seam: point the default engine's store at ``path`` (a
    temp file in tests, so they never touch data/). Resets the
    default engine."""
    global _DEFAULT, _STORE_PATH_OVERRIDE
    _STORE_PATH_OVERRIDE = Path(path) if path is not None else None
    _DEFAULT = None
    return {"ok": True}


def schedule_one_shot(job_id, run_at, handler, payload=None,
                      misfire_grace_time=_DEFAULT_MISFIRE_GRACE_SECONDS,
                      name=None):
    return _default_engine().schedule_one_shot(
        job_id, run_at, handler, payload, misfire_grace_time, name)


def schedule_interval(job_id, start_at, handler, payload=None,
                      misfire_grace_time=_DEFAULT_MISFIRE_GRACE_SECONDS,
                      name=None, **interval):
    return _default_engine().schedule_interval(
        job_id, start_at, handler, payload, misfire_grace_time, name,
        **interval)


def schedule_cron(job_id, handler, timezone, payload=None,
                  misfire_grace_time=_DEFAULT_MISFIRE_GRACE_SECONDS,
                  name=None, **fields):
    return _default_engine().schedule_cron(
        job_id, handler, timezone, payload, misfire_grace_time, name,
        **fields)


def cancel(job_id):
    return _default_engine().cancel(job_id)


def list_jobs():
    return _default_engine().list_jobs()


def get_missed():
    return _default_engine().get_missed()


def start():
    return _default_engine().start()


def stop():
    return _default_engine().stop()


def shutdown():
    return _default_engine().shutdown()


def is_running():
    return _default_engine().is_running()
