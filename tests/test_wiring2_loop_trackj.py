"""Wiring Build 2 tests (stdlib unittest, no GUI, no network, no model).

What is wired into core/brain/planner.py:run_goal here, and what is
NOT claimed: the loop itself is exercised end-to-end on faked
seams (registry execute + observation), following the pattern of
tests/test_regression_bypasses.py. No GUI render, no real control
drive, and no field goal is claimed — field proof on the owner's
machine after the BB101 quiz is the exit proof for loop behaviour
on real goals.

Covered, in the task's order:
- J1: a reference resolves to the right target (explicit context
  and observation-derived context); an unresolved reference does
  not guess — the action runs exactly as picked.
- J7: pause/resume inside the step loop, pause-then-abandon, and
  the interruption budget (a 4th non-urgent interruption abandons
  even when it asks to resume).
- J9: a proven playbook hit runs first through the same funnel;
  a miss changes nothing; a deletion entry is refused at add
  time and therefore never queued.
- Control breadth: a named control is revalidated through the
  hardened path (pass executes, stale halts without executing);
  a coordinates-only request to a name-addressed control tool is
  refused; with no snapshot the gate is honestly skipped and the
  existing registry gate still applies.
- Guardrails: a refused execute result still halts (C-14 funnel
  preserved — the loop never bypasses it), and the step budget
  (25) and strike limit (3) are unchanged.
"""

import asyncio
import itertools
import unittest

from core.memory import playbook_store
from core.brain import planner  # Phase 3 B2-remainder INTENTIONAL: planner moved to core/brain/
from core.brain import planner_checkpoint, planner_rungoal  # INTENTIONAL (skills split B41): impl modules for dual-patching (see setUp notes below)
from core.funnel.tools_registry import tools as registry  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class _Base(unittest.TestCase):
    def setUp(self):
        self._orig_failsafe = planner._failsafe_tripped
        planner._failsafe_tripped = lambda: False
        # INTENTIONAL (skills split B41): _failsafe_tripped moved to the
        # checkpoint module (run_goal imports it from there); patch all
        # three bindings, same fake, all restored below.
        planner_checkpoint._failsafe_tripped = planner._failsafe_tripped
        planner_rungoal._failsafe_tripped = planner._failsafe_tripped
        self._orig_execute = registry.execute
        self.executions = []

        def fake_execute(name, args=None, **kwargs):
            args = dict(args or {})
            self.executions.append((name, args))
            return {"status": "success", "result": "ok"}

        registry.execute = fake_execute
        self._counter = itertools.count()

    def tearDown(self):
        registry.execute = self._orig_execute
        planner._failsafe_tripped = self._orig_failsafe
        planner_checkpoint._failsafe_tripped = self._orig_failsafe
        planner_rungoal._failsafe_tripped = self._orig_failsafe

    def _observe_counter(self):
        return lambda: next(self._counter)

    def _run_goal(self, actions, **kwargs):
        """actions: list of picker actions, then a done summary."""
        queue = list(actions)

        async def pick(goal, observation, history):
            if queue:
                return queue.pop(0)
            return {"done": "done"}

        emitted = []
        report = _run(planner.run_goal(
            "wiring2 test goal", pick_action=pick,
            observe=self._observe_counter(), emit=emitted.append, **kwargs))
        return report, emitted


class J1Tests(_Base):
    def test_reference_resolves_to_focused_control(self):
        context = {"focused_control": {"label": "Save button",
                                       "control_id": "btn-save"}}
        report, _ = self._run_goal(
            [{"tool": "quick_math", "args": {"target": "this"},
              "text": "click this"}],
            goal_context=context)
        self.assertEqual(self.executions[0][1].get("target"), "Save button")
        self.assertEqual(self.executions[0][1].get("control_id"), "btn-save")
        self.assertIn("resolved 'this' to 'Save button'", report)

    def test_reference_resolves_from_observation_focus(self):
        # No explicit context: the UIA snapshot's focus/fg supply it.
        observations = iter([{"fg": "Notepad", "focus": "Save button"},
                             {"fg": "Notepad", "focus": "Saved"}])

        async def pick(goal, observation, history):
            return {"tool": "quick_math", "args": {"target": "this"},
                    "text": "click this"} if not self.executions \
                else {"done": "done"}

        emitted = []
        report = _run(planner.run_goal(
            "wiring2 test goal", pick_action=pick,
            observe=lambda: next(observations, {"fg": "Notepad",
                                                "focus": "Saved"}),
            emit=emitted.append))
        self.assertEqual(self.executions[0][1].get("target"), "Save button")
        self.assertIn("resolved 'this' to 'Save button'", report)

    def test_unresolved_reference_does_not_guess(self):
        report, _ = self._run_goal(
            [{"tool": "quick_math", "args": {"target": "this"},
              "text": "click this"}],
            goal_context={})
        # Executed exactly as picked — the literal word, no invented target.
        self.assertEqual(self.executions[0][1].get("target"), "this")
        self.assertIn("could not be resolved", report)
        self.assertIn("no guessed target", report)

    def test_no_reference_changes_nothing(self):
        report, _ = self._run_goal(
            [{"tool": "quick_math", "args": {"op": "advance", "to": "s1"}}])
        self.assertEqual(self.executions[0][1].get("to"), "s1")
        self.assertNotIn("resolved", report)


class J7Tests(_Base):
    def test_pause_then_resume_completes(self):
        calls = iter([{"reason": "user ping", "resume": True,
                       "held_item": "step draft"}])

        def check():
            return next(calls, None)

        report, _ = self._run_goal(
            [{"tool": "quick_math", "args": {"op": "advance", "to": "s1"}}],
            interruption_check=check)
        self.assertIn("Goal paused", report)
        self.assertIn("Goal resumed", report)
        self.assertIn("Goal complete", report)
        self.assertEqual(len(self.executions), 1)

    def test_pause_then_abandon_stops_without_executing(self):
        def check():
            return {"reason": "stop now", "resume": False,
                    "held_item": "step draft"}

        report, _ = self._run_goal(
            [{"tool": "quick_math", "args": {"op": "advance", "to": "s1"}}],
            interruption_check=check)
        self.assertIn("Goal paused", report)
        self.assertIn("Goal abandoned", report)
        self.assertIn("step draft", report)
        self.assertEqual(self.executions, [])

    def test_interruption_budget_abandons_on_fourth(self):
        def check():
            return {"reason": "ping", "resume": True}

        # Four picker actions; the 4th step boundary is interrupted
        # a 4th time, which exceeds the budget of 3 and abandons.
        report, _ = self._run_goal(
            [{"tool": "quick_math", "args": {"op": "advance", "to": f"s{i}"}}
             for i in range(4)],
            interruption_check=check)
        self.assertIn("interruption budget exceeded", report)
        self.assertEqual(len(self.executions), 3)


class J9Tests(_Base):
    def test_playbook_hit_runs_first_through_the_funnel(self):
        book = playbook_store.PlaybookStore()
        added = book.add("wiring2 test goal",
                         [{"tool": "quick_math",
                           "args": {"op": "advance", "to": "pb1"}}],
                         proof_job_id="field-job-1")
        self.assertTrue(added["added"])
        report, _ = self._run_goal([], playbook=book)
        self.assertIn("Playbook matched", report)
        self.assertIn("proof job field-job-1", report)
        self.assertEqual(self.executions[0][1].get("to"), "pb1")
        self.assertIn("Goal complete", report)

    def test_playbook_miss_changes_nothing(self):
        book = playbook_store.PlaybookStore()
        report, _ = self._run_goal(
            [{"tool": "quick_math", "args": {"op": "advance", "to": "s1"}}],
            playbook=book)
        self.assertNotIn("Playbook matched", report)
        self.assertEqual(self.executions[0][1].get("to"), "s1")

    def test_deletion_entry_never_queues(self):
        book = playbook_store.PlaybookStore()
        added = book.add("wiring2 test goal",
                         [{"tool": "delete_file", "args": {}}],
                         proof_job_id="field-job-1")
        self.assertFalse(added["added"])
        report, _ = self._run_goal(
            [{"tool": "quick_math", "args": {"op": "advance", "to": "s1"}}],
            playbook=book)
        self.assertNotIn("Playbook matched", report)
        self.assertEqual(self.executions[0][1].get("to"), "s1")


class ControlBreadthTests(_Base):
    def test_revalidated_control_executes(self):
        snapshot = [{"control_id": "btn-save", "fingerprint": "fp1"}]
        report, _ = self._run_goal(
            [{"tool": "uia_click_control",
              "args": {"control_id": "btn-save", "fingerprint": "fp1",
                       "name": "Save"}}],
            control_snapshot=snapshot)
        self.assertIn("control revalidated: btn-save", report)
        self.assertEqual(len(self.executions), 1)

    def test_stale_control_halts_without_executing(self):
        snapshot = [{"control_id": "btn-save", "fingerprint": "fp2"}]
        report, _ = self._run_goal(
            [{"tool": "uia_click_control",
              "args": {"control_id": "btn-save", "fingerprint": "fp1",
                       "name": "Save"}}],
            control_snapshot=snapshot)
        self.assertIn("stale control 'btn-save'", report)
        self.assertEqual(self.executions, [])

    def test_coordinates_only_control_tool_refused(self):
        report, _ = self._run_goal(
            [{"tool": "uia_click_control", "args": {"x": 10, "y": 20}}])
        self.assertIn("coordinates-only", report)
        self.assertEqual(self.executions, [])

    def test_no_snapshot_skips_gate_honestly_but_still_executes(self):
        report, _ = self._run_goal(
            [{"tool": "uia_click_control",
              "args": {"control_id": "btn-save", "name": "Save"}}])
        self.assertIn("control revalidation skipped: no snapshot", report)
        self.assertEqual(len(self.executions), 1)


class GuardrailTests(_Base):
    def test_refused_execute_result_still_halts(self):
        # The C-14 funnel is preserved: the wiring adds no gate
        # and bypasses none — a refusal from execute() halts the
        # goal and is never retried or bypassed.
        def refusing_execute(name, args=None, **kwargs):
            self.executions.append((name, dict(args or {})))
            return {"status": "error",
                    "message": "not confirmed (no handler registered)"}

        registry.execute = refusing_execute
        report, _ = self._run_goal(
            [{"tool": "quick_math", "args": {"op": "advance", "to": "s1"}}])
        self.assertIn("halted", report)
        self.assertEqual(len(self.executions), 1)

    def test_step_budget_unchanged_at_25(self):
        async def pick(goal, observation, history):
            return {"tool": "quick_math", "args": {"op": "advance"}}

        emitted = []
        report = _run(planner.run_goal(
            "wiring2 test goal", pick_action=pick,
            observe=self._observe_counter(), emit=emitted.append))
        self.assertIn("step budget exhausted (25 step(s) used in budget window 1; 25 total)", report)
        self.assertEqual(len(self.executions), 25)

    def test_strike_limit_unchanged_at_3(self):
        # An observation that never changes: each step retries
        # once, then strikes; 3 consecutive failed steps stop it.
        async def pick(goal, observation, history):
            return {"tool": "quick_math", "args": {"op": "advance"}}

        emitted = []
        report = _run(planner.run_goal(
            "wiring2 test goal", pick_action=pick,
            observe=lambda: "unchanged", emit=emitted.append))
        self.assertIn("strike limit reached (3/3", report)


if __name__ == "__main__":
    unittest.main()
