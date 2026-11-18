"""Task/Turn Coordinator (identity-gap audit P0).

Covers: classification (all 5 kinds + fall-through rules),
lifecycle (start/pause/resume/cancel/complete), receipt shape,
cancellation actually stopping steps, pause/resume from the next
step, step validation, mission-receipt conversion, turn handling
(saved plan + routine end-to-end, non-task fall-through), the
session-handler-level typed entry, and the C-14 guard (a gated
action inside a task still requires approval — proved through
the REAL funnel, never a stubbed approval).

Stdlib unittest only. Funnel executes use a fresh registry with
the process-global budget snapshot/reset/restored per the
testiso guest rule (the budget itself is covered by the A2
suite, not here); all other executes use stub executors, so
this suite cannot trip the full-run budget window.
"""

import unittest

from core import confirm_gate
from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.funnel import tools_handlers as tools_handlers_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel.tools_registry import ToolRegistry  # Phase 3 F3 INTENTIONAL: moved to core/funnel/


def _ok(**kw):
    def _run(tool, args):
        _run.calls.append((tool, dict(args)))
        return {"status": "success", "kind": None, "result": "done"}
    _run.calls = []
    return _run


def _steps(*tools):
    return [{"tool": t, "args": {}} for t in tools]


class _FakeSession:
    """Minimal session stand-in: only what handle_typed_turn needs."""

    def __init__(self):
        self.posted = []

    def _add_system(self, text):
        self.posted.append(text)


class ClassificationTests(unittest.TestCase):
    def test_saved_plan_exact_and_case_insensitive(self):
        cls = tc.classify_turn("run playbook Morning",
                               plan_names=["Morning", "Evening"])
        self.assertEqual(cls.kind, "saved_plan")
        self.assertEqual(cls.name, "Morning")
        cls = tc.classify_turn('run playbook "morning"',
                               plan_names=["Morning"])
        self.assertEqual(cls.kind, "saved_plan")
        self.assertEqual(cls.name, "Morning")
        cls = tc.classify_turn("playbook Evening", plan_names=["Evening"])
        self.assertEqual(cls.kind, "saved_plan")

    def test_routine_invocation(self):
        cls = tc.classify_turn("run routine Backup",
                               routine_names=["Backup"])
        self.assertEqual(cls.kind, "routine")
        self.assertEqual(cls.name, "Backup")

    def test_unresolvable_name_never_claims_class(self):
        cls = tc.classify_turn("run playbook Nope", plan_names=["Morning"])
        self.assertNotEqual(cls.kind, "saved_plan")
        cls = tc.classify_turn("run routine Nope", routine_names=["Backup"])
        self.assertNotEqual(cls.kind, "routine")

    def test_multi_step_structures(self):
        cases = [
            "1. Open mail\n2. Send summary\n3. Close mail",
            "First check mail then send summary",
            "open mail; send summary; close mail",
            "Step 1: open mail Step 2: send summary",
        ]
        for text in cases:
            with self.subTest(text=text[:30]):
                self.assertEqual(tc.classify_turn(text).kind, "multi_step")

    def test_single_action_and_conversation(self):
        self.assertEqual(tc.classify_turn("list files").kind, "single_action")
        self.assertEqual(tc.classify_turn("what time is it?").kind,
                         "conversation")
        self.assertEqual(tc.classify_turn("hello").kind, "conversation")
        self.assertEqual(tc.classify_turn("").kind, "conversation")
        self.assertEqual(
            tc.classify_turn("The weather looks nice today").kind,
            "conversation")


class LifecycleTests(unittest.TestCase):
    def test_three_step_task_completes_with_receipt(self):
        coord = tc.TurnCoordinator(executor=_ok())
        task = coord.new_task("do things", "saved_plan",
                              [tc.StepDef.from_mapping(m) for m in _steps("a", "b", "c")])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual((receipt.attempted, receipt.completed,
                          receipt.failed, receipt.skipped), (3, 3, 0, 0))
        self.assertEqual(receipt.remaining, [])
        self.assertEqual(receipt.reason, "")
        self.assertEqual(receipt.task_id, task.id)

    def test_failure_halts_and_remaining_includes_failed_step(self):
        def _run(tool, args):
            _run.calls.append(tool)
            if tool == "b":
                return {"status": "failure", "kind": "handler_error",
                        "message": "boom"}
            return {"status": "success", "kind": None, "result": "ok"}
        _run.calls = []
        coord = tc.TurnCoordinator(executor=_run)
        task = coord.new_task("t", "routine",
                              [tc.StepDef.from_mapping(m) for m in _steps("a", "b", "c")])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(_run.calls, ["a", "b"])
        self.assertEqual(receipt.remaining, ["b", "c"])
        self.assertIn("b", receipt.reason)
        self.assertEqual(receipt.error_kind, "handler_error")

    def test_on_fail_skip_continues(self):
        def _run(tool, args):
            if tool == "b":
                return {"status": "refused", "kind": "refused",
                        "message": "no"}
            return {"status": "success", "kind": None, "result": "ok"}
        coord = tc.TurnCoordinator(executor=_run)
        steps = [{"tool": "a", "args": {}},
                 {"tool": "b", "args": {}, "on_fail": "skip"},
                 {"tool": "c", "args": {}}]
        task = coord.new_task("t", "routine",
                              [tc.StepDef.from_mapping(m) for m in steps])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.skipped, 1)
        self.assertEqual(receipt.completed, 2)

    def test_when_probe_skip_and_halt(self):
        def _run(tool, args):
            if tool == "probe":
                return {"status": "failure", "kind": "unavailable",
                        "message": "probe down"}
            _run.calls.append(tool)
            return {"status": "success", "kind": None, "result": "ok"}
        _run.calls = []
        coord = tc.TurnCoordinator(executor=_run)
        skip_steps = [{"tool": "a", "args": {},
                       "when": {"tool": "probe", "args": {}},
                       "on_fail": "skip"},
                      {"tool": "b", "args": {}}]
        task = coord.new_task("t", "routine",
                              [tc.StepDef.from_mapping(m) for m in skip_steps])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.skipped, 1)
        self.assertEqual(_run.calls, ["b"])
        halt_steps = [{"tool": "a", "args": {},
                       "when": {"tool": "probe", "args": {}},
                       "on_fail": "halt"}]
        task2 = coord.new_task("t", "routine",
                               [tc.StepDef.from_mapping(m) for m in halt_steps])
        receipt2 = coord.run_task(task2)
        self.assertEqual(receipt2.state, "failed")
        self.assertEqual(_run.calls, ["b"])

    def test_cancel_via_callback_stops_remaining_steps(self):
        calls = []

        def _run(tool, args):
            calls.append(tool)
            return {"status": "success", "kind": None, "result": "ok"}

        coord = tc.TurnCoordinator(executor=_run)
        task = coord.new_task("t", "saved_plan",
                              [tc.StepDef.from_mapping(m) for m in _steps("a", "b", "c")])

        def _cancel_after_first(t, outcome):
            coord.cancel_task(t.id)

        receipt = coord.run_task(task, on_step=_cancel_after_first)
        self.assertEqual(receipt.state, "cancelled")
        self.assertEqual(calls, ["a"])
        self.assertEqual(receipt.remaining, ["b", "c"])
        self.assertIn("cancelled", receipt.reason)

    def test_pause_then_resume_continues_from_next_step(self):
        calls = []

        def _run(tool, args):
            calls.append(tool)
            return {"status": "success", "kind": None, "result": "ok"}

        coord = tc.TurnCoordinator(executor=_run)
        task = coord.new_task("t", "saved_plan",
                              [tc.StepDef.from_mapping(m) for m in _steps("a", "b", "c")])

        def _pause_after_first(t, outcome):
            coord.pause_task(t.id)

        receipt = coord.run_task(task, on_step=_pause_after_first)
        self.assertEqual(receipt.state, "paused")
        self.assertEqual(calls, ["a"])
        self.assertEqual(receipt.remaining, ["b", "c"])
        receipt2 = coord.resume_task(task.id)
        self.assertEqual(receipt2.state, "completed")
        self.assertEqual(calls, ["a", "b", "c"])
        self.assertEqual(receipt2.completed, 3)

    def test_pause_cancel_resume_guards(self):
        coord = tc.TurnCoordinator(executor=_ok())
        task = coord.new_task("t", "routine",
                              [tc.StepDef.from_mapping(m) for m in _steps("a")])
        self.assertTrue(coord.pause_task(task.id))
        self.assertEqual(task.state, "paused")
        self.assertTrue(coord.cancel_task(task.id))
        self.assertEqual(task.state, "cancelled")
        self.assertIsNone(coord.resume_task(task.id))
        self.assertFalse(coord.cancel_task(task.id))
        task2 = coord.new_task("t", "routine",
                               [tc.StepDef.from_mapping(m) for m in _steps("a")])
        receipt = coord.run_task(task2)
        self.assertEqual(receipt.state, "completed")
        again = coord.run_task(task2)
        self.assertEqual(again.state, "completed")
        self.assertEqual(again.attempted, 1)


class StepValidationTests(unittest.TestCase):
    def test_invalid_steps_rejected(self):
        for bad in ({}, {"args": {}}, {"tool": "  "},
                    {"tool": "a", "args": "nope"},
                    {"tool": "a", "on_fail": "goto"},
                    {"tool": "a", "when": {"args": {}}},
                    {"tool": "a", "when": {"tool": "p", "args": "nope"}}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    tc.StepDef.from_mapping(bad)


class ReceiptTests(unittest.TestCase):
    def test_receipt_dict_shape(self):
        coord = tc.TurnCoordinator(executor=_ok())
        task = coord.new_task("t", "saved_plan",
                              [tc.StepDef.from_mapping(m) for m in _steps("a")])
        receipt = coord.run_task(task).as_dict()
        self.assertEqual(set(receipt.keys()),
                         {"task_id", "classification", "title", "state",
                          "steps", "attempted", "completed", "failed",
                          "skipped", "remaining", "reason", "error_kind",
                          # Retry-on-not-met INTENTIONAL: the receipt
                          # carries the retry history ("retries")
                          # alongside verification — empty here
                          # (no step was ever retried), and the
                          # full shape is pinned by
                          # test_coordinator_retry_notmet.
                          # Second-opinion auto-invoke INTENTIONAL:
                          # the receipt carries the second-opinion
                          # history ("second_opinions") alongside
                          # retries — empty here (no step was
                          # ever checked), and the full shape is
                          # pinned by test_second_opinion_autoinvoke.
                          # B14 INTENTIONAL (pin correction): the
                          # set was STALE — it never gained
                          # "goal_verdict" (B10, the goal check's
                          # verdict) or "jumps" (B4, the jump
                          # history), so this test was already
                          # failing on the pre-batch tree (caught
                          # by B14's neighbour set, which first
                          # included test_coordinator). Both are
                          # receipt keys, pinned in full by
                          # test_goal_verification and the B4
                          # jump tests; this pin now lists them.
                          "verification", "goal_verdict", "retries",
                          "second_opinions", "jumps",
                          # B15 INTENTIONAL: the receipt carries
                          # the answered clarifying questions
                          # ("questions") alongside the histories
                          # — empty here (no step ever asked), and
                          # the full shape is pinned by
                          # test_midtask_questions.
                          "questions",
                          # B22 INTENTIONAL: the receipt carries
                          # the initial decomposition
                          # ("decomposition") and the re-plan
                          # history ("replans") alongside the
                          # histories — empty here (the task ran
                          # a declared plan and never re-planned),
                          # and the full shape is pinned by
                          # test_subgoal_replan.
                          "decomposition", "replans",
                          # B24 INTENTIONAL: the receipt carries
                          # the second-opinion OFF record
                          # ("second_opinion_off") alongside the
                          # histories — empty here (the check
                          # ran, or no step ever needed one),
                          # and the full shape is pinned by
                          # test_secondopinion_surfacing.
                          "second_opinion_off",
                          # B14 INTENTIONAL: the receipt carries
                          # the task's spend ("spend") alongside
                          # the histories — elapsed wall time,
                          # steps, executions, cloud/local calls,
                          # cost where measured. The one-step task
                          # here ran one execution, no cloud
                          # calls, cost known (nothing spent).
                          "spend"})
        self.assertEqual(receipt["retries"], [])
        self.assertEqual(receipt["second_opinions"], [])
        self.assertEqual(receipt["questions"], [])
        self.assertEqual(receipt["decomposition"], {})
        self.assertEqual(receipt["replans"], [])
        self.assertEqual(receipt["spend"]["steps_run"], 1)
        self.assertEqual(receipt["spend"]["executions"], 1)
        self.assertEqual(receipt["spend"]["cloud_calls"], [])
        self.assertEqual(receipt["spend"]["local_calls"], 0)
        # No cloud calls: cost 0.0 is a measured zero (nothing
        # spent), not a fabricated one — None means unknown.
        self.assertEqual(receipt["spend"]["cost_usd"], 0.0)
        self.assertTrue(receipt["spend"]["cost_known"])
        self.assertGreaterEqual(receipt["spend"]["elapsed_s"], 0.0)
        self.assertEqual(receipt["steps"][0]["tool"], "a")

    def test_mission_receipt_conversion(self):
        from core.mission_control import receipts_to_binder
        coord = tc.TurnCoordinator(executor=_ok())
        task = coord.new_task("morning plan", "saved_plan",
                              [tc.StepDef.from_mapping(m) for m in _steps("a", "b")])
        coord.run_task(task)
        mission = coord.to_mission_receipt(task)
        self.assertEqual(mission.status, "completed")
        self.assertTrue(mission.evidence)
        binder = receipts_to_binder([mission], "morning plan")
        self.assertTrue(binder["completed"])
        cancelled = tc.TurnCoordinator(executor=_ok())
        task2 = cancelled.new_task("t", "routine",
                                   [tc.StepDef.from_mapping(m) for m in _steps("a")])
        cancelled.cancel_task(task2.id)
        mission2 = cancelled.to_mission_receipt(task2)
        self.assertEqual(mission2.status, "stopped")
        self.assertTrue(mission2.refusal_reasons)


class TurnHandlingTests(unittest.TestCase):
    def _coord(self):
        return tc.TurnCoordinator(executor=_ok())

    def test_saved_plan_end_to_end(self):
        coord = self._coord()
        result = coord.handle_turn(
            "run playbook Morning", plan_names=["Morning"],
            plan_steps_for=lambda name: _steps("a", "b", "c"))
        self.assertTrue(result["handled"])
        self.assertEqual(result["receipt"].state, "completed")
        self.assertEqual(result["receipt"].completed, 3)
        self.assertEqual(result["task"].classification, "saved_plan")

    def test_routine_three_steps_in_order(self):
        calls = []

        def _run(tool, args):
            calls.append(tool)
            return {"status": "success", "kind": None, "result": "ok"}

        coord = tc.TurnCoordinator(executor=_run)
        result = coord.handle_turn(
            "run routine Backup", routine_names=["Backup"],
            routine_steps_for=lambda name: _steps("a", "b", "c"))
        self.assertTrue(result["handled"])
        self.assertEqual(calls, ["a", "b", "c"])
        self.assertEqual(result["receipt"].state, "completed")

    def test_non_task_turns_fall_through(self):
        coord = self._coord()
        for text in ("hello", "list files",
                     "1. Open mail\n2. Send summary"):
            with self.subTest(text=text[:20]):
                result = coord.handle_turn(text)
                self.assertFalse(result["handled"])
                self.assertIsNone(result["task"])

    def test_unloadable_plan_is_failed_receipt_not_silent(self):
        coord = self._coord()
        result = coord.handle_turn(
            "run playbook Morning", plan_names=["Morning"],
            plan_steps_for=lambda name: None)
        self.assertTrue(result["handled"])
        self.assertEqual(result["receipt"].state, "failed")
        self.assertIn("Morning", result["receipt"].reason)

    def test_args_passed_verbatim_no_confirm_injected(self):
        seen = []

        def _run(tool, args):
            seen.append(dict(args))
            return {"status": "success", "kind": None, "result": "ok"}

        coord = tc.TurnCoordinator(executor=_run)
        task = coord.new_task("t", "saved_plan",
                              [tc.StepDef.from_mapping(
                                  {"tool": "send_email",
                                   "args": {"to": "a@b.c"}})])
        coord.run_task(task)
        self.assertEqual(seen, [{"to": "a@b.c"}])
        self.assertNotIn("confirm", seen[0])


class TypedTurnTests(unittest.TestCase):
    def test_typed_saved_plan_posts_receipt_and_reuses_coordinator(self):
        session = _FakeSession()
        handled = tc.handle_typed_turn(
            session, "run playbook Morning", plan_names=["Morning"],
            plan_steps_for=lambda name: _steps("a", "b"))
        self.assertTrue(handled)
        self.assertEqual(len(session.posted), 1)
        self.assertIn("completed", session.posted[0])
        first = session._task_coordinator
        handled2 = tc.handle_typed_turn(
            session, "run playbook Morning", plan_names=["Morning"],
            plan_steps_for=lambda name: _steps("a"))
        self.assertTrue(handled2)
        self.assertIs(session._task_coordinator, first)

    def test_typed_conversation_falls_through(self):
        session = _FakeSession()
        self.assertFalse(tc.handle_typed_turn(session, "hello"))
        self.assertEqual(session.posted, [])

    def test_typed_turn_without_post_path_still_runs(self):
        class _Bare:
            pass
        session = _Bare()
        self.assertTrue(tc.handle_typed_turn(
            session, "run routine Backup", routine_names=["Backup"],
            routine_steps_for=lambda name: _steps("a")))


class ConfirmGateTests(unittest.TestCase):
    """C-14 through the REAL funnel: the coordinator never approves."""

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

    def test_gated_action_refused_without_approval(self):
        coord = tc.TurnCoordinator(executor=self._registry.execute)
        task = coord.new_task("send it", "saved_plan",
                              [tc.StepDef.from_mapping(
                                  {"tool": "send_email", "args": {}})])
        receipt = coord.run_task(task)
        self.assertEqual(self._ran, [])
        self.assertEqual(receipt.state, "failed")
        self.assertIn(receipt.error_kind, ("not_confirmed", "refused"))

    def test_gated_action_runs_with_approval(self):
        confirm_gate.register_handler(lambda *a, **k: True)
        coord = tc.TurnCoordinator(executor=self._registry.execute)
        task = coord.new_task("send it", "saved_plan",
                              [tc.StepDef.from_mapping(
                                  {"tool": "send_email", "args": {}})])
        receipt = coord.run_task(task)
        self.assertEqual(self._ran, ["send_email"])
        self.assertEqual(receipt.state, "completed")


if __name__ == "__main__":
    unittest.main()
