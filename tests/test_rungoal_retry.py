# B12 — transient-failure retry in run_goal.
#
# A failed tool execution used to halt the goal outright, no
# retry: one network blip, one busy app, one locked file killed
# a whole goal. Now a TRANSIENT execution failure retries the
# SAME step once (sharing the step's one retry with the
# no-change/not-met ladder), then strikes like not-met; only a
# genuine refusal still halts immediately.
#
# The transient/genuine distinction is read ONLY from the
# funnel's own taxonomy (core/funnel/tool_result.py), never a
# guessed sentence:
#   * status "failure", kind "handler_error" — the handler ran
#     and raised (network blip, app busy, file locked all land
#     here) — TRANSIENT, one retry.
#   * status "refused" (any kind) — a gate prevented execution;
#     the call never ran — halts, never retried.
#   * status "timeout" / "unknown" — the outcome is UNKNOWN
#     (audit A5: never auto-retry until reconciled; a retry
#     could double-execute an action that already landed) —
#     halts, never retried. This CORRECTS the audit's loose
#     "timeout" example: a deadline timeout is not a retryable
#     blip.
#   * status "success" carrying a refusal sentence — a refusal
#     returned as prose — halts, never retried.
#   * status "failure", kind "unavailable" / "wallpaper_not_set"
#     — a persistent state (missing dependency, unknown tool,
#     missing file, gone monitor); the identical call fails
#     identically — halts, never retried.
#   * any other kind, no kind, or a non-dict result —
#     unclassifiable; fail-closed, halts.
#
# Fakes only: no real cloud calls, no real tools, no real
# network. The picker is a script; the executor returns scripted
# funnel dicts; the observation changes every call (except the
# one test that needs a fixed snapshot, to force no-change).

import asyncio
import contextlib
import pathlib
import tempfile
import unittest
from unittest import mock

from core.brain import planner_plans as pp
from core.brain import planner_rungoal as rg
from core.brain import task_ledger as tl

GOAL = "organise the downloads folder"

TRANSIENT = {"status": "failure", "kind": "handler_error",
             "message": "Execution failed: the app is busy, try again"}
SUCCESS = {"status": "success", "result": "did it"}


def _fake_verify(script):
    def _verify(expect_text, before, after, result_text):
        return script.get(expect_text, ("unverifiable", "no script"))
    return _verify


class _ChangingSnapshot:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return {"fg": "fake", "n": self.n, "windows": ["fake"],
                "texts": []}


class _FixedSnapshot:
    def __call__(self):
        return {"fg": "fake", "n": 1, "windows": ["fake"], "texts": []}


class RungoalRetryTests(unittest.TestCase):
    def _ledger(self, tmp):
        return tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")

    def _run(self, pick, ledger, executor, verify_script=None,
             budgets=None, snapshot=None, gate=None):
        """Run run_goal with a scripted executor + changing snapshot.

        ``executor`` is a callable (tool, args) -> funnel dict.
        ``budgets`` maps budget names to values (mocked on
        rg._goal_budget_int / _goal_budget_seconds)."""
        snap = snapshot or _ChangingSnapshot()

        async def _pick(goal, observation, history):
            out = pick(goal, observation, history)
            if asyncio.iscoroutine(out):
                out = await out
            return out

        async def _obs():
            return snap()

        def _budget_int(name, default):
            return (budgets or {}).get(name, default)

        def _budget_seconds(name, default):
            return (budgets or {}).get(name, default)

        patches = [
            mock.patch.object(rg, "_goal_budget_int",
                              side_effect=_budget_int),
            mock.patch.object(rg, "_goal_budget_seconds",
                              side_effect=_budget_seconds),
            mock.patch.object(rg, "_known_tools",
                              return_value=None),
            mock.patch.object(rg, "_execute_tool",
                              side_effect=executor),
        ]
        if gate is not None:
            patches.append(mock.patch.object(rg, "_control_gate",
                                            side_effect=gate))
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            return asyncio.run(rg.run_goal(
                GOAL, pick_action=_pick, observe=_obs,
                verify_expect=_fake_verify(verify_script or {}),
                ledger=ledger))

    def _goal_record(self, ledger):
        records = [r for r in ledger.all_records()
                   if r.get("kind") == "goal"]
        self.assertEqual(len(records), 1)
        return records[0]

    def test_transient_failure_retries_same_step_and_completes(self):
        calls = []

        def executor(tool, args):
            calls.append((tool, args))
            return TRANSIENT if len(calls) == 1 else SUCCESS

        async def pick(goal, observation, history):
            if len(calls) >= 2:
                return {"done": "all done"}
            return {"tool": "fake_tool", "args": {"x": 1}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            result = self._run(
                pick, ledger, executor,
                verify_script={GOAL: ("met", "goal state observed")})
            self.assertIn("Goal complete: all done", result)
            self.assertIn("transient failure, retrying the same step",
                          result)
            # The same step was re-executed with the same tool/args.
            self.assertEqual(calls, [("fake_tool", {"x": 1}),
                                     ("fake_tool", {"x": 1})])
            record = self._goal_record(ledger)
            self.assertEqual(record["state"], "completed")
            self.assertEqual(record["steps_used"], 2)
            retries = [h for h in record["history"]
                       if h.get("event") == "retry"]
            self.assertEqual(len(retries), 1)
            self.assertEqual(retries[0]["kind"], "transient")
            self.assertEqual(retries[0]["tool"], "fake_tool")
            self.assertEqual(retries[0]["step"], 1)
            self.assertTrue(retries[0]["retry"])
            self.assertEqual(
                [h["event"] for h in record["history"]],
                ["retry", "completed"])

    def test_transient_failure_twice_strikes_like_not_met(self):
        calls = []

        def executor(tool, args):
            calls.append(tool)
            return TRANSIENT

        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            result = self._run(
                pick, ledger, executor,
                verify_script={GOAL: ("not-met", "goal not done")})
            self.assertIn("strike limit reached", result)
            self.assertIn("Goal check: not-met", result)
            # 3 steps x (first attempt + one retry) = 6 executions.
            self.assertEqual(len(calls), 6)
            record = self._goal_record(ledger)
            self.assertEqual(record["state"], "failed")
            self.assertEqual(record["strikes"], 3)
            self.assertEqual(
                [h["event"] for h in record["history"]],
                ["retry", "step_failed"] * 3)
            self.assertTrue(all(
                h["kind"] == "transient"
                for h in record["history"] if h["event"] == "retry"))
            self.assertTrue(all(
                h["strikes"] == i + 1
                for i, h in enumerate(
                    h for h in record["history"]
                    if h["event"] == "step_failed")))

    def test_genuine_refusal_halts_with_no_retry(self):
        calls = []

        def executor(tool, args):
            calls.append(tool)
            return {"status": "refused", "kind": "not_confirmed",
                    "message": "needs confirm=true"}

        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            result = self._run(pick, ledger, executor)
            self.assertIn("halted: needs confirm=true", result)
            self.assertIn("never retried or bypassed", result)
            self.assertEqual(len(calls), 1)
            record = self._goal_record(ledger)
            self.assertEqual(record["state"], "failed")
            self.assertEqual(
                [h["event"] for h in record["history"]], ["halted"])
            self.assertFalse(any(h.get("event") == "retry"
                                 for h in record["history"]))

    def test_timeout_halts_with_no_retry(self):
        # A5: a timeout's outcome is UNKNOWN — never auto-retried
        # until reconciled. A retry could double-execute an action
        # that already landed.
        calls = []

        def executor(tool, args):
            calls.append(tool)
            return {"status": "timeout", "outcome": "unknown",
                    "operation_id": "op-1",
                    "message": "timed out; do not auto-retry until "
                               "reconciled"}

        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            result = self._run(pick, ledger, executor)
            self.assertIn("halted: timed out", result)
            self.assertEqual(len(calls), 1)
            record = self._goal_record(ledger)
            self.assertEqual(record["state"], "failed")
            self.assertFalse(any(h.get("event") == "retry"
                                 for h in record["history"]))

    def test_persistent_failures_halt_with_no_retry(self):
        # wallpaper_not_set: the file/monitor state cannot change
        # by retrying the identical call. unavailable: the tool's
        # own machinery (or router) is missing. Both halt.
        cases = [
            {"status": "failure", "kind": "wallpaper_not_set",
             "message": "Wallpaper image not found: missing.png"},
            {"status": "failure", "kind": "unavailable",
             "message": "Unknown tool: nope. No domain router offers "
                        "this tool."},
        ]
        for failure in cases:
            calls = []

            def executor(tool, args, _failure=failure):
                calls.append(tool)
                return _failure

            async def pick(goal, observation, history):
                return {"tool": "fake_tool", "args": {}}

            with tempfile.TemporaryDirectory() as tmp:
                ledger = self._ledger(tmp)
                result = self._run(pick, ledger, executor)
                self.assertIn("halted:", result)
                self.assertEqual(len(calls), 1,
                                 f"retried a persistent {failure['kind']}")
                record = self._goal_record(ledger)
                self.assertFalse(any(h.get("event") == "retry"
                                     for h in record["history"]))

    def test_refusal_sentence_success_halts_with_no_retry(self):
        calls = []

        def executor(tool, args):
            calls.append(tool)
            return {"status": "success",
                    "result": "Refused: needs confirm=true"}

        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            result = self._run(pick, ledger, executor)
            self.assertIn("halted: refused:", result)
            self.assertEqual(len(calls), 1)
            record = self._goal_record(ledger)
            self.assertFalse(any(h.get("event") == "retry"
                                 for h in record["history"]))

    def test_retried_step_is_judged_by_goal_check(self):
        # A step that succeeds on the retry is a normal step: the
        # picker's "done" is judged by the B10 goal check, and the
        # goal completes only when the check says met.
        calls = []

        def executor(tool, args):
            calls.append(tool)
            return TRANSIENT if len(calls) == 1 else SUCCESS

        async def pick(goal, observation, history):
            if len(calls) >= 2:
                return {"done": "all done"}
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            result = self._run(
                pick, ledger, executor,
                verify_script={GOAL: ("met", "goal state observed")})
            self.assertIn("Goal complete: all done", result)
            self.assertIn("Goal check: met", result)
            record = self._goal_record(ledger)
            self.assertEqual(record["state"], "completed")
            self.assertEqual(record["goal_verdict"]["verdict"], "met")
            self.assertEqual(record["completed_tools"], ["fake_tool"])

    def test_retry_is_in_ledger_before_re_execution(self):
        # B11 crash-safety: the retry event must already be in
        # the ledger when the re-execution runs, so a crash there
        # does not grant a fresh retry.
        ledger_holder = {}
        calls = []

        def executor(tool, args):
            calls.append(tool)
            if len(calls) == 2:
                records = [r for r in
                           ledger_holder["ledger"].all_records()
                           if r.get("kind") == "goal"]
                self.assertEqual(len(records), 1)
                retries = [h for h in records[0]["history"]
                           if h.get("event") == "retry"]
                self.assertEqual(len(retries), 1)
                self.assertEqual(retries[0]["kind"], "transient")
                return SUCCESS
            return TRANSIENT

        async def pick(goal, observation, history):
            if len(calls) >= 2:
                return {"done": "all done"}
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            ledger_holder["ledger"] = ledger
            result = self._run(
                pick, ledger, executor,
                verify_script={GOAL: ("met", "goal state observed")})
            self.assertIn("Goal complete: all done", result)

    def test_transient_retry_shares_the_step_retry_with_ladder(self):
        # One retry per step, whichever mechanism uses it first:
        # a transient failure spends the step's retry, so a
        # no-change on the re-execution strikes immediately
        # instead of climbing the ladder a second time.
        calls = []
        picks = []

        def executor(tool, args):
            calls.append(tool)
            return TRANSIENT if len(calls) == 1 else SUCCESS

        async def pick(goal, observation, history):
            picks.append(list(history))
            if len(picks) >= 2:
                return {"done": "all done"}
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            result = self._run(
                pick, ledger, executor,
                verify_script={GOAL: ("met", "goal state observed")},
                snapshot=_FixedSnapshot())
            self.assertIn("Goal complete: all done", result)
            self.assertEqual(len(calls), 2)
            # Two picks only: the first pick, then the replan pick
            # after the strike — no ladder re-pick in between.
            self.assertEqual(len(picks), 2)
            record = self._goal_record(ledger)
            self.assertEqual(record["state"], "completed")
            self.assertEqual(record["steps_used"], 2)
            self.assertEqual(record["strikes"], 1)
            # No "completed" event: the goal finished on the
            # picker's {"done"} claim, which appends none — only
            # executed steps do.
            self.assertEqual(
                [h["event"] for h in record["history"]],
                ["retry", "step_failed"])

    def test_retry_re_runs_the_gate_before_re_execution(self):
        # The transient retry re-enters the loop top, so the gate
        # runs again first: a gate that refuses on the retry pass
        # halts the goal with the tool executed exactly once.
        calls = []
        gate_calls = []

        def executor(tool, args):
            calls.append(tool)
            return TRANSIENT if len(calls) == 1 else SUCCESS

        def gate(tool, args, snapshot):
            gate_calls.append(tool)
            if len(gate_calls) == 1:
                return True, "", ""
            return False, "control refused: stale view", ""

        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            result = self._run(pick, ledger, executor, gate=gate)
            self.assertIn("halted: control refused: stale view", result)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(gate_calls), 2)
            record = self._goal_record(ledger)
            self.assertEqual(record["state"], "failed")

    def test_gate_refusal_is_never_retried(self):
        # The gate refuses before any execution: the tool runs
        # zero times, and no retry is attempted.
        calls = []

        def executor(tool, args):
            calls.append(tool)
            return TRANSIENT

        def gate(tool, args, snapshot):
            return False, "control refused: stale view", ""

        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            result = self._run(pick, ledger, executor, gate=gate)
            self.assertIn("halted: control refused: stale view", result)
            self.assertEqual(len(calls), 0)
            record = self._goal_record(ledger)
            self.assertFalse(any(h.get("event") == "retry"
                                 for h in record["history"]))


class TransientClassifierTests(unittest.TestCase):
    """The classifier itself: status/kind only, never sentences."""

    def test_handler_error_is_transient(self):
        self.assertTrue(pp._is_transient_failure(
            {"status": "failure", "kind": "handler_error",
             "message": "Execution failed: boom"}))

    def test_persistent_kinds_are_not_transient(self):
        for kind in ("wallpaper_not_set", "unavailable"):
            self.assertFalse(pp._is_transient_failure(
                {"status": "failure", "kind": kind,
                 "message": "cannot run"}), kind)

    def test_refused_timeout_unknown_are_not_transient(self):
        for result in (
                {"status": "refused", "kind": "not_confirmed",
                 "message": "needs confirm=true"},
                {"status": "refused", "kind": "refused",
                 "message": "refused"},
                {"status": "timeout", "outcome": "unknown",
                 "message": "timed out"},
                {"status": "unknown",
                 "message": "cannot determine outcome"}):
            self.assertFalse(pp._is_transient_failure(result), result)

    def test_refusal_sentence_success_is_not_transient(self):
        self.assertFalse(pp._is_transient_failure(
            {"status": "success",
             "result": "Refused: needs confirm=true"}))

    def test_legacy_error_maps_by_kind(self):
        self.assertTrue(pp._is_transient_failure(
            {"status": "error", "kind": "handler_error",
             "message": "Execution failed: boom"}))
        self.assertFalse(pp._is_transient_failure(
            {"status": "error", "kind": "not_confirmed",
             "message": "needs confirm=true"}))
        self.assertFalse(pp._is_transient_failure(
            {"status": "error", "kind": "timeout",
             "message": "cool-down refusal"}))

    def test_unclassifiable_fails_closed(self):
        for result in (
                {"status": "failure", "kind": "something_new",
                 "message": "unknown kind"},
                {"status": "failure", "message": "no kind at all"},
                {"status": "failure"},
                "",
                None,
                0):
            self.assertFalse(pp._is_transient_failure(result), result)


if __name__ == "__main__":
    unittest.main()
