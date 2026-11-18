"""Coordinator verification phase (wiring item 3, v6.0.0).

The coordinator verification phase runs after the action loop
and before the report for tasks carrying step expectations,
using the injected verifier (default: the existing
``core/uia.verify_expectation`` producer — never rebuilt
here). While the phase runs the task's stored state is
"verifying", which is the real verifier producer the D9
producer-or-hide rule requires, so the Activity surface may
honestly render Verifying.

Stdlib unittest only; every verifier/snapshot is a fake except
the one default-engine safety test (headless: the engine has
no eyes here, so an ungroundable expectation can only come
back "unverifiable", never a guessed verdict). Ledger tests
use temp paths, never the working data directory.
"""

import tempfile
import unittest
from pathlib import Path

from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.brain.task_ledger import TaskLedger  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from gui.task_state import render_task


def _ok_executor():
    def run(tool, args):
        run.calls.append(tool)
        return {"status": "success", "kind": "",
                "message": "", "result": f"{tool} done"}
    run.calls = []
    return run


def _fail_executor():
    def run(tool, args):
        return {"status": "failure", "kind": "handler_error",
                "message": f"{tool} failed"}
    return run


class _Verifier:
    """Fake verifier: records calls, returns configured verdicts."""

    def __init__(self, verdicts=None, hook=None):
        self.calls = []
        self.verdicts = verdicts or {}
        self.hook = hook

    def __call__(self, expect, before, after, result_text):
        self.calls.append({"expect": expect, "before": before,
                           "after": after, "result": result_text})
        if self.hook is not None:
            self.hook()
        return self.verdicts.get(expect, ("met", "fake met"))


class _Snapshot:
    """Fake snapshot probe: one numbered marker per call."""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return {"n": self.calls}


def _coord(executor=None, verifier=None, snapshot=None, ledger=None):
    return tc.TurnCoordinator(
        executor=executor or _ok_executor(),
        verifier=verifier, snapshot=snapshot, ledger=ledger)


def _task(coord, steps):
    return coord.new_task(
        "t", "saved_plan",
        [tc.StepDef.from_mapping(m) for m in steps])


class StepDefExpectTests(unittest.TestCase):
    def test_string_expect_kept_and_stripped(self):
        step = tc.StepDef.from_mapping(
            {"tool": "a", "args": {},
             "expect": "  a dialog titled X appears  "})
        self.assertEqual(step.expect, "a dialog titled X appears")

    def test_missing_blank_or_none_expect_is_empty(self):
        for mapping in ({"tool": "a", "args": {}},
                        {"tool": "a", "args": {}, "expect": "   "},
                        {"tool": "a", "args": {}, "expect": None}):
            with self.subTest(mapping=mapping):
                self.assertEqual(
                    tc.StepDef.from_mapping(mapping).expect, "")

    def test_non_string_expect_rejected(self):
        for bad in ({"expect": {"contains": "x"}},
                    {"expect": 42},
                    {"expect": ["a"]}):
            mapping = {"tool": "a", "args": {}}
            mapping.update(bad)
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    tc.StepDef.from_mapping(mapping)


class PhaseTests(unittest.TestCase):
    def test_phase_runs_after_action_before_report(self):
        verifier = _Verifier()
        snapshot = _Snapshot()
        coord = _coord(verifier=verifier, snapshot=snapshot)
        task = _task(coord, [
            {"tool": "a", "args": {},
             "expect": "text 'a done' present", "label": "step a"},
            {"tool": "b", "args": {}, "label": "step b"},
        ])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        # Exactly one phase check: only the step carrying an expect.
        # (B10: the goal check also calls the verifier once, with
        # the goal title — pinned below; it is not a phase check.)
        phase_calls = [c for c in verifier.calls
                     if c["expect"] == "text 'a done' present"]
        self.assertEqual(len(phase_calls), 1)
        goal_calls = [c for c in verifier.calls
                    if c["expect"] == task.title]
        self.assertEqual(len(goal_calls), 1)
        call = phase_calls[0]
        self.assertEqual(call["expect"], "text 'a done' present")
        # Before-snapshot from the action loop, after-snapshot
        # from the phase, step result text as the fallback.
        self.assertEqual(call["before"], {"n": 1})
        self.assertEqual(call["after"], {"n": 2})
        self.assertEqual(call["result"], "a done")
        self.assertEqual(len(receipt.verification), 1)
        self.assertEqual(receipt.verification[0]["verdict"], "met")
        self.assertEqual(receipt.verification[0]["label"], "step a")
        self.assertIn("Verification: step a — met",
                      tc.format_receipt(receipt))

    def test_snapshots_only_for_expected_steps(self):
        verifier = _Verifier()
        snapshot = _Snapshot()
        coord = _coord(verifier=verifier, snapshot=snapshot)
        task = _task(coord, [
            {"tool": "a", "args": {}},
            {"tool": "b", "args": {}, "expect": "text 'b done' present"},
            {"tool": "c", "args": {}},
        ])
        coord.run_task(task)
        # One before-capture (step b) + one phase after-capture +
        # one B10 goal-check capture (the goal check re-reads the
        # outcome through the same verifier, with the goal title).
        self.assertEqual(snapshot.calls, 3)
        self.assertEqual(len(verifier.calls), 2)
        self.assertEqual(verifier.calls[0]["before"], {"n": 1})
        self.assertEqual(verifier.calls[0]["after"], {"n": 2})
        self.assertEqual(verifier.calls[1]["expect"], "t")

    def test_verifying_visible_while_phase_runs(self):
        seen = {}
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TaskLedger(path=Path(tmp) / "ledger.json")
            coord = _coord(ledger=ledger)
            task = _task(coord, [
                {"tool": "a", "args": {},
                 "expect": "text 'a done' present"},
            ])

            def hook():
                # T-14 (debug pass): the hook fires twice — the
                # verification phase (state "verifying") and the
                # goal check (state "completed"). Record the FIRST
                # call only; the old hook overwrote `seen` on every
                # call, so it observed only the last one.
                if "task_state" not in seen:
                    seen["task_state"] = task.state
                    seen["ledger_state"] = ledger.get(task.id)["state"]
                    seen["rendered"] = render_task(
                        ledger.get(task.id))["state"]

            coord._verifier = _Verifier(hook=hook)
            coord._snapshot = _Snapshot()
            receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(seen["task_state"], "verifying")
        self.assertEqual(seen["ledger_state"], "verifying")
        self.assertEqual(seen["rendered"], "Verifying")

    def test_not_met_fails_honestly_never_silent_success(self):
        verifier = _Verifier(verdicts={
            "text 'zzz' present": ("not-met", "zzz absent afterwards")})
        coord = _coord(verifier=verifier, snapshot=_Snapshot())
        task = _task(coord, [
            {"tool": "a", "args": {}, "label": "step a",
             "expect": "text 'zzz' present"},
        ])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "failed")
        # The action succeeded: counts stay honest about actions;
        # the failure lives in the reason + verification verdict.
        self.assertEqual(receipt.completed, 1)
        self.assertEqual(receipt.failed, 0)
        self.assertIn("verification failed", receipt.reason)
        self.assertIn("step a", receipt.reason)
        self.assertIn("text 'zzz' present", receipt.reason)
        self.assertIn("zzz absent afterwards", receipt.reason)
        self.assertEqual(receipt.verification[0]["verdict"], "not-met")
        text = tc.format_receipt(receipt)
        self.assertIn("verification failed", text)
        self.assertIn("Verification: step a — not-met", text)
        mission = coord.to_mission_receipt(task)
        self.assertEqual(mission.status, "failed")
        self.assertTrue(mission.refusal_reasons)
        self.assertTrue(any("verification: step a — not-met" in e["detail"]
                            for e in mission.evidence))

    def test_unverifiable_completes_with_verdict_recorded(self):
        verifier = _Verifier(verdicts={
            "the frobnicate wobble": ("unverifiable",
                                      "names nothing groundable")})
        coord = _coord(verifier=verifier, snapshot=_Snapshot())
        task = _task(coord, [
            {"tool": "a", "args": {}, "expect": "the frobnicate wobble"},
        ])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.verification[0]["verdict"],
                         "unverifiable")
        self.assertIn("Verification: a — unverifiable",
                      tc.format_receipt(receipt))

    def test_skipped_step_expect_never_checked(self):
        verifier = _Verifier()
        coord = _coord(executor=_fail_executor(), verifier=verifier,
                       snapshot=_Snapshot())
        task = _task(coord, [
            {"tool": "a", "args": {}, "on_fail": "skip",
             "expect": "text 'a done' present"},
        ])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.skipped, 1)
        # The skipped step's expect is never phase-checked — but
        # B10 still judges the goal, so the verifier fires once
        # for the goal title and never for the step's expect.
        self.assertEqual([c for c in verifier.calls
                        if c["expect"] == "text 'a done' present"], [])
        self.assertEqual([c["expect"] for c in verifier.calls], ["t"])
        self.assertEqual(receipt.verification[0]["verdict"], "skipped")
        self.assertIn("expectation not checked",
                      receipt.verification[0]["reason"])

    def test_no_expects_no_phase(self):
        verifier = _Verifier()
        coord = _coord(verifier=verifier, snapshot=_Snapshot())
        task = _task(coord, [{"tool": "a", "args": {}}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        # No step carries an expect, so the phase records nothing —
        # but B10 still judges the goal, so the verifier fires once
        # for the goal title.
        self.assertEqual([c for c in verifier.calls
                        if c["expect"] != "t"], [])
        self.assertEqual([c["expect"] for c in verifier.calls], ["t"])
        self.assertEqual(receipt.verification, [])

    def test_cancel_before_phase_skips_verification(self):
        verifier = _Verifier()
        coord = _coord(verifier=verifier, snapshot=_Snapshot())
        task = _task(coord, [
            {"tool": "a", "args": {}},
            {"tool": "b", "args": {}, "expect": "text 'b done' present"},
        ])

        def cancel_after_first(task_obj, outcome):
            coord.cancel_task(task_obj.id)

        receipt = coord.run_task(task, on_step=cancel_after_first)
        self.assertEqual(receipt.state, "cancelled")
        self.assertEqual(verifier.calls, [])

    def test_verifier_failure_degrades_to_unverifiable(self):
        def boom(expect, before, after, result_text):
            raise RuntimeError("boom")

        coord = _coord(verifier=boom, snapshot=_Snapshot())
        task = _task(coord, [
            {"tool": "a", "args": {}, "expect": "text 'a done' present"},
        ])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.verification[0]["verdict"],
                         "unverifiable")
        self.assertIn("verification callback failed",
                      receipt.verification[0]["reason"])

    def test_snapshot_failure_is_unobserved_not_a_break(self):
        seen = {}

        def boom_snapshot():
            raise RuntimeError("no eyes")

        def capture(expect, before, after, result_text):
            seen["before"] = before
            seen["after"] = after
            return ("met", "fake met")

        coord = _coord(verifier=capture, snapshot=boom_snapshot)
        task = _task(coord, [
            {"tool": "a", "args": {}, "expect": "text 'a done' present"},
        ])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertIsNone(seen["before"])
        self.assertIsNone(seen["after"])

    def test_default_engine_headless_is_safe(self):
        # Default verifier + snapshot (the real uia engine) on
        # headless Linux: no eyes, ungroundable expectation —
        # the engine can only answer "unverifiable", never guess.
        coord = tc.TurnCoordinator(executor=_ok_executor())
        task = _task(coord, [
            {"tool": "a", "args": {}, "expect": "the frobnicate wobble"},
        ])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.verification[0]["verdict"],
                         "unverifiable")


class LedgerPhaseTests(unittest.TestCase):
    def test_record_round_trips_expect_and_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TaskLedger(path=Path(tmp) / "ledger.json")
            coord = _coord(verifier=_Verifier(), snapshot=_Snapshot(),
                           ledger=ledger)
            task = _task(coord, [
                {"tool": "a", "args": {},
                 "expect": "text 'a done' present"},
            ])
            coord.run_task(task)
            record = ledger.get(task.id)
            self.assertEqual(record["steps"][0]["expect"],
                             "text 'a done' present")
            self.assertEqual(len(record["verification"]), 1)
            self.assertEqual(record["verification"][0]["verdict"], "met")
            # Rebuild takes interrupted records only (terminal
            # records are history by design), so rebuild a paused
            # copy of the same record for the restore leg.
            record["state"] = "paused"
            rebuilt = TaskLedger().rebuild_task(record)
            self.assertEqual(rebuilt.steps[0].expect,
                             "text 'a done' present")
            self.assertEqual(rebuilt.verification[0]["verdict"], "met")

    def test_verifying_record_is_interrupted_and_resumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TaskLedger(path=Path(tmp) / "ledger.json")
            record = {
                "schema_version": 1,
                "task_id": "abc123",
                "classification": "saved_plan",
                "title": "t",
                "turn_text": "",
                "brain_id": None,
                "coordinator": "task_coordinator/1",
                "app_version": "6.0.0",
                # A crash mid-phase: all actions succeeded, the
                # phase never finished.
                "state": "verifying",
                "steps": [{"tool": "a", "args": {}, "label": "a",
                           "on_fail": "halt", "when": None,
                           "expect": "text 'a done' present"}],
                "outcomes": [{"tool": "a", "label": "a",
                              "status": "success", "kind": "",
                              "message": "a done"}],
                "verification": [],
                "reason": "",
                "error_kind": "",
                "created_at": "2026-11-01T00:00:00+00:00",
                "updated_at": "2026-11-01T00:00:00+00:00",
            }
            ledger._records[record["task_id"]] = record
            ledger._loaded = True
            ledger._save()
            summaries = ledger.list_interrupted()
            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0]["state"], "verifying")
            self.assertEqual(summaries[0]["remaining_labels"], [])
            # Resume re-runs the phase in full (observations are
            # safe to repeat): met completes, not-met fails.
            for verdict, wanted in ((("met", "fake met"), "completed"),
                                    (("not-met", "absent"), "failed")):
                coord = _coord(verifier=_Verifier(verdicts={
                    "text 'a done' present": verdict}),
                    snapshot=_Snapshot(), ledger=ledger)
                resumed = coord.resume_ledger_task(
                    ledger.get("abc123"), ledger=ledger)
                self.assertEqual(resumed.state, wanted)
                # Reset the record to mid-phase for the next pass.
                record["state"] = "verifying"
                ledger._records["abc123"] = record
                ledger._save()

    def test_render_details_show_verification_verdicts(self):
        record = {
            "state": "completed",
            "title": "t",
            "steps": [{"tool": "a", "label": "a"}],
            "outcomes": [{"tool": "a", "label": "a",
                          "status": "success", "kind": "",
                          "message": "a done"}],
            "verification": [{"tool": "a", "label": "a",
                              "expect": "text 'a done' present",
                              "verdict": "met",
                              "reason": "fake met"}],
        }
        rendered = render_task(record)
        self.assertEqual(rendered["state"], "Done")
        self.assertIn("Verification: a — met: fake met",
                      rendered["details_text"])


if __name__ == "__main__":
    unittest.main()
