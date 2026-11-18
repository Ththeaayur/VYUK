"""Plan approval for direct multi-step requests
(UI rethink Build 4, plan §4.5).

What this suite proves:

* parse_plan_steps() — a {"steps": [...]} object and a bare
  [...] list both parse; prose, bad JSON, a single action
  and unknown tools read as no plan; a step with non-dict
  args is dropped, never guessed.
* plan_direct_goal() — the planning pass for one text goal:
  - no handler registered: no model call at all, outcome
    "no_handler" (a plan nobody can approve is overhead);
  - two approvable steps + the human approves both: the
    pair (task_id, [StepDef, StepDef]) and outcome
    "approved", recorded by approve_plan;
  - one approvable step: no dialog, outcome
    "too_few_steps" (a single call still asks once);
  - no plan / model failure: outcome "no_plan", no dialog;
  - the human declines: outcome "ask_each_time", nothing
    recorded, the goal runs as today;
  - the dialog times out: outcome "timeout", nothing
    recorded;
  - a never-list step smuggled into the dialog's approval:
    approve_plan refuses it and the pair carries only the
    recorded steps;
  - approving a subset returns exactly that subset.
* The helper never raises: every failure path returns
  (None, <outcome>) and the caller runs the goal exactly
  as today (picker alone, per-call asks).
"""

import asyncio
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import plan_approval as _pa  # noqa: E402
from core.voice import direct_plan as _dp  # noqa: E402

VALID = {"write_file", "mouse_control", "send_email",
         "get_current_time", "wallpaper_revert"}


class ParseTests(unittest.TestCase):
    def test_object_with_steps_parses(self):
        steps = _dp.parse_plan_steps(
            json.dumps({"steps": [
                {"tool": "write_file",
                 "args": {"filepath": "/tmp/a.txt",
                          "content": "hi"}},
                {"tool": "mouse_control",
                 "args": {"x": 1, "y": 2}}]}),
            VALID)
        self.assertEqual([s["tool"] for s in steps],
                         ["write_file", "mouse_control"])
        self.assertEqual(steps[0]["args"]["content"], "hi")

    def test_bare_list_parses(self):
        steps = _dp.parse_plan_steps(
            json.dumps([{"tool": "mouse_control",
                         "args": {"x": 1, "y": 2}}]),
            VALID)
        self.assertEqual(len(steps), 1)

    def test_prose_bad_json_and_single_action_are_no_plan(self):
        for reply in ("", "do the thing", "{not json",
                      json.dumps({"tool": "mouse_control",
                                  "args": {}}),
                      json.dumps({"steps": "not a list"})):
            self.assertEqual(_dp.parse_plan_steps(reply, VALID),
                             [], reply)

    def test_unknown_tools_and_bad_args_are_dropped(self):
        steps = _dp.parse_plan_steps(
            json.dumps({"steps": [
                {"tool": "invented_tool", "args": {}},
                {"tool": "write_file", "args": "not a dict"},
                {"tool": "mouse_control", "args": {"x": 1}},
                {"args": {"x": 1}},
                "not a step"]}),
            VALID)
        self.assertEqual([s["tool"] for s in steps],
                         ["mouse_control"])

    def test_plan_prompt_names_the_goal_and_the_candidates(self):
        prompt = _dp.plan_prompt(
            "set up the morning", ["mouse_control", "write_file"])
        self.assertIn("set up the morning", prompt)
        self.assertIn("mouse_control", prompt)
        self.assertIn('"steps"', prompt)
        self.assertIn("ONLY", prompt)


class DirectGoalTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._dest = pathlib.Path(self._tmp.name) / "new_note.txt"
        _pa.clear_all()
        _pa.register_plan_handler(None)
        self._asked = False
        self._handler_calls = []

    def tearDown(self):
        _pa.clear_all()
        _pa.register_plan_handler(None)
        self._tmp.cleanup()
        super().tearDown()

    def _steps(self):
        return [
            {"tool": "write_file",
             "args": {"filepath": str(self._dest),
                      "content": "hi"}},
            {"tool": "wallpaper_revert", "args": {}},
        ]

    def _ask(self, steps):
        async def ask(goal):
            self._asked = True
            return [dict(s, args=dict(s["args"])) for s in steps]
        return ask

    def _run(self, coro):
        return asyncio.run(coro)

    def _handler(self, approved, outcome="approved"):
        def handle(task, preview):
            self._handler_calls.append(
                (task.task_id, [e["index"] for e in
                                preview["approvable"]]))
            return {"approved": list(approved), "outcome": outcome}
        return handle

    def test_no_handler_means_no_model_call(self):
        pair, outcome = self._run(_dp.plan_direct_goal(
            "set up the morning",
            ask_plan=self._ask(self._steps())))
        self.assertIsNone(pair)
        self.assertEqual(outcome, "no_handler")
        self.assertFalse(self._asked)
        self.assertEqual(self._handler_calls, [])

    def test_two_steps_approved_returns_the_pair(self):
        _pa.register_plan_handler(self._handler([0, 1]))
        pair, outcome = self._run(_dp.plan_direct_goal(
            "set up the morning",
            ask_plan=self._ask(self._steps())))
        self.assertEqual(outcome, "approved")
        self.assertIsNotNone(pair)
        task_id, defs = pair
        self.assertEqual([d.tool for d in defs],
                         ["write_file", "wallpaper_revert"])
        # approve_plan recorded both, under this task id.
        self.assertIn(0, _pa._APPROVALS.get(task_id, {}))
        self.assertIn(1, _pa._APPROVALS.get(task_id, {}))
        self.assertEqual(self._handler_calls[0][1], [0, 1])

    def test_one_step_means_no_dialog(self):
        _pa.register_plan_handler(self._handler([0]))
        pair, outcome = self._run(_dp.plan_direct_goal(
            "write a note",
            ask_plan=self._ask(self._steps()[:1])))
        self.assertIsNone(pair)
        self.assertEqual(outcome, "too_few_steps")
        self.assertEqual(self._handler_calls, [])

    def test_no_plan_means_no_dialog(self):
        _pa.register_plan_handler(self._handler([0, 1]))
        pair, outcome = self._run(_dp.plan_direct_goal(
            "set up the morning",
            ask_plan=self._ask([])))
        self.assertIsNone(pair)
        self.assertEqual(outcome, "no_plan")
        self.assertEqual(self._handler_calls, [])

    def test_model_failure_means_no_dialog(self):
        _pa.register_plan_handler(self._handler([0, 1]))

        async def failing_ask(goal):
            raise RuntimeError("model down")

        pair, outcome = self._run(_dp.plan_direct_goal(
            "set up the morning", ask_plan=failing_ask))
        self.assertIsNone(pair)
        self.assertEqual(outcome, "no_plan")
        self.assertEqual(self._handler_calls, [])

    def test_decline_records_nothing(self):
        _pa.register_plan_handler(
            self._handler([], outcome="ask_each_time"))
        pair, outcome = self._run(_dp.plan_direct_goal(
            "set up the morning",
            ask_plan=self._ask(self._steps())))
        self.assertIsNone(pair)
        self.assertEqual(outcome, "ask_each_time")
        self.assertEqual(_pa._APPROVALS, {})

    def test_timeout_records_nothing(self):
        _pa.register_plan_handler(
            self._handler([], outcome="timeout"))
        pair, outcome = self._run(_dp.plan_direct_goal(
            "set up the morning",
            ask_plan=self._ask(self._steps())))
        self.assertIsNone(pair)
        self.assertEqual(outcome, "timeout")
        self.assertEqual(_pa._APPROVALS, {})

    def test_never_list_step_is_refused_even_when_approved(self):
        # Two approvable steps + one never-list step: the
        # dialog claims all three were approved; approve_plan
        # refuses the never-list index and the pair carries
        # only the recorded steps.
        steps = [
            {"tool": "write_file",
             "args": {"filepath": str(self._dest),
                      "content": "hi"}},
            {"tool": "wallpaper_revert", "args": {}},
            {"tool": "send_email",
             "args": {"to": "a@b.c", "subject": "s", "body": "b"}},
        ]
        _pa.register_plan_handler(self._handler([0, 1, 2]))
        pair, outcome = self._run(_dp.plan_direct_goal(
            "write, revert, then email",
            ask_plan=self._ask(steps)))
        self.assertEqual(outcome, "approved")
        self.assertIsNotNone(pair)
        task_id, defs = pair
        self.assertEqual([d.tool for d in defs],
                         ["write_file", "wallpaper_revert"])
        self.assertIn(0, _pa._APPROVALS.get(task_id, {}))
        self.assertIn(1, _pa._APPROVALS.get(task_id, {}))
        self.assertNotIn(2, _pa._APPROVALS.get(task_id, {}))

    def test_approving_a_subset_returns_that_subset(self):
        _pa.register_plan_handler(self._handler([1]))
        pair, outcome = self._run(_dp.plan_direct_goal(
            "set up the morning",
            ask_plan=self._ask(self._steps())))
        self.assertEqual(outcome, "approved")
        self.assertEqual([d.tool for d in pair[1]],
                         ["wallpaper_revert"])


if __name__ == "__main__":
    unittest.main()
