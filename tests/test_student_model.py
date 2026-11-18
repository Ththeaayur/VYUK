"""U1 — student model: one per-skill mastery record, persisted in the
MemoryStore facts table, updated ONLY by graded check evidence.

Lab leg only. The real-Windows field leg — a real tutoring session
on his machine where the record persists between sessions and the
progress report reads it — is OWED, not this batch.

Gap proved first (audit T2/T13): ``_mastered()`` in
``core/skills_study_helpers.py`` is a flat list of card fronts;
``_grade()`` bumps global right/wrong counters only; ``mastered``
appends on the FIRST correct review answer
(``core/skills_study_flashcards.py:147-149``). No per-skill mastery,
no misconception record, no pace anywhere in the tree.

What this suite proves: the record holds per-skill prior/learn/
guess/slip + mastery probability + evidence + transfers; only a
graded check answer moves it (reads never move it; a low-confidence
spoken answer updates nothing); mastery needs 2 unprompted novel
transfers (one correct answer is not mastery; a hinted or repeated
correct answer never counts); cold-start priors stand before ~10
interactions per skill and per-skill guess/slip are fitted only
after that; the record round-trips through a real MemoryStore
(write, rebuild, read back identical); clearing deletes only the
student-model facts and touches nothing else.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from core.brain import student_model as sm
from core.memory import MemoryStore

SKILL_A = "ma105:double-integrals:limits-from-region"
SKILL_B = "ma105:double-integrals:jacobian"


def _tmp_store(testcase):
    tmp = Path(tempfile.mkdtemp(prefix="student_model_"))
    testcase.addCleanup(
        lambda: [os.unlink(p) for p in tmp.iterdir()] or tmp.rmdir())
    return MemoryStore(tmp / "assistant.db")


class TestColdStart(unittest.TestCase):
    def test_unknown_skill_cold_start_defaults(self):
        model = sm.StudentModel()
        record = model.get_skill(SKILL_A)
        self.assertEqual(record["p"], sm.DEFAULT_PRIOR)
        self.assertEqual(record["guess"], sm.DEFAULT_GUESS)
        self.assertEqual(record["slip"], sm.DEFAULT_SLIP)
        self.assertEqual(record["evidence"], 0)
        self.assertEqual(record["transfers"], 0)
        self.assertFalse(record["fitted"])
        self.assertFalse(model.is_mastered(SKILL_A))

    def test_invalid_skill_id_reads_nothing(self):
        model = sm.StudentModel()
        self.assertIsNone(model.get_skill(""))
        self.assertIsNone(model.get_skill(None))
        self.assertIsNone(model.get_skill("   "))
        self.assertFalse(model.is_mastered(""))


class TestBktUpdate(unittest.TestCase):
    def test_correct_moves_p_up_exact_math(self):
        # prior 0.30, learn 0.15, guess 0.20, slip 0.10:
        # P(correct) = 0.3*0.9 + 0.7*0.2 = 0.41
        # post = 0.27/0.41 = 0.6585; new = 0.6585 + 0.3415*0.15.
        model = sm.StudentModel()
        out = model.record_graded(SKILL_A, True)
        self.assertTrue(out["recorded"])
        self.assertAlmostEqual(out["p"], 0.7098, places=4)
        self.assertEqual(out["evidence"], 1)

    def test_incorrect_moves_p_down(self):
        model = sm.StudentModel()
        before = model.get_skill(SKILL_A)["p"]
        out = model.record_graded(SKILL_A, False)
        self.assertTrue(out["recorded"])
        self.assertLess(out["p"], before)

    def test_p_never_reaches_zero_or_one(self):
        model = sm.StudentModel()
        for _ in range(30):
            model.record_graded(SKILL_A, True)
        record = model.get_skill(SKILL_A)
        self.assertLessEqual(record["p"], sm.P_CEIL)
        self.assertGreaterEqual(record["p"], sm.P_FLOOR)


class TestReadsNeverMoveMastery(unittest.TestCase):
    def test_reads_are_pure(self):
        model = sm.StudentModel()
        model.record_graded(SKILL_A, True, novel=True)
        first = model.get_skill(SKILL_A)
        model.all_skills()
        model.mastery_summary()
        model.is_mastered(SKILL_A)
        second = model.get_skill(SKILL_A)
        self.assertEqual(first, second)


class TestMasteryBar(unittest.TestCase):
    def test_one_transfer_is_not_mastery(self):
        model = sm.StudentModel()
        out = model.record_graded(SKILL_A, True, novel=True)
        self.assertEqual(out["transfers"], 1)
        self.assertFalse(out["mastered"])
        self.assertFalse(model.is_mastered(SKILL_A))

    def test_two_unprompted_novel_transfers_is_mastery(self):
        model = sm.StudentModel()
        model.record_graded(SKILL_A, True, novel=True)
        out = model.record_graded(SKILL_A, True, novel=True)
        self.assertEqual(out["transfers"], 2)
        self.assertTrue(out["mastered"])
        self.assertTrue(model.is_mastered(SKILL_A))

    def test_hinted_correct_moves_p_but_never_counts(self):
        model = sm.StudentModel()
        before = model.get_skill(SKILL_A)["p"]
        out = model.record_graded(SKILL_A, True, novel=True, hinted=True)
        self.assertEqual(out["transfers"], 0)
        self.assertFalse(out["mastered"])
        self.assertGreater(out["p"], before)

    def test_repeated_correct_never_counts(self):
        model = sm.StudentModel()
        model.record_graded(SKILL_A, True, novel=False)
        out = model.record_graded(SKILL_A, True, novel=False)
        self.assertEqual(out["transfers"], 0)
        self.assertFalse(out["mastered"])

    def test_wrong_novel_answer_never_counts(self):
        model = sm.StudentModel()
        out = model.record_graded(SKILL_A, False, novel=True)
        self.assertEqual(out["transfers"], 0)
        self.assertFalse(out["mastered"])


class TestColdStartFit(unittest.TestCase):
    def test_no_fit_from_eight_attempts(self):
        model = sm.StudentModel()
        for _ in range(8):
            model.record_graded(SKILL_A, True)
        record = model.get_skill(SKILL_A)
        self.assertEqual(record["evidence"], 8)
        self.assertFalse(record["fitted"])
        self.assertEqual(record["guess"], sm.DEFAULT_GUESS)
        self.assertEqual(record["slip"], sm.DEFAULT_SLIP)

    def test_fit_after_ten_with_band_data(self):
        # 3 correct (p climbs past 0.7), 3 wrong at high p, then
        # correct answers to reach 10 interactions. The high-p
        # band ends at 2 wrong of 7 high-p observations, so slip
        # refits from 0.10 to 2/7; guess stays default (only 2
        # low-p observations, below the band minimum).
        model = sm.StudentModel()
        for _ in range(3):
            model.record_graded(SKILL_A, True)
        for _ in range(3):
            model.record_graded(SKILL_A, False)
        for _ in range(4):
            model.record_graded(SKILL_A, True)
        record = model.get_skill(SKILL_A)
        self.assertEqual(record["evidence"], 10)
        self.assertTrue(record["fitted"])
        self.assertAlmostEqual(record["slip"], 2 / 7, places=4)
        self.assertEqual(record["guess"], sm.DEFAULT_GUESS)


class TestConfidenceGating(unittest.TestCase):
    def test_low_confidence_updates_nothing(self):
        model = sm.StudentModel()
        model.record_graded(SKILL_A, True, novel=True)
        before = model.get_skill(SKILL_A)
        out = model.record_graded(SKILL_A, True, novel=True,
                                  confidence=0.5)
        self.assertTrue(out["ok"])
        self.assertFalse(out["recorded"])
        self.assertEqual(out["reason"], "low_confidence")
        after = model.get_skill(SKILL_A)
        self.assertEqual(before, after)

    def test_confidence_at_floor_records(self):
        model = sm.StudentModel()
        out = model.record_graded(SKILL_A, True,
                                  confidence=sm.CONFIDENCE_FLOOR)
        self.assertTrue(out["recorded"])

    def test_invalid_inputs_record_nothing(self):
        model = sm.StudentModel()
        self.assertEqual(
            model.record_graded("", True)["reason"], "invalid_skill_id")
        self.assertEqual(
            model.record_graded(None, True)["reason"],
            "invalid_skill_id")
        self.assertEqual(
            model.record_graded(SKILL_A, "yes")["reason"],
            "invalid_correct")
        self.assertEqual(model.get_skill(SKILL_A)["evidence"], 0)


class TestPersistence(unittest.TestCase):
    def test_round_trip_through_real_memorystore(self):
        store = _tmp_store(self)
        model_a = sm.StudentModel(store)
        model_a.record_graded(SKILL_A, True, novel=True)
        model_a.record_graded(SKILL_A, True, novel=True)
        model_a.record_graded(SKILL_B, False)
        # A NEW model on the SAME store file reads back identical.
        model_b = sm.StudentModel(store)
        rec_a = model_a.get_skill(SKILL_A)
        rec_b = model_b.get_skill(SKILL_A)
        self.assertEqual(rec_a, rec_b)
        self.assertTrue(model_b.is_mastered(SKILL_A))
        self.assertEqual(model_b.get_skill(SKILL_B)["evidence"], 1)

    def test_corrupt_row_cold_starts_and_overwrites(self):
        store = _tmp_store(self)
        store.save_fact(sm.CATEGORY, SKILL_A, "{not json")
        model = sm.StudentModel(store)
        record = model.get_skill(SKILL_A)
        self.assertEqual(record["p"], sm.DEFAULT_PRIOR)
        self.assertEqual(record["evidence"], 0)
        out = model.record_graded(SKILL_A, True)
        self.assertTrue(out["recorded"])
        self.assertEqual(model.get_skill(SKILL_A)["evidence"], 1)

    def test_clear_deletes_only_student_model_facts(self):
        store = _tmp_store(self)
        store.save_fact("notes", "shopping", "milk")
        model = sm.StudentModel(store)
        model.record_graded(SKILL_A, True)
        model.record_graded(SKILL_B, True)
        deleted = model.clear_all()
        self.assertEqual(deleted, 2)
        self.assertEqual(model.all_skills(), [])
        self.assertIsNotNone(store.get_fact("notes", "shopping"))
        self.assertEqual(
            store.get_fact("notes", "shopping")["value"], "milk")

    def test_in_memory_default_store_works(self):
        model = sm.StudentModel()
        model.record_graded(SKILL_A, True, novel=True)
        self.assertEqual(model.get_skill(SKILL_A)["evidence"], 1)


class TestPerSkillIndependence(unittest.TestCase):
    def test_slip_on_one_skill_never_moves_another(self):
        model = sm.StudentModel()
        model.record_graded(SKILL_A, True, novel=True)
        before_b = model.get_skill(SKILL_B)
        for _ in range(5):
            model.record_graded(SKILL_A, False)
        after_b = model.get_skill(SKILL_B)
        # A cold-start record is generated fresh per read, so its
        # timestamp is meaningless; compare everything else.
        before_b.pop("updated_at")
        after_b.pop("updated_at")
        self.assertEqual(before_b, after_b)
        self.assertEqual(after_b["evidence"], 0)


class TestSummary(unittest.TestCase):
    def test_mastery_summary_counts(self):
        model = sm.StudentModel()
        model.record_graded(SKILL_A, True, novel=True)
        model.record_graded(SKILL_A, True, novel=True)
        model.record_graded(SKILL_B, False)
        summary = model.mastery_summary()
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["mastered_count"], 1)
        by_id = {s["skill_id"]: s for s in summary["skills"]}
        self.assertTrue(by_id[SKILL_A]["mastered"])
        self.assertFalse(by_id[SKILL_B]["mastered"])
        self.assertEqual(by_id[SKILL_B]["evidence"], 1)


if __name__ == "__main__":
    unittest.main()
