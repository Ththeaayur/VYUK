"""Wave 4 breadth tests, part 2: sandbox, continuity, governance, undo,
TOOLS gates, ghost cursor, dev breadth, grounding.
Stdlib unittest, no GUI, no network, no capture, no real execution.
"""

import copy
import unittest

from core import (continuity as cont, dev_breadth as dev,
                  ghost_cursor as ghost, governance as gov,
                  grounding as ground, sandbox_light as sandbox,
                  undo_breadth as undo)
# Phase 4 D2 INTENTIONAL: block removed with the deleted module (see refactor_phase4_deadcode_plan.md §4.1).
from core.desktop.control_hardening import ControlRecord  # Phase 3 D2b INTENTIONAL: control_hardening moved to core/desktop/

SNAP = [{"control_id": "btn-ok", "name": "OK", "fingerprint": "fp1"}]


class SandboxTests(unittest.TestCase):
    def test_write_inside_changes_nothing_outside(self):
        outside = {"a.txt": "original"}
        snap = copy.deepcopy(outside)
        sb = sandbox.LightSandbox(outside)
        sb.write("a.txt", "changed inside")
        sb.write("b.txt", "new inside")
        self.assertTrue(sb.outside_unchanged(snap))
        self.assertEqual(outside, snap)

    def test_promotion_is_request_only_and_copy_only(self):
        sb = sandbox.LightSandbox({"a.txt": "x"})
        self.assertFalse(sb.request_promotion("dir/")["ok"])
        self.assertFalse(sb.request_promotion("*.txt")["ok"])
        res = sb.request_promotion("a.txt")
        self.assertTrue(res["ok"])
        self.assertIn("REQUESTED", res["note"])
        self.assertEqual(sb.promotions(), ["a.txt"])


class ContinuityTests(unittest.TestCase):
    def test_workspace_round_trip_exact(self):
        store = cont.WorkspaceStore()
        state = {"app": "notes", "draft": "hello"}
        self.assertTrue(store.save("work", state)["ok"])
        restored = store.restore("work")
        self.assertEqual(restored["state"], state)
        self.assertFalse(store.restore("missing")["ok"])

    def test_ram_tiers_narrated(self):
        self.assertEqual(cont.tier_for_ram(16384)["tier"], "full")
        self.assertEqual(cont.tier_for_ram(8192)["tier"], "standard")
        self.assertEqual(cont.tier_for_ram(4096)["tier"], "reduced")
        low = cont.tier_for_ram(2048)
        self.assertEqual(low["tier"], "core-only")
        self.assertIn("Core-only", low["narration"])
        self.assertFalse(cont.tier_for_ram(-1)["ok"])
        self.assertFalse(cont.tier_for_ram("8GB")["ok"])

    def test_resume_note_executes_nothing(self):
        note = cont.resume_note("send note", "after draft", ["save"])
        self.assertIn("nothing was re-executed", note["note"])


class GovernanceTests(unittest.TestCase):
    def test_allow_list_and_tier2(self):
        gate = gov.PolicyGate(["open notes", "delete draft"],
                              tier2_actions=["delete draft"])
        self.assertTrue(gate.decide("open notes")["ok"])
        self.assertFalse(gate.decide("delete draft")["ok"])
        self.assertTrue(gate.decide("delete draft", confirmed=True)["ok"])
        self.assertFalse(gate.decide("format disk")["ok"])
        self.assertEqual(len(gate.audit_trail()), 4)

    def test_degraded_mode_narrated(self):
        res = gov.degraded_mode_narration(["globe layers"])
        self.assertIn("degraded mode", res["narration"])


class UndoTests(unittest.TestCase):
    def test_cannot_undo_class_needs_authorisation_first(self):
        u = undo.UniversalUndo()
        refused = u.record_change("message sent to another person", "a", "b")
        self.assertFalse(refused["ok"])
        res = u.record_change("message sent to another person", "a", "b",
                              authorised=True)
        self.assertTrue(res["ok"])
        self.assertFalse(res["undoable"])

    def test_registered_class_undoes_and_reports_failure(self):
        u = undo.UniversalUndo()
        state = {"text": "before"}
        u.register_class("draft edit", lambda before: state.update(before))
        u.record_change("draft edit", {"text": "before"}, {"text": "after"})
        res = u.undo("draft edit")
        self.assertTrue(res["ok"])
        self.assertEqual(state["text"], "before")
        self.assertFalse(u.undo("draft edit")["ok"])

    def test_unregistered_class_not_recorded_undoable(self):
        u = undo.UniversalUndo()
        self.assertFalse(u.record_change("mystery", "a", "b")["ok"])


# Phase 4 D2 INTENTIONAL: block removed with the deleted module (see refactor_phase4_deadcode_plan.md §4.1).


class GhostTests(unittest.TestCase):
    def test_preview_executes_nothing(self):
        rec = ControlRecord("btn-ok", "OK", "fp1")
        res = ghost.preview_intent("click", rec, SNAP)
        self.assertTrue(res["ok"])
        self.assertIn("nothing was executed", res["note"])
        self.assertFalse(ghost.preview_intent("click", None, SNAP)["ok"])

    def test_numbered_marks(self):
        marks = ghost.NumberedMarks(SNAP)
        self.assertEqual(marks.mark(1)["control_id"], "btn-ok")
        self.assertFalse(marks.mark(9)["ok"])

    def test_rewind_flags_consequential(self):
        res = ghost.rewind_dry_run([{"name": "open", "consequential": False},
                                    {"name": "send", "consequential": True}])
        self.assertEqual(res["would_recheck"], ["open"])
        self.assertEqual(res["needs_fresh_confirmation"], ["send"])


class DevTests(unittest.TestCase):
    def test_terminal_allow_list(self):
        self.assertTrue(dev.record_terminal_command("ls", ["ls"])["ok"])
        self.assertFalse(dev.record_terminal_command("rm -rf /", ["ls"])["ok"])

    def test_sandboxed_run_executes_nothing(self):
        res = dev.sandboxed_run("ls", ["ls"])
        self.assertIn("NOT executed", res["verdict"])

    def test_diff_story(self):
        res = dev.tell_diff(["a.py"], ["b.py"], ["c.py"])
        self.assertIn("added: a.py", res["summary"])
        trace = dev.TraceLog()
        self.assertFalse(trace.event("")["ok"])
        trace.event("step", "done")
        self.assertEqual(len(trace.story()), 1)


class GroundingTests(unittest.TestCase):
    def test_ungrounded_claim_shown(self):
        res = ground.ground_claim("it works", [])
        self.assertFalse(res["grounded"])
        grounded = ground.ground_claim("it works", ["log line 12"])
        self.assertTrue(grounded["grounded"])

    def test_calibration_verdicts(self):
        self.assertEqual(ground.calibration_check(0.9, "incorrect")["verdict"],
                         "over-confident")
        self.assertEqual(ground.calibration_check(0.2, "correct")["verdict"],
                         "under-confident")
        self.assertEqual(ground.calibration_check(0.5, "correct")["verdict"],
                         "consistent")

    def test_interruption_budget_holds(self):
        budget = ground.InterruptionBudget(1)
        self.assertTrue(budget.request("first")["ok"])
        held = budget.request("second")
        self.assertFalse(held["ok"])
        self.assertEqual(budget.held_items(), ["second"])
        urgent = budget.request("third", urgent_by_owner_rules=True)
        self.assertTrue(urgent["ok"])
        self.assertEqual(budget.remaining(), 0)


if __name__ == "__main__":
    unittest.main()
