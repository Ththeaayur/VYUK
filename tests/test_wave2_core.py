"""Wave 2 core tests (stdlib unittest, no GUI, no network, no model).

Covers J1, J7, J9, J13, voice foundations, control hardening.
"""

import unittest

from core.desktop import control_hardening as ctrl  # Phase 3 D2b INTENTIONAL: control_hardening moved to core/desktop/
from core import this_resolution as j1
from core import interruption_budget as j7
from core.memory import playbook_store
# Phase 4 D2 INTENTIONAL: block removed with the deleted module (see refactor_phase4_deadcode_plan.md §4.1).
from core import voice_foundations as vf


class J1Tests(unittest.TestCase):
    def test_focused_wins(self):
        r = j1.resolve_this({"focused_control": {"label": "Save", "control_id": "c1"},
                             "selected_item": {"label": "Doc", "control_id": "c2"}})
        self.assertTrue(r.resolved)
        self.assertEqual(r.candidate.label, "Save")

    def test_selected_when_no_focus(self):
        r = j1.resolve_this({"selected_item": {"label": "Doc", "control_id": "c2"}})
        self.assertTrue(r.resolved)
        self.assertEqual(r.candidate.kind, "item")

    def test_recent_most_recent_first(self):
        r = j1.resolve_this({"recent_entities": [{"label": "New"}, {"label": "Old"}]})
        self.assertEqual(r.candidate.label, "New")

    def test_window_fallback(self):
        r = j1.resolve_this({"active_window": {"label": "Notepad"}})
        self.assertEqual(r.candidate.kind, "window")

    def test_no_candidate_is_unresolved_not_guessed(self):
        r = j1.resolve_this({})
        self.assertFalse(r.resolved)
        self.assertIsNone(r.candidate)
        self.assertTrue(j1.is_ambiguous({}))


class J7Tests(unittest.TestCase):
    def test_pause_resume_finish(self):
        run = j7.InterruptionBudget(budget=2).new_run("open app")
        self.assertEqual(run.interrupt("user spoke")["state"], "paused")
        self.assertEqual(run.resume()["state"], "resumed")
        self.assertEqual(run.finish()["state"], "done")

    def test_budget_exceeded_abandons(self):
        run = j7.InterruptionBudget(budget=1).new_run("goal")
        run.interrupt("one")
        result = run.interrupt("two")
        self.assertEqual(result["state"], "abandoned")
        self.assertIn("budget exceeded", result["report"])

    def test_held_back_listed(self):
        run = j7.InterruptionBudget(budget=3).new_run("goal")
        run.interrupt("pause", held_item="step 3")
        self.assertIn("step 3", run.held_back)
        self.assertIn("step 3", run.finish()["report"])

    def test_abandoned_cannot_resume(self):
        run = j7.InterruptionBudget(budget=1).new_run("goal")
        run.abandon("owner stopped")
        self.assertFalse(run.resume()["changed"])


class J9Tests(unittest.TestCase):
    def test_unproven_not_reusable(self):
        pb = playbook_store.PlaybookStore()
        pb.add("morning", [{"tool": "open_app", "description": "open mail"}])
        self.assertFalse(pb.reuse("morning")["ok"])

    def test_proven_reusable(self):
        pb = playbook_store.PlaybookStore()
        pb.add("morning", [{"tool": "open_app", "description": "open mail"}],
               proof_job_id="job-1")
        result = pb.reuse("morning")
        self.assertTrue(result["ok"])
        self.assertEqual(result["proof_job_id"], "job-1")

    def test_deletion_refused(self):
        pb = playbook_store.PlaybookStore()
        result = pb.add("bad", [{"tool": "delete_file", "description": "delete x"}])
        self.assertFalse(result["added"])
        self.assertIn("NOT built", result["reason"])
        self.assertFalse(pb.reuse("bad")["ok"])

    def test_set_proof_enables_reuse(self):
        pb = playbook_store.PlaybookStore()
        pb.add("x", [{"tool": "open_app", "description": "open"}])
        self.assertTrue(pb.set_proof("x", "job-9"))
        self.assertTrue(pb.reuse("x")["ok"])


# Phase 4 D2 INTENTIONAL: block removed with the deleted module (see refactor_phase4_deadcode_plan.md §4.1).


class VoiceFoundationTests(unittest.TestCase):
    def test_vetoed_refused(self):
        self.assertFalse(vf.veto_check("Charon voice")["ok"])
        self.assertFalse(vf.veto_check("earcon family")["ok"])
        self.assertFalse(vf.veto_check("F6 signature")["ok"])
        self.assertTrue(vf.veto_check("plain voice")["ok"])

    def test_split_only_on_blank_lines(self):
        segs = vf.split_commands(["hello world", "", "open mail"])
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["text"], "hello world")

    def test_uncertain_without_threshold_confirms(self):
        d = vf.confirm_if_uncertain("open mail", 0.99, None)
        self.assertEqual(d["decision"], "confirm")

    def test_below_threshold_confirms_above_accepts(self):
        self.assertEqual(vf.confirm_if_uncertain("x", 0.5, 0.8)["decision"], "confirm")
        self.assertEqual(vf.confirm_if_uncertain("x", 0.9, 0.8)["decision"], "accept")

    def test_speaker_label_never_identity(self):
        self.assertEqual(vf.speaker_label({}), "unknown-speaker")
        self.assertEqual(vf.speaker_label({"distinguishing_feature": "low-pitch"}),
                         "speaker:low-pitch")


class ControlHardeningTests(unittest.TestCase):
    def test_coordinates_only_refused(self):
        self.assertFalse(ctrl.use_control(None, [], coordinates=(500, 300))["ok"])

    def test_revalidated_ok(self):
        rec = ctrl.ControlRecord("c1", "Save", "fp1")
        result = ctrl.use_control(rec, [{"control_id": "c1", "fingerprint": "fp1"}])
        self.assertTrue(result["ok"])

    def test_stale_fingerprint_refused(self):
        rec = ctrl.ControlRecord("c1", "Save", "fp1")
        result = ctrl.use_control(rec, [{"control_id": "c1", "fingerprint": "fp2"}])
        self.assertFalse(result["ok"])
        self.assertIn("stale", result["reason"])

    def test_absent_from_snapshot_refused(self):
        rec = ctrl.ControlRecord("c1", "Save", "fp1")
        result = ctrl.use_control(rec, [{"control_id": "c9", "fingerprint": "fp1"}])
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
