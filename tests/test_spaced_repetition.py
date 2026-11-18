"""U8 — Spaced repetition, FSRS: the tutor remembers what you forget.

Lab leg only. The real-Windows field leg — a real MA 105 review on
his machine, a skill due for review surfacing at the right time, a
correct recall pushing its next review later, a forgotten skill
surfacing sooner — is OWED, not this batch.

What this suite proves: the FSRS state moves the right way (correct
raises stability, incorrect lowers it, difficulty follows the
pattern, retrievability decays with time); the due queue orders
lowest retrievability first and a cold-start skill has no due date;
a hinted correct review grows stability LESS than a clean one; a
strong overdue recall is an Easy and grows stability MORE than a
Good; the scheduler never grades and never writes mastery (the U1
record is byte-identical after an observe); the scheduler reads
U1's record only as display data; stability/difficulty/due survive
a reload; the desired-retention target moves the dues; low
confidence records nothing (D-T3); corrupt rows are cold starts;
clear_all deletes only the scheduler's own facts.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.brain import spaced_repetition as sr
from core.brain import student_model as sm
from core.memory import MemoryStore

T0 = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
SKILL = "ma105-double-integrals-limits-from-region"
OTHER = "ma105-regions-and-limits"


class _Clock:
    """Fake clock: the tests never wait in real time."""

    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now = self.now + timedelta(**kwargs)


def _tmp_store(testcase):
    tmp = Path(tempfile.mkdtemp())
    testcase.addCleanup(lambda: _rmtree(tmp))
    return MemoryStore(tmp / "assistant.db")


def _rmtree(path):
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            try:
                os.remove(os.path.join(root, name))
            except OSError:
                pass
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def _scheduler(testcase, retention=0.9, clock=None):
    clock = clock or _Clock(T0)
    sched = sr.ReviewScheduler(_tmp_store(testcase),
                               desired_retention=retention, clock=clock)
    return sched, clock


def _graduate(sched, skill, clock):
    """Drive a skill through its learning steps to Review state:
    Good, then Good again at the 10-minute step."""
    sched.observe(skill, correct=True, now=clock())
    clock.advance(minutes=10)
    return sched.observe(skill, correct=True, now=clock())


class TestRatingMapping(unittest.TestCase):
    def test_incorrect_is_again(self):
        self.assertEqual(sr.rating_for(False), 1)
        self.assertEqual(sr.rating_for(False, hinted=True), 1)

    def test_hinted_correct_is_hard(self):
        self.assertEqual(sr.rating_for(True, hinted=True), 2)

    def test_second_attempt_correct_is_hard(self):
        self.assertEqual(sr.rating_for(True, attempts=2), 2)
        self.assertEqual(sr.rating_for(True, attempts=3), 2)

    def test_clean_first_attempt_is_good(self):
        self.assertEqual(sr.rating_for(True), 3)
        self.assertEqual(sr.rating_for(True, retrievability=0.9), 3)
        self.assertEqual(sr.rating_for(True, retrievability=0.85), 3)

    def test_no_prior_review_is_never_easy(self):
        # A first review has no decay to survive: retrievability
        # unknown (None) must not trigger Easy.
        self.assertEqual(sr.rating_for(True, retrievability=None), 3)

    def test_strong_overdue_recall_is_easy(self):
        self.assertEqual(sr.rating_for(True, retrievability=0.79), 4)
        self.assertEqual(sr.rating_for(True, retrievability=0.5), 4)
        self.assertEqual(sr.rating_for(True, retrievability=0.8), 3)


class TestFirstReviews(unittest.TestCase):
    def test_first_good_is_a_learning_step(self):
        # FSRS learning steps [1m, 10m]: the first Good is a step,
        # not an interval — state stays Learning, due +10 minutes.
        sched, _ = _scheduler(self)
        out = sched.observe(SKILL, correct=True)
        self.assertTrue(out["recorded"])
        self.assertEqual(out["rating_name"], "Good")
        self.assertEqual(out["state"], 1)  # Learning
        self.assertAlmostEqual(out["stability"], 2.3065, places=4)
        self.assertAlmostEqual(out["difficulty"], 2.1181, places=3)
        self.assertEqual(out["due"], "2026-11-08T12:10:00+00:00")
        self.assertIsNone(out["retrievability"])  # no prior review

    def test_first_again_is_one_minute(self):
        sched, _ = _scheduler(self)
        out = sched.observe(SKILL, correct=False)
        self.assertEqual(out["rating_name"], "Again")
        self.assertAlmostEqual(out["stability"], 0.212, places=4)
        self.assertAlmostEqual(out["difficulty"], 6.4133, places=3)
        self.assertEqual(out["due"], "2026-11-08T12:01:00+00:00")
        self.assertEqual(out["lapses"], 1)

    def test_first_hinted_is_hard_five_and_a_half_minutes(self):
        sched, _ = _scheduler(self)
        out = sched.observe(SKILL, correct=True, hinted=True)
        self.assertEqual(out["rating_name"], "Hard")
        self.assertAlmostEqual(out["stability"], 1.2931, places=4)
        self.assertEqual(out["due"], "2026-11-08T12:05:30+00:00")

    def test_second_good_graduates_to_review(self):
        sched, clock = _scheduler(self)
        _graduate(sched, SKILL, clock)
        view = sched.record_for(SKILL)
        self.assertEqual(view["state"], 2)  # Review
        self.assertEqual(view["state_name"], "Review")
        # Interval-based due: stability ~2.31 days at retention 0.9.
        self.assertEqual(view["due"], "2026-11-10T12:10:00+00:00")
        self.assertAlmostEqual(view["stability"], 2.3065, places=4)


class TestStabilityAndDifficulty(unittest.TestCase):
    def test_correct_raises_stability_incorrect_lowers_it(self):
        sched, clock = _scheduler(self)
        _graduate(sched, SKILL, clock)
        before = sched.record_for(SKILL)["stability"]
        clock.advance(days=2)
        out = sched.observe(SKILL, correct=False, now=clock())
        self.assertLess(out["stability"], before)
        self.assertAlmostEqual(out["stability"], 0.6076, places=3)

    def test_difficulty_moves_with_the_pattern(self):
        sched, clock = _scheduler(self)
        _graduate(sched, SKILL, clock)
        d0 = sched.record_for(SKILL)["difficulty"]
        clock.advance(days=2)
        out_bad = sched.observe(SKILL, correct=False, now=clock())
        self.assertGreater(out_bad["difficulty"], d0)
        self.assertAlmostEqual(out_bad["difficulty"], 7.3922, places=3)
        # A Good review after the lapse pulls difficulty back down.
        clock.advance(minutes=10)
        out_good = sched.observe(SKILL, correct=True, now=clock())
        self.assertLess(out_good["difficulty"], out_bad["difficulty"])

    def test_hinted_correct_grows_stability_less_than_clean(self):
        # The SAME starting state, one clean Good and one hinted
        # Hard: the hinted review must grow stability less — the
        # numbers are asserted, not just the direction.
        clean, _ = _scheduler(self)
        hinted, _ = _scheduler(self)
        out_clean = clean.observe(SKILL, correct=True)
        out_hinted = hinted.observe(SKILL, correct=True, hinted=True)
        self.assertAlmostEqual(out_clean["stability"], 2.3065, places=4)
        self.assertAlmostEqual(out_hinted["stability"], 1.2931, places=4)
        self.assertLess(out_hinted["stability"], out_clean["stability"])
        self.assertLess(out_hinted["due"], out_clean["due"])

    def test_strong_overdue_recall_grows_more_than_good(self):
        # Recalled cleanly at retrievability ~0.77 (eleven days out
        # on a ~2.3-day stability) is an Easy, and it must grow
        # stability MORE than an on-time Good at retrievability ~0.9.
        easy, clock_e = _scheduler(self)
        good, clock_g = _scheduler(self)
        _graduate(easy, SKILL, clock_e)
        _graduate(good, SKILL, clock_g)
        clock_e.advance(days=11)
        clock_g.advance(days=2)
        out_easy = easy.observe(SKILL, correct=True, now=clock_e())
        out_good = good.observe(SKILL, correct=True, now=clock_g())
        self.assertEqual(out_easy["rating_name"], "Easy")
        self.assertEqual(out_good["rating_name"], "Good")
        self.assertLess(out_easy["retrievability"], 0.8)
        self.assertGreater(out_easy["stability"], out_good["stability"])

    def test_retrievability_decays_with_time(self):
        sched, clock = _scheduler(self)
        _graduate(sched, SKILL, clock)
        # Measure from the last review (the 10-minute step), not T0.
        base = T0 + timedelta(minutes=10)
        seen = []
        for days in (0, 2, 5, 11):
            view = sched.record_for(SKILL, now=base + timedelta(days=days))
            seen.append(view["retrievability"])
        self.assertAlmostEqual(seen[0], 1.0, places=4)
        self.assertAlmostEqual(seen[1], 0.9095, places=3)
        for earlier, later in zip(seen, seen[1:]):
            self.assertLess(later, earlier)


class TestDueQueue(unittest.TestCase):
    def test_lowest_retrievability_first(self):
        sched, clock = _scheduler(self)
        sched.observe("skill-a", correct=True)   # S 2.31, due +10m
        sched.observe("skill-b", correct=False)  # S 0.212, due +1m
        queue = sched.due_queue(now=T0 + timedelta(days=3))
        self.assertEqual([q["skill_id"] for q in queue],
                         ["skill-b", "skill-a"])
        self.assertLess(queue[0]["retrievability"],
                        queue[1]["retrievability"])
        self.assertAlmostEqual(queue[0]["retrievability"], 0.66, places=2)
        self.assertAlmostEqual(queue[1]["retrievability"], 0.881, places=2)

    def test_nothing_due_before_its_due(self):
        sched, _ = _scheduler(self)
        sched.observe(SKILL, correct=True)
        self.assertEqual(sched.due_queue(now=T0), [])

    def test_cold_start_has_no_due_date(self):
        sched, _ = _scheduler(self)
        self.assertIsNone(sched.record_for(SKILL))
        self.assertIsNone(sched.next_due(SKILL))
        self.assertEqual(sched.due_queue(), [])
        self.assertEqual(sched.schedule(), [])

    def test_order_next_due_review_outranks_new_bite(self):
        due = [{"skill_id": "skill-a"}, {"skill_id": "skill-b"}]
        ready = ["bite-1", "bite-2"]
        self.assertEqual(
            sr.order_next(due, ready),
            [("review", "skill-a"), ("review", "skill-b"),
             ("bite", "bite-1"), ("bite", "bite-2")])
        self.assertEqual(sr.order_next([], ready),
                         [("bite", "bite-1"), ("bite", "bite-2")])
        self.assertEqual(sr.order_next(due, []),
                         [("review", "skill-a"), ("review", "skill-b")])

    def test_due_queue_limit(self):
        sched, clock = _scheduler(self)
        sched.observe("skill-a", correct=True)
        sched.observe("skill-b", correct=False)
        queue = sched.due_queue(now=T0 + timedelta(days=3), limit=1)
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["skill_id"], "skill-b")


class TestNeverGradesNeverWritesMastery(unittest.TestCase):
    def test_observe_leaves_the_u1_record_untouched(self):
        store = _tmp_store(self)
        model = sm.StudentModel(store)
        model.record_graded(SKILL, True, novel=True, hinted=False,
                            confidence=1.0, source="check")
        before = model.get_skill(SKILL)
        sched = sr.ReviewScheduler(store, clock=_Clock(T0))
        sched.observe(SKILL, correct=True)
        sched.observe(SKILL, correct=False)
        after = model.get_skill(SKILL)
        self.assertEqual(before, after)

    def test_scheduler_reads_u1_record_as_display_only(self):
        store = _tmp_store(self)
        model = sm.StudentModel(store)
        model.record_graded(SKILL, True, novel=True, hinted=False,
                            confidence=1.0, source="check")
        sched = sr.ReviewScheduler(store, clock=_Clock(T0))
        sched.observe(SKILL, correct=True)
        view = sched.record_for(SKILL, mastery=model.get_skill(SKILL))
        self.assertAlmostEqual(view["mastery_p"],
                               model.get_skill(SKILL)["p"], places=4)
        self.assertFalse(view["mastered"])
        # The mastery record itself is untouched by the read.
        self.assertEqual(model.get_skill(SKILL)["evidence"], 1)

    def test_no_record_graded_call_in_the_module(self):
        import ast
        path = Path(__file__).resolve().parent.parent / "core" / "brain" \
            / "spaced_repetition.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "record_graded"]
        self.assertEqual(calls, [])


class TestPersistenceAndRetention(unittest.TestCase):
    def test_stability_difficulty_due_survive_a_reload(self):
        store = _tmp_store(self)
        first = sr.ReviewScheduler(store, clock=_Clock(T0))
        out = first.observe(SKILL, correct=True)
        second = sr.ReviewScheduler(store, clock=_Clock(T0))
        view = second.record_for(SKILL)
        self.assertAlmostEqual(view["stability"], out["stability"])
        self.assertAlmostEqual(view["difficulty"], out["difficulty"])
        self.assertEqual(view["due"], out["due"])
        self.assertEqual(view["reviews"], 1)

    def test_desired_retention_moves_dues_earlier(self):
        # Two reviews (graduate to Review), then compare dues: the
        # first review is a learning step in both cases, so the
        # retention target only shows once the interval applies.
        clock90 = _Clock(T0)
        clock95 = _Clock(T0)
        s90, _ = _scheduler(self, retention=0.9, clock=clock90)
        s95, _ = _scheduler(self, retention=0.95, clock=clock95)
        _graduate(s90, SKILL, clock90)
        _graduate(s95, SKILL, clock95)
        self.assertEqual(s90.record_for(SKILL)["due"],
                         "2026-11-10T12:10:00+00:00")
        self.assertEqual(s95.record_for(SKILL)["due"],
                         "2026-11-09T12:10:00+00:00")

    def test_fuzz_off_is_deterministic(self):
        first, _ = _scheduler(self)
        second, _ = _scheduler(self)
        for _ in range(3):
            first.observe(SKILL, correct=True)
            second.observe(SKILL, correct=True)
        self.assertEqual(first.record_for(SKILL)["due"],
                         second.record_for(SKILL)["due"])
        self.assertEqual(first.record_for(SKILL)["stability"],
                         second.record_for(SKILL)["stability"])


class TestGuards(unittest.TestCase):
    def test_low_confidence_records_nothing(self):
        sched, _ = _scheduler(self)
        out = sched.observe(SKILL, correct=True, confidence=0.5)
        self.assertTrue(out["ok"])
        self.assertFalse(out["recorded"])
        self.assertEqual(out["reason"], "low_confidence")
        self.assertIsNone(sched.record_for(SKILL))
        # A low-confidence hearing after a review also records nothing.
        sched.observe(SKILL, correct=True, confidence=1.0)
        out2 = sched.observe(SKILL, correct=False, confidence=0.0)
        self.assertFalse(out2["recorded"])
        self.assertEqual(sched.record_for(SKILL)["reviews"], 1)

    def test_invalid_inputs_never_raise(self):
        sched, _ = _scheduler(self)
        out = sched.observe(None, correct=True)
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "invalid_skill_id")
        out = sched.observe("   ", correct=True)
        self.assertFalse(out["ok"])
        out = sched.observe(SKILL, correct="yes")
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "invalid_correct")
        self.assertIsNone(sched.record_for(SKILL))
        self.assertIsNone(sched.record_for(None))
        self.assertIsNone(sched.record_for(""))

    def test_corrupt_row_is_a_cold_start(self):
        store = _tmp_store(self)
        store.save_fact("spaced_repetition", SKILL, "{not json")
        sched = sr.ReviewScheduler(store, clock=_Clock(T0))
        self.assertIsNone(sched.record_for(SKILL))
        out = sched.observe(SKILL, correct=True)
        self.assertTrue(out["recorded"])
        self.assertEqual(out["rating_name"], "Good")

    def test_valid_json_missing_stability_is_a_cold_start(self):
        # A row that parses but is not a real scheduler record (no
        # stability, no last review) must be a cold start — and the
        # due queue must not crash sorting its retrievability.
        store = _tmp_store(self)
        store.save_fact("spaced_repetition", SKILL, json.dumps({
            "skill_id": SKILL, "version": 1,
            "card": {"card_id": 1, "state": 2, "step": None,
                     "stability": None, "difficulty": None,
                     "due": "2026-11-08T12:00:00+00:00",
                     "last_review": None},
            "reviews": 0, "lapses": 0}))
        sched = sr.ReviewScheduler(store, clock=_Clock(T0))
        self.assertIsNone(sched.record_for(SKILL))
        self.assertEqual(sched.due_queue(), [])
        out = sched.observe(SKILL, correct=True)
        self.assertTrue(out["recorded"])
        self.assertEqual(out["rating_name"], "Good")

    def test_clear_all_deletes_only_scheduler_facts(self):
        store = _tmp_store(self)
        model = sm.StudentModel(store)
        model.record_graded(SKILL, True, novel=True, hinted=False,
                            confidence=1.0, source="check")
        store.save_fact("diagnosis_unclassified", "x", "{}")
        sched = sr.ReviewScheduler(store, clock=_Clock(T0))
        sched.observe(SKILL, correct=True)
        sched.observe(OTHER, correct=False)
        self.assertEqual(sched.clear_all(), 2)
        self.assertEqual(sched.schedule(), [])
        self.assertIsNotNone(model.get_skill(SKILL))
        self.assertEqual(model.get_skill(SKILL)["evidence"], 1)
        self.assertIsNotNone(
            store.get_fact("diagnosis_unclassified", "x"))

    def test_review_counts(self):
        sched, _ = _scheduler(self)
        sched.observe(SKILL, correct=True)
        sched.observe(SKILL, correct=False)
        sched.observe(SKILL, correct=True)
        view = sched.record_for(SKILL)
        self.assertEqual(view["reviews"], 3)
        self.assertEqual(view["lapses"], 1)

    def test_review_state_again_goes_to_relearning(self):
        sched, clock = _scheduler(self)
        _graduate(sched, SKILL, clock)
        clock.advance(days=2)
        out = sched.observe(SKILL, correct=False, now=clock())
        self.assertEqual(out["state"], 3)  # Relearning
        self.assertEqual(out["due"], "2026-11-10T12:20:00+00:00")


if __name__ == "__main__":
    unittest.main()
