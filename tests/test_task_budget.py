"""B14 — coordinator time budget + cost accounting (autonomy L11).

Lab leg only. The real-Windows field leg — a real long task on
his machine that hits its budget and pauses honestly with its
spent accounting — is OWED, not this batch.

Gap proved first (see core/brain/task_budget.py): the
coordinator run loop measured no elapsed wall-clock time (the
only "budget" was the jump budget), and no task record — receipt
or ledger — carried what a task spent, even though a global cost
ledger (core/cost_ledger.py) already exists for the voice
clients. This suite proves: a spent wall-clock budget pauses
resumably at a step boundary (never mid-step, never completed,
never silently killed) and the receipt says what it spent; a
task inside its budget completes with its spend in the receipt
and the ledger record; cloud calls are counted by leg with
measured cost or honest-unknown; local calls are counted as
time, never dollars; a retried step is counted once per
execution; a budget-paused task resumes with a fresh budget
window (pause time never charged); the picker never sees cost.
"""

import os
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_PKGROOT = ROOT / ".pkgroot"
if _PKGROOT.is_dir() and str(_PKGROOT) not in sys.path:
    sys.path.insert(0, str(_PKGROOT))

from core.brain import task_budget as tb  # noqa: E402
from core.brain import task_coordinator as tc  # noqa: E402
from core.brain.task_ledger import TaskLedger  # noqa: E402


def _ok(result="done"):
    return {"status": "success", "result": result}


class _ScriptExecutor:
    """Per-tool scripted results; records every call."""

    def __init__(self, script=None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls = []

    def __call__(self, tool, args):
        self.calls.append(tool)
        results = self.script.get(tool)
        if not results:
            return _ok()
        if len(results) > 1:
            return results.pop(0)
        return results[0]


class _ClockAdvancingExecutor(_ScriptExecutor):
    """Advances the fake clock by `advance_s` on every call."""

    def __init__(self, clock, advance_s, script=None):
        super().__init__(script)
        self._clock = clock
        self._advance_s = advance_s

    def __call__(self, tool, args):
        self._clock[0] += self._advance_s
        return super().__call__(tool, args)


class _ScriptVerifier:
    """Per-expect scripted verdicts."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, expect, before, after, result_text):
        self.calls.append(expect)
        queue = self.script.get(expect)
        if not queue:
            return ("unverifiable", "no script")
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]


class _Snapshot:
    def __call__(self):
        return {}


def _ledger():
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "ledger.json"
    _ledger._dirs.append(tmp)
    return TaskLedger(path)


_ledger._dirs = []


class _BudgetBase(unittest.TestCase):
    """Fake clock on task_budget.monotonic for the whole test."""

    def setUp(self):
        # The clock starts at 1000.0 so a zero elapsed is never
        # confused with "no clock ran".
        self.clock = [1000.0]
        self._patch = mock.patch.object(
            tb, "monotonic", side_effect=lambda: self.clock[0])
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _coord(self, executor=None, verifier=None, brains=None):
        return tc.TurnCoordinator(
            executor=executor or _ScriptExecutor(),
            verifier=verifier, snapshot=_Snapshot(),
            ledger=_ledger(), second_opinion_brains=brains)

    def _task(self, coord, steps, **kwargs):
        return coord.new_task(
            "t", "saved_plan",
            [tc.StepDef.from_mapping(m) for m in steps], **kwargs)


class BudgetPauseTests(_BudgetBase):
    def test_budget_exceeded_pauses_at_step_boundary(self):
        executor = _ClockAdvancingExecutor(self.clock, 11.0)
        coord = self._coord(executor=executor)
        task = self._task(coord, [
            {"tool": "a", "label": "step a"},
            {"tool": "b", "label": "step b"},
            {"tool": "c", "label": "step c"},
        ], time_budget_s=10.0)
        receipt = coord.run_task(task)
        # Step a ran (11 s elapsed during it); the budget check
        # before step b paused — b and c never ran.
        self.assertEqual(receipt.state, "paused")
        self.assertEqual(executor.calls, ["a"])
        self.assertEqual(receipt.attempted, 1)
        self.assertEqual(receipt.remaining, ["step b", "step c"])
        self.assertIn("time budget spent", receipt.reason)
        self.assertIn("10s", receipt.reason)
        # The receipt says what it spent.
        spend = receipt.spend
        self.assertGreaterEqual(spend["elapsed_s"], 11.0)
        self.assertEqual(spend["steps_run"], 1)
        self.assertEqual(spend["executions"], 1)
        self.assertEqual(spend["cloud_calls"], [])
        self.assertEqual(spend["cost_usd"], 0.0)
        self.assertTrue(spend["cost_known"])
        self.assertEqual(task._budget_windows, 1)

    def test_budget_pause_is_resumable_not_terminal(self):
        executor = _ClockAdvancingExecutor(self.clock, 11.0)
        coord = self._coord(executor=executor)
        task = self._task(coord, [
            {"tool": "a", "label": "step a"},
            {"tool": "b", "label": "step b"},
        ], time_budget_s=10.0)
        coord.run_task(task)
        self.assertEqual(task.state, "paused")
        # The ledger holds a resumable paused record (B11 shape).
        record = coord._ledger.get(task.id)
        self.assertEqual(record["state"], "paused")
        self.assertEqual(record["spend"]["steps_run"], 1)

    def test_inside_budget_completes_with_spend(self):
        coord = self._coord()
        task = self._task(coord, [
            {"tool": "a", "label": "step a"},
            {"tool": "b", "label": "step b"},
        ], time_budget_s=600.0)
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        spend = receipt.spend
        self.assertEqual(spend["steps_run"], 2)
        self.assertEqual(spend["executions"], 2)
        self.assertEqual(spend["cloud_calls"], [])
        self.assertEqual(spend["local_calls"], 0)
        self.assertEqual(spend["cost_usd"], 0.0)
        self.assertTrue(spend["cost_known"])
        self.assertGreaterEqual(spend["elapsed_s"], 0.0)
        self.assertIn("Spent:", tc.format_receipt(receipt))
        # The ledger record carries the same spend.
        record = coord._ledger.get(task.id)
        self.assertEqual(record["spend"]["steps_run"], 2)
        self.assertEqual(record["spend"]["executions"], 2)

    def test_budget_default_is_600_and_invalid_falls_back(self):
        coord = self._coord()
        self.assertEqual(
            self._task(coord, [{"tool": "a"}])._time_budget_s, 600.0)
        self.assertEqual(
            self._task(coord, [{"tool": "a"}],
                       time_budget_s=30.0)._time_budget_s, 30.0)
        self.assertEqual(
            self._task(coord, [{"tool": "a"}],
                       time_budget_s=0)._time_budget_s, 600.0)
        self.assertEqual(
            self._task(coord, [{"tool": "a"}],
                       time_budget_s=-5)._time_budget_s, 600.0)
        self.assertEqual(
            self._task(coord, [{"tool": "a"}],
                       time_budget_s="nope")._time_budget_s, 600.0)

    def test_budget_paused_task_resumes_with_fresh_window(self):
        executor = _ClockAdvancingExecutor(self.clock, 11.0)
        coord = self._coord(executor=executor)
        task = self._task(coord, [
            {"tool": "a", "label": "step a"},
            {"tool": "b", "label": "step b"},
            {"tool": "c", "label": "step c"},
        ], time_budget_s=10.0)
        coord.run_task(task)
        self.assertEqual(task.state, "paused")
        self.assertEqual(task._budget_windows, 1)
        # 100 s pass while paused — pause time is never charged.
        self.clock[0] += 100.0
        # The resumed run must not advance the clock again
        # (steps b/c run fast); the fresh window measures from
        # the resume.
        executor._advance_s = 0.0
        receipt = coord.resume_task(task.id)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(executor.calls, ["a", "b", "c"])
        self.assertEqual(task._budget_windows, 2)
        # Elapsed is ~11 s of running time, not 111 s.
        self.assertLess(receipt.spend["elapsed_s"], 20.0)
        self.assertGreaterEqual(receipt.spend["elapsed_s"], 11.0)
        self.assertEqual(receipt.spend["steps_run"], 3)


class CostLedgerTests(_BudgetBase):
    def test_cloud_call_counted_with_measured_cost(self):
        coord = self._coord()
        task = self._task(coord, [{"tool": "a", "label": "step a"}])
        coord.record_task_cloud_call(
            task, leg="groq", model="llama-3.3-70b-versatile",
            prompt_tokens=1000, completion_tokens=500,
            seconds=2.0, cost_usd=0.001, rate_source="caller")
        coord.record_task_cloud_call(
            task, leg="groq", model="llama-3.3-70b-versatile",
            prompt_tokens=1000, completion_tokens=500,
            seconds=2.0, cost_usd=0.001, rate_source="caller")
        receipt = coord.run_task(task)
        calls = receipt.spend["cloud_calls"]
        self.assertEqual(len(calls), 1)
        entry = calls[0]
        self.assertEqual(entry["leg"], "groq")
        self.assertEqual(entry["calls"], 2)
        self.assertEqual(entry["prompt_tokens"], 2000)
        self.assertEqual(entry["completion_tokens"], 1000)
        self.assertEqual(entry["measurement"], "exact")
        self.assertAlmostEqual(receipt.spend["cost_usd"], 0.002)
        self.assertTrue(receipt.spend["cost_known"])
        # Both calls reported durations, so the local-time split
        # is computable (elapsed minus 4 s of cloud time).
        self.assertIsNotNone(receipt.spend["local_seconds"])

    def test_unmeasured_cloud_call_is_honest_unknown(self):
        coord = self._coord()
        task = self._task(coord, [{"tool": "a", "label": "step a"}])
        # No tokens, no cost, no rate: the provider returned no
        # usage. The call is counted; its cost is unknown.
        coord.record_task_cloud_call(task, leg="second-opinion")
        receipt = coord.run_task(task)
        self.assertIsNone(receipt.spend["cost_usd"])
        self.assertFalse(receipt.spend["cost_known"])
        self.assertIn("second-opinion", receipt.spend["cost_note"])
        self.assertIn("unknown", receipt.spend["cost_note"])
        entry = receipt.spend["cloud_calls"][0]
        self.assertEqual(entry["measurement"], "unknown")
        self.assertIsNone(entry["cost_usd"])
        # A partial sum is never presented as the total: one
        # known call plus one unknown call still totals unknown.
        coord2 = self._coord()
        task2 = self._task(coord2, [{"tool": "a", "label": "step a"}])
        coord2.record_task_cloud_call(
            task2, leg="groq", model="m",
            prompt_tokens=10, completion_tokens=5,
            cost_usd=0.5, rate_source="caller")
        coord2.record_task_cloud_call(task2, leg="second-opinion")
        receipt2 = coord2.run_task(task2)
        self.assertIsNone(receipt2.spend["cost_usd"])
        self.assertFalse(receipt2.spend["cost_known"])

    def test_local_calls_counted_as_time_never_dollars(self):
        coord = self._coord()
        task = self._task(coord, [{"tool": "a", "label": "step a"}])
        coord.record_task_local_call(task)
        coord.record_task_local_call(task)
        coord.record_task_local_call(task)
        receipt = coord.run_task(task)
        self.assertEqual(receipt.spend["local_calls"], 3)
        # Local calls are time (inside elapsed_s), never money:
        # no cloud calls, so the cost is a measured zero.
        self.assertEqual(receipt.spend["cost_usd"], 0.0)
        self.assertTrue(receipt.spend["cost_known"])
        self.assertIn("Spent:", tc.format_receipt(receipt))
        self.assertIn("3 local calls", tc.format_receipt(receipt))

    def test_second_opinion_check_records_cloud_and_local(self):
        # Both brains answer YES on a destructive step: the
        # check proceeds, the step runs, and the check's two
        # legs are recorded — one cloud call (Groq, brain_a)
        # with unknown cost (cross_check returns text only,
        # no usage payload) and one local call (brain_b).
        brains = (lambda q: "yes", lambda q: "yes")
        executor = _ScriptExecutor()
        coord = self._coord(executor=executor, brains=brains)
        task = self._task(coord, [
            {"tool": "delete_file", "label": "Delete old backup",
             "args": {"path": "/tmp/x"}}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(executor.calls, ["delete_file"])
        calls = receipt.spend["cloud_calls"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["leg"], "second-opinion")
        self.assertEqual(calls[0]["calls"], 1)
        self.assertEqual(calls[0]["measurement"], "unknown")
        self.assertIsNone(calls[0]["cost_usd"])
        self.assertEqual(receipt.spend["local_calls"], 1)
        self.assertIsNone(receipt.spend["cost_usd"])
        self.assertFalse(receipt.spend["cost_known"])
        # The check's seconds were measured, so the local-time
        # split stays computable.
        self.assertIsNotNone(receipt.spend["local_seconds"])

    def test_retried_step_counted_once_per_execution(self):
        # Verification retry: the step runs twice (initial +
        # one retry), one outcome — two executions, counted
        # once each, never folded into one.
        executor = _ScriptExecutor(
            {"a": [_ok("first"), _ok("second")]})
        verifier = _ScriptVerifier(
            {"E": [("not-met", "E absent"), ("met", "E present")]})
        coord = self._coord(executor=executor, verifier=verifier)
        task = self._task(coord, [
            {"tool": "a", "args": {}, "expect": "E",
             "label": "step a"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(len(receipt.steps), 1)
        self.assertEqual(receipt.spend["executions"], 2)
        self.assertEqual(receipt.spend["steps_run"], 1)


class SpendPersistenceTests(_BudgetBase):
    def test_spend_round_trips_through_ledger(self):
        executor = _ClockAdvancingExecutor(self.clock, 11.0)
        coord = self._coord(executor=executor)
        task = self._task(coord, [
            {"tool": "a", "label": "step a"},
            {"tool": "b", "label": "step b"},
        ], time_budget_s=10.0)
        coord.record_task_cloud_call(
            task, leg="groq", model="m",
            prompt_tokens=10, completion_tokens=5,
            seconds=1.0, cost_usd=0.25, rate_source="caller")
        coord.record_task_local_call(task)
        coord.run_task(task)
        self.assertEqual(task.state, "paused")
        record = coord._ledger.get(task.id)
        self.assertEqual(record["time_budget_s"], 10.0)
        self.assertEqual(record["budget_windows"], 1)
        self.assertEqual(record["spend"]["executions"], 1)
        self.assertEqual(record["spend"]["local_calls"], 1)
        self.assertEqual(len(record["spend"]["cloud_calls"]), 1)
        rebuilt = coord._ledger.rebuild_task(record)
        self.assertEqual(rebuilt._time_budget_s, 10.0)
        self.assertEqual(rebuilt._budget_windows, 1)
        self.assertEqual(rebuilt._spend["executions"], 1)
        self.assertEqual(rebuilt._spend["local_calls"], 1)
        self.assertEqual(len(rebuilt._spend["cloud_calls"]), 1)
        self.assertEqual(
            rebuilt._spend["cloud_calls"][0]["cost_usd"], 0.25)
        # Elapsed restores as the finished-windows base, and no
        # stale window is inherited.
        self.assertGreaterEqual(rebuilt._spend_elapsed_base, 11.0)
        self.assertIsNone(rebuilt._budget_started_at)

    def test_old_record_without_spend_loads_empty(self):
        coord = self._coord()
        task = self._task(coord, [{"tool": "a", "label": "step a"}])
        coord.run_task(task)
        # A completed task is terminal; rebuild from a paused
        # copy of the record with the B14 keys stripped (an
        # old record, pre-B14).
        record = coord._ledger.get(task.id)
        record = dict(record)
        record["state"] = "paused"
        for key in ("spend", "time_budget_s", "budget_windows"):
            record.pop(key, None)
        rebuilt = coord._ledger.rebuild_task(record)
        self.assertEqual(rebuilt._time_budget_s, 600.0)
        self.assertEqual(rebuilt._budget_windows, 0)
        self.assertEqual(rebuilt._spend["executions"], 0)
        self.assertEqual(rebuilt._spend["cloud_calls"], [])
        self.assertTrue(rebuilt._spend["cost_known"])


class PickerBlindTests(_BudgetBase):
    def test_picker_never_sees_cost(self):
        # Static: the goal-picker modules never import the
        # budget module, so no spend can reach a pick.
        for name in ("planner_rungoal.py", "planner.py"):
            src = (ROOT / "core" / "brain" / name).read_text(
                encoding="utf-8")
            self.assertNotIn("task_budget", src)
            self.assertNotIn("record_task_cloud_call", src)
        # Behavioural: a task that records cloud + local calls
        # never hands spend to the executor (the picker path
        # would be an arg, and no arg carries it).
        seen_args = []

        def _recording_executor(tool, args):
            seen_args.append(dict(args))
            return _ok()

        coord = self._coord(executor=_recording_executor)
        task = self._task(coord, [{"tool": "a", "label": "step a",
                                  "args": {"x": 1}}])
        coord.record_task_cloud_call(task, leg="groq", model="m",
                                     prompt_tokens=1, completion_tokens=1,
                                     cost_usd=0.01, rate_source="caller")
        coord.record_task_local_call(task)
        coord.run_task(task)
        for args in seen_args:
            self.assertNotIn("spend", args)
            self.assertNotIn("cost_usd", args)


if __name__ == "__main__":
    unittest.main()
