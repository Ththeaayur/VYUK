"""Wave 1 rig tests (stdlib unittest, no GUI, no network, no model).

Covers core/k1_measurement_rig.py (data only, no verdict)
(first slices, reproducibility rules, no pass/fail on an unset margin),
and core/headline_acceptance.py (fail-closed pass rule, false-done guard).
"""

import asyncio
import unittest

# Phase 4 D1 INTENTIONAL: staged eval-rig import removed with its deleted module (plan §4.1 D-2).
from core import k1_measurement_rig as k1
from core import headline_acceptance as r4h


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class _StubBrain:
    def __init__(self, report):
        self.report = report

    async def run_goal(self, goal_text):
        return self.report


class K1RigTests(unittest.TestCase):
    def test_no_verdict_no_winner(self):
        data = _run(k1.run_comparison(
            ["What time is it?"],
            current_brain=_StubBrain("Goal complete: 10:30"),
            trial_brain=_StubBrain("Goal stopped: budget exhausted")))
        self.assertIsNone(data["verdict"])
        self.assertIsNone(data["winner"])
        self.assertEqual(data["goals"][0]["runs"]["current"][0]["outcome"],
                         "complete")
        self.assertEqual(data["goals"][0]["runs"]["trial"][0]["outcome"],
                         "stopped")

    def test_consequential_goals_refused_not_run(self):
        data = _run(k1.run_comparison(
            ["Please send the report to Sam"],
            current_brain=_StubBrain("Goal complete: sent"),
            trial_brain=_StubBrain("Goal complete: sent")))
        self.assertEqual(data["goals"][0]["runs"]["current"]["outcome"],
                         "refused")
        self.assertEqual(data["goals"][0]["runs"]["current"]["refused_verb"],
                         "send")

    def test_classify_unknown(self):
        self.assertEqual(k1.classify_outcome("something vague"), "unknown")


# Phase 4 D1 INTENTIONAL: staged eval-rig test class removed with its deleted module (plan §4.1 D-2); no other test touched.
class R4HScriptTests(unittest.TestCase):
    def test_pass_requires_complete_and_check(self):
        # Report claims complete but check fails -> false-done, fail.
        task = r4h.TASKS[1]  # T2 expects "42"
        scored = r4h.score_task(task, "Goal complete: the answer is 43")
        self.assertEqual(scored["result"], "fail")
        self.assertTrue(scored["false_done"])
        # Complete + check -> pass.
        scored = r4h.score_task(task, "Goal complete: the answer is 42")
        self.assertEqual(scored["result"], "pass")

    def test_halted_never_passes(self):
        task = r4h.TASKS[1]
        scored = r4h.score_task(
            task, "Goal halted: failsafe\nGoal complete: 42")
        self.assertEqual(scored["result"], "fail")

    def test_five_tasks_and_no_brain_not_run(self):
        self.assertEqual(len(r4h.TASKS), 5)
        summary = _run(r4h.run_acceptance(None))
        self.assertEqual(summary["result"], "not run")

    def test_acceptance_with_stub_brain(self):
        class _Brain:
            async def run_goal(self, goal):
                if "17 plus 25" in goal:
                    return "Goal complete: 42"
                if "days" in goal:
                    return "Goal complete: 298 days"
                if "kilometres" in goal:
                    return "Goal complete: 3.11 miles"
                if "time" in goal:
                    return "Goal complete: it is 10:30"
                return "Goal complete: plan list: plan-1, plan-2"
        summary = _run(r4h.run_acceptance(_Brain()))
        self.assertEqual(summary["passes"], 5)
        self.assertEqual(summary["false_done_count"], 0)


if __name__ == "__main__":
    unittest.main()
