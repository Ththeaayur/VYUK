"""B-D9 (2026-11-09) apscheduler timed-job engine.

Runs against a FAKE backend (in-memory jobs) + FAKE triggers + a FAKE
clock — no real waiting, no real scheduler thread — so the wrapper's
rules are proved on BOTH interpreters: duplicate refusal, naive-time
refusal, cancel-removes-from-store, misfire reporting, no-drift
recurring fires, restart survival (the fake store is shared across
backend instances, like the real SQLite file is across scheduler
instances). The missed log is the REAL stdlib-sqlite one (same file
as the jobs), so missed-record persistence is proved here too.

The REAL engine is proved where it exists: the test_real_engine_*
tests run only when apscheduler imports in this interpreter (the Qt
venv, where 3.11.3 is installed); on system python3 (no apscheduler)
they skip with the reason named, AND test_real_backend_degrades_
honestly asserts schedule() returns engine_unavailable there instead
of skipping. A suite that silently skips the scheduling checks on an
interpreter without the library is a lie, not a pass.

The real-Windows leg (a real job firing on his machine, surviving a
restart) is a field leg, owed, not this batch.
"""

import datetime
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from core.desktop import job_scheduler as js

try:
    import apscheduler  # noqa: F401
    _HAS_APS = True
except ImportError:
    _HAS_APS = False

UTC = datetime.timezone.utc
KOLKATA = ZoneInfo("Asia/Kolkata")
T0 = datetime.datetime(2026, 11, 9, 9, 0, 0, tzinfo=UTC)


# -- fake clock ----------------------------------------------------------
class _FakeClock:
    def __init__(self, now):
        self.now = now

    def advance(self, **kwargs):
        self.now = self.now + datetime.timedelta(**kwargs)

    def __call__(self):
        return self.now


# -- fake triggers (test scaffolding only; embody the intended-time rule)
class DateTrigger:
    def __init__(self, run_at):
        self.run_at = run_at

    def get_next_fire_time(self, previous, now):
        if previous is not None:
            return None
        return self.run_at if self.run_at > now else None


class IntervalTrigger:
    def __init__(self, start_at, weeks=0, days=0, hours=0, minutes=0,
                 seconds=0):
        self.start_at = start_at
        self.delta = datetime.timedelta(weeks=weeks, days=days,
                                        hours=hours, minutes=minutes,
                                        seconds=seconds)

    def get_next_fire_time(self, previous, now):
        if previous is not None:
            return previous + self.delta
        if self.start_at > now:
            return self.start_at
        n = (now - self.start_at) // self.delta + 1
        return self.start_at + n * self.delta


class CronTrigger:
    _DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4,
            "sat": 5, "sun": 6}

    def __init__(self, timezone, **fields):
        self.tz = ZoneInfo(timezone)
        self.fields = fields

    def _matches(self, dt):
        for key, val in self.fields.items():
            if key == "day_of_week":
                want = set()
                for part in str(val).split(","):
                    part = part.strip().lower()
                    want.add(self._DOW[part] if part in self._DOW
                             else int(part))
                if dt.weekday() not in want:
                    return False
            elif key == "second":
                if dt.second != int(val):
                    return False
            else:
                if getattr(dt, key) != int(val):
                    return False
        return True

    def get_next_fire_time(self, previous, now):
        start = previous if previous is not None else now
        cand = start.astimezone(self.tz).replace(second=0, microsecond=0)
        if cand <= start.astimezone(self.tz):
            cand += datetime.timedelta(minutes=1)
        for _ in range(60 * 24 * 366):
            if self._matches(cand):
                return cand
            cand += datetime.timedelta(minutes=1)
        return None


def _fake_trigger_factory(kind, **params):
    if kind == "date":
        return DateTrigger(params["run_at"])
    if kind == "interval":
        return IntervalTrigger(
            params["start_at"],
            weeks=params.get("weeks", 0), days=params.get("days", 0),
            hours=params.get("hours", 0), minutes=params.get("minutes", 0),
            seconds=params.get("seconds", 0))
    if kind == "cron":
        return CronTrigger(params["timezone"], **params["fields"])
    raise ValueError(f"unknown trigger kind {kind!r}")


# -- fake backend + fake store --------------------------------------------
class _FakeRecord:
    def __init__(self, job_id, trigger, handler, payload,
                 misfire_grace_time):
        self.id = job_id
        self.trigger = trigger
        self.handler = handler
        self.payload = payload
        self.misfire_grace_time = misfire_grace_time
        self.coalesce = True
        self.max_instances = 1
        self.next_run_time = None


class _FakeStore(dict):
    """Shared across backend instances — the restart-survival seam."""


class _FakeBackend:
    """In-memory APScheduler stand-in, same protocol as
    ``_RealBackend`` (see job_scheduler's docstring), plus
    ``fire_due(now)`` driven by the fake clock. Mirrors
    APScheduler's _process_jobs + run_job closely enough for the
    wrapper's rules: coalesce (one fire per missed window),
    misfire grace (beyond grace -> missed, never run late),
    next fire from the INTENDED schedule."""

    def __init__(self, store, now_fn):
        self._store = store
        self._now_fn = now_fn
        self._running = False

    def add_job(self, job_id, trigger, handler, payload,
                misfire_grace_time, coalesce, max_instances, name):
        if job_id in self._store:
            raise KeyError(f"job id {job_id!r} conflicts with an existing job")
        rec = _FakeRecord(job_id, trigger, handler, payload,
                          misfire_grace_time)
        rec.coalesce = coalesce
        rec.max_instances = max_instances
        rec.next_run_time = trigger.get_next_fire_time(None, self._now_fn())
        self._store[job_id] = rec
        return js._JobView(rec.id, rec.handler, rec.payload,
                           js._trigger_kind(trigger), rec.next_run_time,
                           rec.misfire_grace_time)

    def remove_job(self, job_id):
        if job_id not in self._store:
            raise KeyError(f"No job by the id of {job_id} was found")
        del self._store[job_id]

    def get_job(self, job_id):
        rec = self._store.get(job_id)
        if rec is None:
            return None
        return js._JobView(rec.id, rec.handler, rec.payload,
                           js._trigger_kind(rec.trigger),
                           rec.next_run_time, rec.misfire_grace_time)

    def get_jobs(self):
        views = [js._JobView(r.id, r.handler, r.payload,
                             js._trigger_kind(r.trigger),
                             r.next_run_time, r.misfire_grace_time)
                 for r in self._store.values()]
        views.sort(key=lambda v: (v.next_run_time is None,
                                  v.next_run_time))
        return views

    def start(self):
        self._running = True

    def pause(self):
        self._running = False

    def shutdown(self):
        self._running = False

    def is_running(self):
        return self._running

    def fire_due(self, now):
        outcomes = []
        due = sorted(
            (r for r in self._store.values()
             if r.next_run_time is not None and r.next_run_time <= now),
            key=lambda r: r.next_run_time)
        for rec in due:
            run_times = []
            nxt = rec.next_run_time
            guard = 0
            while nxt is not None and nxt <= now and guard < 10000:
                run_times.append(nxt)
                nxt = rec.trigger.get_next_fire_time(nxt, now)
                guard += 1
                if nxt is not None and run_times and nxt <= run_times[-1]:
                    break
            if rec.coalesce and run_times:
                run_times = run_times[-1:]
            for run_time in run_times:
                late = (now - run_time).total_seconds()
                if late > rec.misfire_grace_time:
                    outcomes.append({
                        "kind": "missed", "job_id": rec.id,
                        "scheduled_at": run_time,
                        "reason": f"missed its fire time by {late:.0f}s, "
                                  f"beyond the {rec.misfire_grace_time}s grace"})
                    continue
                try:
                    js._fire_job(rec.handler, rec.payload)
                    outcomes.append({"kind": "fired", "job_id": rec.id,
                                     "scheduled_at": run_time})
                except Exception as e:  # noqa: BLE001 — the error IS the outcome
                    outcomes.append({
                        "kind": "error", "job_id": rec.id,
                        "scheduled_at": run_time,
                        "reason": f"handler raised {type(e).__name__}: {e}"})
            last = run_times[-1] if run_times else None
            new_next = (rec.trigger.get_next_fire_time(last, now)
                        if last is not None else None)
            if new_next is None:
                self._store.pop(rec.id, None)
            else:
                rec.next_run_time = new_next
        return outcomes


# -- shared harness --------------------------------------------------------
class _SchedulerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tmp.name) / "jobs.sqlite3"
        self.clock = _FakeClock(T0)
        self.fired = []
        js.register_handler("record", lambda p: self.fired.append(p))
        js.register_handler("boom", lambda p: (_ for _ in ()).throw(
            RuntimeError("handler blew up")))
        self.engine = self._make_engine()

    def tearDown(self):
        js.unregister_handler("record")
        js.unregister_handler("boom")
        js.reset_handlers()
        self.tmp.cleanup()

    def _make_engine(self, store=None):
        backend = _FakeBackend(store if store is not None else _FakeStore(),
                               self.clock)
        return js.JobScheduler(store_path=self.store_path, backend=backend,
                               trigger_factory=_fake_trigger_factory,
                               now_fn=self.clock)


class TestFakeBackendScheduling(_SchedulerCase):
    def test_one_shot_fires_at_its_time_and_reports_id_and_next_fire(self):
        run_at = T0 + datetime.timedelta(minutes=30)
        got = self.engine.schedule_one_shot(
            "remind-tea", run_at, "record", {"what": "tea"})
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["job_id"], "remind-tea")
        self.assertEqual(got["next_fire"], run_at.isoformat())
        self.assertEqual(got["trigger"], "date")
        listed = self.engine.list_jobs()
        self.assertEqual([j["job_id"] for j in listed["jobs"]],
                         ["remind-tea"])
        self.engine.tick(T0 + datetime.timedelta(minutes=29))
        self.assertEqual(self.fired, [])
        tick = self.engine.tick(T0 + datetime.timedelta(minutes=30))
        self.assertEqual(len(tick["fired"]), 1)
        self.assertEqual(self.fired, [{"what": "tea"}])
        self.assertEqual(self.engine.list_jobs()["jobs"], [])

    def test_recurring_interval_fires_without_drifting(self):
        start = T0 + datetime.timedelta(hours=1)
        got = self.engine.schedule_interval(
            "hourly", start, "record", {"n": 1}, hours=1)
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["next_fire"], start.isoformat())
        # Fire 1: on time. Next fire must be start+2h, NOT tick-time+1h.
        self.engine.tick(start)
        self.assertEqual(len(self.fired), 1)
        nxt = self.engine.list_jobs()["jobs"][0]["next_fire"]
        self.assertEqual(nxt, (start + datetime.timedelta(hours=1)).isoformat())
        # Fire 2: 30 minutes LATE (within the 1h grace) — fires, and the
        # NEXT fire is still start+3h: a late run never drifts the schedule.
        self.engine.tick(start + datetime.timedelta(hours=1, minutes=30))
        self.assertEqual(len(self.fired), 2)
        nxt = self.engine.list_jobs()["jobs"][0]["next_fire"]
        self.assertEqual(nxt, (start + datetime.timedelta(hours=2)).isoformat())

    def test_job_survives_restart(self):
        store = _FakeStore()
        first = self._make_engine(store=store)
        run_at = T0 + datetime.timedelta(hours=2)
        self.assertTrue(first.schedule_one_shot(
            "after-restart", run_at, "record", {"x": 1})["ok"])
        # A NEW engine on the SAME store (the restart) still lists it…
        second = self._make_engine(store=store)
        listed = second.list_jobs()["jobs"]
        self.assertEqual([j["job_id"] for j in listed], ["after-restart"])
        self.assertEqual(listed[0]["next_fire"], run_at.isoformat())
        # …and fires it exactly once.
        second.tick(run_at)
        self.assertEqual(self.fired, [{"x": 1}])
        self.assertEqual(second.list_jobs()["jobs"], [])

    def test_cancelled_job_never_fires(self):
        run_at = T0 + datetime.timedelta(hours=1)
        self.engine.schedule_one_shot("doomed", run_at, "record")
        got = self.engine.cancel("doomed")
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["was_next_fire"], run_at.isoformat())
        self.engine.tick(run_at + datetime.timedelta(hours=1))
        self.assertEqual(self.fired, [])
        self.assertEqual(self.engine.list_jobs()["jobs"], [])
        again = self.engine.cancel("doomed")
        self.assertFalse(again["ok"])
        self.assertIn("no job", again["reason"])

    def test_misfire_is_reported_never_silently_dropped(self):
        run_at = T0 + datetime.timedelta(minutes=10)
        self.engine.schedule_one_shot(
            "late-one", run_at, "record", misfire_grace_time=60)
        # 2 hours late, grace 60s: never runs late…
        tick = self.engine.tick(T0 + datetime.timedelta(hours=2, minutes=10))
        self.assertEqual(self.fired, [])
        self.assertEqual(tick["fired"], [])
        # …and IS reported in the persisted missed log.
        missed = self.engine.get_missed()["missed"]
        self.assertEqual(len(missed), 1)
        self.assertEqual(missed[0]["job_id"], "late-one")
        self.assertEqual(missed[0]["kind"], "missed")
        # The report survives a restart (a new engine, same store file).
        second = self._make_engine(store=_FakeStore())
        second._store_path = self.store_path
        self.assertEqual(
            [m["job_id"] for m in second.get_missed()["missed"]],
            ["late-one"])

    def test_recurring_misfire_coalesces_to_one_report(self):
        start = T0 + datetime.timedelta(minutes=5)
        self.engine.schedule_interval(
            "every-5", start, "record", minutes=5, misfire_grace_time=60)
        # Jump 1 hour and 7 minutes ahead: 12 windows missed, and even
        # the LATEST window (1h5m) is 2 minutes old — beyond the 60s
        # grace. Coalesce -> ONE report, zero fires (the job is NOT run
        # late, and it is NOT reported 12 times).
        self.engine.tick(T0 + datetime.timedelta(hours=1, minutes=7))
        self.assertEqual(self.fired, [])
        missed = self.engine.get_missed()["missed"]
        self.assertEqual(len(missed), 1)
        self.assertEqual(missed[0]["job_id"], "every-5")
        # …and the job is still scheduled, from its intended time.
        nxt = self.engine.list_jobs()["jobs"][0]["next_fire"]
        self.assertEqual(
            nxt, (T0 + datetime.timedelta(hours=1, minutes=10)).isoformat())

    def test_naive_time_is_refused(self):
        naive = datetime.datetime(2026, 11, 9, 10, 0, 0)
        got = self.engine.schedule_one_shot("naive", naive, "record")
        self.assertFalse(got["ok"])
        self.assertIn("naive", got["reason"])
        got = self.engine.schedule_interval(
            "naive-iv", naive, "record", hours=1)
        self.assertFalse(got["ok"])
        self.assertIn("naive", got["reason"])
        self.assertEqual(self.engine.list_jobs()["jobs"], [])

    def test_cron_without_timezone_is_refused(self):
        got = self.engine.schedule_cron("no-tz", "record", "",
                                        hour=9, minute=30)
        self.assertFalse(got["ok"])
        self.assertIn("timezone", got["reason"])
        got = self.engine.schedule_cron("bad-tz", "record", "Mars/Olympus",
                                        hour=9, minute=30)
        self.assertFalse(got["ok"])
        self.assertIn("timezone", got["reason"])

    def test_duplicate_job_id_refuses(self):
        run_at = T0 + datetime.timedelta(hours=1)
        first = self.engine.schedule_one_shot("dup", run_at, "record")
        self.assertTrue(first["ok"], first)
        second = self.engine.schedule_one_shot(
            "dup", run_at + datetime.timedelta(hours=1), "record")
        self.assertFalse(second["ok"])
        self.assertIn("already scheduled", second["reason"])
        self.assertEqual(len(self.engine.list_jobs()["jobs"]), 1)

    def test_unknown_handler_refuses_at_schedule_time(self):
        run_at = T0 + datetime.timedelta(hours=1)
        got = self.engine.schedule_one_shot(
            "ghost", run_at, "never-registered")
        self.assertFalse(got["ok"])
        self.assertIn("no handler", got["reason"])
        self.assertEqual(self.engine.list_jobs()["jobs"], [])

    def test_handler_unregistered_before_fire_is_recorded(self):
        run_at = T0 + datetime.timedelta(minutes=5)
        self.engine.schedule_one_shot("orphan", run_at, "record")
        js.unregister_handler("record")
        try:
            self.engine.tick(run_at)
        finally:
            js.register_handler("record",
                                lambda p: self.fired.append(p))
        self.assertEqual(self.fired, [])
        missed = self.engine.get_missed()["missed"]
        self.assertEqual(len(missed), 1)
        self.assertEqual(missed[0]["kind"], "error")
        self.assertIn("no handler", missed[0]["reason"])

    def test_handler_error_is_recorded_not_silent(self):
        run_at = T0 + datetime.timedelta(minutes=5)
        self.engine.schedule_one_shot("blows-up", run_at, "boom")
        self.engine.tick(run_at)
        missed = self.engine.get_missed()["missed"]
        self.assertEqual(len(missed), 1)
        self.assertEqual(missed[0]["kind"], "error")
        self.assertIn("RuntimeError", missed[0]["reason"])

    def test_payload_must_be_plain_data(self):
        run_at = T0 + datetime.timedelta(hours=1)
        got = self.engine.schedule_one_shot(
            "bad-payload", run_at, "record", {"fn": lambda: 1})
        self.assertFalse(got["ok"])
        self.assertIn("plain data", got["reason"])
        got = self.engine.schedule_one_shot(
            "good-payload", run_at, "record",
            {"nested": {"a": [1, 2.5, "x", True, None]}})
        self.assertTrue(got["ok"], got)

    def test_interval_validation(self):
        start = T0 + datetime.timedelta(hours=1)
        self.assertFalse(self.engine.schedule_interval(
            "zero", start, "record")["ok"])
        self.assertFalse(self.engine.schedule_interval(
            "neg", start, "record", hours=-1)["ok"])
        self.assertFalse(self.engine.schedule_interval(
            "bool", start, "record", hours=True)["ok"])
        self.assertFalse(self.engine.schedule_interval(
            "past", T0 - datetime.timedelta(hours=1), "record",
            hours=1)["ok"])
        self.assertFalse(self.engine.schedule_one_shot(
            "past-shot", T0 - datetime.timedelta(minutes=1),
            "record")["ok"])
        self.assertEqual(self.engine.list_jobs()["jobs"], [])

    def test_cron_fires_on_schedule_from_intended_time(self):
        # T0 is Monday 2026-11-09 09:00 UTC. Cron: Mon 09:30 Kolkata
        # (04:00 UTC) — next fire is NEXT Monday 09:30 Kolkata.
        got = self.engine.schedule_cron(
            "monday-briefing", "record", "Asia/Kolkata",
            hour=9, minute=30, day_of_week="mon")
        self.assertTrue(got["ok"], got)
        expected = datetime.datetime(2026, 11, 16, 9, 30, tzinfo=KOLKATA)
        self.assertEqual(got["next_fire"], expected.isoformat())
        # Firing it advances to the NEXT Monday, not tick-time + 7 days
        # from a late tick: tick 1 day late (within the 1h grace? no —
        # 1 day is beyond grace, so it MISSES and reports instead).
        self.engine.tick(expected + datetime.timedelta(days=1))
        self.assertEqual(self.fired, [])
        self.assertEqual(len(self.engine.get_missed()["missed"]), 1)
        nxt = self.engine.list_jobs()["jobs"][0]["next_fire"]
        self.assertEqual(
            nxt,
            datetime.datetime(2026, 11, 23, 9, 30,
                              tzinfo=KOLKATA).isoformat())

    def test_cron_next_monday_fires_on_time(self):
        got = self.engine.schedule_cron(
            "monday-briefing", "record", "Asia/Kolkata",
            hour=9, minute=30, day_of_week="mon")
        self.assertTrue(got["ok"], got)
        expected = datetime.datetime(2026, 11, 16, 9, 30, tzinfo=KOLKATA)
        self.engine.tick(expected)
        self.assertEqual(len(self.fired), 1)
        nxt = self.engine.list_jobs()["jobs"][0]["next_fire"]
        self.assertEqual(
            nxt,
            datetime.datetime(2026, 11, 23, 9, 30,
                              tzinfo=KOLKATA).isoformat())

    def test_tick_is_refused_on_the_real_backend(self):
        engine = js.JobScheduler(store_path=self.store_path)
        got = engine.tick()
        self.assertFalse(got["ok"])
        self.assertIn("backend", got["reason"])


class TestEngineAvailability(unittest.TestCase):
    def test_engine_availability_matches_reality(self):
        """The wrapper agrees with the interpreter: apscheduler is
        importable here iff engine_available() says so."""
        try:
            import apscheduler  # noqa: F401
            real = True
        except ImportError:
            real = False
        self.assertEqual(js.engine_available(), real)
        status = js.engine_status()
        self.assertEqual(status["available"], real)
        if real:
            self.assertIsNotNone(status["version"])
        else:
            self.assertIsNone(status["version"])
            self.assertIn("not installed", status["reason"])

    @unittest.skipUnless(not _HAS_APS,
                         "apscheduler IS installed in this interpreter")
    def test_real_backend_degrades_honestly_when_engine_missing(self):
        run_at = (datetime.datetime.now(UTC)
                  + datetime.timedelta(hours=1))
        js.register_handler("record", lambda p: None)
        try:
            got = js.schedule_one_shot("x", run_at, "record")
        finally:
            js.unregister_handler("record")
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "engine_unavailable")


@unittest.skipUnless(_HAS_APS,
                     "apscheduler not installed in this interpreter "
                     "(system python3) — real-engine leg runs in the "
                     "Qt venv, where 3.11.3 is installed")
class TestRealEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tmp.name) / "jobs.sqlite3"
        self.fired = []
        js.register_handler("record", lambda p: self.fired.append(p))
        self.engines = []

    def tearDown(self):
        for engine in self.engines:
            try:
                engine.shutdown()
            except Exception:  # noqa: BLE001 — teardown must not mask failures
                pass
        js.unregister_handler("record")
        js.reset_handlers()
        self.tmp.cleanup()

    def _real_engine(self):
        engine = js.JobScheduler(store_path=self.store_path)
        self.engines.append(engine)
        return engine

    def test_real_engine_one_shot_fires_exactly_once(self):
        engine = self._real_engine()
        run_at = (datetime.datetime.now(UTC)
                  + datetime.timedelta(seconds=0.4))
        got = engine.schedule_one_shot("real-once", run_at, "record",
                                       {"v": 1})
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["trigger"], "date")
        self.assertTrue(engine.start()["ok"])
        time.sleep(1.5)
        self.assertEqual(self.fired, [{"v": 1}])
        self.assertEqual(engine.list_jobs()["jobs"], [])

    def test_real_engine_job_survives_restart(self):
        first = self._real_engine()
        run_at = (datetime.datetime.now(UTC)
                  + datetime.timedelta(hours=2))
        got = first.schedule_interval("real-hourly", run_at, "record",
                                      hours=1)
        self.assertTrue(got["ok"], got)
        # The job is in the store file itself (a fresh store instance
        # on the same file reads it — no second engine needed)…
        store = js.SQLiteJobStore()(str(self.store_path))
        self.assertEqual([j.id for j in store.get_all_jobs()],
                         ["real-hourly"])
        # …and a NEW engine on the same file, after a real teardown
        # of the first (shutdown() is the restart; stop() only
        # pauses, and two live engines on one file could double-fire),
        # still lists the job with the same next fire.
        first.shutdown()
        second = self._real_engine()
        listed = second.list_jobs()["jobs"]
        self.assertEqual([j["job_id"] for j in listed], ["real-hourly"])
        self.assertEqual(listed[0]["next_fire"], run_at.isoformat())
        # …and the store file is the real SQLite shape.
        conn = sqlite3.connect(str(self.store_path))
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        self.assertIn("apscheduler_jobs", tables)
        self.assertIn("scheduler_missed", tables)

    def test_real_engine_cron_next_fire_matches_schedule(self):
        engine = self._real_engine()
        now_kolkata = datetime.datetime.now(KOLKATA)
        target_minute = (now_kolkata + datetime.timedelta(minutes=2))
        target_minute = target_minute.replace(second=0, microsecond=0)
        got = engine.schedule_cron(
            "real-cron", "record", "Asia/Kolkata",
            hour=target_minute.hour, minute=target_minute.minute)
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["next_fire"], target_minute.isoformat())

    def test_real_engine_missed_job_is_reported(self):
        engine = self._real_engine()
        run_at = (datetime.datetime.now(UTC)
                  + datetime.timedelta(seconds=0.3))
        got = engine.schedule_one_shot("real-late", run_at, "record",
                                       misfire_grace_time=1)
        self.assertTrue(got["ok"], got)
        # Scheduling starts the engine lazily, so stop it at once:
        # the job now sits in the store while the engine is down.
        # Restart AFTER the fire time + grace have passed: the job
        # must be reported missed, never run late.
        self.assertTrue(engine.stop()["ok"])
        time.sleep(2.0)
        self.assertTrue(engine.start()["ok"])
        time.sleep(1.5)
        self.assertEqual(self.fired, [])
        missed = engine.get_missed()["missed"]
        self.assertEqual([m["job_id"] for m in missed], ["real-late"])
        self.assertEqual(missed[0]["kind"], "missed")

    def test_real_store_roundtrip_across_instances(self):
        """The SQLiteJobStore itself: add/get/remove/due across two
        store instances on the same file (restart survival at the
        store level, no scheduler thread). The job is added through
        a real (unstarted) BackgroundScheduler, so the Job object
        is built by APScheduler itself."""
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.date import DateTrigger

        cls = js.SQLiteJobStore()
        path = str(self.store_path)
        run_at = datetime.datetime.now(UTC) + datetime.timedelta(hours=1)
        sched = BackgroundScheduler(
            jobstores={"default": cls(path)}, timezone="UTC")
        sched.start()
        try:
            sched.add_job("core.desktop.job_scheduler:_fire_job",
                          trigger=DateTrigger(run_date=run_at),
                          id="store-job", args=["record", {"s": 1}],
                          misfire_grace_time=3600, coalesce=True,
                          max_instances=1)
        finally:
            sched.shutdown(wait=True)
        second = cls(path)
        found = second.lookup_job("store-job")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, "store-job")
        self.assertEqual(found.args[1], {"s": 1})
        due = second.get_due_jobs(datetime.datetime.now(UTC)
                                  + datetime.timedelta(hours=2))
        self.assertEqual([j.id for j in due], ["store-job"])
        second.remove_job("store-job")
        third = cls(path)
        self.assertIsNone(third.lookup_job("store-job"))
        self.assertEqual(third.get_all_jobs(), [])


if __name__ == "__main__":
    unittest.main()
