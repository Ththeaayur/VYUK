"""GAPS-8 (BLOCKS #7, 2026-11-13): scheduled agents — the agent
layer over the existing APScheduler engine, plus the morning digest.

The agent-level tests (manifest, digest, enforcement) run on BOTH
interpreters with a fake registry and fake ledger — no engine
needed. The tool-level tests run the REAL engine on Qt (a temp
store, so they never touch data/) and assert the honest
engine_unavailable refusal on system python3 (which has no
apscheduler) instead of skipping — a suite that silently skips the
scheduling checks on an interpreter without the library is a lie,
not a pass (the test_job_scheduler.py rule).

The real-Windows leg (a real job firing on his machine, surviving
a restart, the 7am digest speaking on a real morning) is a field
leg, owed, not this batch.
"""

import datetime
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from core import scheduled_agents as sa
from core.desktop import job_scheduler as js

try:
    import apscheduler  # noqa: F401
    _HAS_APS = True
except ImportError:
    _HAS_APS = False

UTC = datetime.timezone.utc
KOLKATA = ZoneInfo("Asia/Kolkata")

_FAKE_SUMMARY = ("Yesterday you recorded a fact. "
                 "Nothing on the calendar.")


class _FakeLedger:
    def __init__(self):
        self.events = []

    def append(self, kind, data):
        self.events.append((kind, dict(data)))
        return "evt-%d" % len(self.events)


class _FakeOutcome:
    def __init__(self, verified, message=""):
        self.verified = verified
        self.message = message
        self.kind = "verified" if verified else "failed"


class _FakeRegistry:
    """A funnel stand-in: get_declarations for manifest validation,
    execute records calls and returns funnel-style dicts."""

    def __init__(self, tools):
        self.tools = tools
        self.calls = []

    def get_declarations(self):
        return [{"name": name,
                 "parameters": {"properties": {},
                                "additionalProperties": False}}
                for name in self.tools]

    def execute(self, name, args):
        self.calls.append((name, dict(args)))
        fn = self.tools.get(name)
        if fn is None:
            return {"status": "error", "kind": "unknown_tool",
                    "message": "unknown tool %r" % name}
        return fn(args)


def _activity_lines(now):
    """A temp activity log: three recent entries, one old, one
    malformed. `now` is the digest's now."""
    recent = now - datetime.timedelta(hours=2)
    old = now - datetime.timedelta(hours=25)
    return [
        "%s | remember_user_fact | success" % recent.isoformat(
            timespec="seconds"),
        "%s | run_goal | success" % (recent + datetime.timedelta(
            minutes=30)).isoformat(timespec="seconds"),
        "%s | remember_user_fact | success" % old.isoformat(
            timespec="seconds"),
        "not a log line at all",
    ]


class _ScheduledAgentsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        self.manifests_file = self.data_dir / "manifests.json"
        self.digests_file = self.data_dir / "digests.jsonl"
        self.activity_file = self.data_dir / "activity.log"
        self.now = datetime.datetime(2026, 11, 13, 7, 0, 0)
        self.activity_file.write_text(
            "\n".join(_activity_lines(self.now)) + "\n",
            encoding="utf-8")
        self._orig_manifests_path = sa.manifests_path
        self._orig_digests_path = sa.digests_path
        self._orig_activity_path = sa.activity_path
        self._orig_ledger = sa._ledger
        sa.manifests_path = lambda: self.manifests_file
        sa.digests_path = lambda: self.digests_file
        sa.activity_path = lambda: self.activity_file
        self.ledger = _FakeLedger()
        sa._ledger = self.ledger
        self.known = {"speak_text", "list_jobs", "get_todays_schedule"}
        self.calendar = [{"id": "e1", "title": "Dentist appointment",
                          "start": "2026-11-13T10:00:00",
                          "location": "", "all_day": False}]

    def tearDown(self):
        sa.manifests_path = self._orig_manifests_path
        sa.digests_path = self._orig_digests_path
        sa.activity_path = self._orig_activity_path
        sa._ledger = self._orig_ledger
        sa.set_summariser_provider(None)
        self.tmp.cleanup()

    def _speak_ok(self, text):
        return {"ok": True, "reason": ""}

    def _full_summary(self):
        return ("Yesterday you recorded a fact and ran a goal. "
                "Coming up: Dentist appointment at 10am.")


class TestManifest(_ScheduledAgentsCase):
    def test_digest_manifest_validates(self):
        from core.funnel.tools_registry import tools
        names = {d["name"] for d in tools.get_declarations()}
        ok, reason = sa.validate_manifest(sa.digest_manifest(),
                                          known_tools=names)
        self.assertTrue(ok, reason)

    def test_digest_manifest_shape(self):
        manifest = sa.digest_manifest()
        self.assertEqual(manifest["job_id"], "morning_digest")
        self.assertEqual(manifest["allowed_tools"], ["speak_text"])
        self.assertIn("activity_log", manifest["allowed_data"])
        self.assertIn("calendar_upcoming", manifest["allowed_data"])
        self.assertIn("speak_digest", manifest["side_effects"])
        self.assertIn("store_digest", manifest["side_effects"])
        self.assertEqual(manifest["payload"],
                         {"job_id": "morning_digest", "kind": "digest"})

    def test_unknown_tool_refused(self):
        bad = sa.create_manifest("x", "X", ["no_such_tool"], [], [])
        self.assertFalse(
            sa.validate_manifest(bad, known_tools=self.known)[0])

    def test_unknown_data_refused(self):
        bad = sa.create_manifest("x", "X", [], ["secret_vault"], [])
        self.assertFalse(
            sa.validate_manifest(bad, known_tools=self.known)[0])

    def test_unknown_side_effect_refused(self):
        bad = sa.create_manifest("x", "X", [], [], ["delete_everything"])
        self.assertFalse(
            sa.validate_manifest(bad, known_tools=self.known)[0])

    def test_steps_tool_outside_allowed_refused(self):
        bad = sa.create_manifest(
            "x", "X", ["speak_text"], [], ["run_tool"],
            payload={"job_id": "x", "kind": "steps",
                     "steps": [{"tool": "list_jobs", "args": {}}]})
        self.assertFalse(
            sa.validate_manifest(bad, known_tools=self.known)[0])

    def test_non_dict_manifest_refused(self):
        self.assertFalse(sa.validate_manifest("nope")[0])

    def test_store_round_trip(self):
        manifest = sa.digest_manifest()
        ok, reason = sa.store_manifest(manifest, known_tools=self.known)
        self.assertTrue(ok, reason)
        back, err = sa.load_manifests()
        self.assertIsNone(err)
        self.assertEqual(back.get("morning_digest"), manifest)

    def test_corrupt_store_is_an_error(self):
        self.manifests_file.write_text("{not json", encoding="utf-8")
        back, err = sa.load_manifests()
        self.assertEqual(back, {})
        self.assertIsNotNone(err)
        manifest, err = sa.get_manifest("morning_digest")
        self.assertIsNone(manifest)
        self.assertIsNotNone(err)

    def test_delete_manifest(self):
        sa.store_manifest(sa.digest_manifest(), known_tools=self.known)
        other = sa.create_manifest("other", "Other", [], [], [])
        sa.store_manifest(other, known_tools=self.known)
        ok, reason = sa.delete_manifest("other")
        self.assertTrue(ok, reason)
        back, _err = sa.load_manifests()
        self.assertIn("morning_digest", back)
        self.assertNotIn("other", back)
        ok, _reason = sa.delete_manifest("other")
        self.assertFalse(ok)

    def test_digest_defaults(self):
        self.assertEqual(sa.DIGEST_TIMEZONE, "Asia/Kolkata")
        self.assertEqual(sa.DIGEST_HOUR, 7)
        self.assertEqual(sa.DIGEST_MINUTE, 0)
        self.assertEqual(sa.DIGEST_WORD_CAP, 200)


class TestDigestCollect(_ScheduledAgentsCase):
    def test_collect_window(self):
        items = sa.collect_digest_inputs(
            now=self.now, calendar_fn=lambda: list(self.calendar))
        self.assertEqual(len(items["activity"]), 2)
        self.assertEqual(items["activity_counts"],
                         {"remember_user_fact": 1, "run_goal": 1})
        self.assertEqual(items["events"], self.calendar)
        self.assertTrue(items["calendar_available"])

    def test_collect_missing_log(self):
        items = sa.collect_digest_inputs(
            now=self.now, activity=self.data_dir / "no-such.log",
            calendar_fn=lambda: [])
        self.assertFalse(items["activity_available"])
        self.assertEqual(items["activity"], [])
        self.assertEqual(items["activity_counts"], {})

    def test_collect_dead_calendar(self):
        def _dead():
            raise RuntimeError("calendar down")

        items = sa.collect_digest_inputs(now=self.now,
                                         calendar_fn=_dead)
        self.assertFalse(items["calendar_available"])
        self.assertEqual(items["events"], [])


class TestDigestSummarise(_ScheduledAgentsCase):
    def _items(self):
        return sa.collect_digest_inputs(
            now=self.now, calendar_fn=lambda: list(self.calendar))

    def test_self_eval_coverage(self):
        items = self._items()
        ev = sa._self_eval(self._full_summary(), items)
        self.assertFalse(ev["weak"])
        self.assertGreaterEqual(ev["coverage"], 0.5)
        ev = sa._self_eval("Nothing much happened. All quiet.", items)
        self.assertTrue(ev["weak"])
        self.assertEqual(ev["coverage"], 0.0)
        ev = sa._self_eval("word " * 250, items)
        self.assertTrue(ev["weak"])
        self.assertEqual(ev["words"], 250)

    def test_run_digest_llm(self):
        registry = _FakeRegistry({"speak_text": lambda a: {
            "status": "success", "result": _FakeOutcome(True)}})
        out = sa.run_digest(now=self.now,
                            summariser=lambda p: self._full_summary(),
                            speak_fn=self._speak_ok, registry=registry,
                            calendar_fn=lambda: list(self.calendar))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["digest"]["kind"], "llm")
        self.assertEqual(out["digest"]["text"],
                         self._full_summary()
                         + " Days used this week: 0/7 \u00b7 last week: 0/7")  # RET-1: count suffix
        self.assertTrue(out["digest"]["spoken"]["ok"])
        self.assertEqual(sa.load_last_digest()["id"],
                         out["digest"]["id"])

    def test_run_digest_regenerates_once(self):
        registry = _FakeRegistry({"speak_text": lambda a: {
            "status": "success", "result": _FakeOutcome(True)}})
        calls = []

        def _weak_first(prompt):
            calls.append(prompt)
            return ("Nothing much happened. All quiet."
                    if len(calls) == 1 else self._full_summary())

        out = sa.run_digest(now=self.now, summariser=_weak_first,
                            speak_fn=self._speak_ok, registry=registry,
                            calendar_fn=lambda: list(self.calendar))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["digest"]["kind"], "llm_regenerated")
        self.assertEqual(len(calls), 2)

    def test_run_digest_extractive_on_dead_llm(self):
        registry = _FakeRegistry({"speak_text": lambda a: {
            "status": "success", "result": _FakeOutcome(True)}})

        def _dead(prompt):
            raise RuntimeError("LLM down")

        out = sa.run_digest(now=self.now, summariser=_dead,
                            speak_fn=self._speak_ok, registry=registry,
                            calendar_fn=lambda: list(self.calendar))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["digest"]["kind"], "extractive")
        self.assertIn("Extractive list", out["digest"]["text"])

    def test_run_digest_extractive_without_summariser(self):
        registry = _FakeRegistry({"speak_text": lambda a: {
            "status": "success", "result": _FakeOutcome(True)}})
        out = sa.run_digest(now=self.now, summariser=None,
                            speak_fn=self._speak_ok, registry=registry,
                            calendar_fn=lambda: list(self.calendar))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["digest"]["kind"], "extractive")

    def test_run_digest_speak_not_allowed_still_stores(self):
        registry = _FakeRegistry({"speak_text": lambda a: {
            "status": "success", "result": _FakeOutcome(True)}})
        out = sa.run_digest(now=self.now,
                            summariser=lambda p: self._full_summary(),
                            speak_fn=self._speak_ok, registry=registry,
                            calendar_fn=lambda: list(self.calendar),
                            speak=False)
        self.assertTrue(out["ok"], out)
        self.assertFalse(out["digest"]["spoken"]["ok"])
        self.assertIsNotNone(sa.load_last_digest())

    def test_load_last_digest_empty(self):
        self.assertIsNone(sa.load_last_digest())


class TestOperativeAgent(_ScheduledAgentsCase):
    def _steps_registry(self):
        return _FakeRegistry({
            "list_jobs": lambda a: {"status": "success", "result": []},
            "get_todays_schedule": lambda a: {"status": "success",
                                              "result": []},
        })

    def _digest_registry(self):
        # The digest manifest allows speak_text, so the registry
        # the agent runs against must declare it (or the manifest
        # is invalid before the digest logic is reached).
        return _FakeRegistry({
            "speak_text": lambda a: {"status": "success",
                                     "result": _FakeOutcome(True)},
        })

    def _store_steps_manifest(self):
        manifest = sa.create_manifest(
            "steps1", "Steps one", ["list_jobs"], [], ["run_tool"],
            payload={"job_id": "steps1", "kind": "steps",
                     "steps": [{"tool": "list_jobs", "args": {}}]})
        ok, reason = sa.store_manifest(manifest, known_tools=self.known)
        self.assertTrue(ok, reason)
        return manifest

    def test_no_manifest_refused(self):
        out = sa.run_job({"job_id": "ghost", "kind": "digest"},
                         registry=self._steps_registry())
        self.assertFalse(out["ok"])
        self.assertIn("no manifest", out["reason"])
        self.assertTrue(self.ledger.events)
        self.assertFalse(self.ledger.events[-1][1]["ok"])
        self.assertIsNone(sa.load_last_digest())

    def test_invalid_manifest_refused(self):
        # Written straight to the store file (bypassing the
        # store-time validation) — run_job validates on every run.
        self.manifests_file.write_text(json.dumps({
            "bad": {"job_id": "bad", "name": "Bad",
                    "allowed_tools": ["nope"], "allowed_data": [],
                    "side_effects": [], "confirm_required": True}},
        ), encoding="utf-8")
        out = sa.run_job({"job_id": "bad", "kind": "digest"},
                         registry=self._steps_registry())
        self.assertFalse(out["ok"])
        self.assertIn("invalid manifest", out["reason"])

    def test_steps_outside_manifest_refused(self):
        self._store_steps_manifest()
        registry = self._steps_registry()
        out = sa.run_job({"job_id": "steps1", "kind": "steps",
                          "steps": [{"tool": "get_todays_schedule",
                                     "args": {}}]}, registry=registry)
        self.assertFalse(out["ok"])
        self.assertIn("outside the manifest", out["reason"])
        self.assertEqual(registry.calls, [])
        out = sa.run_job({"job_id": "steps1", "kind": "steps",
                          "steps": [{"tool": "list_jobs", "args": {}},
                                    {"tool": "get_todays_schedule",
                                     "args": {}}]}, registry=registry)
        self.assertFalse(out["ok"])
        self.assertEqual(out["steps_run"], 1)
        self.assertEqual(registry.calls, [("list_jobs", {})])

    def test_steps_funnel_refusal_stops_run(self):
        self._store_steps_manifest()
        refusing = _FakeRegistry({
            "list_jobs": lambda a: {"status": "error",
                                    "kind": "confirm_refused",
                                    "message": "the human said no"},
        })
        out = sa.run_job({"job_id": "steps1", "kind": "steps",
                          "steps": [{"tool": "list_jobs", "args": {}}]},
                         registry=refusing)
        self.assertFalse(out["ok"])
        self.assertIn("refused", out["reason"])

    def test_steps_happy_path(self):
        self._store_steps_manifest()
        registry = self._steps_registry()
        out = sa.run_job({"job_id": "steps1", "kind": "steps",
                          "steps": [{"tool": "list_jobs", "args": {}}]},
                         registry=registry)
        self.assertTrue(out["ok"], out)
        self.assertEqual(registry.calls, [("list_jobs", {})])
        self.assertTrue(self.ledger.events[-1][1]["ok"])
        self.assertEqual(self.ledger.events[-1][1]["kind"], "steps")

    def test_digest_via_agent(self):
        sa.store_manifest(sa.digest_manifest(), known_tools=self.known)
        registry = self._steps_registry()
        registry.tools["speak_text"] = lambda a: {
            "status": "success", "result": _FakeOutcome(True)}
        out = sa.run_job({"job_id": "morning_digest", "kind": "digest"},
                         registry=registry,
                         summariser=lambda p: self._full_summary(),
                         speak_fn=self._speak_ok,
                         calendar_fn=lambda: list(self.calendar))
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["spoken"]["ok"])
        self.assertTrue(self.ledger.events[-1][1]["ok"])
        self.assertEqual(self.ledger.events[-1][1]["digest_id"],
                         out["digest_id"])
        self.assertNotIn("Dentist appointment",
                         json.dumps(self.ledger.events[-1][1]))

    def test_digest_without_speak_permission(self):
        quiet = sa.create_manifest(
            "quiet_digest", "Quiet", ["speak_text"],
            ["activity_log", "calendar_upcoming"], ["store_digest"],
            payload={"job_id": "quiet_digest", "kind": "digest"})
        sa.store_manifest(quiet, known_tools=self.known)
        registry = self._digest_registry()
        out = sa.run_job({"job_id": "quiet_digest", "kind": "digest"},
                         registry=registry,
                         summariser=lambda p: self._full_summary(),
                         speak_fn=self._speak_ok,
                         calendar_fn=lambda: list(self.calendar))
        self.assertTrue(out["ok"], out)
        self.assertFalse(out["spoken"]["ok"])
        self.assertIsNotNone(sa.load_last_digest())

    def test_unknown_kind_refused(self):
        sa.store_manifest(sa.digest_manifest(), known_tools=self.known)
        out = sa.run_job({"job_id": "morning_digest", "kind": "dance"},
                         registry=self._digest_registry())
        self.assertFalse(out["ok"])
        self.assertIn("unknown job kind", out["reason"])

    def test_handler_never_raises(self):
        out = sa.operative_agent_handler(None)
        self.assertIsInstance(out, dict)
        self.assertFalse(out["ok"])
        out = sa.operative_agent_handler(
            {"job_id": "ghost", "kind": "digest"})
        self.assertIsInstance(out, dict)
        self.assertFalse(out["ok"])


class _RealEngineCase(_ScheduledAgentsCase):
    """Tool-level tests against the default engine on a temp store."""

    def setUp(self):
        super().setUp()
        self.store_path = Path(self.tmp.name) / "jobs.sqlite3"
        js.set_store_path(self.store_path)
        js.register_handler(sa.HANDLER_NAME,
                            sa.operative_agent_handler)
        sa.set_summariser_provider(lambda: (lambda p: _FAKE_SUMMARY))
        # The real-engine tests digest at the REAL now, so the
        # activity lines must be recent relative to it (the base
        # fixture is relative to the fixed self.now).
        self.activity_file.write_text(
            "\n".join(_activity_lines(datetime.datetime.now())) + "\n",
            encoding="utf-8")

    def tearDown(self):
        try:
            js.shutdown()
        except Exception:  # noqa: BLE001 — teardown never masks
            pass
        js.set_store_path(None)
        js.reset_backend()
        super().tearDown()


class TestToolRealEngine(_RealEngineCase):
    def test_setup_digest_bad_timezone_refused(self):
        out = sa.scheduled_agents("setup_digest", timezone="Mars/Olympus")
        self.assertFalse(out["ok"])
        self.assertIn("unknown timezone", out["reason"])

    def test_unknown_action_refused(self):
        out = sa.scheduled_agents("dance")
        self.assertFalse(out["ok"])

    @unittest.skipUnless(_HAS_APS, "apscheduler is not installed here")
    def test_setup_digest_creates_job_and_manifest(self):
        out = sa.scheduled_agents("setup_digest")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["job_id"], "morning_digest")
        self.assertEqual(out["timezone"], "Asia/Kolkata")
        self.assertEqual(out["hour"], 7)
        self.assertIsNotNone(out.get("next_fire"))
        listed = sa.scheduled_agents("list")
        self.assertTrue(listed["ok"], listed)
        self.assertEqual(len(listed["jobs"]), 1)
        self.assertEqual(listed["jobs"][0]["job_id"], "morning_digest")
        self.assertEqual(listed["jobs"][0]["manifest"], "Morning digest")
        self.assertTrue(listed["jobs"][0]["has_manifest"])
        next_fire = datetime.datetime.fromisoformat(
            listed["jobs"][0]["next_fire"])
        self.assertEqual(next_fire.astimezone(KOLKATA).hour, 7)
        self.assertEqual(next_fire.astimezone(KOLKATA).minute, 0)
        manifest, err = sa.get_manifest("morning_digest")
        self.assertIsNone(err)
        # The stored manifest equals a fresh digest manifest on
        # every field but created_at (the stored one was created
        # at setup time, the fresh one now).
        fresh = sa.digest_manifest()
        for key in ("job_id", "name", "allowed_tools", "allowed_data",
                    "side_effects", "confirm_required", "payload"):
            self.assertEqual(manifest[key], fresh[key], key)
        out2 = sa.scheduled_agents("setup_digest")
        self.assertFalse(out2["ok"])
        self.assertIn("already set up", out2["reason"])

    @unittest.skipUnless(_HAS_APS, "apscheduler is not installed here")
    def test_status_reports_engine_and_last_digest(self):
        sa.scheduled_agents("setup_digest")
        status = sa.scheduled_agents("status")
        self.assertTrue(status["ok"], status)
        self.assertTrue(status["engine"]["available"])
        self.assertEqual(status["jobs"], 1)
        self.assertEqual(status["manifests"], 1)
        self.assertIsNone(status["last_digest"])
        self.assertEqual(status["missed"], 0)

    @unittest.skipUnless(_HAS_APS, "apscheduler is not installed here")
    def test_run_now_digest(self):
        sa.scheduled_agents("setup_digest")
        out = sa.scheduled_agents("run_now", job_id="morning_digest")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "digest")
        self.assertIsNotNone(out.get("digest_id"))
        self.assertIsInstance(out.get("spoken"), dict)
        self.assertIsInstance(out["spoken"].get("ok"), bool)
        last = sa.load_last_digest()
        self.assertIsNotNone(last)
        self.assertEqual(last["id"], out["digest_id"])
        self.assertEqual(last["text"], _FAKE_SUMMARY
                         + " Days used this week: 0/7 \u00b7 last week: 0/7")  # RET-1: count suffix
        digest = sa.scheduled_agents("digest")
        self.assertTrue(digest["ok"], digest)
        self.assertEqual(digest["digest"]["id"], out["digest_id"])

    @unittest.skipUnless(_HAS_APS, "apscheduler is not installed here")
    def test_run_now_unknown_refused(self):
        out = sa.scheduled_agents("run_now", job_id="nope")
        self.assertFalse(out["ok"])
        self.assertIn("no scheduled job", out["reason"])

    @unittest.skipUnless(_HAS_APS, "apscheduler is not installed here")
    def test_cancel_removes_job_and_manifest(self):
        sa.scheduled_agents("setup_digest")
        out = sa.scheduled_agents("cancel", job_id="morning_digest")
        self.assertTrue(out["ok"], out)
        listed = sa.scheduled_agents("list")
        self.assertEqual(listed["jobs"], [])
        manifest, err = sa.get_manifest("morning_digest")
        self.assertIsNone(err)
        self.assertIsNone(manifest)
        out = sa.scheduled_agents("cancel", job_id="morning_digest")
        self.assertFalse(out["ok"])

    @unittest.skipUnless(_HAS_APS, "apscheduler is not installed here")
    def test_real_one_shot_fire_runs_the_digest(self):
        sa.store_manifest(sa.digest_manifest(), known_tools=self.known)
        run_at = datetime.datetime.now(UTC) + datetime.timedelta(
            seconds=0.4)
        got = js.schedule_one_shot("morning_digest", run_at,
                                   "operative_agent",
                                   {"job_id": "morning_digest",
                                    "kind": "digest"})
        self.assertTrue(got["ok"], got)
        self.assertTrue(js.start()["ok"])
        time.sleep(1.5)
        last = sa.load_last_digest()
        self.assertIsNotNone(last)
        self.assertEqual(last["text"], _FAKE_SUMMARY
                         + " Days used this week: 0/7 \u00b7 last week: 0/7")  # RET-1: count suffix
        self.assertEqual(last["kind"], "llm")
        self.assertTrue(any(
            e[0] == "scheduled_agent_run" and e[1].get("ok") is True
            and e[1].get("job_id") == "morning_digest"
            for e in self.ledger.events))
        self.assertEqual(js.list_jobs()["jobs"], [])

    @unittest.skipUnless(_HAS_APS, "apscheduler is not installed here")
    def test_job_survives_restart(self):
        first = sa.scheduled_agents("setup_digest")
        self.assertTrue(first["ok"], first)
        second = js.JobScheduler(store_path=self.store_path)
        try:
            listed = second.list_jobs()["jobs"]
        finally:
            second.shutdown()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["job_id"], "morning_digest")
        self.assertEqual(listed[0]["next_fire"], first["next_fire"])

    @unittest.skipIf(_HAS_APS, "apscheduler IS installed here")
    def test_setup_digest_engine_unavailable(self):
        out = sa.scheduled_agents("setup_digest")
        self.assertFalse(out["ok"])
        self.assertIn("engine_unavailable", out["reason"])
        # The failed setup rolls the manifest back — no orphan.
        manifest, err = sa.get_manifest("morning_digest")
        self.assertIsNone(err)
        self.assertIsNone(manifest)

    @unittest.skipIf(_HAS_APS, "apscheduler IS installed here")
    def test_list_engine_unavailable(self):
        out = sa.scheduled_agents("list")
        self.assertFalse(out["ok"])
        self.assertIn("engine_unavailable", out["reason"])


class TestCensus(unittest.TestCase):
    def test_tool_registered_and_schema(self):
        from core.funnel.tools_registry import tools
        decls = tools.get_declarations()
        mine = [d for d in decls if d["name"] == "scheduled_agents"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(set(mine[0]["parameters"]["properties"]),
                         {"action", "job_id", "timezone", "name", "steps",
                          "schedule"})  # INTENTIONAL — RET-2 (2026-11-15): scheduled_agents gains 3 properties (name, steps, schedule) for the save_routine/run_routine actions
        self.assertEqual(mine[0]["parameters"]["required"], ["action"])

    def test_registry_total_is_772(self):  # INTENTIONAL — Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        from core.funnel.tools_registry import tools
        decls = tools.get_declarations()
        names = [d["name"] for d in decls]
        self.assertEqual(len(decls), 772)  # INTENTIONAL — Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(len(set(names)), 772)  # INTENTIONAL — Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertIn("scheduled_agents", names)

    def test_registry_params_total_is_2126(self):  # INTENTIONAL — GAPS-20 loopback-meeting pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        from core.funnel.tools_registry import tools
        decls = tools.get_declarations()
        total = sum(len(d["parameters"]["properties"]) for d in decls)
        self.assertEqual(total, 2126)  # INTENTIONAL — GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_catalogue_grouped_is_769(self):  # INTENTIONAL — field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        from core.catalogue_snapshot import GROUP_MEMBERS
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 769)  # INTENTIONAL — field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertIn("scheduled_agents",
                      GROUP_MEMBERS.get("Your world", ()))

    def test_registry_closed_is_771(self):  # INTENTIONAL — Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        from core.funnel.tools_registry import tools
        decls = tools.get_declarations()
        closed = sum(1 for d in decls
                     if d["parameters"].get("additionalProperties")
                     is False)
        self.assertEqual(closed, 771)  # INTENTIONAL — Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_confirm_gated_is_170(self):  #Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
        self.assertEqual(len(_CONFIRM_GATE_TOOLS), 170)  # INTENTIONAL — round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertIn("scheduled_agents", _CONFIRM_GATE_TOOLS)
        from core.funnel.tools_gate import _needs_confirmation
        self.assertFalse(_needs_confirmation(
            "scheduled_agents", {"action": "list"}))
        self.assertFalse(_needs_confirmation(
            "scheduled_agents", {"action": "status"}))
        self.assertFalse(_needs_confirmation(
            "scheduled_agents", {"action": "digest"}))
        self.assertTrue(_needs_confirmation(
            "scheduled_agents", {"action": "setup_digest"}))
        self.assertTrue(_needs_confirmation(
            "scheduled_agents", {"action": "cancel"}))
        self.assertTrue(_needs_confirmation(
            "scheduled_agents", {"action": "run_now"}))

    def test_classification_snapshot_entry(self):
        snap = json.loads(open(os.path.join(
            os.path.dirname(__file__), "_classification_snapshot.json"),
            encoding="utf-8").read())
        self.assertEqual(len(snap), 772)  # INTENTIONAL — Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(snap.get("scheduled_agents"),
                         {"confirm": True, "free": None, "read": False})
        self.assertEqual(sum(1 for v in snap.values() if v.get("read")),
                         386)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 382 -> 383 — one new read tool get_morning_briefing; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(sum(1 for v in snap.values()
                             if not v.get("read")), 386)  # INTENTIONAL — Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374 -> 375 — one new tool run_plan; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(sum(1 for v in snap.values()
                             if v.get("confirm")), 170)  # INTENTIONAL — round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan (confirm-gated); fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170


if __name__ == "__main__":
    unittest.main()
