"""U3 — check-question loop: one bite + one check, graded by
rubric, retried with a hint, never revealing the answer.

Lab leg only. The real-Windows field leg — a real MA 105
check question on his machine, graded by rubric, retried with
a hint, never revealing the answer, with mastery moving
exactly once — is OWED, not this batch.

Gap proved first (audit T3, T8, T13): ``flashcard_answer``
grades by exact string match and reveals the gold on a wrong
answer (``core/skills_study_flashcards.py:141``); ``quiz_me``
is MCQ from own cards only; no rubric grading, no numeric
tolerance, no retry loop anywhere; no graded check writes
mastery through U1's ``record_graded``.

What this suite proves: rubric grading passes a correct
answer in different words (exact-string would fail); a wrong
answer NEVER reveals the gold (in any reply); the retry loop
(wrong → hint → re-teach → fresh variant; the variant differs
from the first check); the bound (3 wrong graded answers end
honestly as handed_to_diagnosis, never looping forever, never
inventing a pass); numeric tolerance (equivalent forms / units
pass, a wrong value or wrong unit fails); a low-confidence
spoken answer re-asks and grades nothing; the deterministic
fail-safe (an invalid judge output → "not proven", never a
guessed pass); mastery moves exactly once per graded check
through record_graded only (an ungraded turn moves nothing);
the B15 pause shape (the question pauses, the answer resumes
it); the snapshot round-trip.
"""

import os
import tempfile
import unittest
from pathlib import Path

from core.brain import check_loop as cl
from core.brain import student_model as sm
from core.brain.lesson_planner import Bite
from core.memory import MemoryStore

SKILL = "ma105:double-integrals:limits-from-region"
GOLD = "1/2"
RUBRIC = ("A correct answer gives the value of the integral. "
          "The limits must come from the region, not from habit.")
BITE_TEXT = ("The region is the triangle under y = x from 0 to 1. "
             "Set up the integral and give its value.")


def _tmp_store(testcase):
    tmp = Path(tempfile.mkdtemp(prefix="check_loop_"))
    testcase.addCleanup(
        lambda: [os.unlink(p) for p in tmp.iterdir()] or tmp.rmdir())
    return MemoryStore(tmp / "assistant.db")


def _bite(**overrides):
    fields = {
        "skill": SKILL,
        "bite": BITE_TEXT,
        "check": "What is the value of the integral?",
        "gold": GOLD,
        "rubric": RUBRIC,
        "reinforce": SKILL,
    }
    fields.update(overrides)
    return Bite(**fields)


def _session(testcase, bite=None, **kwargs):
    model = sm.StudentModel(_tmp_store(testcase))
    session = cl.CheckSession(bite or _bite(), model, **kwargs)
    return session, model


def _variants():
    seen = []

    def variant_fn(bite, attempt):
        text = f"Variant {attempt}: what is the value now?"
        seen.append(text)
        return text

    return variant_fn, seen


def _judge_correct(answer, gold, rubric):
    return {"correct": True, "deduction": "",
            "nuggets": ["value matches"]}


def _judge_wrong(answer, gold, rubric):
    return {"correct": False,
            "deduction": "the value does not match the gold",
            "nuggets": []}


class TestGrading(unittest.TestCase):
    def test_numeric_equivalent_form_passes(self):
        # Exact-string match would fail "0.5" against "1/2".
        grade = cl.grade_answer("0.5", "1/2")
        self.assertTrue(grade["correct"])
        self.assertEqual(grade["method"], "numeric")
        self.assertNotEqual("0.5".lower(), "1/2".lower())

    def test_numeric_fraction_and_scientific_pass(self):
        self.assertTrue(cl.grade_answer("5e-1", "1/2")["correct"])
        self.assertTrue(cl.grade_answer("0.500", "1/2")["correct"])

    def test_numeric_wrong_value_fails_deterministically(self):
        grade = cl.grade_answer("0.6", "1/2")
        self.assertFalse(grade["correct"])
        self.assertEqual(grade["method"], "numeric")

    def test_units_must_match_when_gold_has_one(self):
        self.assertTrue(cl.grade_answer("5 cm", "5 cm")["correct"])
        self.assertTrue(
            cl.grade_answer("5 centimetres", "5 cm")["correct"])
        grade = cl.grade_answer("5 m", "5 cm")
        self.assertFalse(grade["correct"])
        self.assertEqual(grade["reason"], "right value, wrong unit")
        grade = cl.grade_answer("5", "5 cm")
        self.assertFalse(grade["correct"])

    def test_normalised_exact_passes(self):
        grade = cl.grade_answer("  The Area Under The Curve ",
                                "the area under the curve")
        self.assertTrue(grade["correct"])
        self.assertEqual(grade["method"], "exact")

    def test_judge_fallback_passes_different_words(self):
        # "area under the curve" vs gold "the integral's value"
        # is not an exact match and not numeric — the judge
        # fallback is the only path that can pass it.
        grade = cl.grade_answer("area under the curve",
                                "the integral's value",
                                judge=_judge_correct)
        self.assertTrue(grade["correct"])
        self.assertEqual(grade["method"], "judge")

    def test_no_judge_and_no_deterministic_verdict_is_not_proven(self):
        grade = cl.grade_answer("area under the curve",
                                "the integral's value")
        self.assertFalse(grade["correct"])
        self.assertEqual(grade["method"], "fail_safe")
        self.assertEqual(grade["reason"], "no_judge")


class TestJudgeFailSafe(unittest.TestCase):
    def test_judge_returning_a_string_fails_safe(self):
        grade = cl.grade_answer("something", "the gold",
                                judge=lambda a, g, r: "yes")
        self.assertFalse(grade["correct"])
        self.assertEqual(grade["method"], "fail_safe")
        self.assertEqual(grade["reason"], "judge_unparseable")

    def test_judge_missing_correct_fails_safe(self):
        grade = cl.grade_answer("something", "the gold",
                                judge=lambda a, g, r: {"deduction": "x"})
        self.assertFalse(grade["correct"])
        self.assertEqual(grade["reason"], "judge_unparseable")

    def test_judge_raising_fails_safe(self):
        def boom(a, g, r):
            raise RuntimeError("judge down")

        grade = cl.grade_answer("something", "the gold", judge=boom)
        self.assertFalse(grade["correct"])
        self.assertEqual(grade["method"], "fail_safe")
        self.assertEqual(grade["reason"], "judge_error")

    def test_judge_wrong_is_wrong_with_deduction(self):
        grade = cl.grade_answer("something", "the gold",
                                judge=_judge_wrong)
        self.assertFalse(grade["correct"])
        self.assertEqual(grade["method"], "judge")
        self.assertIn("does not match", grade["deduction"])

    def test_deterministic_numeric_verdict_beats_judge(self):
        # A wrong value is wrong; no judge overrules arithmetic.
        grade = cl.grade_answer("0.6", "1/2",
                                judge=_judge_correct)
        self.assertFalse(grade["correct"])
        self.assertEqual(grade["method"], "numeric")


class TestHintNeverRevealsGold(unittest.TestCase):
    def test_hint_is_a_rubric_nudge_not_the_gold(self):
        hint = cl.hint_for(_bite())
        self.assertEqual(
            hint, "A correct answer gives the value of the integral.")
        self.assertNotIn(GOLD, hint)

    def test_hint_falls_back_when_rubric_contains_gold(self):
        bite = _bite(rubric="The answer is 1/2 and must be exact.")
        hint = cl.hint_for(bite)
        self.assertNotIn(GOLD, hint)
        self.assertIn("what the answer has to show", hint)

    def test_wrong_answer_reply_never_contains_gold(self):
        variant_fn, _ = _variants()
        session, _ = _session(self, variant_fn=variant_fn)
        session.start()
        out = session.answer("0.9")
        self.assertNotIn(GOLD, out["reply"])
        self.assertNotIn("1/2", out["reply"])
        # The re-teach bite text is gold-free by construction.
        self.assertNotIn(GOLD, out["reply"])


class TestPauseShape(unittest.TestCase):
    def test_start_returns_pending_question_record(self):
        session, _ = _session(self)
        pending = session.start()
        self.assertEqual(pending["question"],
                         "What is the value of the integral?")
        self.assertEqual(pending["bite_skill"], SKILL)
        self.assertEqual(pending["attempt"], 1)
        self.assertEqual(pending["label"], "tutor check")
        self.assertEqual(session.status, "pending")

    def test_answer_before_start_resolves_nothing(self):
        session, model = _session(self)
        out = session.answer("0.5")
        self.assertEqual(out["status"], "not_started")
        self.assertIsNone(out["grade"])
        self.assertEqual(model.get_skill(SKILL)["evidence"], 0)

    def test_empty_answer_resolves_nothing(self):
        session, model = _session(self)
        session.start()
        out = session.answer("   ")
        self.assertEqual(out["status"], "pending")
        self.assertIsNone(out["grade"])
        self.assertEqual(model.get_skill(SKILL)["evidence"], 0)

    def test_answer_after_done_grades_nothing(self):
        session, model = _session(self)
        session.start()
        first = session.answer("0.5")
        self.assertEqual(session.status, "done")
        out = session.answer("0.5")
        self.assertEqual(out["status"], "done")
        # No new grading happened: the grade is still the first
        # answer's, and mastery moved exactly once.
        self.assertEqual(out["grade"], first["grade"])
        self.assertEqual(model.get_skill(SKILL)["evidence"], 1)

    def test_diagnostic_bite_check_runs_before_teaching(self):
        session, _ = _session(self, bite=_bite(diagnostic=True))
        pending = session.start()
        self.assertTrue(pending["diagnostic"])
        self.assertEqual(pending["question"],
                         "What is the value of the integral?")


class TestRetryLoop(unittest.TestCase):
    def test_wrong_then_right_on_variant(self):
        variant_fn, seen = _variants()
        session, model = _session(self, variant_fn=variant_fn)
        session.start()
        first = session.answer("0.9")
        self.assertEqual(first["status"], "pending")
        self.assertIn("Hint:", first["reply"])
        self.assertIn("Let's go over the bite once more",
                      first["reply"])
        self.assertIsNotNone(first["pending"])
        self.assertNotEqual(first["pending"]["question"],
                            "What is the value of the integral?")
        self.assertEqual(len(seen), 1)
        second = session.answer("0.5")
        self.assertEqual(second["status"], "done")
        self.assertEqual(second["outcome"]["outcome"],
                         "still_learning")
        # The variant answer came after a hint: p moved, but no
        # clean transfer was counted.
        self.assertEqual(model.get_skill(SKILL)["transfers"], 0)
        self.assertEqual(model.get_skill(SKILL)["evidence"], 2)

    def test_correct_first_try_counts_a_transfer(self):
        session, model = _session(self)
        session.start()
        out = session.answer("0.5")
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["outcome"]["outcome"], "still_learning")
        self.assertEqual(model.get_skill(SKILL)["transfers"], 1)
        self.assertIn("one clean transfer", out["reply"])

    def test_bound_hands_to_diagnosis_honestly(self):
        variant_fn, _ = _variants()
        session, model = _session(self, variant_fn=variant_fn)
        session.start()
        out = session.answer("0.9")
        self.assertEqual(out["status"], "pending")
        out = session.answer("0.8")
        self.assertEqual(out["status"], "pending")
        out = session.answer("0.7")
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["outcome"]["outcome"],
                         "handed_to_diagnosis")
        self.assertEqual(out["outcome"]["reason"], "retry_bound")
        self.assertEqual(out["outcome"]["attempts"], 3)
        self.assertEqual(out["outcome"]["reinforce"], SKILL)
        self.assertNotIn(GOLD, out["reply"])
        self.assertFalse(model.is_mastered(SKILL))
        self.assertEqual(model.get_skill(SKILL)["evidence"], 3)
        self.assertEqual(model.get_skill(SKILL)["wrong"], 3)

    def test_no_variant_source_ends_honestly_after_hint(self):
        session, model = _session(self)  # no variant_fn
        session.start()
        out = session.answer("0.9")
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["outcome"]["outcome"],
                         "handed_to_diagnosis")
        self.assertEqual(out["outcome"]["reason"],
                         "no_fresh_variant")
        self.assertIn("rather than ask the same question twice",
                      out["reply"])
        self.assertNotIn(GOLD, out["reply"])
        self.assertEqual(model.get_skill(SKILL)["evidence"], 1)

    def test_variant_fn_raising_ends_honestly(self):
        def boom(bite, attempt):
            raise RuntimeError("no variants today")

        session, model = _session(self, variant_fn=boom)
        session.start()
        out = session.answer("0.9")
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["outcome"]["reason"],
                         "no_fresh_variant")


class TestLowConfidence(unittest.TestCase):
    def test_low_confidence_reasks_and_grades_nothing(self):
        variant_fn, _ = _variants()
        session, model = _session(self, variant_fn=variant_fn)
        session.start()
        out = session.answer("0.5", confidence=0.3)
        self.assertEqual(out["status"], "pending")
        self.assertIn("did you say", out["reply"])
        self.assertIsNone(out["grade"])
        self.assertIsNone(out["mastery"])
        self.assertEqual(model.get_skill(SKILL)["evidence"], 0)
        # The re-ask consumes no attempt: the next answer is
        # still attempt 1 and can still pass.
        out = session.answer("0.5", confidence=1.0)
        self.assertEqual(out["status"], "done")
        self.assertEqual(model.get_skill(SKILL)["evidence"], 1)

    def test_bad_confidence_is_treated_as_unheard(self):
        session, model = _session(self)
        session.start()
        out = session.answer("0.5", confidence="not-a-number")
        self.assertEqual(out["status"], "pending")
        self.assertIsNone(out["grade"])
        self.assertEqual(model.get_skill(SKILL)["evidence"], 0)


class TestMasteryWrites(unittest.TestCase):
    def test_exactly_one_record_per_graded_answer(self):
        variant_fn, _ = _variants()
        session, model = _session(self, variant_fn=variant_fn)
        session.start()
        session.answer("0.9")
        self.assertEqual(model.get_skill(SKILL)["evidence"], 1)
        session.answer("0.8")
        self.assertEqual(model.get_skill(SKILL)["evidence"], 2)
        session.answer("0.5")
        self.assertEqual(model.get_skill(SKILL)["evidence"], 3)

    def test_two_clean_transfers_master_through_the_loop(self):
        session, model = _session(self)
        session.start()
        session.answer("0.5")
        self.assertFalse(model.is_mastered(SKILL))
        second = cl.CheckSession(_bite(), model)
        second.start()
        out = second.answer("1/2")
        self.assertTrue(model.is_mastered(SKILL))
        self.assertEqual(out["outcome"]["outcome"], "mastered")
        self.assertIn("counts as mastered", out["reply"])

    def test_malformed_bite_is_refused_at_construction(self):
        model = sm.StudentModel(_tmp_store(self))
        with self.assertRaises(ValueError):
            cl.CheckSession(_bite(gold="   "), model)


class TestSnapshot(unittest.TestCase):
    def test_snapshot_round_trips_a_pending_session(self):
        variant_fn, _ = _variants()
        session, model = _session(self, variant_fn=variant_fn)
        session.start()
        session.answer("0.9")
        snap = session.snapshot()
        self.assertEqual(snap["attempts"], 1)
        self.assertTrue(snap["hinted"])
        self.assertIsNotNone(snap["pending"])
        restored = cl.CheckSession.from_snapshot(
            snap, model, variant_fn=variant_fn)
        self.assertEqual(restored.status, "pending")
        self.assertEqual(restored.pending()["question"],
                         snap["pending"]["question"])
        out = restored.answer("0.5")
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["outcome"]["outcome"],
                         "still_learning")


if __name__ == "__main__":
    unittest.main()
