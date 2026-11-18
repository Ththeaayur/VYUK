"""Coordinator retry-on-not-met (regression-batch follow-up).

When a step's verification verdict is "not-met", the phase
retries the SAME step's action (three-strikes, mirroring
planner's _EXPECT_STRIKE_LIMIT) instead of failing outright.
"unverifiable" never retries (unknown effect state — a re-run
could double-apply); refusals and cancellations never retry;
a retried step needing approval pauses through the existing
pending-approval path, never around it.

Stdlib unittest only; every executor/verifier/snapshot is a
fake except the ledger tests, which use temp paths, never the
working data directory. Every seam below is read off
core/brain/task_coordinator.py + its existing suites
(test_wiring_verify_phase.py): injected executor, injected
verifier (expect, before, after, result_text), injected
snapshot, temp-path ledger.
"""

import tempfile
import unittest
from pathlib import Path

from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.brain.task_ledger import TaskLedger  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/


class _ScriptExecutor:
    """Executor returning a scripted outcome per tool.

    script: {tool: [outcome, ...]} (one per call of that tool,
    in order); when a tool's script runs out, its last outcome
    repeats; a tool with no script always succeeds. Calls are
    recorded as (tool, args) so tests can prove which steps
    ran, how often, and with what approval flags. Scripts are
    per-tool, not global call order — a later step's call must
    never consume an earlier step's scripted outcome.
    """

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, tool, args):
        self.calls.append((tool, dict(args)))
        queue = self.script.get(tool)
        if not queue:
            return {"status": "success", "kind": "",
                    "message": "", "result": f"{tool} done"}
        idx = min(len(self.calls_for(tool)) - 1, len(queue) - 1)
        return dict(queue[idx])

    def calls_for(self, tool):
        return [c for c in self.calls if c[0] == tool]


class _ScriptVerifier:
    """Verifier returning a scripted verdict per expect text.

    verdicts: {expect: [(verdict, reason), ...]}; when a queue
    runs out, the last verdict repeats. Calls are recorded as
    (expect, before, after, result_text) so tests can prove
    verification read the CURRENT outcome, not a stale one.
    A hook, when set, runs inside every call (used by the
    cancel test: the hook cancels the task mid-phase).
    """

    def __init__(self, verdicts=None):
        self.queues = {k: list(v) for k, v in (verdicts or {}).items()}
        self.calls = []
        self.hook = None

    def __call__(self, expect, before, after, result_text):
        self.calls.append({"expect": expect, "before": before,
                           "after": after, "result": result_text})
        if self.hook is not None:
            self.hook()
        queue = self.queues.get(expect)
        if not queue:
            return ("met", "fake met")
        idx = min(len([c for c in self.calls
                       if c["expect"] == expect]) - 1, len(queue) - 1)
        return queue[idx]


class _Snapshot:
    """Fake snapshot probe: one numbered marker per call."""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return {"n": self.calls}


def _ok(tool_result="done"):
    return {"status": "success", "kind": "",
            "message": "", "result": tool_result}


def _coord(executor, verifier=None, snapshot=None, ledger=None):
    return tc.TurnCoordinator(
        executor=executor, verifier=verifier, snapshot=snapshot,
        ledger=ledger)


def _task(coord, steps):
    return coord.new_task(
        "t", "saved_plan",
        [tc.StepDef.from_mapping(m) for m in steps])


def _ledger():
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "ledger.json"
    # Keep the tempdir alive for the test's duration.
    _ledger._dirs.append(tmp)
    return TaskLedger(path)


_ledger._dirs = []


class RetrySuccessTests(unittest.TestCase):
    def test_retry_succeeds_after_not_met(self):
        executor = _ScriptExecutor({"a": [_ok("first"), _ok("second")]})
        verifier = _ScriptVerifier({
            "E": [("not-met", "E absent"), ("met", "E present")]})
        coord = _coord(executor, verifier, _Snapshot())
        task = _task(coord, [{"tool": "a", "args": {},
                              "expect": "E", "label": "step a"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        # The step ran twice (initial + one retry), nothing else.
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(len(receipt.steps), 1)
        self.assertEqual(receipt.steps[0]["status"], "success")
        # Final verdict met; the miss survives in retries.
        self.assertEqual(receipt.verification[0]["verdict"], "met")
        self.assertEqual(len(receipt.retries), 2)
        miss, reset = receipt.retries
        self.assertEqual(miss["verdict"], "not-met")
        self.assertEqual(miss["strike"], 1)
        self.assertTrue(miss["retried"])
        self.assertEqual(miss["reason"], "E absent")
        self.assertEqual(reset["verdict"], "met")
        self.assertEqual(reset["strike"], 0)
        self.assertFalse(reset["retried"])
        self.assertIn("Retry: step a — not-met (strike 1) — retried",
                      tc.format_receipt(receipt))

    def test_verification_reads_current_outcome(self):
        # The re-verify must see the retry's result text, not
        # the first attempt's: the verifier asserts it.
        seen = []
        executor = _ScriptExecutor({"a": [_ok("first"), _ok("second")]})

        def verify(expect, before, after, result_text):
            seen.append((expect, result_text))
            if len(seen) == 1:
                return ("not-met", "not second yet")
            return ("met", "second seen")

        coord = _coord(executor, verify, _Snapshot())
        task = _task(coord, [{"tool": "a", "args": {},
                              "expect": "E", "label": "step a"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        # The phase re-verify reads the retry's result, not the
        # first attempt's; the B10 goal check then reads the same
        # current outcome once more, under the goal title.
        self.assertEqual([r for e, r in seen if e == "E"],
                         ["first", "second"])
        self.assertEqual([r for e, r in seen if e == "t"],
                         ["second"])

    def test_later_steps_not_rerun(self):
        executor = _ScriptExecutor({"a": [_ok("a1"), _ok("a2")], "b": [_ok("b1")]})
        verifier = _ScriptVerifier({
            "E": [("not-met", "E absent"), ("met", "E present")]})
        coord = _coord(executor, verifier, _Snapshot())
        task = _task(coord, [{"tool": "a", "args": {},
                              "expect": "E", "label": "step a"},
                             {"tool": "b", "args": {},
                              "label": "step b"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        # A ran twice (initial + retry); B ran exactly once —
        # the retry re-runs one step, never rewinds the loop.
        self.assertEqual(len(executor.calls_for("a")), 2)
        self.assertEqual(len(executor.calls_for("b")), 1)
        self.assertEqual(len(receipt.steps), 2)


class RetryExhaustionTests(unittest.TestCase):
    def test_retry_exhausted_fails_honestly(self):
        executor = _ScriptExecutor({"a": [_ok()]})
        verifier = _ScriptVerifier({
            "E": [("not-met", "E absent")]})
        coord = _coord(executor, verifier, _Snapshot())
        task = _task(coord, [{"tool": "a", "args": {},
                              "expect": "E", "label": "step a"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "failed")
        # Three-strikes: initial + 2 retries, never a 4th run.
        self.assertEqual(len(executor.calls), 3)
        # The action succeeded: counts stay honest about
        # actions; the failure lives in the reason + verdict.
        self.assertEqual(receipt.completed, 1)
        self.assertEqual(receipt.failed, 0)
        self.assertIn("verification failed", receipt.reason)
        self.assertIn("step a", receipt.reason)
        self.assertIn("E", receipt.reason)
        self.assertIn("E absent", receipt.reason)
        self.assertIn("3 strikes", receipt.reason)
        self.assertEqual(receipt.verification[0]["verdict"], "not-met")
        self.assertEqual(len(receipt.retries), 3)
        self.assertEqual([r["strike"] for r in receipt.retries],
                         [1, 2, 3])
        self.assertEqual([r["retried"] for r in receipt.retries],
                         [True, True, False])

    def test_unverifiable_never_retried(self):
        executor = _ScriptExecutor({"a": [_ok()]})
        verifier = _ScriptVerifier({
            "E": [("unverifiable", "names nothing groundable")]})
        coord = _coord(executor, verifier, _Snapshot())
        task = _task(coord, [{"tool": "a", "args": {},
                              "expect": "E", "label": "step a"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        # One action, one check: an inconclusive verdict is not
        # a miss, so no re-run (a re-run could double-apply an
        # effect whose state is unknown).
        self.assertEqual(len(executor.calls), 1)
        # One phase check (expect "E") plus the B10 goal check
        # (goal title "t"): the goal check only judges, it
        # never retries — no re-run either way.
        self.assertEqual([c["expect"] for c in verifier.calls],
                         ["E", "t"])
        self.assertEqual(receipt.verification[0]["verdict"],
                         "unverifiable")
        self.assertEqual(receipt.retries, [])


class RetrySafetyTests(unittest.TestCase):
    def test_first_run_refusal_never_reaches_verification(self):
        executor = _ScriptExecutor({"a": [{"status": "failure",
                                     "kind": "handler_error",
                                     "message": "boom"}]})
        verifier = _ScriptVerifier({"E": [("met", "fake met")]})
        coord = _coord(executor, verifier, _Snapshot())
        task = _task(coord, [{"tool": "a", "args": {},
                              "expect": "E", "label": "step a"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(verifier.calls, [])
        self.assertEqual(receipt.retries, [])

    def test_refusal_on_retry_fails_without_further_retry(self):
        executor = _ScriptExecutor({"a": [
            _ok("first"),
            {"status": "failure", "kind": "refused",
             "message": "no, absolutely not"}]})
        verifier = _ScriptVerifier({
            "E": [("not-met", "E absent")]})
        coord = _coord(executor, verifier, _Snapshot())
        task = _task(coord, [{"tool": "a", "args": {},
                              "expect": "E", "label": "step a"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "failed")
        # Initial + one retry; the refusal is a decision, not a
        # miss — no third run, no second strike.
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(len(receipt.steps), 1)
        self.assertEqual(receipt.steps[0]["status"], "failure")
        self.assertEqual(receipt.steps[0]["kind"], "refused")
        self.assertEqual(receipt.failed, 1)
        self.assertEqual(len(receipt.retries), 1)
        self.assertTrue(receipt.retries[0]["retried"])
        self.assertIn("retry action failed",
                      receipt.verification[0]["reason"])

    def test_retry_action_skip_records_skipped_entry(self):
        executor = _ScriptExecutor({"a": [
            _ok("first"),
            {"status": "failure", "kind": "handler_error",
             "message": "probe failed"}]})
        verifier = _ScriptVerifier({
            "E": [("not-met", "E absent")]})
        coord = _coord(executor, verifier, _Snapshot())
        task = _task(coord, [{"tool": "a", "args": {},
                              "on_fail": "skip",
                              "expect": "E", "label": "step a"}])
        receipt = coord.run_task(task)
        # The retry action skipped per on_fail=skip: the task
        # completes (skipped steps complete, per existing
        # semantics) with a skipped verification entry — the
        # expectation was never re-checked, and the entry says
        # exactly that instead of claiming a verdict.
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(receipt.skipped, 1)
        self.assertEqual(receipt.verification[0]["verdict"], "skipped")
        self.assertIn("expectation not checked",
                      receipt.verification[0]["reason"])

    def test_cancelled_never_retried(self):
        executor = _ScriptExecutor({"a": [_ok()]})
        verifier = _ScriptVerifier({
            "E": [("not-met", "E absent")]})
        coord = _coord(executor, verifier, _Snapshot())
        task = _task(coord, [{"tool": "a", "args": {},
                              "expect": "E", "label": "step a"}])
        # The verifier hook cancels the task mid-phase: the
        # not-met verdict stands, but no retry action runs.
        verifier.hook = lambda: coord.cancel_task(task.id)
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "cancelled")
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(receipt.verification[0]["verdict"], "not-met")
        self.assertEqual(len(receipt.retries), 1)
        self.assertFalse(receipt.retries[0]["retried"])

    def test_cancel_before_phase_wins_over_completion(self):
        executor = _ScriptExecutor({"a": [_ok()]})
        verifier = _ScriptVerifier({
            "E": [("met", "fake met")]})
        coord = _coord(executor, verifier, _Snapshot())
        task = _task(coord, [{"tool": "a", "args": {},
                              "expect": "E", "label": "step a"}])
        # A step callback cancels after the last action: the
        # phase must not complete a cancelled task.
        receipt = coord.run_task(
            task, on_step=lambda t, o: coord.cancel_task(t.id))
        self.assertEqual(receipt.state, "cancelled")
        self.assertEqual(verifier.calls, [])


class RetryPendingApprovalTests(unittest.TestCase):
    # Faithful engine confirm-stop shape, read off
    # _detect_s7_confirmation_stop: a journal_action success
    # carrying "Reconciliation needs confirmation: ..." pauses
    # for approval with confirm_arg "confirmed".
    _STOP = {"status": "success", "kind": "",
             "message": "",
             "result": "Reconciliation needs confirmation: overwrite?"}

    def _journal_task(self, on_fail="halt"):
        return [{"tool": "journal_action", "args": {},
                 "expect": "E", "label": "step j",
                 "on_fail": on_fail},
                {"tool": "b", "args": {}, "label": "step b"}]

    def _paused_run(self, coord, task):
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "paused")
        pending = coord.pending_approval(task.id)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["step_index"], 0)
        self.assertEqual(pending["tool"], "journal_action")
        self.assertEqual(pending["confirm_arg"], "confirmed")
        return receipt

    def test_retry_pending_approval_roundtrip(self):
        executor = _ScriptExecutor({
            "journal_action": [_ok("journal done"), self._STOP,
                                _ok("journal redone")],
            "b": [_ok("b done")]})
        verifier = _ScriptVerifier({
            "E": [("not-met", "E absent"), ("met", "E present")]})
        coord = _coord(executor, verifier, _Snapshot())
        task = _task(coord, self._journal_task())
        self._paused_run(coord, task)
        # Initial journal run + retry re-run (the stop); B ran
        # once in the first action loop.
        self.assertEqual(len(executor.calls_for("journal_action")), 2)
        self.assertEqual(len(executor.calls_for("b")), 1)
        # Approve: the re-issue carries exactly the engine flag
        # (confirmed=True) — the retry goes through approval,
        # never around it — and only the journal step re-runs.
        receipt = coord.resolve_pending(task.id, approved=True)
        self.assertEqual(receipt.state, "completed")
        journal_calls = executor.calls_for("journal_action")
        self.assertEqual(len(journal_calls), 3)
        self.assertTrue(journal_calls[2][1].get("confirmed"))
        self.assertEqual(len(executor.calls_for("b")), 1)
        # No duplicate outcome: one entry per step, positions
        # aligned, final verdict met.
        self.assertEqual(len(receipt.steps), 2)
        self.assertEqual(receipt.steps[0]["status"], "success")
        self.assertEqual(receipt.verification[0]["verdict"], "met")
        self.assertEqual(len(receipt.retries), 2)  # miss + reset

    def test_retry_pending_denied_skip_completes(self):
        executor = _ScriptExecutor({
            "journal_action": [_ok("journal done"), self._STOP],
            "b": [_ok("b done")]})
        verifier = _ScriptVerifier({
            "E": [("not-met", "E absent")]})
        coord = _coord(executor, verifier, _Snapshot())
        task = _task(coord, self._journal_task(on_fail="skip"))
        self._paused_run(coord, task)
        receipt = coord.resolve_pending(task.id, approved=False)
        self.assertEqual(receipt.state, "completed")
        # The denied step skipped in place (no duplicate, no
        # re-run of later steps); its expectation was never
        # re-checked.
        self.assertEqual(len(receipt.steps), 2)
        self.assertEqual(receipt.steps[0]["status"], "skipped")
        self.assertEqual(receipt.steps[0]["kind"], "approval_denied")
        self.assertEqual(len(executor.calls_for("b")), 1)
        self.assertEqual(receipt.verification[0]["verdict"], "skipped")

    def test_retry_pending_denied_halt_fails(self):
        executor = _ScriptExecutor({
            "journal_action": [_ok("journal done"), self._STOP],
            "b": [_ok("b done")]})
        verifier = _ScriptVerifier({
            "E": [("not-met", "E absent")]})
        coord = _coord(executor, verifier, _Snapshot())
        task = _task(coord, self._journal_task(on_fail="halt"))
        self._paused_run(coord, task)
        receipt = coord.resolve_pending(task.id, approved=False)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(len(receipt.steps), 2)
        self.assertEqual(receipt.steps[0]["status"], "refused")
        self.assertEqual(receipt.steps[0]["kind"], "approval_denied")
        self.assertEqual(len(executor.calls_for("b")), 1)


class RetryLedgerTests(unittest.TestCase):
    def test_retries_stored_in_ledger_record(self):
        ledger = _ledger()
        executor = _ScriptExecutor({"a": [_ok("first"), _ok("second")]})
        verifier = _ScriptVerifier({
            "E": [("not-met", "E absent"), ("met", "E present")]})
        coord = _coord(executor, verifier, _Snapshot(), ledger)
        task = _task(coord, [{"tool": "a", "args": {},
                              "expect": "E", "label": "step a"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        record = ledger.get(task.id)
        self.assertEqual(len(record["retries"]), 2)
        self.assertEqual(record["retries"][0]["verdict"], "not-met")
        self.assertEqual(record["retries"][0]["strike"], 1)
        self.assertTrue(record["retries"][0]["retried"])
        self.assertEqual(record["retries"][1]["verdict"], "met")

    def test_old_records_without_retries_load_empty(self):
        ledger = _ledger()
        coord = _coord(_ScriptExecutor({"a": [_ok()]}), None, _Snapshot(),
                       ledger)
        task = _task(coord, [{"tool": "a", "args": {}}])
        coord.run_task(task)
        record = ledger.get(task.id)
        del record["retries"]
        rebuilt = TaskLedger().rebuild_task(
            {**record, "state": "paused"})
        self.assertEqual(rebuilt._retries, [])

    def test_strikes_persist_across_phase_rerun(self):
        # Real path to a paused record carrying strike 1: the
        # first verify misses, the retry re-run stops for
        # approval, the task pauses. Rebuild + resume must count
        # the next miss as strike 2 (not a fresh strike 1), so
        # the step gets 2 more runs, not 3 — a crash or a pause
        # must not grant fresh retries.
        ledger = _ledger()
        executor = _ScriptExecutor({"journal_action": [
            _ok("journal done"),
            {"status": "success", "kind": "", "message": "",
             "result": "Reconciliation needs confirmation: overwrite?"}]})
        verifier = _ScriptVerifier({
            "E": [("not-met", "E absent")]})
        coord = _coord(executor, verifier, _Snapshot(), ledger)
        task = _task(coord, [{"tool": "journal_action", "args": {},
                              "expect": "E", "label": "step j"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "paused")
        record = ledger.get(task.id)
        self.assertEqual(len(record["retries"]), 1)
        self.assertEqual(record["retries"][0]["strike"], 1)
        # Resumed coordinator: fresh executor + always-not-met
        # verifier. The rebuilt phase's first miss is strike 2
        # (one retry run follows), the next miss is strike 3
        # and halts: exactly 1 more action run, not 2 — the
        # original strike already consumed one of the three
        # attempts, so the three-strikes total holds across
        # the pause + rebuild.
        resumed_executor = _ScriptExecutor({"journal_action": [_ok("redone")]})
        resumed_verifier = _ScriptVerifier({
            "E": [("not-met", "still absent")]})
        coord2 = _coord(resumed_executor, resumed_verifier,
                        _Snapshot(), ledger)
        receipt2 = coord2.resume_ledger_task(record)
        self.assertEqual(receipt2.state, "failed")
        self.assertEqual(len(resumed_executor.calls), 1)
        self.assertEqual([r["strike"] for r in receipt2.retries],
                         [1, 2, 3])
        self.assertIn("3 strikes", receipt2.reason)

    def test_strike_counting_unit(self):
        # The reset-on-met rule (planner's rule, mirrored):
        # strikes count the trailing run of not-met events for
        # one step; a met event resets it; other steps' events
        # are ignored.
        coord = _coord(_ScriptExecutor({}))
        task = _task(coord, [{"tool": "a", "args": {}},
                             {"tool": "b", "args": {}}])

        def ev(index, verdict):
            return {"step_index": index, "tool": "a", "label": "x",
                    "verdict": verdict, "reason": "", "strike": 0,
                    "retried": False}

        task._retries = [ev(0, "not-met")]
        self.assertEqual(coord._retry_strikes(task, 0), 1)
        task._retries = [ev(0, "not-met"), ev(0, "not-met")]
        self.assertEqual(coord._retry_strikes(task, 0), 2)
        task._retries = [ev(0, "not-met"), ev(0, "met")]
        self.assertEqual(coord._retry_strikes(task, 0), 0)
        task._retries = [ev(0, "not-met"), ev(0, "met"),
                         ev(0, "not-met")]
        self.assertEqual(coord._retry_strikes(task, 0), 1)
        task._retries = [ev(0, "not-met"), ev(1, "not-met")]
        self.assertEqual(coord._retry_strikes(task, 0), 1)
        self.assertEqual(coord._retry_strikes(task, 1), 1)


if __name__ == "__main__":
    unittest.main()
