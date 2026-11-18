"""RET-2 (2026-11-15): named saved routines + one-tap/voice re-run.

Two new actions on the existing scheduled_agents tool — save_routine
(confirm-gated) and run_routine (free dispatch) — no new tool, census
unchanged. Agent-level tests run on BOTH interpreters with a fake
registry and temp stores (the test_scheduled_agents.py harness
pattern); the handler-level tests run the REAL registry (a read tool
end to end, and a gated tool against a deny handler) on both
interpreters too. The GUI formatter test imports the mixin when
PySide6 is present and verifies statically otherwise (the
test_gaps23.py Qt-guard pattern).
"""

import datetime
import json
import tempfile
import unittest
from pathlib import Path

import core.scheduled_agents as sa
from core.brain import planner as facade
from core.brain import planner_routines as proutines
from core.brain import planner_storage as pstore
from core.funnel import tool_classification as tc
from core.funnel import tools_gate as tg
from core import confirm_gate as confirm_gate
from core import confirm_gate_scrub as scrub

TREE = Path(__file__).resolve().parent.parent
SECRET = "sk_live_51H7exampleSecretKeyForTestOnly1234567890"


class _FakeLedger:
    def __init__(self):
        self.events = []

    def append(self, kind, data):
        self.events.append((kind, dict(data)))
        return "evt-%d" % len(self.events)


class _FakeRegistry:
    """A funnel stand-in: get_declarations for manifest validation,
    validate_args for the action's save/run-start validation,
    execute records calls and returns funnel-style dicts."""

    def __init__(self, tools, refuse=None):
        self.tools = tools  # {name: {arg: type}}
        self.calls = []
        self.refuse = refuse or {}  # name -> (kind, message)

    def get_declarations(self):
        return [{"name": n, "parameters": {"properties": {},
                                           "additionalProperties": False}}
                for n in self.tools]

    def validate_args(self, name, args):
        if name not in self.tools:
            return "Unknown tool '%s'." % name
        if not isinstance(args, dict):
            return ("Invalid arguments for tool '%s': args must be "
                    "an object." % name)
        for arg, typ in self.tools[name].items():
            if arg not in args:
                return ("Invalid arguments for tool '%s': '%s' is "
                        "required." % (name, arg))
            if not isinstance(args[arg], typ):
                return ("Invalid arguments for tool '%s': '%s' must be "
                        "a %s (got %s)." % (name, arg, typ.__name__,
                                            type(args[arg]).__name__))
        return None

    def execute(self, name, args):
        self.calls.append((name, dict(args or {})))
        if name in self.refuse:
            kind, msg = self.refuse[name]
            return {"status": "error", "kind": kind, "message": msg}
        if name not in self.tools:
            return {"status": "error", "kind": "unknown_tool",
                    "message": "unknown tool %r" % name}
        return {"status": "success", "result": "ok"}


REG_A = {"fake_read": {}, "fake_write": {"text": str},
         "fake_gated": {}}
STEPS = [{"tool": "fake_read", "args": {}},
         {"tool": "fake_write", "args": {"text": "hello"}}]


class _Ret2Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        self.manifests_file = self.data_dir / "manifests.json"
        self.digests_file = self.data_dir / "digests.jsonl"
        self.activity_file = self.data_dir / "activity.log"
        self.activity_file.write_text("", encoding="utf-8")
        self._orig_manifests_path = sa.manifests_path
        self._orig_digests_path = sa.digests_path
        self._orig_activity_path = sa.activity_path
        self._orig_ledger = sa._ledger
        sa.manifests_path = lambda: self.manifests_file
        sa.digests_path = lambda: self.digests_file
        sa.activity_path = lambda: self.activity_file
        self.ledger = _FakeLedger()
        sa._ledger = self.ledger
        self.routines_file = self.data_dir / "routines.json"
        self._orig_routines = (pstore._ROUTINES_PATH,
                               proutines._ROUTINES_PATH,
                               facade._ROUTINES_PATH)
        for mod in (pstore, proutines, facade):
            mod._ROUTINES_PATH = self.routines_file

    def tearDown(self):
        sa.manifests_path = self._orig_manifests_path
        sa.digests_path = self._orig_digests_path
        sa.activity_path = self._orig_activity_path
        sa._ledger = self._orig_ledger
        for mod, path in zip((pstore, proutines, facade),
                             self._orig_routines):
            mod._ROUTINES_PATH = path
        confirm_gate.clear_handler()
        self.tmp.cleanup()

    # --- save: validation -------------------------------------------------
    def test_save_routine_unknown_tool_refused(self):
        out = sa._save_routine_action(
            "bad tool", [{"tool": "ghost_tool", "args": {}}], None,
            registry=_FakeRegistry(REG_A))
        self.assertFalse(out["ok"])
        self.assertIn("ghost_tool", out["reason"])
        self.assertEqual(proutines.get_routines(), [])

    def test_save_routine_bad_args_refused(self):
        out = sa._save_routine_action(
            "bad args", [{"tool": "fake_write", "args": {}}], None,
            registry=_FakeRegistry(REG_A))
        self.assertFalse(out["ok"])
        self.assertIn("text", out["reason"])
        self.assertEqual(proutines.get_routines(), [])

    def test_save_routine_unparseable_schedule_refuses_whole(self):
        out = sa._save_routine_action(
            "bad sched", STEPS, "every second Tuesday at dawn",
            registry=_FakeRegistry(REG_A))
        self.assertFalse(out["ok"])
        self.assertEqual(proutines.get_routines(), [])

    def test_save_routine_empty_steps_refused(self):
        out = sa._save_routine_action(
            "empty", [], None, registry=_FakeRegistry(REG_A))
        self.assertFalse(out["ok"])
        self.assertEqual(proutines.get_routines(), [])

    def test_save_routine_roundtrip(self):
        out = sa._save_routine_action(
            "morning", STEPS, "daily 07:30",
            registry=_FakeRegistry(REG_A))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["routine"], "morning")
        self.assertEqual(out["steps"], 2)
        self.assertEqual(out["schedule"], "daily 07:30")
        self.assertFalse(out["replaces"])
        rec = proutines.get_routines()
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["schedule"],
                         {"kind": "daily", "time": "07:30"})

    def test_resave_without_schedule_preserves_schedule(self):
        reg = _FakeRegistry(REG_A)
        sa._save_routine_action("keep", STEPS, "daily 07:30",
                                registry=reg)
        out = sa._save_routine_action(
            "keep", [{"tool": "fake_read", "args": {}}], None,
            registry=reg)
        self.assertTrue(out["ok"], out)
        rec = proutines.get_routines()
        self.assertEqual(rec[0]["schedule"],
                         {"kind": "daily", "time": "07:30"})
        self.assertEqual(rec[0]["schedule_text"], "daily 07:30")

    # --- save: confirmation ------------------------------------------------
    def test_save_confirmation_renders_full_step_list(self):
        args = {"action": "save_routine", "name": "my morning routine",
                "steps": [{"tool": "fake_read", "args": {}},
                          {"tool": "fake_write",
                           "args": {"text": "hello", "token": SECRET}}],
                "schedule": "daily 07:30"}
        view = tg._confirmation_args("scheduled_agents", args)
        self.assertIsInstance(view.get("steps"), str)
        self.assertIn("1. fake_read", view["steps"])
        self.assertIn("2. fake_write", view["steps"])
        summary = scrub.summarize_args(
            view, verbatim_keys={"routine", "schedule", "steps",
                                 "replaces"})
        self.assertIn("fake_read", summary)
        self.assertIn("fake_write", summary)
        self.assertIn("hello", summary)
        self.assertNotIn("sk_live_", summary)

    def test_save_confirmation_is_not_name_only(self):
        args = {"action": "save_routine", "name": "morning",
                "steps": STEPS, "schedule": None}
        view = tg._confirmation_args("scheduled_agents", args)
        # A name-only confirm is not a confirmation: the view must
        # carry the routine, its schedule, and the rendered steps.
        self.assertEqual(set(view) & {"routine", "schedule", "steps"},
                         {"routine", "schedule", "steps"})
        self.assertIn("fake_read", view["steps"])
        self.assertIn("fake_write", view["steps"])

    def test_save_over_existing_name_says_so(self):
        reg = _FakeRegistry(REG_A)
        sa._save_routine_action("morning", STEPS, None, registry=reg)
        out = sa._save_routine_action("morning", STEPS, None,
                                      registry=reg)
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["replaces"])
        self.assertIn("updated", out["message"])
        view = tg._confirmation_args(
            "scheduled_agents",
            {"action": "save_routine", "name": "morning",
             "steps": STEPS, "schedule": None})
        self.assertIn("replaces", view)
        self.assertIn("morning", view["replaces"])

    # --- classification -----------------------------------------------------
    def test_classification_save_gates_run_free(self):
        self.assertTrue(tc.classify_call(
            "scheduled_agents",
            {"action": "save_routine"}).needs_confirmation)
        self.assertFalse(tc.classify_call(
            "scheduled_agents",
            {"action": "run_routine"}).needs_confirmation)
        for action in ("list", "status", "digest"):
            self.assertFalse(tc.classify_call(
                "scheduled_agents",
                {"action": action}).needs_confirmation)

    # --- run: re-validation + enforcement ----------------------------------
    def test_run_vanished_tool_refuses_whole_nothing_runs(self):
        reg = _FakeRegistry(REG_A)
        sa._save_routine_action("vanish", STEPS, None, registry=reg)
        reg_b = _FakeRegistry({"fake_read": {}})
        out = sa._run_routine_action("vanish", registry=reg_b)
        self.assertFalse(out["ok"])
        self.assertIn("fake_write", out["reason"])
        self.assertEqual(reg_b.calls, [])

    def test_run_unknown_routine_refused(self):
        out = sa.scheduled_agents(action="run_routine", name="ghost")
        self.assertFalse(out["ok"])
        self.assertIn("ghost", out["reason"])

    def test_run_step_outside_manifest_refused(self):
        reg = _FakeRegistry(REG_A)
        sa.store_manifest(sa.create_manifest(
            "steps1", "steps test", allowed_tools=["fake_read"],
            allowed_data=[], side_effects=["run_tool"],
            confirm_required=True,
            payload={"job_id": "steps1", "kind": "steps",
                     "steps": [{"tool": "fake_read", "args": {}}]}),
            known_tools=set(REG_A))
        out = sa.run_job(
            {"job_id": "steps1", "kind": "steps",
             "steps": [{"tool": "fake_read", "args": {}},
                       {"tool": "fake_write", "args": {"text": "x"}}]},
            registry=reg)
        self.assertFalse(out["ok"])
        self.assertEqual(out["steps_run"], 1)
        self.assertIn("fake_write", out["reason"])
        self.assertEqual([c[0] for c in reg.calls], ["fake_read"])

    def test_run_gated_step_not_auto_approved(self):
        reg = _FakeRegistry(REG_A)
        sa._save_routine_action(
            "gated", [{"tool": "fake_read", "args": {}},
                      {"tool": "fake_gated", "args": {}},
                      {"tool": "fake_write", "args": {"text": "x"}}],
            None, registry=reg)
        refusing = _FakeRegistry(REG_A, refuse={
            "fake_gated": ("not_confirmed", "the human said no")})
        out = sa._run_routine_action("gated", registry=refusing)
        self.assertFalse(out["ok"])
        # The funnel WAS asked (the step is in calls) and its
        # verdict stands — the step is refused, not auto-approved.
        self.assertEqual([c[0] for c in refusing.calls],
                         ["fake_read", "fake_gated"])
        self.assertEqual(out["steps"][1]["outcome"], "refused")
        self.assertIn("the human said no", out["steps"][1]["reason"])
        self.assertEqual(out["steps"][2]["outcome"], "not_run")

    def test_run_gated_step_asked_real_funnel(self):
        # The real funnel, a deny handler, a real gated tool: the
        # ask happens (last_decision records it) and the refusal
        # stands — no auto-approval on re-run.
        confirm_gate.register_handler(lambda *a: False)
        try:
            saved = sa.scheduled_agents(
                action="save_routine", name="mail",
                steps=[{"tool": "send_email",
                        "args": {"to": "nobody@example.com",
                                 "subject": "RET-2 test",
                                 "body": "test"}}])
            self.assertTrue(saved["ok"], saved)
            out = sa._run_routine_action("mail")
        finally:
            confirm_gate.clear_handler()
        self.assertFalse(out["ok"])
        self.assertEqual(out["steps"][0]["outcome"], "refused")
        self.assertIsNotNone(confirm_gate.last_decision())

    def test_run_failed_step_halts_and_reports(self):
        reg = _FakeRegistry(REG_A)
        sa._save_routine_action(
            "gated", [{"tool": "fake_read", "args": {}},
                      {"tool": "fake_gated", "args": {}},
                      {"tool": "fake_write", "args": {"text": "x"}}],
            None, registry=reg)
        failing = _FakeRegistry(REG_A, refuse={
            "fake_gated": ("handler_error", "boom")})
        out = sa._run_routine_action("gated", registry=failing)
        self.assertFalse(out["ok"])
        self.assertEqual([c[0] for c in failing.calls],
                         ["fake_read", "fake_gated"])
        self.assertEqual(out["halted_at"], 1)
        self.assertEqual(out["steps"][0]["outcome"], "done")
        self.assertEqual(out["steps"][1]["outcome"], "failed")
        self.assertIn("boom", out["steps"][1]["reason"])
        self.assertEqual(out["steps"][2]["outcome"], "not_run")
        self.assertIn("stopped at step 2", out["summary"])

    # --- run: the report ------------------------------------------------------
    def test_run_report_lists_every_step_no_time_saved(self):
        reg = _FakeRegistry(REG_A)
        sa._save_routine_action("clean", STEPS, None, registry=reg)
        out = sa._run_routine_action("clean", registry=_FakeRegistry(REG_A))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["steps_total"], 2)
        self.assertEqual(out["steps_run"], 2)
        self.assertEqual([s["outcome"] for s in out["steps"]],
                         ["done", "done"])
        self.assertTrue(all("duration_ms" in s for s in out["steps"]))
        self.assertIsInstance(out["elapsed_ms"], int)
        self.assertTrue(out["summary"])
        blob = json.dumps(out, default=str).lower()
        self.assertNotIn("time saved", blob)

    def test_run_report_returns_through_triggering_channel(self):
        # Voice gets the speakable summary; tap gets the structured
        # steps on screen (the GUI formatter renders the same report).
        reg = _FakeRegistry(REG_A)
        sa._save_routine_action("clean", STEPS, None, registry=reg)
        out = sa._run_routine_action("clean", registry=_FakeRegistry(REG_A))
        self.assertIsInstance(out["summary"], str)
        self.assertTrue(out["summary"])
        self.assertEqual(len(out["steps"]), 2)
        try:
            from gui.window_memory_routines import MemoryRoutinesMixin
        except ImportError:
            src = (TREE / "gui" / "window_memory_routines.py").read_text(
                encoding="utf-8")
            self.assertIn("_format_routine_report", src)
            self.assertIn("run_routine", src)
            return
        text = MemoryRoutinesMixin._format_routine_report("clean", out)
        self.assertIn(out["summary"], text)
        self.assertIn("1. fake_read — done", text)
        self.assertIn("2. fake_write — done", text)

    def test_manifest_allowed_tools_exactly_routine_steps(self):
        reg = _FakeRegistry(REG_A)
        sa._save_routine_action("clean", STEPS, None, registry=reg)
        captured = {}
        real_store = sa.store_manifest

        def _capture(manifest, known_tools=None):
            captured.update(manifest)
            return real_store(manifest, known_tools=known_tools)

        sa.store_manifest = _capture
        try:
            sa._run_routine_action("clean", registry=_FakeRegistry(REG_A))
        finally:
            sa.store_manifest = real_store
        self.assertEqual(captured.get("allowed_tools"),
                         ["fake_read", "fake_write"])
        self.assertEqual(captured.get("side_effects"), ["run_tool"])
        self.assertEqual((captured.get("payload") or {}).get("kind"),
                         "steps")

    def test_manifest_validation_edges(self):
        base = {"job_id": "m1", "name": "edges",
                "allowed_tools": ["fake_read"], "allowed_data": [],
                "side_effects": ["run_tool"], "confirm_required": True}
        known = set(REG_A)
        ok, _ = sa.validate_manifest(
            dict(base, payload={"job_id": "m1", "kind": "steps",
                                "steps": [{"tool": "fake_read",
                                           "args": {}}]}),
            known_tools=known)
        self.assertTrue(ok)
        ok, reason = sa.validate_manifest(
            dict(base, payload={"job_id": "m1", "kind": "steps",
                                "steps": [{"tool": "fake_read",
                                           "args": {}},
                                          {"tool": "fake_write",
                                           "args": {"text": "x"}}]}),
            known_tools=known)
        self.assertFalse(ok)
        self.assertIn("fake_write", reason)
        ok, _ = sa.validate_manifest(
            dict(base, payload={"job_id": "m1", "kind": "steps",
                                "steps": []}),
            known_tools=known)
        self.assertFalse(ok)
        ok, _ = sa.validate_manifest(
            dict(base, payload={"job_id": "m1", "kind": "steps",
                                "steps": [{"tool": "fake_read",
                                           "args": "nope"}]}),
            known_tools=known)
        self.assertFalse(ok)

    # --- handler level, real registry -----------------------------------------
    def test_save_then_run_through_handler_live_registry(self):
        saved = sa.scheduled_agents(
            action="save_routine", name="clock",
            steps=[{"tool": "get_current_time_date", "args": {}}])
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(saved["steps"], 1)
        out = sa.scheduled_agents(action="run_routine", name="clock")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["steps_total"], 1)
        self.assertEqual(out["steps"][0]["outcome"], "done")
        self.assertEqual(out["steps"][0]["tool"], "get_current_time_date")
        self.assertTrue(out["summary"])
        # The transient manifest is gone after the run.
        manifest, err = sa.get_manifest("routine:clock")
        self.assertIsNone(manifest)
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
