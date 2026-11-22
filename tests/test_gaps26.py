"""GAPS-26 regression suite: review section 6.7, S-series
(scheduler + agents).

Pins: the GUI boot hook that starts the scheduled-agents engine
(S-V1/S-V2), the persisted-job-fires-after-restart simulation,
rider-failure reporting in RoutineScheduler.tick (S-V3), the
single briefing path + legacy migration/drop (S-V4), the
proactivity measurement-only posture with the autonomous loop
OWED (S-V5), the unified listing + misfire details (S-V6), and
the morning digest agent (S-V7). No new tool: census pins
(755/382/373/164, params 2077, grouped 752, closed 754).

All behavioural checks run against a fake job backend (dict
store, controllable clock — mirrors tests/test_job_scheduler.py)
and tempfile stores — they never touch the tree's data/.

Run from the tree root with PYTHONPATH=. :
    python -m unittest tests.test_gaps26
"""

import contextlib
import datetime
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from core import scheduled_agents as sa
from core.desktop import job_scheduler as js

TREE = Path(__file__).resolve().parent.parent
UTC = datetime.timezone.utc
KOLKATA = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


# -- fake scheduler backend (mirrors tests/test_job_scheduler.py) ---------
class _FakeCron:
    def __init__(self, timezone, **fields):
        self.fields = fields

    def get_next_fire_time(self, previous, now):
        import datetime as _dt
        cand = now.astimezone(KOLKATA).replace(second=0, microsecond=0)
        if previous is not None:
            cand = previous.astimezone(KOLKATA) + _dt.timedelta(days=1)
            cand = cand.replace(hour=int(self.fields["hour"]),
                                minute=int(self.fields["minute"]))
            return cand.astimezone(UTC)
        if (cand.hour, cand.minute) < (int(self.fields["hour"]),
                                       int(self.fields["minute"])):
            cand = cand.replace(hour=int(self.fields["hour"]),
                                minute=int(self.fields["minute"]))
            return cand.astimezone(UTC)
        cand = (cand + _dt.timedelta(days=1)).replace(
            hour=int(self.fields["hour"]), minute=int(self.fields["minute"]))
        return cand.astimezone(UTC)


def _fake_trigger_factory(kind, **params):
    if kind == "cron":
        return _FakeCron(params["timezone"], **params["fields"])
    raise ValueError("suite probe only drives cron, got %r" % kind)


class _FakeBackend:
    def __init__(self, store, now_fn):
        self._store = store
        self._now_fn = now_fn
        self._running = False

    def add_job(self, job_id, trigger, handler, payload,
                misfire_grace_time, coalesce, max_instances, name):
        if job_id in self._store:
            raise KeyError("job id %r conflicts" % job_id)
        rec = {"id": job_id, "trigger": trigger, "handler": handler,
               "payload": payload, "misfire_grace_time": misfire_grace_time,
               "coalesce": coalesce, "max_instances": max_instances,
               "next_run_time": trigger.get_next_fire_time(None,
                                                           self._now_fn())}
        self._store[job_id] = rec
        return js._JobView(rec["id"], rec["handler"], rec["payload"],
                           js._trigger_kind(trigger), rec["next_run_time"],
                           rec["misfire_grace_time"])

    def remove_job(self, job_id):
        if job_id not in self._store:
            raise KeyError("No job by the id %r" % job_id)
        del self._store[job_id]

    def get_job(self, job_id):
        rec = self._store.get(job_id)
        if rec is None:
            return None
        return js._JobView(rec["id"], rec["handler"], rec["payload"],
                           js._trigger_kind(rec["trigger"]),
                           rec["next_run_time"], rec["misfire_grace_time"])

    def get_jobs(self):
        views = [js._JobView(r["id"], r["handler"], r["payload"],
                             js._trigger_kind(r["trigger"]),
                             r["next_run_time"], r["misfire_grace_time"])
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
             if r["next_run_time"] is not None and r["next_run_time"] <= now),
            key=lambda r: r["next_run_time"])
        for rec in due:
            nxt = rec["next_run_time"]
            late = (now - nxt).total_seconds()
            if late > rec["misfire_grace_time"]:
                outcomes.append({"kind": "missed", "job_id": rec["id"],
                                 "scheduled_at": nxt,
                                 "reason": "beyond grace"})
                continue
            try:
                js._fire_job(rec["handler"], rec["payload"])
                outcomes.append({"kind": "fired", "job_id": rec["id"],
                                 "scheduled_at": nxt})
            except Exception as exc:  # noqa: BLE001 — the error IS the outcome
                outcomes.append({"kind": "error", "job_id": rec["id"],
                                 "scheduled_at": nxt,
                                 "reason": "%s: %s" % (type(exc).__name__,
                                                       exc)})
            nxt2 = rec["trigger"].get_next_fire_time(nxt, now)
            if nxt2 is None:
                self._store.pop(rec["id"], None)
            else:
                rec["next_run_time"] = nxt2
        return outcomes


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._clock = [datetime.datetime(2026, 11, 14, 9, 0, tzinfo=UTC)]
        self._store = {}
        self._store_path = Path(self._tmp.name) / "jobs.sqlite"
        js.reset_handlers()
        js.set_backend(_FakeBackend(self._store, lambda: self._clock[0]))
        js.set_store_path(str(self._store_path))

    def tearDown(self):
        js.reset_handlers()
        js.reset_backend()
        try:
            js.set_store_path(None)
        except Exception:  # noqa: BLE001 — teardown must not mask failures
            pass
        self._tmp.cleanup()

    def _now_fn(self):
        return self._clock[0]

    def _engine(self):
        return js.JobScheduler(
            store_path=self._store_path,
            backend=_FakeBackend(self._store, self._now_fn),
            trigger_factory=_fake_trigger_factory,
            now_fn=self._now_fn)

    def _schedule_probe(self, engine):
        # Scheduling refuses without a registered handler — register
        # a no-op first (the restart simulation wipes it afterwards).
        js.register_handler("operative_agent", lambda p: None)
        scheduled = engine.schedule_cron(
            job_id="probe_job", handler="operative_agent",
            timezone="Asia/Kolkata",
            payload={"job_id": "probe_job", "kind": "steps"},
            misfire_grace_time=3600, name="Probe", hour=14, minute=31)
        self.assertTrue(scheduled.get("ok"), scheduled)
        return scheduled


class BootHookTests(_Base):
    def test_gui_boot_calls_ensure_engine_running(self):
        """S-V1: the GUI boot path starts the engine — GAPS-28
        (G-V8) moved the call into the service host: the window
        starts the host in _start_background_services, and the
        host calls ensure_engine_running."""
        src = (TREE / "gui/window_services_trust.py").read_text(
            encoding="utf-8")
        body = src.split("def _start_background_services", 1)[1]
        self.assertIn("host.start()", body)
        host = (TREE / "core/service_host.py").read_text(
            encoding="utf-8")
        self.assertIn("ensure_engine_running", host)

    def test_ensure_engine_running_starts_engine_and_registers_handler(self):
        """S-V1/S-V2: the hook starts the engine and registers the
        production operative-agent handler."""
        out = sa.ensure_engine_running()
        self.assertTrue(out.get("ok"), out)
        self.assertIs(js._HANDLERS.get("operative_agent"),
                      sa.operative_agent_handler)
        self.assertTrue(js.engine_status()["running"])


class RestartTests(_Base):
    def test_persisted_job_fires_after_simulated_restart(self):
        """S-V2: a job persisted before the restart fires after it,
        once the boot hook runs — the engine is never started by
        the schedule alone. (No tick before the hook: an error
        tick advances a cron past its slot.)"""
        fired = []
        js.register_handler("operative_agent",
                            lambda p: fired.append(p.get("job_id")))
        engine = self._engine()
        self._schedule_probe(engine)
        # Simulated restart: handlers are volatile, engine is down.
        js.reset_handlers()
        self._clock[0] = datetime.datetime(2026, 11, 14, 9, 1, tzinfo=UTC)
        # The boot hook, exactly as the GUI runs it.
        self.assertTrue(sa.ensure_engine_running().get("ok"))
        self.assertIs(js._HANDLERS.get("operative_agent"),
                      sa.operative_agent_handler)
        # Swap in an observer so the fire is visible without
        # running a real job (a production-handler fire would
        # record a refused run in the tree's ledger).
        js.register_handler("operative_agent",
                            lambda p: fired.append(p.get("job_id")))
        result = js._default_engine().tick(now=self._clock[0])
        self.assertEqual([f.get("job_id") for f in result.get("fired", [])],
                         ["probe_job"])
        self.assertEqual(fired, ["probe_job"])

    def test_restart_without_hook_reports_error(self):
        """S-V2 (mechanism): without the hook, the persisted job
        reports an error after a restart — the handler is gone
        (LookupError), so the job cannot fire."""
        engine = self._engine()
        self._schedule_probe(engine)
        js.reset_handlers()
        self._clock[0] = datetime.datetime(2026, 11, 14, 9, 1, tzinfo=UTC)
        result = js._default_engine().tick(now=self._clock[0])
        self.assertEqual([r.get("kind") for r in result.get("reported", [])],
                         ["error"])
        self.assertEqual(result.get("fired"), [])


class RiderTests(unittest.TestCase):
    def test_no_bare_except_pass_in_routine_tick(self):
        """S-V3: no rider in RoutineScheduler.tick() fails
        silently any more."""
        src = (TREE / "core/brain/routine_scheduler.py").read_text(
            encoding="utf-8")
        tick = src[src.index("def tick("):]
        bare = len(re.findall(r"except Exception:[^\n]*\n\s*pass(?:\s*#.*)?\n",
                              tick))
        self.assertEqual(bare, 0)

    def test_failing_rider_is_reported_and_others_still_run(self):
        """S-V3: a failing rider is printed and the other riders
        still run — the tick never swallows a failure."""
        import core.brain.routine_scheduler as rs
        import core.w6_wiring as w6
        import core.w7_wiring as w7
        import core.w10_wiring as w10
        orig = (w7.daily_tick, w6.w6_tick, w10.backup_tick)
        calls = []

        def _boom(*a, **k):
            raise RuntimeError("rider exploded (suite probe)")

        def _ok6(*a, **k):
            calls.append("w6")
            return {}

        def _ok10(*a, **k):
            calls.append("w10")
            return {}
        try:
            w7.daily_tick = _boom
            w6.w6_tick = _ok6
            w10.backup_tick = _ok10
            sched = rs.RoutineScheduler(
                on_fire=lambda r: None,
                now_fn=lambda: datetime.datetime.now(UTC))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                sched.tick()
            out = buf.getvalue()
            self.assertIn("[RoutineScheduler] daily-alarm tick failed",
                          out)
            self.assertIn("rider exploded", out)
            self.assertIn("w6", calls)
            self.assertIn("w10", calls)
        finally:
            w7.daily_tick, w6.w6_tick, w10.backup_tick = orig


class BriefingTests(unittest.TestCase):
    def test_no_scheduler_briefing_home(self):
        """S-V4: add_daily_briefing is gone and the scheduler
        never constructs a kind='briefing' reminder; the single
        briefing home is the QTimer/live-session path."""
        from core.scheduler import ProactiveScheduler
        self.assertFalse(hasattr(ProactiveScheduler, "add_daily_briefing"))
        src = (TREE / "core/scheduler.py").read_text(encoding="utf-8")
        self.assertNotIn('kind="briefing"', src)
        self.assertNotIn("kind='briefing'", src)

    def test_enable_briefing_drives_the_single_path(self):
        """S-V4: _enable_briefing writes the presence keys instead
        of creating a scheduler-side briefing."""
        src = (TREE / "gui/window_memory_learned.py").read_text(
            encoding="utf-8")
        self.assertIsNone(re.search(r"add_daily_briefing\s*\(", src))
        self.assertIn("presence/briefing_enabled", src)
        self.assertIn("presence/briefing_time", src)

    def test_legacy_record_dropped_and_migrated(self):
        """S-V4: a legacy briefing record is dropped on load, and
        the migration preserves its time and rewrites the store."""
        from core.scheduler import (
            ProactiveScheduler,
            migrate_legacy_briefing_records,
        )
        tmp = tempfile.TemporaryDirectory()
        try:
            path = str(Path(tmp.name) / "store.json")
            payload = {
                "version": 1,
                "reminders": [
                    {"label": "morning_briefing",
                     "message": "Good morning.",
                     "fire_at": "2030-01-01 08:00",
                     "repeat": True, "repeat_kind": "daily",
                     "kind": "briefing",
                     "briefing_hour": 7, "briefing_minute": 30},
                    {"label": "once", "message": "once",
                     "fire_at": "2030-01-01 12:00",
                     "repeat": False, "repeat_kind": None,
                     "kind": "reminder"},
                ],
                "fired_history": [], "missed_history": [],
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            self.assertEqual(migrate_legacy_briefing_records(path), (7, 30))
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual([r["label"] for r in payload["reminders"]],
                             ["once"])
            self.assertIsNone(migrate_legacy_briefing_records(path))
            # And the drop path, for a store that was never migrated:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "version": 1,
                    "reminders": [
                        {"label": "morning_briefing",
                         "message": "Good morning.",
                         "fire_at": "2030-01-01 08:00",
                         "repeat": True, "repeat_kind": "daily",
                         "kind": "briefing",
                         "briefing_hour": 7, "briefing_minute": 30}],
                    "fired_history": [], "missed_history": []}, f)
            s = ProactiveScheduler(store_path=path)
            s._load_and_apply_policy(
                datetime.datetime(2026, 1, 1, tzinfo=UTC))
            self.assertEqual(s.get_all_reminders(), [])
        finally:
            tmp.cleanup()


class UnifiedListingTests(_Base):
    def _temp_sources(self):
        import core.brain.planner as facade
        import core.brain.planner_routines as proutines
        import core.brain.planner_storage as pstore
        import core.brain.routine_scheduler as rs
        import core.scheduler as sched_mod
        tmp = Path(self._tmp.name) / "sources"
        tmp.mkdir()
        old = (sa._DATA_DIR, sched_mod.scheduler, pstore._ROUTINES_PATH,
               proutines._ROUTINES_PATH, facade._ROUTINES_PATH,
               rs._STATE_PATH)
        sa._DATA_DIR = tmp
        from core.scheduler import ProactiveScheduler
        temp_sched = ProactiveScheduler(
            store_path=tmp / "scheduler_reminders.json")
        sched_mod.scheduler = temp_sched
        for mod in (pstore, proutines, facade):
            mod._ROUTINES_PATH = tmp / "routines.json"
        rs._STATE_PATH = tmp / "routine_state.json"
        (tmp / "routines.json").write_text(json.dumps([
            {"name": "Evening tidy",
             "schedule": {"kind": "daily", "time": "18:00"}}]),
            encoding="utf-8")

        def restore():
            (sa._DATA_DIR, sched_mod.scheduler, pstore._ROUTINES_PATH,
             proutines._ROUTINES_PATH, facade._ROUTINES_PATH,
             rs._STATE_PATH) = old
        return tmp, temp_sched, restore

    def test_list_covers_jobs_reminders_routines_with_source_and_address(self):
        """S-V6: the tool's list is the ONE listing — agent jobs,
        reminders and routines, each tagged with its source and
        its address (job_id / label / name)."""
        tmp, temp_sched, restore = self._temp_sources()
        try:
            engine = self._engine()
            self._schedule_probe(engine)
            # Parent fix (GAPS-30 verification, 2026-11-15): the
            # hardcoded 2026-11-14 18:00 UTC expired — add_reminder
            # refuses a past time, so this errored on all four
            # neighbour combos once the run crossed it (GAPS-30
            # r2). The reminder only needs a future time (same
            # pattern as test_gaps28.py); the assertions check
            # source/address, never the time.
            temp_sched.add_reminder(
                "call_mum", datetime.datetime.now(UTC)
                + datetime.timedelta(hours=2),
                "Call mum", repeat=False)
            listed = sa.scheduled_agents("list")
            self.assertTrue(listed.get("ok"), listed)
            self.assertIsNone(listed.get("reminders_error"))
            self.assertIsNone(listed.get("routines_error"))
            jobs = [j for j in listed["jobs"]
                    if j.get("job_id") == "probe_job"]
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["source"], "agent_job")
            self.assertEqual(jobs[0]["address"], "probe_job")
            rems = [r for r in listed["reminders"]
                    if r.get("label") == "call_mum"]
            self.assertEqual(len(rems), 1)
            self.assertEqual(rems[0]["source"], "reminder")
            self.assertEqual(rems[0]["address"], "call_mum")
            routs = [r for r in listed["routines"]
                     if r.get("name") == "Evening tidy"]
            self.assertEqual(len(routs), 1)
            self.assertEqual(routs[0]["source"], "routine")
            self.assertEqual(routs[0]["address"], "Evening tidy")
            # Parent fix (GAPS-30 verification, 2026-11-15): the
            # routine's next_run is real LOCAL now, not the fake
            # clock — a never-run daily 18:00 routine always shows
            # today's 18:00 (planner_routines.next_run, last_run
            # None). The hardcoded "2026-11-14 18:00" expired at
            # midnight, same time bomb as the reminder above.
            expected_next = datetime.datetime.now().replace(
                hour=18, minute=0, second=0, microsecond=0)
            self.assertEqual(
                routs[0]["next_run"],
                expected_next.strftime("%Y-%m-%d %H:%M"))
        finally:
            restore()

    def test_status_reports_misfire_details(self):
        """S-V6: status reports misfires as DETAILS — the missed
        jobs (persisted) and the missed reminders — not a count."""
        tmp, temp_sched, restore = self._temp_sources()
        try:
            # A missed job: schedule, restart (handlers gone), tick
            # — the fire fails and is recorded in the missed log.
            engine = self._engine()
            self._schedule_probe(engine)
            js.reset_handlers()
            self._clock[0] = datetime.datetime(2026, 11, 14, 9, 1,
                                               tzinfo=UTC)
            js._default_engine().tick(now=self._clock[0])
            # A missed reminder: overdue past the grace in the store.
            overdue = {
                "version": 1,
                "reminders": [{
                    "label": "missed_call",
                    "message": "missed",
                    "fire_at": "2026-11-13 07:00",
                    "repeat": False, "repeat_kind": None,
                    "kind": "reminder"}],
                "fired_history": [], "missed_history": []}
            (tmp / "scheduler_reminders.json").write_text(
                json.dumps(overdue), encoding="utf-8")
            from core.scheduler import ProactiveScheduler
            import core.scheduler as sched_mod
            sched_mod.scheduler = ProactiveScheduler(
                store_path=tmp / "scheduler_reminders.json")
            sched_mod.scheduler._load_and_apply_policy(self._clock[0])
            status = sa.scheduled_agents("status")
            self.assertTrue(status.get("ok"), status)
            self.assertEqual(status["missed"], 1)
            self.assertEqual(
                [m.get("job_id") for m in status["missed_jobs"]],
                ["probe_job"])
            self.assertEqual(
                [m.get("label") for m in status["missed_reminders"]],
                ["missed_call"])
        finally:
            restore()


class MeasurementOnlyTests(unittest.TestCase):
    def test_proactivity_states_measurement_only(self):
        """S-V5: the scorer is measurement-only by documented
        design — it never acts, acting stays behind the owner's
        decision (C-14)."""
        src = (TREE / "core/proactivity.py").read_text(encoding="utf-8")
        self.assertIn("MEASUREMENT MODE ONLY", src)
        self.assertIn("It NEVER acts", src)

    def test_no_production_actor_on_scores(self):
        """S-V5: no production module calls score_intervention()
        except its own module (the tool + bench consume it as
        measurement). An autonomous intervention loop is OWED,
        not built — see the GAPS-26 report."""
        hits = []
        for d in ("core", "gui"):
            for p in sorted((TREE / d).rglob("*.py")):
                if p.name == "proactivity.py":
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
                if re.search(r"score_intervention\s*\(", text):
                    hits.append(str(p.relative_to(TREE)))
        self.assertEqual(hits, [])


class DigestTests(unittest.TestCase):
    def test_morning_digest_agent_exists(self):
        """S-V7: the morning digest agent exists (GAPS-8) — its
        manifest allows only speak_text, and the digest is
        generated, never the retired fixed string."""
        from core.scheduled_agents import DIGEST_TIMEZONE
        self.assertEqual(DIGEST_TIMEZONE, "Asia/Kolkata")
        src = (TREE / "core/scheduled_agents.py").read_text(
            encoding="utf-8")
        self.assertIn("build_digest", src)
        self.assertIn('"kind": "digest"', src)
        self.assertNotIn("Good morning. Today is", src)


class CensusTests(unittest.TestCase):
    def test_census_unchanged(self):
        """No new tool: AUDIT-TOTALS and the live registry agree
        on 755/382/373/164, params 2077, grouped 752, closed 754."""
        totals = re.search(
            r'AUDIT-TOTALS: \{"total": (\d+), "read": (\d+), '
            r'"effectful": (\d+), "confirm_gated": (\d+), '
            r'"seam_wired": (\d+), "already_verified_in_handler": (\d+), '
            r'"honestly_unverifiable": (\d+), "next_pass_pool": (\d+)\}',
            (TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
                encoding="utf-8"))
        self.assertIsNotNone(totals)
        self.assertEqual(tuple(int(g) for g in totals.groups()),
                         (769, 384, 385, 169, 44, 319, 22, 0))  # INTENTIONAL — field-check fix #32 (2026-11-18): 755/373/307 -> 756/374/308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756/374/164/308 -> 757/375/165/309 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        from core.funnel.tools_registry import tools as _TOOLS
        from core.funnel.tools_gate import _CONFIRM_GATE_TOOLS
        import core.catalogue_groups_data as _cat
        tools = _TOOLS._tools
        grouped = set()
        for members in _cat.GROUP_MEMBERS.values():
            grouped.update(members)
        params = sum(len((s.get("parameters") or {}).get("properties")
                         or {}) for s in tools.values())
        self.assertEqual(len(tools), 769)  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertEqual(params, 2121)  #Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (Group E, 2026-11-22): 2079 -> 2095 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertEqual(len([n for n in tools
                              if n in _CONFIRM_GATE_TOOLS]), 169)  # INTENTIONAL — round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan (confirm-gated); fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(len(grouped), 766)  # INTENTIONAL — field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 755 -> 759 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertEqual(len(tools) - 1, 768)  # closed = total - 1; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 757 -> 761 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169


if __name__ == "__main__":
    unittest.main()
