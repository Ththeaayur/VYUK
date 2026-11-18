"""U7 — Spoken maths: the tutor speaks maths and grades spoken maths.

Lab leg only. The real-Windows field leg — a real MA 105 spoken
check on his machine, the tutor speaking "seven over three" and
grading his spoken answer, never marking a mishearing wrong — is
OWED, not this batch.

What this suite proves: canonical speech strings for a fraction, a
power, a product with pi, a derivative and an integral (never raw
notation); spoken-answer normalisation (three ways of saying the
same fraction grade identically; "thirty-two pi" grades as 32 pi; a
spoken power grades as the power); the normaliser feeds U3 (a
normalised correct answer passes U3's grading and records a
transfer through a real CheckSession; a normalised wrong answer
fails; an un-normalisable spoken answer is "not proven", never a
guessed pass); low-confidence re-asks and grades nothing (D-T3);
the table is per-language data (a second language's table loads
without touching the grader); no spoken string reveals the gold
(the written-form guard is blind to spoken gold — proved in the
module docstring — so spoken_reveals_gold is the guard); typed
answers grade exactly as before U7.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from core.brain import check_loop as cl
from core.brain import spoken_maths as sm
from core.brain import student_model as stm
from core.brain.lesson_planner import Bite
from core.memory import MemoryStore

SKILL = "ma105-double-integrals-limits-from-region"
REINFORCE = "ma105-regions-and-limits"
GOLD = "7/3"
RUBRIC = ("The answer must show the integrated value from "
          "integrating term by term. The limits must follow the "
          "region, not the usual range.")
BITE_TEXT = ("Integrate term by term: the integral of a sum is "
             "the sum of the integrals, each term on its own.")
CHECK = "What is the value of the integral?"


def _tmp_store(testcase):
    tmp = Path(tempfile.mkdtemp(prefix="spoken_maths_"))
    testcase.addCleanup(
        lambda: [os.unlink(p) for p in tmp.iterdir()] or tmp.rmdir())
    return MemoryStore(tmp / "assistant.db")


def _bite(**overrides):
    fields = {
        "skill": SKILL,
        "bite": BITE_TEXT,
        "check": CHECK,
        "gold": GOLD,
        "rubric": RUBRIC,
        "reinforce": REINFORCE,
        "requires": (REINFORCE,),
    }
    fields.update(overrides)
    return Bite(**fields)


class TestTable(unittest.TestCase):
    def test_english_table_loads_from_file(self):
        table, source = sm.load_table("en")
        self.assertEqual(source, "file")
        self.assertEqual(table["language"], "en")
        self.assertEqual(table["ones"]["seven"], 7)
        self.assertEqual(table["ordinal_denominators"]["thirds"], 3)

    def test_missing_language_falls_back_to_defaults(self):
        table, source = sm.load_table("zz")
        self.assertEqual(source, "default")
        self.assertEqual(table["ones"]["seven"], 7)

    def test_second_language_table_loads_without_touching_grader(self):
        # A tiny second table (fictional language "xx"): the
        # fraction word is "sur" and the numbers are different
        # words. The grader reads the table — no English is
        # hardcoded into it.
        block = {
            "language": "xx",
            "ones": {"shunya": 0, "ek": 1, "do": 2, "teen": 3,
                     "char": 4, "paanch": 5, "chhah": 6,
                     "saat": 7, "aath": 8, "nau": 9, "das": 10},
            "tens": {"bees": 20, "tees": 30},
            "scales": {"sau": 100, "hazaar": 1000},
            "point": "dashamlav",
            "negative": ["rina"],
            "fraction_words": ["sur"],
            "ordinal_denominators": {"tihai": 3},
            "power_words": {"varg": 2},
            "power_phrase": "ki ghaat",
            "constants": {"pi": "pi"},
            "operators": {},
            "variables": ["x", "y"],
            "units": ["metre"],
            "symbols": {},
            "derivative_words": [],
            "integral_words": [],
        }
        tmp = Path(tempfile.mkdtemp(prefix="spoken_maths_xx_"))
        self.addCleanup(lambda: [os.unlink(p) for p in tmp.iterdir()]
                        or tmp.rmdir())
        path = tmp / "SPOKEN_MATHS_XX.md"
        path.write_text(
            "# test table\n\n```spoken-maths\n"
            + json.dumps(block) + "\n```\n",
            encoding="utf-8")
        table, source = sm.load_table("xx", path=path)
        self.assertEqual(source, "file")
        self.assertEqual(sm.speak_expression("1/2", table),
                         "ek sur do")
        self.assertEqual(sm.normalise_spoken("ek sur do", table),
                         "1/2")
        grade = sm.grade_spoken("ek sur do", "1/2", "r",
                                table=table)
        self.assertTrue(grade["correct"])
        # And the English grader is untouched by the detour.
        self.assertEqual(sm.speak_expression("7/3"), "seven over three")


class TestSpeakExpression(unittest.TestCase):
    def test_fraction_speaks_never_raw_notation(self):
        spoken = sm.speak_expression("7/3")
        self.assertEqual(spoken, "seven over three")
        self.assertNotIn("/", spoken)
        self.assertNotIn("7", spoken)

    def test_negative_fraction_speaks(self):
        self.assertEqual(sm.speak_expression("-7/3"),
                         "negative seven over three")

    def test_power_speaks(self):
        self.assertEqual(sm.speak_expression("x^2"), "x squared")
        self.assertEqual(sm.speak_expression("x^3"), "x cubed")
        self.assertEqual(sm.speak_expression("x^4"),
                         "x to the power of four")
        self.assertEqual(sm.speak_expression("x²"), "x squared")

    def test_pi_product_speaks(self):
        self.assertEqual(sm.speak_expression("32π"), "thirty-two pi")
        self.assertEqual(sm.speak_expression("32 pi"), "thirty-two pi")
        self.assertEqual(sm.speak_expression("pi/2"), "pi over two")

    def test_derivative_speaks(self):
        self.assertEqual(sm.speak_expression("d/dx"), "d d x")
        self.assertEqual(
            sm.speak_expression("d/dx x^2"),
            "the derivative of x squared with respect to x")
        self.assertEqual(
            sm.speak_expression("dy/dx"),
            "the derivative of y with respect to x")

    def test_integral_speaks(self):
        self.assertEqual(
            sm.speak_expression("∫_0^1 x dx"),
            "the integral from zero to one of x d x")
        self.assertEqual(
            sm.speak_expression("∬_D f dA"),
            "the double integral over D of f d A")
        self.assertEqual(
            sm.speak_expression("∭ f dV"),
            "the triple integral of f d V")
        self.assertEqual(
            sm.speak_expression("∮_C F dr"),
            "the line integral along C of F d r")

    def test_divergence_and_root_speak(self):
        self.assertEqual(sm.speak_expression("∇·F"),
                         "the divergence of F")
        self.assertEqual(sm.speak_expression("√x"),
                         "the square root of x")

    def test_decimal_speaks(self):
        self.assertEqual(sm.speak_expression("2.333"),
                         "two point three three three")

    def test_unparseable_passes_through_unchanged(self):
        # Named limitation: no pilot form, no canonical string.
        self.assertEqual(sm.speak_expression("sin(x) + cos(y)"),
                         "sin(x) + cos(y)")

    def test_speak_text_replaces_spans_leaves_prose(self):
        spoken = sm.speak_text(
            "What is 7/3? The integral from 0 to 1 of x dx "
            "equals pi over two.")
        self.assertIn("seven over three", spoken)
        self.assertIn("the integral from zero to one of x d x",
                      spoken)
        self.assertIn("pi over two", spoken)
        self.assertNotIn("7/3", spoken)
        self.assertNotIn("The the integral", spoken)
        # Already-spoken prose passes through untouched.
        self.assertEqual(
            sm.speak_text("Seven over three is the answer."),
            "Seven over three is the answer.")


class TestNormaliseSpoken(unittest.TestCase):
    def test_three_ways_of_saying_one_fraction(self):
        for spoken in ("seven over three", "seven thirds",
                       "seven divided by three"):
            self.assertEqual(sm.normalise_spoken(spoken), "7/3")

    def test_pi_product_normalises(self):
        self.assertEqual(sm.normalise_spoken("thirty-two pi"),
                         "32 pi")

    def test_power_and_coefficient_normalise(self):
        self.assertEqual(sm.normalise_spoken("x squared"), "x^2")
        self.assertEqual(sm.normalise_spoken("two x"), "2x")

    def test_bare_number_and_filler(self):
        self.assertEqual(sm.normalise_spoken("seven"), "7")
        self.assertEqual(sm.normalise_spoken("the answer is seven"),
                         "7")
        self.assertEqual(sm.normalise_spoken("one third"), "1/3")

    def test_unit_survives(self):
        self.assertEqual(
            sm.normalise_spoken("seven over three metres"),
            "7/3 metres")

    def test_typed_forms_pass_through_unchanged(self):
        self.assertEqual(sm.normalise_spoken("7/3"), "7/3")
        self.assertEqual(sm.normalise_spoken("2.333"), "2.333")

    def test_unparseable_is_none(self):
        self.assertIsNone(sm.normalise_spoken("I don't know"))
        self.assertIsNone(sm.normalise_spoken("banana"))
        self.assertIsNone(sm.normalise_spoken(""))

    def test_two_claims_is_ambiguous_none(self):
        self.assertIsNone(sm.normalise_spoken(
            "seven over three or eight over three"))
        self.assertIsNone(sm.normalise_spoken(
            "seven over three or maybe eight"))
        # A swallowed operator is ambiguous too: (7/3) pi or 7/(3 pi)?
        self.assertIsNone(sm.normalise_spoken("seven over three pi"))


class TestGradeSpoken(unittest.TestCase):
    def test_three_ways_grade_identically_through_u3(self):
        for spoken in ("seven over three", "seven thirds",
                       "seven divided by three"):
            grade = sm.grade_spoken(spoken, "7/3", RUBRIC)
            self.assertTrue(grade["correct"], spoken)
            self.assertEqual(grade["method"], "exact")

    def test_pi_and_power_grade(self):
        self.assertTrue(sm.grade_spoken(
            "thirty-two pi", "32 pi", RUBRIC)["correct"])
        self.assertTrue(sm.grade_spoken(
            "thirty-two pi", "32π", RUBRIC)["correct"])
        self.assertTrue(sm.grade_spoken(
            "x squared", "x^2", RUBRIC)["correct"])
        self.assertTrue(sm.grade_spoken("two x", "2x", RUBRIC)[
            "correct"])

    def test_wrong_spoken_answer_fails(self):
        grade = sm.grade_spoken("eight over three", "7/3", RUBRIC)
        self.assertFalse(grade["correct"])
        self.assertEqual(grade["method"], "numeric")

    def test_decimal_approximation_does_not_pass(self):
        # 2.333 is not 7/3 within U3's tolerance — Fraction-exact.
        grade = sm.grade_spoken("two point three three three",
                                "7/3", RUBRIC)
        self.assertFalse(grade["correct"])

    def test_unparseable_is_not_proven_never_guessed(self):
        grade = sm.grade_spoken("I don't know", "7/3", RUBRIC)
        self.assertFalse(grade["correct"])
        self.assertEqual(grade["method"], "fail_safe")

    def test_low_confidence_grades_nothing(self):
        # D-T3: a mishearing is never a wrong answer — even a
        # correct hearing at low confidence grades nothing, and
        # even a judge that would pass cannot rescue it.
        grade = sm.grade_spoken("seven over three", "7/3", RUBRIC,
                                judge=lambda a, g, r: {
                                    "correct": True, "deduction": ""},
                                confidence=0.5)
        self.assertFalse(grade["correct"])
        self.assertEqual(grade["method"], "fail_safe")
        self.assertEqual(grade["reason"], "low_confidence")

    def test_typed_answers_grade_exactly_as_before(self):
        grade = sm.grade_spoken("7/3", "7/3", RUBRIC)
        self.assertTrue(grade["correct"])
        self.assertEqual(grade["method"], "exact")
        direct = cl.grade_answer("7/3", "7/3", RUBRIC)
        self.assertEqual(grade, direct)

    def test_normalised_correct_answer_records_a_transfer(self):
        # The driver wiring: normalise first, then feed U3's
        # CheckSession the canonical form. A spoken correct answer
        # records a transfer exactly as a typed one does.
        model = stm.StudentModel(_tmp_store(self))
        session = cl.CheckSession(_bite(), model)
        session.start()
        canon = sm.normalise_spoken("seven over three")
        self.assertEqual(canon, "7/3")
        result = session.answer(canon)
        self.assertTrue(result["grade"]["correct"])
        self.assertEqual(
            model.get_skill(SKILL)["transfers"], 1)


class TestSpokenGoldGuard(unittest.TestCase):
    def test_spoken_gold_is_detected(self):
        self.assertTrue(sm.spoken_reveals_gold(
            "seven over three", "7/3"))
        self.assertTrue(sm.spoken_reveals_gold("two x", "2x"))
        self.assertTrue(sm.spoken_reveals_gold("7/3", "7/3"))

    def test_spoken_gold_is_not_over_triggered(self):
        self.assertFalse(sm.spoken_reveals_gold(
            "the value is a fraction", "7/3"))
        self.assertFalse(sm.spoken_reveals_gold(
            "seven over three", "8/3"))
        self.assertFalse(sm.spoken_reveals_gold("", "7/3"))

    def test_spoken_hint_does_not_reveal_gold(self):
        # The tutor's spoken hint (the rubric's first clause,
        # spoken through speak_text) must not reveal the gold.
        hint = cl.hint_for(_bite())
        spoken = sm.speak_text(hint)
        self.assertFalse(sm.spoken_reveals_gold(spoken, "7/3"))


if __name__ == "__main__":
    unittest.main()
