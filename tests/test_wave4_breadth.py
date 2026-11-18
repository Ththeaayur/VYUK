"""Wave 4 breadth tests, part 1: CTRL, VIS, AUDIO, MISS, PLAN.
Stdlib unittest, no GUI, no network, no capture.
"""

import unittest

from core import (audio_breadth as audio,
                  mission_contracts as mission, plan_breadth as plan,
                  visual_breadth as visual)
from core.desktop import control_breadth as ctrl  # Phase 3 D2b INTENTIONAL: control_breadth moved to core/desktop/
from core.desktop.control_hardening import ControlRecord  # Phase 3 D2b INTENTIONAL: control_hardening moved to core/desktop/

SNAP = [{"control_id": "btn-ok", "name": "OK", "role": "button",
         "automation_id": "okBtn", "fingerprint": "fp1"},
        {"control_id": "txt-name", "name": "Name", "role": "textbox",
         "automation_id": "nameBox", "fingerprint": "fp2"}]


class CtrlTests(unittest.TestCase):
    def test_locator_unique_match(self):
        loc = ctrl.AccessibilityLocator(role="button", name="OK")
        res = ctrl.resolve_locator(loc, SNAP)
        self.assertTrue(res["ok"])
        self.assertEqual(res["record"].control_id, "btn-ok")

    def test_locator_ambiguous_refused(self):
        loc = ctrl.AccessibilityLocator(role="button")
        # Add a second button to force ambiguity.
        snap = SNAP + [{"control_id": "btn-x", "name": "X", "role": "button",
                        "fingerprint": "fp3"}]
        self.assertFalse(ctrl.resolve_locator(loc, snap)["ok"])

    def test_locator_empty_refused(self):
        self.assertFalse(ctrl.resolve_locator(ctrl.AccessibilityLocator(),
                                              SNAP)["ok"])

    def test_reuse_revalidates_and_refuses_stale(self):
        rec = ControlRecord("btn-ok", "OK", "fp1")
        self.assertTrue(ctrl.reuse_control(rec, SNAP)["ok"])
        stale = [{"control_id": "btn-ok", "name": "OK", "fingerprint": "fp9"}]
        self.assertFalse(ctrl.reuse_control(rec, stale)["ok"])
        self.assertFalse(ctrl.reuse_control(None, SNAP,
                                            coordinates=(10, 20))["ok"])

    def test_coordinates_only_refused(self):
        res = ctrl.reuse_control(None, SNAP, coordinates=(1, 2))
        self.assertIn("coordinates-only", res["reason"])


class VisualTests(unittest.TestCase):
    def test_difference_explanation(self):
        before = visual.Frame("f1", SNAP)
        after = visual.Frame("f2", [SNAP[0],
                                    {"control_id": "btn-new", "name": "New",
                                     "fingerprint": "fp4"}])
        diff = visual.explain_difference(before, after)
        self.assertEqual(diff["added"], ["btn-new"])
        self.assertEqual(diff["removed"], ["txt-name"])
        self.assertEqual(diff["changed"], [])

    def test_memory_revalidates_and_drops_stale(self):
        mem = visual.ElementMemory()
        mem.remember(ControlRecord("btn-ok", "OK", "fp1"))
        self.assertTrue(mem.recall("btn-ok", visual.Frame("f1", SNAP))["ok"])
        stale_frame = visual.Frame("f2", [{"control_id": "btn-ok",
                                           "fingerprint": "fp9"}])
        self.assertFalse(mem.recall("btn-ok", stale_frame)["ok"])
        self.assertEqual(mem.known_ids(), [])


class AudioTests(unittest.TestCase):
    def test_split_commands_and_interrupt(self):
        res = audio.split_commands(
            "[command]open notes[/command] [command]save[interrupt]")
        self.assertEqual(res["commands"], ["open notes"])
        self.assertTrue(res["interrupted"])

    def test_unterminated_is_partial_not_command(self):
        res = audio.split_commands("[command]half a thought")
        self.assertEqual(res["commands"], [])
        self.assertIsNotNone(res["partial"])

    def test_uncertain_needs_confirmation(self):
        res = audio.gate_recognition("save", 0.4, threshold=0.7)
        self.assertFalse(res["ok"])
        self.assertTrue(res["confirm"])
        self.assertTrue(audio.gate_recognition("save", 0.9)["ok"])

    def test_invalid_confidence_refused(self):
        self.assertFalse(audio.gate_recognition("save", 1.5)["ok"])

    def test_speaker_label_only_when_distinguishable(self):
        self.assertFalse(audio.speaker_label(
            1, reliably_distinguishable=False)["ok"])
        res = audio.speaker_label(2, reliably_distinguishable=True)
        self.assertEqual(res["label"], "speaker-2")


class MissionTests(unittest.TestCase):
    def test_dry_run_flags_missing_precondition(self):
        contract = mission.MissionContract(
            goal="send note", preconditions=("app open",),
            postconditions=("note saved",), checkpoints=("after draft",))
        steps = [{"name": "draft", "requires": ["app open"],
                  "ensures": ["draft ready"]},
                 {"name": "save", "requires": ["draft ready"],
                  "ensures": ["note saved"]}]
        res = mission.dry_run(contract, steps)
        self.assertTrue(res["ok"])
        self.assertEqual(res["planned"], ["draft", "save"])
        bad = mission.dry_run(contract, [{"name": "send",
                                          "requires": ["logged in"]}])
        self.assertFalse(bad["ok"])
        self.assertTrue(bad["recovery_preview"])

    def test_risk_severity_validated(self):
        contract = mission.MissionContract(goal="g")
        res = mission.dry_run(contract, [], risks={"x": "extreme"})
        self.assertFalse(res["ok"])

    def test_binder_append_only_and_named_checks(self):
        binder = mission.EvidenceBinder()
        self.assertFalse(binder.record("", "pass")["ok"])
        binder.record("draft check", "pass")
        binder.record("save check", "fail", "file locked")
        summary = binder.summary()
        self.assertEqual((summary["passed"], summary["failed"]), (1, 1))


class PlanTests(unittest.TestCase):
    def test_calibrate_is_minimum(self):
        steps = [plan.PlanStep("a", 0.9), plan.PlanStep("b", 0.6),
                 plan.PlanStep("c", 0.8)]
        res = plan.calibrate(steps)
        self.assertEqual(res["plan_confidence"], 0.6)
        self.assertEqual(res["weakest_steps"], ["b"])

    def test_invalid_confidence_raises(self):
        with self.assertRaises(ValueError):
            plan.PlanStep("a", 1.4)

    def test_decompose_needs_names(self):
        self.assertFalse(plan.decompose("g", [])["ok"])
        self.assertFalse(plan.decompose("", ["a"])["ok"])

    def test_constraint_violations_flagged(self):
        steps = [{"name": "s1", "violates": ["no-network"]}]
        res = plan.check_constraints(steps, {"no-network": True})
        self.assertFalse(res["ok"])
        self.assertEqual(res["violations"][0]["constraint"], "no-network")


if __name__ == "__main__":
    unittest.main()
