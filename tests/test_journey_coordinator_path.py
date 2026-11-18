"""Journey 3 — coordinator path (whole-path phase).

A goal with declared steps + expects runs action loop -> verifying
phase -> receipt, through the REAL funnel executor and the REAL
ledger, with receipt text. The wiring suite proved the phase with
fake executors; this journey proves the whole path with a real
action (get_current_time_date, read-class, works headless):

- met: the verifier checks the REAL result text (current year
  present) -> task completes, verification met, receipt text says so;
- not-met: the verifier checks the REAL result text (year 1999
  absent) -> task FAILS honestly, reason names step + expectation +
  verifier reason, mission receipt failed with verification evidence;
- verifying visible: during the phase the task, ledger, and
  Activity render all read "verifying"/"Verifying".

The verifier is the coordinator's designed injection seam
(TurnCoordinator takes verifier=); the REAL visual verifier needs
eyes (a screen + a groundable expectation), so its met/not-met
verdicts on a real desktop are field-owed. What is proven here is
the coordinator flow itself: real action -> phase -> receipt ->
ledger, with verdicts computed from real action output, never
guessed.

Stdlib unittest only. Ledger uses temp paths, never working data/.
"""

from __future__ import annotations

import datetime
import tempfile
import unittest
from pathlib import Path

try:
    from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
    from core.funnel import tools_registry as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
    from core.brain.task_ledger import TaskLedger  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
    from gui.task_state import render_task
    from tests._journey_base import FunnelBase
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    raise unittest.SkipTest(f"core modules unavailable: {exc}")


def _real_executor():
    def run(tool, args):
        return tools_mod.tools.execute(tool, dict(args))
    return run


class _YearVerifier:
    """Verdict from the REAL result text, never a canned answer."""

    def __init__(self, hook=None):
        self.calls = []
        self.hook = hook

    def __call__(self, expect, before, after, result_text):
        self.calls.append({"expect": expect, "before": before,
                           "after": after, "result": result_text})
        if self.hook is not None:
            self.hook()
        year = str(datetime.datetime.now().year)
        if "1999" in expect:
            if "1999" in (result_text or ""):
                return ("met", "1999 present in result")
            return ("not-met", "1999 absent from result")
        if year in (result_text or ""):
            return ("met", f"current year {year} present in result")
        return ("not-met", f"current year {year} absent from result")


class _Snapshot:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return {"n": self.calls}


class TestCoordinatorJourney(FunnelBase):
    def _coord(self, verifier, ledger):
        return tc.TurnCoordinator(
            executor=_real_executor(), verifier=verifier,
            snapshot=_Snapshot(), ledger=ledger)

    def _ledger(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return TaskLedger(path=Path(tmp.name) / "ledger.json")

    def test_met_path_completes_with_receipt_and_ledger(self):
        ledger = self._ledger()
        coord = self._coord(_YearVerifier(), ledger)
        task = coord.new_task(
            "t", "saved_plan",
            [tc.StepDef.from_mapping(
                {"tool": "get_current_time_date", "args": {},
                 "expect": "the result names the current year",
                 "label": "step time"})])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(receipt.verification), 1)
        self.assertEqual(receipt.verification[0]["verdict"], "met")
        self.assertEqual(receipt.verification[0]["label"], "step time")
        text = tc.format_receipt(receipt)
        self.assertIn("Verification: step time — met", text)
        self.assertEqual(ledger.get(task.id)["state"], "completed")

    def test_not_met_fails_honestly(self):
        ledger = self._ledger()
        coord = self._coord(_YearVerifier(), ledger)
        task = coord.new_task(
            "t", "saved_plan",
            [tc.StepDef.from_mapping(
                {"tool": "get_current_time_date", "args": {},
                 "expect": "the result names the year 1999",
                 "label": "step time"})])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.completed, 1)
        self.assertIn("verification failed", receipt.reason)
        self.assertIn("step time", receipt.reason)
        self.assertIn("the result names the year 1999", receipt.reason)
        self.assertIn("1999 absent from result", receipt.reason)
        self.assertEqual(receipt.verification[0]["verdict"], "not-met")
        text = tc.format_receipt(receipt)
        self.assertIn("Verification: step time — not-met", text)
        mission = coord.to_mission_receipt(task)
        self.assertEqual(mission.status, "failed")
        self.assertTrue(any("verification: step time — not-met" in e["detail"]
                            for e in mission.evidence))

    def test_verifying_visible_while_phase_runs(self):
        seen = {}
        ledger = self._ledger()
        coord = self._coord(_YearVerifier(), ledger)
        task = coord.new_task(
            "t", "saved_plan",
            [tc.StepDef.from_mapping(
                {"tool": "get_current_time_date", "args": {},
                 "expect": "the result names the current year"})])

        def hook():
            # T-14 (debug pass): the verifier hook fires TWICE —
            # once during the step phase (state "verifying") and
            # once during the goal check (state "completed"). The
            # old hook overwrote `seen` on every call, so it
            # observed only the LAST call (the goal check) and
            # asserted "verifying" against "completed". Record
            # the FIRST call — the phase this test is about.
            if "task_state" not in seen:
                seen["task_state"] = task.state
                seen["ledger_state"] = ledger.get(task.id)["state"]
                seen["rendered"] = render_task(ledger.get(task.id))["state"]

        coord._verifier = _YearVerifier(hook=hook)
        coord._snapshot = _Snapshot()
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(seen["task_state"], "verifying")
        self.assertEqual(seen["ledger_state"], "verifying")
        self.assertEqual(seen["rendered"], "Verifying")


if __name__ == "__main__":
    unittest.main()
