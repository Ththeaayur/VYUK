"""UI Slice 6 — behavioral tests (complexity doc slice 6, S6).

Proves the eight doc scenarios work through REAL seams; fixes nothing
except the one small broken behavior found while writing them (S2
offline draft restore — see the note on that test). Everything else
is tests-only.

What is REAL here: TurnCoordinator + handle_typed_turn / handle_turn
(S1), the confirm gate + fresh ToolRegistry funnel (S3 — the gate is
NEVER stubbed), coordinator cancel + voice-goal cancel (S4),
ProactiveScheduler with a fake clock + temp store (S5), the real
_brain_engine body exec'd + real save_env_setting against a temp
.env (S6), the real screen_watch switchboard (S7), coordinator +
TaskLedger resume paths (S8).

What is STUBBED, and why it is unavoidable in this lab: the model
picker / live brain (stub executors returning canned step results —
the lab has no live model); Qt widgets (gui modules import frozen
voice_assistant.* names and build real widgets, so GUI behavior is
verified by AST over the real files, exactly like the S1–S5 suites).
The gate, the ledger, and the scheduler store logic are never stubbed.

What is FIELD-OWED (lab cannot prove it): GUI pixels for every
scenario; live audio for voice goals; real screen frames for watch
mode; real Windows render of the offline draft restore; the async
send-failure path (see the S2 residual note).

Hygiene (testiso guest rule): the S3 funnel tests snapshot the
process-global tool budget + clear the confirm handler before, and
restore byte-for-byte after. Scheduler/ledger state lives in temp
dirs only, never shipped data/. The screen_watch switchboard is
restored (listeners + enabled state) after S7.
"""

import ast
import datetime
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core import confirm_gate
from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.funnel import tools_handlers as tools_handlers_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.scheduler import ProactiveScheduler
from core.desktop.screen_watch import (
    CONTROL,
    pill_state,
    set_watch_enabled,
    subscribe_watch,
    watch_enabled,
)  # Phase 3 D3 INTENTIONAL: screen_watch moved to core/desktop/
from core.brain.task_ledger import TaskLedger  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.funnel.tools_registry import ToolRegistry  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.brain.voice_goal import cancel_voice_goal  # Phase 3 B1b INTENTIONAL: voice_goal moved to core/brain/

TREE = Path(__file__).resolve().parents[1]
UTC = datetime.timezone.utc


def _src(rel):
    return (TREE / rel).read_text(encoding="utf-8")


def _func(tree, name, in_class=None):
    """A FunctionDef by name, optionally scoped to a class body."""
    for node in ast.walk(tree):
        if in_class is not None:
            if isinstance(node, ast.ClassDef) and node.name == in_class:
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == name:
                        return item
        elif isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found")


def _ok_executor(calls):
    def _run(tool, args):
        calls.append(tool)
        return {"status": "success", "kind": None, "result": "done"}
    return _run


def _steps(*tools):
    return [tc.StepDef.from_mapping({"tool": t, "args": {}})
            for t in tools]


class _FakeSession:
    """Minimal session stand-in: only what handle_typed_turn needs."""

    def __init__(self):
        self.posted = []

    def _add_system(self, text):
        self.posted.append(text)


# ---------------------------------------------------------------- S1: first task
class S1FirstTaskTests(unittest.TestCase):
    def test_first_typed_plan_turn_creates_task_with_receipt(self):
        coord = tc.TurnCoordinator(executor=_ok_executor([]))
        session = _FakeSession()
        handled = tc.handle_typed_turn(
            session, "run playbook Morning", coordinator=coord,
            plan_names=["Morning"],
            plan_steps_for=lambda name: [{"tool": "probe_a", "args": {}}])
        self.assertTrue(handled)
        self.assertEqual(len(session.posted), 1)
        self.assertIn("Morning", session.posted[0])
        # Direct handle_turn proves the receipt state, not just the post.
        result = coord.handle_turn(
            "run playbook Morning", plan_names=["Morning"],
            plan_steps_for=lambda name: [{"tool": "probe_a", "args": {}}])
        self.assertTrue(result["handled"])
        self.assertEqual(result["receipt"].state, "completed")

    def test_first_conversation_turn_flows_to_live_session(self):
        coord = tc.TurnCoordinator(executor=_ok_executor([]))
        session = _FakeSession()
        handled = tc.handle_typed_turn(
            session, "hello, how are you", coordinator=coord)
        self.assertFalse(handled)
        self.assertEqual(session.posted, [])

    def test_free_text_multi_step_falls_through_with_routing_record(self):
        coord = tc.TurnCoordinator(executor=_ok_executor([]))
        result = coord.handle_turn("First check mail then send summary")
        self.assertFalse(result["handled"])
        # The turn flows to the live session (model work), but the
        # routing decision is contract-checked, not assumed.
        self.assertIn("routing", result)
        self.assertIsNone(result["routing"]["live_brain_freeform_goal"])


# ------------------------------------------------------------- S2: offline draft
class S2OfflineDraftTests(unittest.TestCase):
    def _send_fn(self):
        # GUI split #11 batch 2 INTENTIONAL: _send_typed_message moved
        # verbatim to window_session_typed.py.
        tree = ast.parse(_src("gui/window_session_typed.py"))
        return _func(tree, "_send_typed_message")

    def test_offline_branch_restores_draft_and_names_reconnect(self):
        fn = self._send_fn()
        # GUI split #11 batch 2 INTENTIONAL: typed module (see _send_fn).
        src = ast.get_source_segment(_src("gui/window_session_typed.py"), fn)
        # The not-connected branch must restore the draft it cleared
        # at submit (S6 fix for UI-05) and name the reconnect path.
        self.assertIn("You're not connected yet", src)
        self.assertIn("press Connect first", src)
        self.assertIn("self.input_cmd.setText(text)", src)
        # The restore lives in the not-connected else branch, not on
        # the connected path: find the if/else on is_connected and
        # check the else body carries the restore.
        for node in ast.walk(fn):
            if isinstance(node, ast.If) and node.orelse:
                else_src = ast.get_source_segment(
                    _src("gui/window_session_typed.py"), node)
                if else_src and "not connected yet" in else_src:
                    self.assertIn("setText(text)", else_src)
                    break
        else:
            self.fail("no not-connected else branch found")

    def test_history_predates_clear_and_handled_turns_skip_restore(self):
        fn = self._send_fn()
        seg = ast.get_source_segment(_src("gui/window_session_typed.py"), fn)
        append_at = seg.find("hist.append(text)")
        clear_at = seg.find("self.input_cmd.clear()")
        self.assertGreater(append_at, -1)
        self.assertGreater(clear_at, append_at)
        # A coordinator-handled turn returns before the connection
        # check: an executed turn never leaves its text behind.
        handle_at = seg.find("_tc.handle_typed_turn(")
        conn_at = seg.find("live_session.is_connected")
        self.assertGreater(handle_at, -1)
        self.assertGreater(conn_at, handle_at)

    # Residual, NOT fixed here (reported, not hidden): an ASYNC send
    # failure (the _on_typed_send_done callback) still relies on
    # Up-arrow history, not an in-place restore — that callback runs
    # on the worker loop thread and must never touch widgets, so an
    # in-place restore there needs a GUI-thread hop, a bigger change
    # than this slice's small-repair rule allows.


# ------------------------------------------------------------ S3: approval (REAL gate)
class S3ApprovalTests(unittest.TestCase):
    def setUp(self):
        confirm_gate.clear_handler()
        with tools_handlers_mod._budget_lock:
            self._saved_budget = list(tools_handlers_mod._budget_timestamps)
            tools_handlers_mod._budget_timestamps.clear()
        self._registry = ToolRegistry()
        self._ran = []
        self._registry.register(
            "send_email", "probe gated tool",
            {"type": "object", "properties": {}},
            lambda **kw: self._ran.append("send_email") or {"ok": True})

    def tearDown(self):
        confirm_gate.clear_handler()
        with tools_handlers_mod._budget_lock:
            tools_handlers_mod._budget_timestamps[:] = self._saved_budget

    def _run_gated(self):
        coord = tc.TurnCoordinator(executor=self._registry.execute)
        task = coord.new_task("send it", "saved_plan",
                              [tc.StepDef.from_mapping(
                                  {"tool": "send_email", "args": {}})])
        return coord.run_task(task)

    def test_gated_step_refused_without_approval(self):
        receipt = self._run_gated()
        self.assertEqual(self._ran, [])
        self.assertEqual(receipt.state, "failed")
        self.assertIn(receipt.error_kind, ("not_confirmed", "refused"))

    def test_gated_step_runs_with_approval(self):
        confirm_gate.register_handler(lambda *a, **k: True)
        receipt = self._run_gated()
        self.assertEqual(self._ran, ["send_email"])
        self.assertEqual(receipt.state, "completed")

    def test_gated_step_denied_on_refusal(self):
        confirm_gate.register_handler(lambda *a, **k: False)
        receipt = self._run_gated()
        self.assertEqual(self._ran, [])
        self.assertEqual(receipt.state, "failed")


# ----------------------------------------------------------------- S4: stop
class S4StopTests(unittest.TestCase):
    def test_coordinator_cancel_stops_remaining_steps(self):
        calls = []
        coord = tc.TurnCoordinator(executor=_ok_executor(calls))
        task = coord.new_task("t", "routine", _steps("one", "two", "three"))

        def cancel_after_first(task_obj, outcome):
            coord.cancel_task(task_obj.id)

        receipt = coord.run_task(task, on_step=cancel_after_first)
        self.assertEqual(receipt.state, "cancelled")
        self.assertEqual(calls, ["one"])
        self.assertIn("remaining steps did not run", receipt.reason)

    def test_voice_cancel_flag_terminal_and_missing_cases(self):
        coord = tc.TurnCoordinator(executor=_ok_executor([]))
        task = coord.new_task("goal", "voice_goal", _steps("a", "b"))
        session = SimpleNamespace(_voice_goal_coordinator=coord,
                                  _voice_goal_task=task)
        self.assertTrue(cancel_voice_goal(session))
        self.assertEqual(task.state, "cancelled")
        # Already terminal: cancel is a no-op, not an error.
        task.state = "completed"
        self.assertFalse(cancel_voice_goal(session))
        # No voice goal stashed at all.
        self.assertFalse(cancel_voice_goal(object()))


# ------------------------------------------------------------ S5: reminders
class S5ReminderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._now = {"at": datetime.datetime(2026, 10, 30, 9, 0, tzinfo=UTC)}
        self._path = Path(self._tmp.name) / "scheduler_reminders.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _sched(self):
        return ProactiveScheduler(
            store_path=self._path,
            now_fn=lambda: self._now["at"])

    def test_reminder_create_list_and_persist(self):
        sched = self._sched()
        fire = self._now["at"] + datetime.timedelta(hours=1)
        label = sched.add_reminder("dentist", fire, "Dentist appointment")
        found = [r for r in sched.get_all_reminders() if r["label"] == label]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["persistent"])
        self.assertFalse(found[0]["fired"])
        # A fresh scheduler on the same store sees it (restart-proof).
        sched2 = self._sched()
        sched2.start()
        try:
            found2 = [r for r in sched2.get_all_reminders()
                      if r["label"] == label]
            self.assertEqual(len(found2), 1)
        finally:
            sched2.stop()

    def test_reminder_fires_once_then_purges(self):
        sched = self._sched()
        fire = self._now["at"] + datetime.timedelta(hours=1)
        label = sched.add_reminder("dentist", fire, "Dentist appointment")
        fired = []
        sched.on_trigger = lambda lbl, msg: fired.append((lbl, msg))
        self._now["at"] = fire + datetime.timedelta(minutes=1)
        sched._tick(self._now["at"])
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][0], label)
        self.assertIn("Dentist", fired[0][1])
        self.assertTrue(any(h["label"] == label
                            for h in sched.get_fired_history()))
        self.assertEqual([r for r in sched.get_all_reminders()
                          if r["label"] == label], [])
        # A second pass never refires a purged one-shot.
        sched._tick(self._now["at"] + datetime.timedelta(minutes=5))
        self.assertEqual(len(fired), 1)

    def test_activity_scheduled_tab_reads_scheduler(self):
        # GUI split #8 INTENTIONAL: _build_activity_page moved verbatim to
        # window_pages_activity.py. Assertion unchanged.
        pages = _src("gui/window_pages_activity.py")
        tree = ast.parse(pages)
        fn = _func(tree, "_build_activity_page")
        seg = ast.get_source_segment(pages, fn)
        self.assertIn('"Scheduled"', seg)
        self.assertIn("_build_reminders_page()", seg)
        # GAPS-28 (G-V1): the scheduler read moved to
        # gui/memory_view.py reminders_view(); _refresh_reminders
        # renders from the view.
        view = _src("gui/memory_view.py")
        vtree = ast.parse(view)
        vfn = _func(vtree, "reminders_view")
        vseg = ast.get_source_segment(view, vfn)
        self.assertIn("get_all_reminders()", vseg)
        memory = _src("gui/window_memory_learned.py")
        mtree = ast.parse(memory)
        refresh = _func(mtree, "_refresh_reminders")
        rseg = ast.get_source_segment(memory, refresh)
        self.assertIn("reminders_view()", rseg)


# ---------------------------------------------------------- S6: voice change
class S6VoiceChangeTests(unittest.TestCase):
    def _exec_brain_engine(self, value):
        # GUI split #4 INTENTIONAL: provider_resolution + _brain_engine
        # moved verbatim to window_brain_engine.py (class
        # BrainEngineMixin); the exec'd _brain_engine body references
        # BrainEngineMixin after the split's forced repoint, so the
        # namespace alias follows the mixin name. Behaviour
        # assertions below still execute real code, not a stub.
        src = _src("gui/window_brain_engine.py")
        tree = ast.parse(src)
        ns = {"config": SimpleNamespace(
            brain_engine=value, cloud_provider="gemini")}
        # Phase 1: _brain_engine derives from
        # provider_resolution(), which imports the frozen
        # voice_assistant.* name the lab cannot import. Alias the
        # REAL core.provider_identity under that name, and exec
        # both real function bodies — the behaviour assertions
        # below still execute real code, not a stub.
        import sys
        import types
        from core import provider_identity as _real_pi
        from core import gui_facade as _real_facade
        for _name in ("voice_assistant", "voice_assistant.core"):
            if _name not in sys.modules:
                _mod = types.ModuleType(_name)
                _mod.__path__ = []
                sys.modules[_name] = _mod
        # The frozen-name aliases live ONLY for the exec + call
        # below: leaving sys.modules["voice_assistant.core.
        # gui_facade"] pointing at the top-level facade would
        # leak into later suites in the same process — a GUI file
        # doing `from voice_assistant.core.gui_facade import
        # DATA_DIR` would then read core.config.DATA_DIR (never
        # patched) instead of voice_assistant.core.config.DATA_DIR
        # (what the voice-drop suites patch), and their DROP lines
        # would land in the real data dir instead of the temp one.
        _saved = {}
        for _key, _mod in (
                ("voice_assistant.core.provider_identity", _real_pi),
                ("voice_assistant.core.gui_facade", _real_facade)):
            _saved[_key] = sys.modules.get(_key)
            sys.modules[_key] = _mod
        try:
            for _fname in ("provider_resolution", "_brain_engine"):
                _fn = _func(tree, _fname, in_class="BrainEngineMixin")
                _seg = ast.get_source_segment(src, _fn)
                exec(compile(ast.parse(_seg), "<brain_engine>", "exec"), ns)
            ns["BrainEngineMixin"] = SimpleNamespace(
                provider_resolution=ns["provider_resolution"])
            return ns["_brain_engine"]()
        finally:
            for _key, _prev in _saved.items():
                if _prev is None:
                    sys.modules.pop(_key, None)
                else:
                    sys.modules[_key] = _prev

    def test_brain_engine_validation_uses_real_function_body(self):
        self.assertEqual(self._exec_brain_engine("gemini"), "gemini")
        self.assertEqual(self._exec_brain_engine("local"), "local")
        self.assertEqual(self._exec_brain_engine("LOCAL"), "local")
        self.assertEqual(self._exec_brain_engine("bogus"), "gemini")
        self.assertEqual(self._exec_brain_engine(""), "gemini")
        self.assertEqual(self._exec_brain_engine(None), "gemini")

    def test_engine_change_handler_sets_and_persists(self):
        # GUI split #4 INTENTIONAL: _on_brain_engine_changed moved
        # verbatim to window_brain_engine.py. Assertions unchanged.
        tree = ast.parse(_src("gui/window_brain_engine.py"))
        fn = _func(tree, "_on_brain_engine_changed", in_class="BrainEngineMixin")
        seg = ast.get_source_segment(_src("gui/window_brain_engine.py"), fn)
        self.assertIn('engine not in ("gemini", "local")', seg)
        self.assertIn("config.brain_engine = engine", seg)
        self.assertIn('_persist_config_setting("BRAIN_ENGINE", engine)', seg)
        self.assertIn("_maybe_auto_provision()", seg)
        self.assertIn("_refresh_setup_banner()", seg)
        # The Settings combo chains the think-card sync to the pick.
        # INTENTIONAL (refactor P1 step 5): the Advanced-brain section
        # moved verbatim to window_settings_advbrain.py. Assertion unchanged.
        settings = _src("gui/window_settings_advbrain.py")
        self.assertIn(
            "combo_brain_engine.currentIndexChanged.connect("
            "lambda *_: self._sync_think_card())",
            settings)

    def test_engine_persistence_writes_temp_env_not_real_env(self):
        from core import config as cfgmod
        from core.config import save_env_setting
        with tempfile.TemporaryDirectory() as tmp:
            old_root = cfgmod.PROJECT_ROOT
            old_env = os.environ.get("BRAIN_ENGINE")
            try:
                cfgmod.PROJECT_ROOT = Path(tmp)
                save_env_setting("BRAIN_ENGINE", "local")
                text = (Path(tmp) / ".env").read_text(encoding="utf-8")
                self.assertIn("BRAIN_ENGINE=local", text)
                self.assertEqual(os.environ.get("BRAIN_ENGINE"), "local")
            finally:
                cfgmod.PROJECT_ROOT = old_root
                if old_env is None:
                    os.environ.pop("BRAIN_ENGINE", None)
                else:
                    os.environ["BRAIN_ENGINE"] = old_env


# -------------------------------------------------------- S7: screen access
class S7ScreenAccessTests(unittest.TestCase):
    def setUp(self):
        self._saved_enabled = CONTROL.enabled
        self._saved_listeners = list(CONTROL._listeners)

    def tearDown(self):
        CONTROL._listeners[:] = self._saved_listeners
        if CONTROL.enabled != self._saved_enabled:
            set_watch_enabled(self._saved_enabled, source="test-restore")

    def test_watch_state_truth_pill_and_listeners(self):
        seen = []
        subscribe_watch(seen.append)
        if watch_enabled():
            set_watch_enabled(False, source="test")
            seen.clear()
        self.assertFalse(watch_enabled())
        self.assertEqual(pill_state(), (False, ""))
        self.assertTrue(set_watch_enabled(True, source="test"))
        self.assertTrue(watch_enabled())
        visible, text = pill_state()
        self.assertTrue(visible)
        self.assertTrue(text)
        self.assertEqual(seen, [True])
        # A no-change set is not a change: no re-notify.
        self.assertFalse(set_watch_enabled(True, source="test"))
        self.assertEqual(seen, [True])
        self.assertTrue(set_watch_enabled(False, source="test"))
        self.assertEqual(pill_state(), (False, ""))
        self.assertEqual(seen, [True, False])

    def test_window_pill_renders_switchboard_state(self):
        # GUI split #11 batch 2 INTENTIONAL: _sync_watch_pill moved
        # verbatim to window_session_watch.py.
        src = _src("gui/window_session_watch.py")
        tree = ast.parse(src)
        fn = _func(tree, "_sync_watch_pill")
        seg = ast.get_source_segment(src, fn)
        self.assertIn("pill_state(", seg)
        self.assertIn("setVisible(visible)", seg)
        self.assertIn("setText(", seg)
        # The pill is wired to the switchboard subscription path.
        self.assertIn("subscribe_watch", src)
        self.assertIn("_sync_watch_pill", src)


# ------------------------------------------------------------ S8: recovery
class S8RecoveryTests(unittest.TestCase):
    def test_failed_task_surfaces_reason(self):
        def failing(tool, args):
            return {"status": "failure", "kind": "handler_error",
                    "message": "boom"}
        coord = tc.TurnCoordinator(executor=failing)
        task = coord.new_task("t", "routine",
                              [tc.StepDef.from_mapping(
                                  {"tool": "b", "args": {},
                                   "label": "step-b"})])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "failed")
        self.assertIn("step-b", receipt.reason)
        self.assertIn("boom", receipt.reason)
        self.assertEqual(receipt.error_kind, "handler_error")

    def test_paused_task_resumes_without_rerunning(self):
        calls = []
        coord = tc.TurnCoordinator(executor=_ok_executor(calls))
        task = coord.new_task("t", "routine", _steps("a", "b", "c"))

        def pause_after_first(task_obj, outcome):
            coord.pause_task(task_obj.id)

        receipt = coord.run_task(task, on_step=pause_after_first)
        self.assertEqual(receipt.state, "paused")
        self.assertEqual(calls, ["a"])
        receipt = coord.resume_task(task.id)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(calls, ["a", "b", "c"])

    def test_ledger_resume_and_terminal_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task_ledger.json"
            calls = []
            ledger = TaskLedger(path=path)
            coord = tc.TurnCoordinator(executor=_ok_executor(calls),
                                       ledger=ledger)
            task = coord.new_task("t", "routine", _steps("a", "b", "c"))

            def pause_after_first(task_obj, outcome):
                coord.pause_task(task_obj.id)

            coord.run_task(task, on_step=pause_after_first)
            summaries = ledger.list_interrupted()
            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0]["remaining_labels"], ["b", "c"])
            restarted = TaskLedger(path=path)
            coord2 = tc.TurnCoordinator(executor=_ok_executor(calls))
            calls.clear()
            receipt = coord2.resume_ledger_task(
                restarted.get(task.id), ledger=restarted)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(calls, ["b", "c"])
            self.assertEqual(restarted.get(task.id)["state"], "completed")
            self.assertEqual(restarted.list_interrupted(), [])
            # Terminal records are history, not resumable — the
            # honest boundary: a FAILED task is not auto-resumed.
            # Recovery for it is a new task (fix + rerun), and the
            # failure reason above is what the user acts on.
            with self.assertRaises(ValueError):
                TaskLedger().rebuild_task(
                    {"task_id": "x", "state": "failed",
                     "steps": [], "outcomes": []})


if __name__ == "__main__":
    unittest.main()
