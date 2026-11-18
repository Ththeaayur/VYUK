"""U4 — error diagnosis: named misconceptions, diagnostic questions, back-track.

Worker-verified only. No real cloud calls, no real waiting.
"""

import inspect
import unittest

from core.brain import error_diagnosis as ed
from core.brain.error_diagnosis import (
    Diagnoser, MisconceptionLibrary, SEEDED_ENTRIES, validate_entry,
)
from core.brain.check_loop import _contains_gold
from core.brain.lesson_planner import Bite


def _outcome(skill="ma105-double-integrals", attempts=3,
             reason="value does not match the gold",
             method="numeric", reinforce="ma105-regions-and-limits"):
    return {
        "outcome": "handed_to_diagnosis",
        "skill": skill,
        "attempts": attempts,
        "reinforce": reinforce,
        "grade": {"correct": False, "method": method,
                  "reason": reason, "deduction": ""},
        "mastery": {"p": 0.3, "evidence": 3, "transfers": 0},
        "reason": "retry_bound",
    }


def _bite(gold="4"):
    return Bite(
        skill="ma105-double-integrals",
        bite="Set up the double integral over the region.",
        check="What is the value of the integral?",
        gold=gold,
        rubric="The answer must show the value of the integral "
               "over the region described, with the integrand "
               "complete.",
        reinforce="ma105-regions-and-limits")


class TestSeededLibrary(unittest.TestCase):
    def test_library_seeded_with_his_slips(self):
        lib = MisconceptionLibrary()
        self.assertEqual(len(lib), 2)
        dropped = lib.get("ma105-dropped-factor")
        limits = lib.get("ma105-limits-off-habit")
        self.assertIsNotNone(dropped)
        self.assertIsNotNone(limits)
        self.assertIn("factor", dropped["diagnostic_question"])
        self.assertIn("region", limits["diagnostic_question"])

    def test_every_seeded_entry_has_question_prerequisite_feedback(self):
        for entry in SEEDED_ENTRIES:
            ok, error = validate_entry(entry)
            self.assertTrue(ok, error)
            self.assertTrue(entry["diagnostic_question"].strip())
            self.assertTrue(entry["prerequisite"].strip())
            self.assertTrue(entry["feedback"].strip())

    def test_add_entry_validates_shape(self):
        lib = MisconceptionLibrary()
        bad = dict(SEEDED_ENTRIES[0])
        bad["id"] = "test-bad"
        bad["diagnostic_question"] = "   "
        ok, error = lib.add_entry(bad)
        self.assertFalse(ok)
        self.assertIn("diagnostic_question", error)
        self.assertEqual(len(lib), 2)

    def test_add_entry_refuses_duplicate_id(self):
        lib = MisconceptionLibrary()
        ok, error = lib.add_entry(dict(SEEDED_ENTRIES[0]))
        self.assertFalse(ok)
        self.assertIn("already exists", error)

    def test_new_entry_is_data_not_code(self):
        lib = MisconceptionLibrary()
        entry = {
            "id": "test-sign-error",
            "name": "Sign error",
            "skills": ["ma105-"],
            "surface": "The answer has the wrong sign.",
            "cause": "A sign was flipped while evaluating.",
            "markers": [{"kind": "answer_contains",
                         "value": "negative", "weight": 2}],
            "diagnostic_question": "Where did the sign change?",
            "diagnostic_markers": ["sign", "negative"],
            "prerequisite": "ma105-signs",
            "feedback": "The sign is wrong — trace where it "
                        "changed.",
        }
        ok, error = lib.add_entry(entry)
        self.assertTrue(ok, error)
        diag = Diagnoser(lib).diagnose(
            _outcome(), answer="the answer is negative 5",
            gold="5")
        self.assertEqual(diag["status"], "classified")
        self.assertEqual(diag["misconception"], "test-sign-error")


class TestClassify(unittest.TestCase):
    def test_dropped_factor_classifies(self):
        diag = Diagnoser().diagnose(
            _outcome(), answer="2", gold="4")
        self.assertEqual(diag["status"], "classified")
        self.assertEqual(diag["misconception"],
                         "ma105-dropped-factor")
        self.assertEqual(diag["prerequisite"],
                         "ma105-integrand-setup")
        self.assertTrue(diag["question"].strip())
        self.assertTrue(diag["feedback"].strip())

    def test_wrong_value_with_no_clean_ratio_is_ambiguous(self):
        diag = Diagnoser().diagnose(
            _outcome(), answer="3", gold="4")
        self.assertEqual(diag["status"], "ambiguous")
        ids = {c["id"] for c in diag["candidates"]}
        self.assertEqual(ids, {"ma105-dropped-factor",
                               "ma105-limits-off-habit"})
        self.assertTrue(diag["question"].strip())

    def test_diagnostic_answer_separates_to_dropped_factor(self):
        first = Diagnoser().diagnose(
            _outcome(), answer="3", gold="4")
        self.assertEqual(first["status"], "ambiguous")
        settled = Diagnoser().resolve_diagnostic(
            first, "I think I dropped a factor somewhere")
        self.assertEqual(settled["status"], "classified")
        self.assertEqual(settled["misconception"],
                         "ma105-dropped-factor")

    def test_diagnostic_answer_separates_to_limits_slip(self):
        first = Diagnoser().diagnose(
            _outcome(), answer="3", gold="4")
        settled = Diagnoser().resolve_diagnostic(
            first, "I used the limits from 0 to a")
        self.assertEqual(settled["status"], "classified")
        self.assertEqual(settled["misconception"],
                         "ma105-limits-off-habit")

    def test_diagnostic_answer_confirming_nothing_stays_ambiguous(self):
        first = Diagnoser().diagnose(
            _outcome(), answer="3", gold="4")
        settled = Diagnoser().resolve_diagnostic(
            first, "I don't know what happened")
        self.assertEqual(settled["status"], "ambiguous")

    def test_resolve_on_classified_returns_unchanged(self):
        diag = Diagnoser().diagnose(
            _outcome(), answer="2", gold="4")
        self.assertEqual(diag["status"], "classified")
        again = Diagnoser().resolve_diagnostic(
            diag, "I dropped a factor")
        self.assertEqual(again, diag)

    def test_bite_supplies_skill_gold_reinforce(self):
        diag = Diagnoser().diagnose(
            {"outcome": "handed_to_diagnosis", "attempts": 3,
             "grade": {"correct": False, "method": "numeric",
                       "reason": "value does not match the gold"}},
            bite=_bite(), answer="2")
        self.assertEqual(diag["status"], "classified")
        self.assertEqual(diag["misconception"],
                         "ma105-dropped-factor")


class TestBackTrack(unittest.TestCase):
    def test_repeat_failure_back_tracks_now(self):
        diag = Diagnoser().diagnose(
            _outcome(attempts=3), answer="2", gold="4")
        self.assertTrue(diag["backtrack_now"])
        self.assertEqual(diag["backtrack"]["skill"],
                         "ma105-integrand-setup")
        self.assertEqual(diag["backtrack"]["plan_topic"],
                         "ma105-integrand-setup")

    def test_first_failure_asks_the_question_first(self):
        diag = Diagnoser().diagnose(
            _outcome(attempts=1), answer="2", gold="4")
        self.assertFalse(diag["backtrack_now"])
        self.assertEqual(diag["prerequisite"],
                         "ma105-integrand-setup")

    def test_unclassified_falls_back_to_reinforce_edge(self):
        # "banana" matches no marker; the judge verdict below
        # is not a numeric mismatch, so nothing scores.
        diag = Diagnoser().diagnose(
            _outcome(reason="judge graded it incorrect",
                     method="judge"),
            answer="banana", gold="4")
        self.assertEqual(diag["status"], "unclassified")
        self.assertEqual(diag["prerequisite"],
                         "ma105-regions-and-limits")
        self.assertEqual(diag["backtrack"]["skill"],
                         "ma105-regions-and-limits")


class TestNeverRevealsGold(unittest.TestCase):
    def test_seeded_feedback_never_contains_gold(self):
        for entry in SEEDED_ENTRIES:
            for gold in ("4", "12", "pi/2", "0.5"):
                self.assertFalse(
                    _contains_gold(entry["feedback"], gold),
                    f"{entry['id']} feedback reveals {gold!r}")

    def test_gold_containing_feedback_falls_back_to_generic(self):
        lib = MisconceptionLibrary()
        entry = {
            "id": "test-leaky",
            "name": "Leaky feedback",
            "skills": ["ma105-"],
            "surface": "Test entry.",
            "cause": "Test entry.",
            "markers": [{"kind": "grade_reason",
                         "value": "value does not match the gold",
                         "weight": 3}],
            "diagnostic_question": "What went wrong?",
            "diagnostic_markers": ["wrong"],
            "prerequisite": "ma105-test",
            "feedback": "The answer is 4.",
        }
        ok, error = lib.add_entry(entry)
        self.assertTrue(ok, error)
        diag = Diagnoser(lib).diagnose(
            _outcome(), answer="3", gold="4")
        self.assertEqual(diag["status"], "classified")
        self.assertEqual(diag["misconception"], "test-leaky")
        self.assertNotIn("4", diag["feedback"])
        self.assertIn("step where it went wrong",
                      diag["feedback"])


class TestUnclassified(unittest.TestCase):
    def test_unmatched_error_is_honestly_unclassified(self):
        diagnoser = Diagnoser()
        diag = diagnoser.diagnose(
            _outcome(reason="judge graded it incorrect",
                     method="judge"),
            answer="banana", gold="4")
        self.assertEqual(diag["status"], "unclassified")
        self.assertIsNone(diag["misconception"])
        self.assertTrue(diag["recorded"])
        self.assertEqual(len(diagnoser.unclassified()), 1)
        record = diagnoser.unclassified()[0]
        self.assertEqual(record["answer"], "banana")
        self.assertEqual(record["skill"],
                         "ma105-double-integrals")
        self.assertEqual(record["attempts"], 3)

    def test_unclassified_is_recorded_through_store(self):
        store = _FakeStore()
        diagnoser = Diagnoser(store=store)
        diag = diagnoser.diagnose(
            _outcome(reason="judge graded it incorrect",
                     method="judge"),
            answer="banana", gold="4")
        self.assertEqual(diag["status"], "unclassified")
        self.assertTrue(diag["persisted"])
        self.assertEqual(len(store.rows), 1)
        self.assertEqual(store.rows[0][0],
                         ed.UNCATEGORISED_CATEGORY)

    def test_non_ma105_skill_matches_nothing(self):
        diag = Diagnoser().diagnose(
            _outcome(skill="french-verbs"), answer="2", gold="4")
        self.assertEqual(diag["status"], "unclassified")


class TestHonesty(unittest.TestCase):
    def test_diagnose_never_raises_on_garbage(self):
        diagnoser = Diagnoser()
        for outcome in (None, {}, {"skill": ""}, "not a dict"):
            diag = diagnoser.diagnose(outcome)
            self.assertIn(diag["status"],
                          ("classified", "ambiguous",
                           "unclassified"))

    def test_diagnose_with_outcome_only_still_works(self):
        diag = Diagnoser().diagnose(_outcome())
        self.assertIn(diag["status"], ("classified", "ambiguous",
                                       "unclassified"))

    def test_deterministic(self):
        diagnoser = Diagnoser()
        first = diagnoser.diagnose(_outcome(), answer="3",
                                   gold="4")
        second = diagnoser.diagnose(_outcome(), answer="3",
                                    gold="4")
        self.assertEqual(first, second)

    def test_diagnoser_never_writes_mastery(self):
        source = inspect.getsource(ed)
        self.assertNotIn("record_graded", source)
        self.assertNotIn("StudentModel", source)


class _FakeStore:
    """Minimal MemoryStore-shaped sink for the tests."""

    def __init__(self):
        self.rows = []

    def save_fact(self, category, key, value):
        self.rows.append((category, key, value))
        return True


if __name__ == "__main__":
    unittest.main()
