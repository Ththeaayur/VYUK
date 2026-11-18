"""U10 — check-my-working + solve-then-speak (SymPy).

Lab leg only. The real-Windows field leg — a real MA 105 working
check on his machine, a dropped factor in his own working named at
the right line, a solver walk-through spoken step by step — is OWED,
not this batch.

Gap proved first (audit T10): ``grade_answer`` grades ONE answer
against a gold, with no notion of intermediate lines; no ``sympy``
import anywhere in ``core/``; no working-check or solver module
anywhere in ``core/`` or ``gui/``.

What this suite proves: a correct working checks out line by line
(integral, derivative, limit); a dropped factor names the FIRST
wrong line (later lines are never blamed); a wrong final answer
names only the final line; an unparseable line is "not readable",
never "wrong"; a float-close-but-symbolically-wrong line is wrong
(exact, never a tolerance); setup/prose lines are skipped, never
graded; the integration constant ``+ C`` is stripped but ``+ 5``
is not; the solver produces steps for an integral, a derivative,
a limit, an equation and a series, and every step is proven by
the engine (``verify_all``); the solver never dumps the whole
solution (one step at a time, gated on a graded check); spoken
steps go through U7's canonical form and never reveal the gold;
the working check never grades and never writes mastery (AST);
a wrong line hands over to U4 in the ``handed_to_diagnosis``
shape; unknown and too-complex problems fail honestly.
"""

import ast
import sys
import unittest
from pathlib import Path

from core.brain import check_working as cw
from core.brain.check_loop import _contains_gold
from core.brain.error_diagnosis import Diagnoser
from core.brain.lesson_planner import Bite
from core.brain.spoken_maths import spoken_reveals_gold


class EngineTests(unittest.TestCase):
    def test_engine_available(self):
        self.assertTrue(cw.engine_available())

    def test_engine_is_sympy_1x(self):
        eng = cw._get_engine()
        self.assertIsNotNone(eng)
        version = eng.sp.__version__
        self.assertTrue(version.startswith("1."),
                        f"unexpected sympy version: {version}")

    def test_engine_unavailable_when_sympy_missing(self):
        saved = sys.modules.get("sympy")
        sys.modules["sympy"] = None  # `import sympy` raises now.
        old_engine, old_tried = cw._ENGINE, cw._ENGINE_TRIED
        cw._ENGINE, cw._ENGINE_TRIED = None, False
        try:
            self.assertFalse(cw.engine_available())
            result = cw.check_working("x**2", ["x**2"])
            self.assertEqual(result["verdict"], "engine_unavailable")
            self.assertFalse(result["ok"])
            walk = cw.solve("Integral(2*x, x)", skill="ma105-test")
            self.assertFalse(walk.ok)
            self.assertEqual(walk.reason, "engine_unavailable")
        finally:
            if saved is None:
                sys.modules.pop("sympy", None)
            else:
                sys.modules["sympy"] = saved
            cw._ENGINE, cw._ENGINE_TRIED = old_engine, old_tried


class WorkingCheckTests(unittest.TestCase):
    def test_correct_integral_working(self):
        result = cw.check_working(
            "Integral(2*x, x)", ["2*x**2/2", "x**2 + C"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "all_ok")
        self.assertEqual(result["checked"], 2)

    def test_correct_derivative_working(self):
        result = cw.check_working("Derivative(x**2, x)", ["2*x"])
        self.assertTrue(result["ok"])

    def test_correct_limit_working(self):
        result = cw.check_working("Limit(sin(x)/x, x, 0)", ["1"])
        self.assertTrue(result["ok"])

    def test_correct_working_with_equation_lines(self):
        result = cw.check_working(
            "x**2", ["x**2 = x*x", "= x*x"])
        self.assertTrue(result["ok"], result)

    def test_dropped_factor_names_first_wrong_line(self):
        result = cw.check_working("Integral(2*x, x)", ["x**2/2"])
        self.assertEqual(result["verdict"], "wrong_line")
        self.assertEqual(result["first_wrong"]["index"], 0)

    def test_dropped_factor_in_the_middle(self):
        result = cw.check_working(
            "Integral(2*x, x)",
            ["2*x**2/2", "x**2/2 + C", "x**2/2"])
        self.assertEqual(result["verdict"], "wrong_line")
        # The middle line is blamed; the line after it is never
        # even checked (the check stops at the first wrong line).
        self.assertEqual(result["first_wrong"]["index"], 1)
        self.assertEqual(len(result["lines"]), 2)

    def test_wrong_final_answer_only(self):
        result = cw.check_working(
            "Integral(2*x, x)", ["2*x**2/2", "3*x**2"])
        self.assertEqual(result["verdict"], "wrong_line")
        self.assertEqual(result["first_wrong"]["index"], 1)

    def test_false_equation_line_is_wrong(self):
        result = cw.check_working("x**2", ["2*x**2 = x**2"])
        self.assertEqual(result["verdict"], "wrong_line")
        self.assertIn("does not hold",
                      result["first_wrong"]["detail"])

    def test_unparseable_line_is_not_readable_never_wrong(self):
        result = cw.check_working("x**2", ["2*x **"])
        self.assertEqual(result["verdict"], "not_readable")
        self.assertIsNone(result["first_wrong"])
        self.assertEqual(result["not_readable"]["index"], 0)

    def test_float_close_but_symbolically_wrong(self):
        # The Taylor approximation of sin(x) is numerically close
        # near zero and symbolically wrong — exact, never tolerance.
        result = cw.check_working("sin(x)", ["x - x**3/6"])
        self.assertEqual(result["verdict"], "wrong_line")
        result = cw.check_working("x**2", ["x**2 + 0.1"])
        self.assertEqual(result["verdict"], "wrong_line")

    def test_integration_constant_stripped_but_not_five(self):
        self.assertTrue(
            cw.check_working("x**2", ["x**2 + C"])["ok"])
        result = cw.check_working("x**2", ["x**2 + 5"])
        self.assertEqual(result["verdict"], "wrong_line")

    def test_setup_and_prose_lines_skipped_never_graded(self):
        result = cw.check_working(
            "Integral(2*x, x)",
            ["Let u = x**2", "integrate term by term",
             "2*x**2/2", "x**2 + C"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["skipped"], [0, 1])
        self.assertEqual(result["checked"], 2)

    def test_spoken_line_via_u7_normaliser(self):
        typed = cw.spoken_to_typed("x squared")
        self.assertEqual(typed, "x^2")
        result = cw.check_working("x**2", [typed])
        self.assertTrue(result["ok"], result)

    def test_bad_input_never_raises(self):
        self.assertEqual(
            cw.check_working("", ["x**2"])["verdict"], "bad_input")
        self.assertEqual(
            cw.check_working("x**2", "not a list")["verdict"],
            "bad_input")
        self.assertEqual(
            cw.check_working("x**2 = 3*x**2", [])["verdict"],
            "bad_input")

    def test_never_grades_never_writes_mastery(self):
        # By construction: this module holds no model and no store.
        # Asserted by AST — no record_graded call anywhere, not
        # even in a string the code could eval (the docstring only
        # NAMES it; a Call node is what would write mastery).
        source = Path(cw.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                self.assertNotEqual(
                    name, "record_graded",
                    "check_working must never call record_graded")


class HandoverTests(unittest.TestCase):
    def test_wrong_line_hands_over_in_u3_shape(self):
        result = cw.check_working(
            "Integral(2*x, x)", ["2*x**2/2", "x**2/2 + C"])
        handover = cw.handover_for(result, skill="ma105-integrals")
        self.assertEqual(handover["outcome"], "handed_to_diagnosis")
        self.assertEqual(handover["skill"], "ma105-integrals")
        self.assertEqual(handover["reason"], "wrong_working_line")
        self.assertEqual(handover["line_index"], 1)
        self.assertEqual(handover["line"], "x**2/2 + C")
        self.assertEqual(handover["grade"]["method"], "symbolic")
        self.assertFalse(handover["grade"]["correct"])
        # The exact U3 keys, nothing missing.
        for key in ("outcome", "skill", "attempts", "reinforce",
                    "grade", "mastery", "reason"):
            self.assertIn(key, handover)

    def test_handover_accepted_by_u4_diagnosis(self):
        result = cw.check_working(
            "Integral(2*x, x)", ["2*x**2/2", "x**2/2 + C"])
        handover = cw.handover_for(result, skill="ma105-integrals")
        diagnosis = Diagnoser().diagnose(handover)
        self.assertIn(diagnosis.get("status"),
                      ("classified", "ambiguous", "unclassified"))
        self.assertIn("prerequisite", diagnosis)

    def test_no_handover_without_a_wrong_line(self):
        result = cw.check_working(
            "Integral(2*x, x)", ["2*x**2/2", "x**2 + C"])
        handover = cw.handover_for(result, skill="ma105-integrals")
        self.assertFalse(handover["ok"])


class SolverTests(unittest.TestCase):
    def _walk_all(self, walk):
        """Collect every bite by advancing on graded-correct only."""
        bites = []
        while walk.current() is not None:
            bites.append(walk.current())
            walk.advance(correct=True)
        return bites

    def test_solver_integral_steps_proven(self):
        walk = cw.solve("Integral(2*x, x)", skill="ma105-integrals")
        self.assertTrue(walk.ok, walk.reason)
        self.assertGreaterEqual(walk.total_steps, 2)
        self.assertTrue(walk.verify_all()["ok"], walk.verify_all())

    def test_solver_derivative_steps_proven(self):
        walk = cw.solve("Derivative(x**2 + sin(x), x)",
                        skill="ma105-derivatives")
        self.assertTrue(walk.ok, walk.reason)
        self.assertEqual(walk.total_steps, 3)
        self.assertTrue(walk.verify_all()["ok"])

    def test_solver_limit_steps_proven(self):
        walk = cw.solve("Limit((x**2 - 1)/(x - 1), x, 1)",
                        skill="ma105-limits")
        self.assertTrue(walk.ok, walk.reason)
        self.assertTrue(walk.verify_all()["ok"])

    def test_solver_equation_steps_proven(self):
        walk = cw.solve("x**2 - 4 = 0", skill="ma105-equations")
        self.assertTrue(walk.ok, walk.reason)
        self.assertEqual(walk.total_steps, 2)
        self.assertTrue(walk.verify_all()["ok"])
        self.assertIn("x = -2", walk.current().gold)
        self.assertIn("x = 2", walk.current().gold)

    def test_solver_series_steps_proven(self):
        walk = cw.solve("exp(x)", skill="ma105-series", kind="series")
        self.assertTrue(walk.ok, walk.reason)
        self.assertEqual(walk.total_steps, 4)
        self.assertTrue(walk.verify_all()["ok"])

    def test_solver_substitution_steps_proven(self):
        walk = cw.solve("Integral(2*x*exp(x**2), x)",
                        skill="ma105-integrals")
        self.assertTrue(walk.ok, walk.reason)
        self.assertTrue(walk.verify_all()["ok"], walk.verify_all())

    def test_solver_parts_steps_proven(self):
        walk = cw.solve("Integral(x*exp(x), x)",
                        skill="ma105-integrals")
        self.assertTrue(walk.ok, walk.reason)
        self.assertTrue(walk.verify_all()["ok"])

    def test_solver_definite_steps_proven(self):
        walk = cw.solve("Integral(2*x, (x, 0, 1))",
                        skill="ma105-integrals")
        self.assertTrue(walk.ok, walk.reason)
        self.assertTrue(walk.verify_all()["ok"])
        golds = [b.gold for b in self._walk_all(walk)]
        # The substitution step and the value step differ.
        self.assertEqual(golds, ["1 - 0", "1"])

    def test_solver_never_dumps_the_solution(self):
        walk = cw.solve("Integral(2*x, x)", skill="ma105-integrals")
        # No public attribute holds the step list.
        self.assertFalse(hasattr(walk, "steps"))
        self.assertFalse(hasattr(walk, "all_steps"))
        self.assertFalse(hasattr(walk, "bites"))
        # One step at a time, gated on a graded check.
        first = walk.current()
        self.assertIsInstance(first, Bite)
        self.assertIs(walk.advance(correct=False), first)
        self.assertIs(walk.current(), first)
        second = walk.advance(correct=True)
        self.assertIsNot(second, first)
        self.assertIsNotNone(second)

    def test_solver_steps_are_u2_bites_without_gold_in_text(self):
        walk = cw.solve("Integral(2*x, x)", skill="ma105-integrals")
        for bite in self._walk_all(walk):
            self.assertIsInstance(bite, Bite)
            self.assertEqual(bite.skill, "ma105-integrals")
            self.assertTrue(bite.check)
            self.assertTrue(bite.gold)
            self.assertTrue(bite.rubric)
            self.assertFalse(_contains_gold(bite.bite, bite.gold),
                             f"bite reveals its gold: {bite.bite!r}")
            self.assertFalse(_contains_gold(bite.check, bite.gold),
                             "check reveals its gold")

    def test_spoken_steps_never_reveal_the_gold(self):
        walk = cw.solve("Integral(2*x, x)", skill="ma105-integrals")
        while walk.current() is not None:
            spoken = walk.spoken_current()
            gold = walk.current().gold
            self.assertFalse(spoken_reveals_gold(spoken, gold),
                             f"spoken step reveals gold: {spoken!r}")
            self.assertNotIn("**", spoken)
            self.assertNotIn("^", spoken)
            walk.advance(correct=True)

    def test_solver_unknown_and_too_complex_fail_honestly(self):
        walk = cw.solve("hello world", skill="ma105-test")
        self.assertFalse(walk.ok)
        self.assertEqual(walk.reason, "unknown_kind")
        walk = cw.solve("Integral(x**x, x)", skill="ma105-test")
        self.assertFalse(walk.ok)
        walk = cw.solve("Integral(x **, x)", skill="ma105-test")
        self.assertFalse(walk.ok)
        self.assertEqual(walk.reason, "unparseable_problem")
        walk = cw.solve("", skill="ma105-test")
        self.assertFalse(walk.ok)
        self.assertEqual(walk.reason, "bad_input")


if __name__ == "__main__":
    unittest.main()
