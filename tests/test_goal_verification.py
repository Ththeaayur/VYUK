"""B10 — goal-level verification: the goal check after the step
phase (coordinator) and after the picker's "done" (run_goal).

Lab leg only. The real-Windows field leg — a real complex task
on his machine where every step passes but the goal is not done,
and the goal check must catch it there, not just in the lab —
is OWED, not this batch.

Gap proved first (see core/brain/task_coordinator_execution.
_run_verification_phase and planner_rungoal.run_goal): the
verification phase checks each step's `expect` only (a step
with no `expect` is skipped with `continue`), and run_goal
completion was the picker returning {"done": summary} — the
brain certifying its own work. No code anywhere re-read the
goal text after the steps and asked whether the goal is
achieved. A task whose every step "succeeds" while the goal
is not actually done reported `completed`. That is "done by
declaration".

What this batch builds (and this suite proves): ONE goal check,
after the step verification phase — the goal text (task.title)
judged against a fresh snapshot through the EXISTING verifier,
producing a `goal_verdict` of `met` / `not-met` /
`unverifiable`, recorded in the receipt AND the ledger. `met`
completes as today; `not-met` fails honestly, naming the goal
and the reason (no silent retry of the whole plan — re-planning
is B22's job); `unverifiable` completes with the verdict
recorded, never faked to `met`. The same check judges the
picker's "done" in run_goal, and budget-stop / strike-stop
reports gain the goal verdict line.
"""

import asyncio
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_PKGROOT = ROOT / ".pkgroot"
if _PKGROOT.is_dir() and str(_PKGROOT) not in sys.path:
    sys.path.insert(0, str(_PKGROOT))

from core.brain import planner_rungoal as rg  # noqa: E402
from core.brain import task_coordinator as tc  # noqa: E402
from core.brain import task_ledger as tl  # noqa: E402
from core.brain.task_coordinator import StepDef  # noqa: E402
from core.brain.task_coordinator_steps import format_receipt  # noqa: E402


# ---------------------------------------------------------------------------
# fakes (mirroring tests/test_complex_task.py)
# ---------------------------------------------------------------------------

def _ok(result="done"):
    return {"status": "success", "result": result}


def _fail(message="simulated failure"):
    return {"status": "failure", "message": message}


class _ScriptExecutor:
    """Per-tool scripted results; records every call."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, tool, args):
        self.calls.append(tool)
        results = self.script.get(tool)
        if not results:
            return _ok()
        if len(results) > 1:
            return results.pop(0)
        return results[0]


class _RecordingVerifier:
    """Per-expect scripted verdicts; records every full call."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []  # (expect_text, before, after, result_text)

    def __call__(self, expect_text, before, after, result_text):
        self.calls.append((expect_text, before, after, result_text))
        verdicts = self.script.get(expect_text)
        if not verdicts:
            return ("unverifiable", "no script")
        if len(verdicts) > 1:
            return verdicts.pop(0)
        return verdicts[0]


class _Snapshot:
    def __call__(self):
        return {"fg": "fake", "windows": ["fake"], "texts": []}


def _steps(*mappings):
    return [StepDef.from_mapping(m) for m in mappings]


# The goal texts used as task titles: the fake verifier is keyed
# on the text it is asked to judge, so the goal title IS the
# script key.
GOAL_MET = "text 'done' present"
GOAL_UNMET = "text 'file saved' present"
GOAL_UNGROUNDABLE = "do the thing properly"


# ---------------------------------------------------------------------------
# 1. coordinator: the goal check after the step phase
# ---------------------------------------------------------------------------

class CoordinatorGoalCheckTests(unittest.TestCase):
    def _run(self, title, steps, verifier):
        coord = tc.TurnCoordinator(
            executor=_ScriptExecutor({}), verifier=verifier,
            snapshot=_Snapshot())
        task = coord.new_task(title, "routine", steps)
        receipt = coord.run_task(task)
        return task, receipt, verifier

    def test_goal_met_completes_with_verdict(self):
        verifier = _RecordingVerifier({
            "text 'step one done' present": [("met", "step observed")],
            GOAL_MET: [("met", "goal state observed")],
        })
        task, receipt, _v = self._run(
            GOAL_MET,
            _steps({"tool": "a", "args": {},
                    "expect": "text 'step one done' present"}),
            verifier)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.goal_verdict["verdict"], "met")
        self.assertEqual(receipt.goal_verdict["reason"],
                         "goal state observed")
        self.assertEqual(receipt.as_dict()["goal_verdict"],
                         receipt.goal_verdict)
        self.assertIn("Goal: met", format_receipt(receipt))

    def test_goal_not_met_fails_honestly(self):
        verifier = _RecordingVerifier({
            "text 'step one done' present": [("met", "step observed")],
            GOAL_UNMET: [("not-met",
                          "text 'file saved' is not present")],
        })
        task, receipt, _v = self._run(
            GOAL_UNMET,
            _steps({"tool": "a", "args": {},
                    "expect": "text 'step one done' present"}),
            verifier)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.goal_verdict["verdict"], "not-met")
        # The receipt names the goal and the unmet check.
        self.assertIn(GOAL_UNMET, receipt.reason)
        self.assertIn("file saved", receipt.reason)
        # Every action succeeded — the failure is the goal
        # check's alone, carried on the _verify_error path.
        self.assertEqual(receipt.failed, 0)
        self.assertEqual(receipt.error_kind, "verification_failed")

    def test_goal_unverifiable_completes_with_verdict_recorded(self):
        verifier = _RecordingVerifier({
            GOAL_UNGROUNDABLE: [("unverifiable",
                                 "names nothing groundable")],
        })
        task, receipt, _v = self._run(
            GOAL_UNGROUNDABLE,
            _steps({"tool": "a", "args": {},
                    "expect": "text 'step one done' present"}),
            verifier)
        # Steps passed, goal unjudgeable: completes, and says
        # so — never faked to "met".
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.goal_verdict["verdict"], "unverifiable")
        self.assertEqual(receipt.goal_verdict["reason"],
                         "names nothing groundable")
        self.assertNotEqual(receipt.goal_verdict["verdict"], "met")

    def test_step_without_expect_still_gets_goal_check(self):
        verifier = _RecordingVerifier({
            GOAL_MET: [("met", "goal state observed")],
        })
        task, receipt, _v = self._run(
            GOAL_MET,
            _steps({"tool": "a", "args": {}},
                   {"tool": "b", "args": {}}),
            verifier)
        # No step had an `expect`, so the step phase never ran
        # — but the goal check still did.
        self.assertEqual(task.verification, [])
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.goal_verdict["verdict"], "met")
        goal_calls = [c for c in verifier.calls if c[0] == GOAL_MET]
        self.assertEqual(len(goal_calls), 1)

    def test_failed_step_never_runs_goal_check(self):
        verifier = _RecordingVerifier({
            GOAL_MET: [("met", "goal state observed")],
        })
        coord = tc.TurnCoordinator(
            executor=_ScriptExecutor({"b": [_fail("boom")]}),
            verifier=verifier, snapshot=_Snapshot())
        task = coord.new_task(
            GOAL_MET, "routine",
            _steps({"tool": "a", "args": {}},
                   {"tool": "b", "args": {}}))
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "failed")
        # The task never reached the end of its action loop
        # with all steps passed, so no goal check ran.
        self.assertEqual(receipt.goal_verdict,
                         {"verdict": "unverifiable",
                          "reason": "no goal check recorded"})
        self.assertEqual(
            [c for c in verifier.calls if c[0] == GOAL_MET], [])

    def test_goal_check_uses_existing_verifier_not_picker(self):
        verifier = _RecordingVerifier({
            GOAL_MET: [("met", "goal state observed")],
        })
        task, receipt, _v = self._run(
            GOAL_MET,
            _steps({"tool": "a", "args": {}}),
            verifier)
        self.assertEqual(receipt.goal_verdict["verdict"], "met")
        goal_calls = [c for c in verifier.calls if c[0] == GOAL_MET]
        self.assertEqual(len(goal_calls), 1)
        expect_text, before, after, result_text = goal_calls[0]
        # The goal text is judged as an expectation, against a
        # fresh snapshot, with no "before" (the goal state is
        # judged now, not its causation) — the existing
        # verifier's machinery, never the brain's word for it.
        self.assertEqual(expect_text, GOAL_MET)
        self.assertIsNone(before)
        self.assertEqual(after, {"fg": "fake", "windows": ["fake"],
                                 "texts": []})


# ---------------------------------------------------------------------------
# 2. ledger: goal_verdict round-trips, old records load unverifiable
# ---------------------------------------------------------------------------

class LedgerGoalVerdictTests(unittest.TestCase):
    def _completed_task(self, title, goal_script):
        verifier = _RecordingVerifier({title: [goal_script]})
        coord = tc.TurnCoordinator(
            executor=_ScriptExecutor({}), verifier=verifier,
            snapshot=_Snapshot())
        task = coord.new_task(title, "routine",
                              _steps({"tool": "a", "args": {}}))
        receipt = coord.run_task(task)
        return task, receipt

    def test_ledger_round_trip_preserves_goal_verdict(self):
        task, receipt = self._completed_task(
            GOAL_MET, ("met", "goal state observed"))
        self.assertEqual(receipt.state, "completed")
        record = tl.task_to_record(task)
        self.assertEqual(record["goal_verdict"],
                         {"verdict": "met",
                          "reason": "goal state observed"})

    def test_ledger_record_never_checked_stores_default(self):
        # A task that never ran the check (failed mid-action)
        # stores the default, not an invented verdict.
        coord = tc.TurnCoordinator(
            executor=_ScriptExecutor({"b": [_fail("boom")]}),
            verifier=_RecordingVerifier({}),
            snapshot=_Snapshot())
        task = coord.new_task(
            GOAL_MET, "routine",
            _steps({"tool": "a", "args": {}},
                   {"tool": "b", "args": {}}))
        coord.run_task(task)
        record = tl.task_to_record(task)
        self.assertEqual(record["goal_verdict"],
                         {"verdict": "unverifiable",
                          "reason": "no goal check recorded"})

    def test_old_record_without_key_loads_unverifiable(self):
        task, receipt = self._completed_task(
            GOAL_MET, ("met", "goal state observed"))
        record = tl.task_to_record(task)
        # An old record: no "goal_verdict" key, interrupted
        # state so it is resumable (terminal records are
        # history, not rebuilt).
        del record["goal_verdict"]
        record["state"] = "paused"
        with tempfile.TemporaryDirectory() as tmp:
            ledger = tl.TaskLedger(
                path=pathlib.Path(tmp) / "ledger.json")
            rebuilt = ledger.rebuild_task(record)
        self.assertEqual(rebuilt.receipt().goal_verdict,
                         {"verdict": "unverifiable",
                          "reason": "no goal check recorded"})


# ---------------------------------------------------------------------------
# 3. run_goal: the picker's "done" is a claim the check judges
# ---------------------------------------------------------------------------

def _fake_snapshot():
    return {"fg": "fake", "windows": ["fake"], "texts": []}


def _fake_verify(script):
    def _verify(expect_text, before, after, result_text):
        return script.get(expect_text, ("unverifiable", "no script"))
    return _verify


class RunGoalGoalCheckTests(unittest.TestCase):
    def _run_goal(self, pick, verify_script, observe=None):
        async def _pick(goal, observation, history):
            # pick is itself async here; run_goal accepts plain
            # or async callables, so await it when awaitable.
            out = pick(goal, observation, history)
            if asyncio.iscoroutine(out):
                out = await out
            return out

        async def _obs():
            return _fake_snapshot() if observe is None else observe()

        return asyncio.run(rg.run_goal(
            GOAL_MET, pick_action=_pick, observe=_obs,
            verify_expect=_fake_verify(verify_script)))

    def test_picker_done_with_goal_unmet_does_not_complete(self):
        async def pick(goal, observation, history):
            return {"done": "all done, trust me"}

        result = self._run_goal(
            pick, {GOAL_MET: ("not-met", "the goal state is absent")})
        self.assertIn("Goal check: not-met", result)
        self.assertIn("Goal NOT complete", result)
        self.assertNotIn("Goal complete:", result)

    def test_picker_done_with_goal_met_completes(self):
        async def pick(goal, observation, history):
            return {"done": "all done"}

        result = self._run_goal(
            pick, {GOAL_MET: ("met", "goal state observed")})
        self.assertIn("Goal check: met", result)
        self.assertIn("Goal complete: all done", result)

    def test_picker_done_with_goal_unverifiable_completes_honestly(self):
        async def pick(goal, observation, history):
            return {"done": "all done"}

        result = self._run_goal(
            pick, {GOAL_MET: ("unverifiable", "nothing groundable")})
        # Completes because the steps passed — and says the
        # goal itself was never verified, never faked to met.
        self.assertIn("Goal check: unverifiable", result)
        self.assertIn("Goal complete: all done", result)
        self.assertNotIn("Goal check: met", result)

    def test_budget_stop_carries_goal_verdict_line(self):
        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {}}

        def _budget_int(name, default):
            return 1 if name == "goal_max_steps" else default

        with mock.patch.object(rg, "_goal_budget_int",
                               side_effect=_budget_int), \
                mock.patch.object(rg, "_known_tools",
                                  return_value=None), \
                mock.patch.object(
                    rg, "_execute_tool",
                    return_value={"status": "success",
                                  "result": "did it"}):
            result = self._run_goal(
                pick, {GOAL_MET: ("not-met", "still not there")})
        self.assertIn("Goal stopped: step budget exhausted", result)
        self.assertIn("Goal check: not-met", result)

    def test_strike_stop_carries_goal_verdict_line(self):
        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {}}

        # The observation never moves, so every step is a
        # no-change failure: retry once, strike, replan —
        # three strikes stop the goal.
        with mock.patch.object(rg, "_known_tools",
                               return_value=None), \
                mock.patch.object(
                    rg, "_execute_tool",
                    return_value={"status": "success",
                                  "result": "did it"}):
            result = self._run_goal(
                pick, {GOAL_MET: ("not-met", "still not there")})
        self.assertIn("strike limit reached", result)
        self.assertIn("Goal check: not-met", result)


if __name__ == "__main__":
    unittest.main()
