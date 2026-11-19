"""Round-3 fix Q1 (section-B FAIL B9): bad-args plan/routine steps
recorded as done.

Before the fix, a plan step `quick_math "2+"` was ticked done and
the plan reported "3/3 step(s) done" although it computed nothing:
quick_math returned its refusal as a plain string, the funnel
wrapped it as success, and `_execution_succeeded` only recognised
the five REFUSAL_MARKERS — none of which matches quick_math's
sentence. The routine loop shared the flaw.

The fix has two layers, and this suite pins both:
  1. quick_math + unit_convert return funnel-shaped failure dicts
     on every non-computation path (the exact refusal sentence
     survives as the message); the funnel's is_refusal_text also
     reads refusal OPENERS ("I can't", "Can't", "Could not", ...)
     as a prefix match, so other skills-pack refusals (roll_dice's
     "I can't read ... as dice.") stop masquerading as success.
  2. the plan walk and the routine loop use the same shared
     is_refusal_text, so a refusal sentence reaching them through
     any path halts the walk instead of completing the step.
"""
import json
import tempfile
import unittest
from pathlib import Path

from core.brain import planner, planner_checkpoint, planner_plans, planner_routines
from core.funnel.tool_result import is_refusal_text
from core.funnel.tools_registry import tools as TOOLS


def _body(out):
    if isinstance(out, dict):
        return str(out.get("result") or out.get("message") or "")
    return str(out or "")


class RefusalDetectorTests(unittest.TestCase):
    """is_refusal_text: markers (old behaviour) + openers (Q1)."""

    def test_markers_still_detected(self):
        for text in ("Refused: no", "Blocked by VYUK's trust layer",
                     "needs confirm=true",
                     "Ask again with confirmation",
                     "needs the user's OK in VYUK's window"):
            self.assertTrue(is_refusal_text(text), text)

    def test_openers_detected(self):
        for text in (
            "I can't evaluate that — quick_math only allows numbers",
            "I cannot do that",
            "Can't convert miles to kg",
            "Could not find the file",
            "Couldn't open the page",
        ):
            self.assertTrue(is_refusal_text(text), text)

    def test_opener_mid_string_is_not_a_refusal(self):
        # A success sentence that merely CONTAINS an opener is data,
        # not a refusal — the opener rule is a prefix match only.
        self.assertFalse(
            is_refusal_text("The report says I can't come today"))
        self.assertFalse(
            is_refusal_text("She said she could not attend"))
        self.assertFalse(is_refusal_text("Done: deleted the file"))

    def test_i_dont_know_is_not_a_refusal(self):
        # Ambiguous by design: an admission of ignorance, not
        # necessarily a refusal to act.
        self.assertFalse(
            is_refusal_text("I don't know a dice mode called 'weird'"))

    def test_success_and_non_string_are_not_refusals(self):
        self.assertFalse(is_refusal_text("17*3 = 51"))
        self.assertFalse(is_refusal_text("5 mi = 8.04672 km"))
        self.assertFalse(is_refusal_text(""))
        self.assertFalse(is_refusal_text(42))
        self.assertFalse(is_refusal_text(None))


class FunnelFailureTests(unittest.TestCase):
    """The funnel surfaces non-computations as failure/refused."""

    def test_quick_math_bad_expression_is_failure(self):
        out = TOOLS.execute("quick_math", {"expression": "2+"})
        self.assertEqual(out.get("status"), "failure")
        self.assertEqual(out.get("kind"), "invalid_expression")
        self.assertIn("can't evaluate", _body(out))

    def test_quick_math_empty_is_failure(self):
        out = TOOLS.execute("quick_math", {"expression": ""})
        self.assertEqual(out.get("status"), "failure")
        self.assertIn("Give me an expression", _body(out))

    def test_quick_math_valid_still_succeeds(self):
        out = TOOLS.execute("quick_math", {"expression": "2+3*4"})
        self.assertEqual(out.get("status"), "success")
        self.assertIn("2+3*4 = 14", _body(out))

    def test_unit_convert_cross_category_is_failure(self):
        out = TOOLS.execute("unit_convert", {"value": 5,
                                             "from_unit": "miles",
                                             "to_unit": "kg"})
        self.assertEqual(out.get("status"), "failure")
        self.assertEqual(out.get("kind"), "incompatible_units")
        self.assertIn("Can't convert", _body(out))

    def test_unit_convert_bad_number_is_refused_at_validation(self):
        # The schema declares value a number, so the funnel refuses
        # before the handler runs — still a non-success, so a plan
        # step with a non-numeric value halts honestly.
        out = TOOLS.execute("unit_convert", {"value": "five",
                                             "from_unit": "miles",
                                             "to_unit": "km"})
        self.assertEqual(out.get("status"), "refused")
        self.assertEqual(out.get("kind"), "validation")

    def test_unit_convert_bad_number_handler_returns_failure(self):
        # Direct call (bypassing schema validation): the handler
        # itself returns a funnel-shaped failure dict.
        from core.skills_smart import unit_convert
        out = unit_convert("five", "miles", "km")
        self.assertEqual(out.get("status"), "failure")
        self.assertEqual(out.get("kind"), "invalid_number")
        self.assertIn("isn't a number", out.get("message", ""))

    def test_unit_convert_unknown_unit_is_failure(self):
        out = TOOLS.execute("unit_convert", {"value": 5,
                                             "from_unit": "furlongs",
                                             "to_unit": "km"})
        self.assertEqual(out.get("status"), "failure")
        self.assertEqual(out.get("kind"), "invalid_unit")
        self.assertIn("I don't know the unit", _body(out))

    def test_unit_convert_valid_still_succeeds(self):
        out = TOOLS.execute("unit_convert", {"value": 5,
                                             "from_unit": "miles",
                                             "to_unit": "km"})
        self.assertEqual(out.get("status"), "success")
        self.assertIn("5 mi = 8.04672 km", _body(out))

    def test_opener_class_refusal_is_refused(self):
        # roll_dice was NOT changed: its "I can't read ..." sentence
        # is caught by the funnel's opener rule alone.
        out = TOOLS.execute("roll_dice", {"notation": "banana"})
        self.assertEqual(out.get("status"), "refused")
        self.assertIn("I can't read", _body(out))

    def test_roll_dice_valid_still_succeeds(self):
        out = TOOLS.execute("roll_dice", {"notation": "1d6"})
        self.assertEqual(out.get("status"), "success")

    def test_outcome_failed_keeps_its_verdict(self):
        # The opener rule must NOT override an explicit Outcome
        # verdict: a handler that returned outcome.failed already
        # said exactly what happened (verdict + evidence + kind),
        # so the funnel surfaces success + outcome_verified=False
        # + kind "failed" even though the message opens with
        # "Could not". The verdict rides in the keys; the plan walk
        # and the flow classifier read it from there.
        from core import tool_outcome as outcome_mod
        from core.funnel import tools_registry as registry_mod
        reg = registry_mod.ToolRegistry()
        reg.register("q1_probe_failed", "probe",
                     {"type": "object", "properties": {}},
                     lambda **kw: outcome_mod.failed(
                         "Could not confirm the write: no file",
                         "no file"))
        out = reg.execute("q1_probe_failed", {})
        self.assertEqual(out.get("status"), "success")
        self.assertFalse(out.get("outcome_verified"))
        self.assertEqual(out.get("outcome_kind"), "failed")
        self.assertIn("Could not confirm", _body(out))

    def test_outcome_unverified_keeps_its_verdict(self):
        from core import tool_outcome as outcome_mod
        from core.funnel import tools_registry as registry_mod
        reg = registry_mod.ToolRegistry()
        reg.register("q1_probe_unverified", "probe",
                     {"type": "object", "properties": {}},
                     lambda **kw: outcome_mod.unverified(
                         "Could not re-read the folder", "no read-back"))
        out = reg.execute("q1_probe_unverified", {})
        self.assertEqual(out.get("status"), "success")
        self.assertFalse(out.get("outcome_verified"))
        self.assertEqual(out.get("outcome_kind"), "unverified")


class DagflowClassifierTests(unittest.TestCase):
    """_classify_result reads the Outcome verdict before the text."""

    def test_bare_opener_string_is_refused(self):
        from core.dagflow import _classify_result
        outcome, retryable, _ = _classify_result(
            {"status": "success", "result": "Could not find the file"})
        self.assertEqual(outcome, "refused")
        self.assertFalse(retryable)

    def test_failed_outcome_is_failed_not_refused(self):
        from core.dagflow import _classify_result
        outcome, retryable, _ = _classify_result({
            "status": "success",
            "result": "Could not create a folder at /x: it is not a "
                      "folder afterwards.",
            "outcome_verified": False,
            "outcome_evidence": "folder missing after mkdir",
            "outcome_kind": "failed"})
        self.assertEqual(outcome, "failed")
        self.assertFalse(retryable)

    def test_unverified_outcome_stays_success(self):
        from core.dagflow import _classify_result
        outcome, retryable, _ = _classify_result({
            "status": "success",
            "result": "Could not re-read the folder",
            "outcome_verified": False,
            "outcome_evidence": "no read-back",
            "outcome_kind": "unverified"})
        self.assertEqual(outcome, "success")
        self.assertFalse(retryable)

    def test_verified_outcome_is_success(self):
        from core.dagflow import _classify_result
        outcome, retryable, _ = _classify_result({
            "status": "success", "result": "done",
            "outcome_verified": True,
            "outcome_evidence": "3 bytes on disk",
            "outcome_kind": "verified"})
        self.assertEqual(outcome, "success")
        self.assertFalse(retryable)

    def test_plain_success_stays_success(self):
        from core.dagflow import _classify_result
        outcome, _, _ = _classify_result(
            {"status": "success", "result": "17*3 = 51"})
        self.assertEqual(outcome, "success")


class _TempStoreMixin(unittest.TestCase):
    """Redirect plans.json / routines.json into a temp dir."""

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="r3q1_badargs_"))
        self._orig_plans = planner._PLANS_PATH
        self._orig_routines = planner._ROUTINES_PATH
        planner._PLANS_PATH = self._tmp / "plans.json"
        planner._ROUTINES_PATH = self._tmp / "routines.json"
        planner_plans._PLANS_PATH = planner._PLANS_PATH
        planner_checkpoint._PLANS_PATH = planner._PLANS_PATH
        planner_routines._ROUTINES_PATH = planner._ROUTINES_PATH

    def tearDown(self):
        planner._PLANS_PATH = self._orig_plans
        planner._ROUTINES_PATH = self._orig_routines
        planner_plans._PLANS_PATH = self._orig_plans
        planner_checkpoint._PLANS_PATH = self._orig_plans
        planner_routines._ROUTINES_PATH = self._orig_routines
        super().tearDown()


class PlanWalkTests(_TempStoreMixin):
    """A bad-args plan step halts the walk; valid plans complete."""

    def _write_plan(self, steps):
        (self._tmp / "plans.json").write_text(json.dumps(
            [{"id": "p1", "goal": "badargs", "steps": steps}]),
            encoding="utf-8")

    def _badargs_steps(self):
        return [
            {"text": "Calculate 17 times 3", "tool": "quick_math",
             "args": {"expression": "17*3"}, "status": "pending"},
            {"text": "Calculate a broken expression", "tool": "quick_math",
             "args": {"expression": "2+"}, "status": "pending"},
            {"text": "Check the time", "tool": "get_current_time_date",
             "args": {}, "status": "pending"},
        ]

    def test_bad_args_step_halts_plan(self):
        self._write_plan(self._badargs_steps())
        report = planner.run_plan_tool("p1")
        self.assertNotIn("3/3 step(s) done", report)
        self.assertIn("Plan stopped at step 2 of 3", report)
        plans, _ = planner._load_plans()
        steps = plans[0]["steps"]
        self.assertEqual(steps[0]["status"], "done")
        # The failed step stays pending (never recorded done) and
        # the step after it never ran.
        self.assertEqual(steps[1]["status"], "pending")
        self.assertEqual(steps[2]["status"], "pending")

    def test_valid_plan_still_completes(self):
        self._write_plan([
            {"text": "Calculate 17 times 3", "tool": "quick_math",
             "args": {"expression": "17*3"}, "status": "pending"},
            {"text": "Check the time", "tool": "get_current_time_date",
             "args": {}, "status": "pending"},
        ])
        report = planner.run_plan_tool("p1")
        self.assertIn("2/2 step(s) done", report)
        plans, _ = planner._load_plans()
        self.assertTrue(all(s["status"] == "done"
                            for s in plans[0]["steps"]))


class RoutineLoopTests(_TempStoreMixin):
    """A bad-args routine step halts the run; valid routines finish."""

    def _badargs_actions(self):
        return [
            {"tool": "quick_math", "args": {"expression": "17*3"}},
            {"tool": "quick_math", "args": {"expression": "2+"}},
            {"tool": "get_current_time_date", "args": {}},
        ]

    def test_bad_args_step_halts_routine(self):
        planner_routines.save_routine("r3q1", self._badargs_actions())
        report = planner_routines.run_routine("r3q1")
        self.assertNotIn("all 3 action(s) completed", report)
        self.assertIn("Routine stopped at step 2 of 3", report)

    def test_valid_routine_still_completes(self):
        planner_routines.save_routine("r3q1-good", [
            {"tool": "quick_math", "args": {"expression": "17*3"}},
            {"tool": "get_current_time_date", "args": {}},
        ])
        report = planner_routines.run_routine("r3q1-good")
        self.assertIn("all 2 action(s) completed", report)


if __name__ == "__main__":
    unittest.main()
