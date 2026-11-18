"""U5/U6/U7/U8-style suite for U9 — the tutor progress report.

Lab leg only. The real-Windows field leg — a real MA 105 progress
report on his machine — is OWED, not this batch.

What this suite proves: the assembly reads the records (seeded
mastery/schedule/diagnosis numbers appear exactly); weak skills are
shown, never hidden; hinted answers are never shown as transfers;
low-confidence hearings are never shown as wrong; unclassified
diagnoses are shown as "not yet named" with their counts, never
forced into a category; named library entries are listed WITHOUT
counts (classified counts are not recorded anywhere — see the named
residual in progress_report.py); learning speed is computed from
the records' timestamps; the plain-text and GUI renderings agree;
the clear action deletes ONLY tutor records and reports exactly
what it deleted; the report never grades and never writes.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.brain import progress_report as pr
from core.brain import spaced_repetition as sr
from core.brain import student_model as sm
from core.brain.error_diagnosis import Diagnoser, MisconceptionLibrary
from core.memory import MemoryStore

try:
    from PySide6.QtWidgets import QApplication, QWidget

    from gui.progress_report_panel import ProgressReportPanel

    _GUI = True
except ImportError:  # noqa: BLE001 — system python3 has no PySide6;
    # the GUI tests skip there and run on the Qt venv.
    _GUI = False

SKILL_A = "ma105-double-integrals-limits-from-region"
SKILL_B = "ma105-regions-and-limits"
SKILL_C = "ma105-stokes-theorem"
SOURCE_A = "MA 105 L13 slides, p. 4"

T0 = datetime(2026, 11, 8, 10, 0, 0, tzinfo=timezone.utc)


class _Clock:
    """Fake clock: the tests never wait in real time."""

    def __init__(self, start):
        self._now = start

    def __call__(self):
        return self._now

    def advance(self, **kwargs):
        self._now = self._now + timedelta(**kwargs)


def _tmp_store(testcase):
    tmp = Path(tempfile.mkdtemp())
    testcase.addCleanup(lambda: _rmtree(tmp))
    return MemoryStore(tmp / "assistant.db")


def _rmtree(path):
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _seed_mastery(store, skill, *, correct=1, wrong=0, hinted_correct=0,
                  low_confidence=0):
    """Seed one skill's mastery record through record_graded."""
    model = sm.StudentModel(store)
    for _ in range(correct):
        model.record_graded(skill, True, novel=True, hinted=False)
    for _ in range(wrong):
        model.record_graded(skill, False, novel=True, hinted=False)
    for _ in range(hinted_correct):
        model.record_graded(skill, True, novel=True, hinted=True)
    for _ in range(low_confidence):
        model.record_graded(skill, True, novel=True, hinted=False,
                            confidence=0.5)
    return model


def _seed_schedule(store, skill, *, correct=True, clock=None):
    """Seed one skill's FSRS record through observe."""
    sched = sr.ReviewScheduler(store, clock=clock or _Clock(T0))
    sched.observe(skill, correct=correct, now=None)
    return sched


def _seed_unclassified(store, skill, count=1, answer="5"):
    """Seed unclassified errors through diagnose (a skill with no
    library entries matches nothing, so every handover is
    unclassified)."""
    diagnoser = Diagnoser(MisconceptionLibrary(), store=store)
    for _ in range(count):
        outcome = {"skill": skill, "attempts": 3,
                   "grade": {"reason": "unmatched", "method": "exact"},
                   "reinforce": SKILL_B}
        result = diagnoser.diagnose(outcome, answer=answer, gold="7/3")
        assert result["status"] == "unclassified", result
    return diagnoser


def _facts_snapshot(store):
    rows = store.get_all_facts()
    return sorted((r["category"], r["key"], r["value"]) for r in rows)


class TestAssemble(unittest.TestCase):
    def test_assembly_reads_records_exactly(self):
        store = _tmp_store(self)
        _seed_mastery(store, SKILL_A, correct=2, wrong=1)
        _seed_mastery(store, SKILL_B, correct=1)
        _seed_schedule(store, SKILL_A, clock=_Clock(T0))
        report = pr.assemble_report(store, now=T0)
        self.assertTrue(report["ok"])
        self.assertEqual(report["totals"]["skills"], 2)
        self.assertEqual(report["totals"]["evidence"], 4)
        self.assertEqual(report["totals"]["correct"], 3)
        self.assertEqual(report["totals"]["wrong"], 1)
        self.assertEqual(report["totals"]["transfers"], 3)
        row_a = next(s for s in report["skills"]
                     if s["skill_id"] == SKILL_A)
        self.assertEqual(row_a["evidence"], 3)
        self.assertEqual(row_a["correct"], 2)
        self.assertEqual(row_a["wrong"], 1)
        self.assertEqual(row_a["transfers"], 2)
        self.assertTrue(row_a["mastered"])
        self.assertIsNotNone(row_a["due"])
        self.assertIsNotNone(row_a["stability"])
        row_b = next(s for s in report["skills"]
                     if s["skill_id"] == SKILL_B)
        self.assertEqual(row_b["transfers"], 1)
        self.assertFalse(row_b["mastered"])
        self.assertIsNone(row_b["due"])

    def test_weak_skills_shown_never_hidden(self):
        store = _tmp_store(self)
        _seed_mastery(store, SKILL_A, correct=0, wrong=3)
        report = pr.assemble_report(store, now=T0)
        self.assertEqual(len(report["weakest"]), 1)
        self.assertEqual(report["weakest"][0]["skill_id"], SKILL_A)
        self.assertLess(report["weakest"][0]["p"], 0.6)
        text = pr.render_text(report)
        self.assertIn(SKILL_A, text)
        self.assertIn("Weakest skills", text)

    def test_hinted_never_shown_as_transfer(self):
        store = _tmp_store(self)
        _seed_mastery(store, SKILL_A, correct=0, hinted_correct=2)
        report = pr.assemble_report(store, now=T0)
        row = report["skills"][0]
        self.assertEqual(row["evidence"], 2)
        self.assertEqual(row["transfers"], 0)
        self.assertFalse(row["mastered"])
        self.assertEqual(report["totals"]["transfers"], 0)
        self.assertNotIn("1 clean transfer", pr.render_text(report))

    def test_low_confidence_never_shown_wrong(self):
        store = _tmp_store(self)
        _seed_mastery(store, SKILL_A, correct=1, low_confidence=2)
        report = pr.assemble_report(store, now=T0)
        row = report["skills"][0]
        # The two low-confidence hearings were never recorded.
        self.assertEqual(row["evidence"], 1)
        self.assertEqual(row["wrong"], 0)
        self.assertEqual(report["totals"]["wrong"], 0)

    def test_unclassified_shown_not_yet_named(self):
        store = _tmp_store(self)
        _seed_unclassified(store, SKILL_C, count=2)
        _seed_unclassified(store, SKILL_A, count=1)
        report = pr.assemble_report(store, now=T0)
        misconceptions = report["misconceptions"]
        self.assertEqual(misconceptions["unclassified_total"], 3)
        by_skill = {g["skill"]: g["count"]
                    for g in misconceptions["unclassified"]}
        self.assertEqual(by_skill[SKILL_C], 2)
        self.assertEqual(by_skill[SKILL_A], 1)
        text = pr.render_text(report)
        self.assertIn("Not yet named", text)
        self.assertIn(SKILL_C, text)

    def test_named_library_listed_without_counts(self):
        store = _tmp_store(self)
        library = MisconceptionLibrary()
        report = pr.assemble_report(store, now=T0, library=library)
        named = report["misconceptions"]["named"]
        self.assertTrue(len(named) >= 2)
        self.assertIn("ma105-dropped-factor", [e["id"] for e in named])
        for entry in named:
            self.assertNotIn("count", entry)
        self.assertFalse(report["misconceptions"]
                         ["named_counts_recorded"])
        text = pr.render_text(report)
        self.assertIn("counts not recorded", text)
        self.assertIn("dropped factor", text.lower())

    def test_learning_speed_from_record_timestamps(self):
        store = _tmp_store(self)
        _seed_mastery(store, SKILL_A, correct=2)
        _seed_mastery(store, SKILL_B, correct=1)
        # Rewrite the two student rows with chosen updated_at values
        # (same JSON shape, three calendar days apart).
        for skill, stamp in ((SKILL_A, "2026-11-06T10:00:00+00:00"),
                             (SKILL_B, "2026-11-08T10:00:00+00:00")):
            row = store.get_fact("student_model", skill)
            record = json.loads(row["value"])
            record["updated_at"] = stamp
            self.assertTrue(store.save_fact(
                "student_model", skill, json.dumps(record, sort_keys=True)))
        _seed_unclassified(store, SKILL_C, count=1)
        row = next(r for r in store.get_all_facts()
                   if r["category"] == "diagnosis_unclassified")
        record = json.loads(row["value"])
        record["recorded_at"] = "2026-11-07T10:00:00+00:00"
        self.assertTrue(store.save_fact(
            "diagnosis_unclassified", row["key"],
            json.dumps(record, sort_keys=True)))
        report = pr.assemble_report(store, now=T0)
        speed = report["learning_speed"]
        self.assertEqual(speed["span_start"], "2026-11-06")
        self.assertEqual(speed["span_end"], "2026-11-08")
        self.assertEqual(speed["span_days"], 3)
        # 3 evidence over 3 days; 3 transfers over 3 days.
        self.assertEqual(speed["evidence_per_day"], 1.0)
        self.assertEqual(speed["transfers_per_day"], 1.0)

    def test_learning_speed_empty_store(self):
        store = _tmp_store(self)
        report = pr.assemble_report(store, now=T0)
        self.assertTrue(report["ok"])
        speed = report["learning_speed"]
        self.assertIsNone(speed["span_days"])
        self.assertIsNone(speed["evidence_per_day"])
        self.assertIsNone(speed["transfers_per_day"])
        self.assertIn("no record yet", pr.render_text(report))

    def test_due_now_and_due_24h(self):
        store = _tmp_store(self)
        clock = _Clock(T0)
        _seed_schedule(store, SKILL_A, clock=clock)
        sched = sr.ReviewScheduler(store, clock=clock)
        due = datetime.fromisoformat(
            sched.record_for(SKILL_A)["due"])
        early = pr.assemble_report(store, now=due - timedelta(hours=12))
        self.assertIn(SKILL_A, early["due_24h"])
        self.assertNotIn(SKILL_A, early["due_now"])
        late = pr.assemble_report(store, now=due + timedelta(hours=1))
        self.assertIn(SKILL_A, late["due_now"])
        self.assertIn(SKILL_A, late["due_24h"])

    def test_sources_attributed_or_not_recorded(self):
        store = _tmp_store(self)
        _seed_mastery(store, SKILL_A, correct=1)
        _seed_mastery(store, SKILL_B, correct=1)
        report = pr.assemble_report(store, now=T0,
                                    sources={SKILL_A: SOURCE_A})
        rows = {s["skill_id"]: s for s in report["skills"]}
        self.assertEqual(rows[SKILL_A]["source"], SOURCE_A)
        self.assertEqual(rows[SKILL_B]["source"], "")
        text = pr.render_text(report)
        self.assertIn(SOURCE_A, text)
        self.assertIn("not recorded", text)

    def test_report_never_writes(self):
        store = _tmp_store(self)
        _seed_mastery(store, SKILL_A, correct=2, wrong=1)
        _seed_schedule(store, SKILL_A, clock=_Clock(T0))
        _seed_unclassified(store, SKILL_C, count=1)
        before = _facts_snapshot(store)
        report = pr.assemble_report(store, now=T0,
                                    library=MisconceptionLibrary())
        pr.render_text(report)
        self.assertEqual(_facts_snapshot(store), before)

    def test_report_never_writes_via_raising_fake(self):
        class _NoWriteStore:
            def __init__(self, real):
                self._real = real

            def get_all_facts(self):
                return self._real.get_all_facts()

            def get_fact(self, category, key):
                return self._real.get_fact(category, key)

            def delete_fact(self, fact_id):
                return self._real.delete_fact(fact_id)

            def save_fact(self, *args):
                raise AssertionError("the report must never write")

        store = _tmp_store(self)
        _seed_mastery(store, SKILL_A, correct=1)
        _seed_schedule(store, SKILL_A, clock=_Clock(T0))
        fake = _NoWriteStore(store)
        report = pr.assemble_report(fake, now=T0)
        self.assertTrue(report["ok"])
        pr.render_text(report)

    def test_bad_store_ok_false(self):
        report = pr.assemble_report(object(), now=T0)
        self.assertFalse(report["ok"])
        self.assertIn("unavailable", pr.render_text(report))
        result = pr.clear_tutor_records(object())
        self.assertFalse(result["ok"])


class TestClear(unittest.TestCase):
    def test_clear_deletes_only_tutor_records(self):
        store = _tmp_store(self)
        _seed_mastery(store, SKILL_A, correct=2)
        _seed_mastery(store, SKILL_B, correct=1)
        _seed_schedule(store, SKILL_A, clock=_Clock(T0))
        _seed_unclassified(store, SKILL_C, count=2)
        self.assertTrue(store.save_fact("general", "note",
                                        "keep this"))
        # A hand-seeded unclassified fact: same category, so the
        # clear must delete it too (the clear deletes by category,
        # not by key).
        self.assertTrue(store.save_fact("diagnosis_unclassified",
                                        "hand-seeded", "x"))
        result = pr.clear_tutor_records(store)
        self.assertTrue(result["ok"])
        # 2 student-model + 1 spaced-repetition + 3 unclassified.
        self.assertEqual(result["deleted"]["student_model"], 2)
        self.assertEqual(result["deleted"]["spaced_repetition"], 1)
        self.assertEqual(result["deleted"]["diagnosis_unclassified"], 3)
        self.assertEqual(result["total"], 6)
        remaining = {(r["category"], r["key"])
                     for r in store.get_all_facts()}
        self.assertEqual(remaining, {("general", "note")})
        # A second clear deletes nothing.
        again = pr.clear_tutor_records(store)
        self.assertEqual(again["total"], 0)
        self.assertEqual(
            {(r["category"], r["key"]) for r in store.get_all_facts()},
            {("general", "note")})

    def test_clear_empty_store(self):
        store = _tmp_store(self)
        result = pr.clear_tutor_records(store)
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 0)


@unittest.skipUnless(_GUI, "PySide6 unavailable on this interpreter")
class TestPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def _report(self):
        store = _tmp_store(self)
        _seed_mastery(store, SKILL_A, correct=2, wrong=1)
        _seed_mastery(store, SKILL_B, correct=1)
        _seed_schedule(store, SKILL_A, clock=_Clock(T0))
        _seed_unclassified(store, SKILL_C, count=2)
        return pr.assemble_report(store, now=T0,
                                  sources={SKILL_A: SOURCE_A},
                                  library=MisconceptionLibrary())

    def test_panel_renders_report(self):
        parent = QWidget()
        parent.show()
        panel = ProgressReportPanel(parent)
        report = self._report()
        panel.show_report(report)
        self.assertTrue(panel.isVisible())
        texts = " ".join(panel.rendered_texts())
        self.assertIn(SKILL_A, texts)
        self.assertIn(SKILL_B, texts)
        self.assertIn("2 clean transfers", texts)
        self.assertIn(SOURCE_A, texts)
        self.assertIn("not recorded", texts)
        self.assertIn(SKILL_C, texts)
        self.assertFalse(panel.isWindow())

    def test_panel_renderings_agree(self):
        parent = QWidget()
        panel = ProgressReportPanel(parent)
        report = self._report()
        text = pr.render_text(report)
        panel.show_report(report)
        joined = " ".join(panel.rendered_texts())
        # Every skill id, every total, and the unclassified skill
        # appear in both renderings.
        for skill_id in (SKILL_A, SKILL_B, SKILL_C):
            self.assertIn(skill_id, text)
            self.assertIn(skill_id, joined)
        self.assertIn("4 evidence", text)
        self.assertIn("4 evidence", joined)
        self.assertIn("3 clean transfers", text)
        self.assertIn("3 clean transfers", joined)
        self.assertIn("Due now:", text)
        self.assertIn("Due now:", joined)

    def test_panel_clear_button(self):
        parent = QWidget()
        panel = ProgressReportPanel(parent)
        self.assertFalse(panel.clear_btn.isEnabled())
        calls = []
        panel.set_clear_handler(lambda: calls.append(1))
        self.assertTrue(panel.clear_btn.isEnabled())
        panel.clear_btn.click()
        self.assertEqual(calls, [1])
        panel.set_clear_handler(None)
        self.assertFalse(panel.clear_btn.isEnabled())

    def test_panel_clear_empties(self):
        parent = QWidget()
        parent.show()
        panel = ProgressReportPanel(parent)
        panel.show_report(self._report())
        self.assertIsNotNone(panel.report())
        panel.clear()
        self.assertIsNone(panel.report())
        self.assertFalse(panel.isVisible())
        self.assertNotIn(SKILL_A, " ".join(panel.rendered_texts()))


if __name__ == "__main__":
    unittest.main()
