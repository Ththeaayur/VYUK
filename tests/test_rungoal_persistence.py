# B11 — run_goal persistence + resumable budget stops.
#
# A goal writes its goal text, history, and progress to the
# task ledger (the ONE durable record) after every completed
# step, so a crash mid-goal leaves an interrupted record
# instead of nothing. A step/time budget stop is a RESUMABLE
# paused record, not a terminal stop: resume_goal continues
# from the next pick with the history intact, in a FRESH
# budget window (window step count restarts at 0, window
# number + 1, resume count + 1 — all visible in the record).
# The budgets themselves are unchanged (25 / 600 / 3).
#
# Fakes only: no real cloud calls, no real tools. The picker
# is a script; the executor and the verifier are fakes; the
# observation changes every call so steps count as changed.

import asyncio
import pathlib
import tempfile
import time
import unittest
from unittest import mock

from core.brain import planner_rungoal as rg
from core.brain import task_ledger as tl

GOAL = "organise the downloads folder"


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


class RungoalPersistenceTests(unittest.TestCase):
    def _ledger(self, tmp):
        return tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")

    def _run(self, pick, ledger, verify_script=None, budgets=None):
        """Run run_goal with a changing snapshot + fake tool.

        ``budgets`` maps budget names to values (mocked on
        rg._goal_budget_int / _goal_budget_seconds)."""
        snapshot = _ChangingSnapshot()

        async def _pick(goal, observation, history):
            out = pick(goal, observation, history)
            if asyncio.iscoroutine(out):
                out = await out
            return out

        async def _obs():
            return snapshot()

        def _budget_int(name, default):
            return (budgets or {}).get(name, default)

        def _budget_seconds(name, default):
            return (budgets or {}).get(name, default)

        with mock.patch.object(rg, "_goal_budget_int",
                               side_effect=_budget_int), \
                mock.patch.object(rg, "_goal_budget_seconds",
                                  side_effect=_budget_seconds), \
                mock.patch.object(rg, "_known_tools",
                                  return_value=None), \
                mock.patch.object(
                    rg, "_execute_tool",
                    return_value={"status": "success",
                                  "result": "did it"}):
            return asyncio.run(rg.run_goal(
                GOAL, pick_action=_pick, observe=_obs,
                verify_expect=_fake_verify(verify_script or {}),
                ledger=ledger))

    def _records(self, ledger):
        return ledger.all_records()

    def _goal_record(self, ledger):
        records = [r for r in self._records(ledger)
                   if r.get("kind") == "goal"]
        self.assertEqual(len(records), 1)
        return records[0]

    def test_goal_writes_record_per_step(self):
        seen = []

        async def pick(goal, observation, history):
            # On the 2nd pick (after step 1 completed) the
            # ledger must already hold step 1's record.
            if len(seen) == 1:
                records = [r for r in self._ledger.all_records()
                           if r.get("kind") == "goal"]
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["state"], "running")
                self.assertEqual(records[0]["steps_used"], 1)
                self.assertEqual(len(records[0]["history"]), 1)
            seen.append(list(history))
            if len(seen) >= 4:
                return {"done": "all done"}
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            self._ledger = self._ledger(tmp)
            result = self._run(
                pick, self._ledger,
                verify_script={GOAL: ("met", "goal state observed")})
            self.assertIn("Goal complete: all done", result)
            record = self._goal_record(self._ledger)
            self.assertEqual(record["state"], "completed")
            self.assertEqual(record["goal_text"], GOAL)
            self.assertEqual(record["steps_used"], 3)
            self.assertEqual(record["completed_tools"],
                             ["fake_tool"] * 3)
            self.assertEqual(
                [h["event"] for h in record["history"]],
                ["completed"] * 3)
            self.assertEqual(record["goal_verdict"]["verdict"], "met")
            self.assertEqual(record["budget_windows_used"], 1)
            self.assertEqual(record["resume_count"], 0)

    def test_step_budget_stop_is_resumable_paused_record(self):
        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            result = self._run(
                pick, ledger,
                verify_script={GOAL: ("not-met", "still not there")},
                budgets={"goal_max_steps": 1})
            self.assertIn("Goal stopped: step budget exhausted", result)
            self.assertIn("Goal check: not-met", result)
            self.assertIn("Resumable: resume_goal('", result)
            record = self._goal_record(ledger)
            self.assertEqual(record["state"], "paused")
            self.assertEqual(record["reason"], "budget_exhausted (step)")
            self.assertEqual(record["steps_used"], 1)
            self.assertEqual(record["window_steps"], 1)
            self.assertEqual(record["budget_windows_used"], 1)
            self.assertEqual(record["resume_count"], 0)
            self.assertEqual(record["goal_verdict"]["verdict"], "not-met")
            goal_id = record["task_id"]

            # Resume: the picker sees the pre-resume history
            # intact, then finishes the goal.
            seen_histories = []

            async def resume_pick(goal, observation, history):
                seen_histories.append(list(history))
                return {"done": "finished after resume"}

            async def _obs():
                return {"fg": "fake", "n": 99, "windows": ["fake"],
                        "texts": []}

            with mock.patch.object(rg, "_known_tools",
                                   return_value=None):
                resumed = asyncio.run(rg.resume_goal(
                    goal_id, ledger=ledger, pick_action=resume_pick,
                    observe=_obs,
                    verify_expect=_fake_verify(
                        {GOAL: ("met", "goal state observed")})))
            self.assertIn("Resuming goal (resume 1, budget window 2",
                          resumed)
            self.assertIn("Goal complete: finished after resume", resumed)
            self.assertEqual(len(seen_histories), 1)
            self.assertEqual(len(seen_histories[0]), 1)
            self.assertEqual(seen_histories[0][0]["event"], "completed")
            final = self._goal_record(ledger)
            self.assertEqual(final["state"], "completed")
            self.assertEqual(final["steps_used"], 1)
            self.assertEqual(final["resume_count"], 1)
            self.assertEqual(final["budget_windows_used"], 2)
            self.assertEqual(len(final["history"]), 1)
            self.assertEqual(final["goal_verdict"]["verdict"], "met")

    def test_time_budget_stop_is_resumable(self):
        calls = []

        async def pick(goal, observation, history):
            calls.append(1)
            return {"tool": "fake_tool", "args": {}}

        # The fake executor sleeps past the tiny time budget,
        # so the loop-top check halts on time after step 1.
        def _slow_execute(tool, args):
            time.sleep(0.12)
            return {"status": "success", "result": "did it"}

        snapshot = _ChangingSnapshot()

        async def _pick(goal, observation, history):
            out = pick(goal, observation, history)
            if asyncio.iscoroutine(out):
                out = await out
            return out

        async def _obs():
            return snapshot()

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            with mock.patch.object(
                    rg, "_goal_budget_seconds",
                    side_effect=lambda name, default: 0.05
                    if name == "goal_max_seconds" else default), \
                    mock.patch.object(rg, "_known_tools",
                                      return_value=None), \
                    mock.patch.object(rg, "_execute_tool",
                                      side_effect=_slow_execute):
                result = asyncio.run(rg.run_goal(
                    GOAL, pick_action=_pick, observe=_obs,
                    verify_expect=_fake_verify(
                        {GOAL: ("not-met", "still not there")}),
                    ledger=ledger))
            self.assertIn("the time budget was exhausted", result)
            self.assertIn("Resumable: resume_goal('", result)
            record = self._goal_record(ledger)
            self.assertEqual(record["state"], "paused")
            self.assertEqual(record["reason"], "budget_exhausted (time)")
            self.assertEqual(record["steps_used"], 1)

            async def resume_pick(goal, observation, history):
                return {"done": "finished after resume"}

            with mock.patch.object(rg, "_known_tools",
                                   return_value=None):
                resumed = asyncio.run(rg.resume_goal(
                    record["task_id"], ledger=ledger,
                    pick_action=resume_pick, observe=_obs,
                    verify_expect=_fake_verify(
                        {GOAL: ("met", "goal state observed")})))
            self.assertIn("Goal complete: finished after resume", resumed)
            final = self._goal_record(ledger)
            self.assertEqual(final["state"], "completed")

    def test_crash_mid_goal_leaves_interrupted_record_that_resumes(self):
        calls = []

        def _crashing_execute(tool, args):
            calls.append(1)
            if len(calls) >= 3:
                raise RuntimeError("boom: tool blew up mid-goal")
            return {"status": "success", "result": "did it"}

        snapshot = _ChangingSnapshot()

        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {}}

        async def _obs():
            return snapshot()

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            with mock.patch.object(rg, "_known_tools",
                                   return_value=None), \
                    mock.patch.object(rg, "_execute_tool",
                                      side_effect=_crashing_execute):
                with self.assertRaises(RuntimeError):
                    asyncio.run(rg.run_goal(
                        GOAL, pick_action=pick, observe=_obs,
                        verify_expect=_fake_verify({}),
                        ledger=ledger))
            # The crash left the last per-step write: running,
            # 2 completed steps, nothing lost.
            record = self._goal_record(ledger)
            self.assertEqual(record["state"], "running")
            self.assertEqual(record["steps_used"], 2)
            self.assertEqual(
                [h["event"] for h in record["history"]],
                ["completed"] * 2)

            # Resume from the interrupted record: the crashed
            # step is re-picked (it has no completed event),
            # then the goal finishes with its full history.
            seen_histories = []

            async def resume_pick(goal, observation, history):
                seen_histories.append(list(history))
                if len(seen_histories) >= 2:
                    return {"done": "finished after crash"}
                return {"tool": "fake_tool", "args": {}}

            with mock.patch.object(rg, "_known_tools",
                                   return_value=None), \
                    mock.patch.object(
                        rg, "_execute_tool",
                        return_value={"status": "success",
                                      "result": "did it"}):
                resumed = asyncio.run(rg.resume_goal(
                    record["task_id"], ledger=ledger,
                    pick_action=resume_pick, observe=_obs,
                    verify_expect=_fake_verify(
                        {GOAL: ("met", "goal state observed")})))
            self.assertIn("Goal complete: finished after crash", resumed)
            self.assertEqual(len(seen_histories[0]), 2)
            final = self._goal_record(ledger)
            self.assertEqual(final["state"], "completed")
            self.assertEqual(final["steps_used"], 3)
            self.assertEqual(
                [h["event"] for h in final["history"]],
                ["completed"] * 3)
            self.assertEqual(final["resume_count"], 1)

    def test_resumed_goal_carries_full_history_on_completion(self):
        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            self._run(pick, ledger,
                      verify_script={GOAL: ("not-met", "still not there")},
                      budgets={"goal_max_steps": 2})
            record = self._goal_record(ledger)
            self.assertEqual(record["state"], "paused")
            self.assertEqual(record["steps_used"], 2)

            async def resume_pick(goal, observation, history):
                if len(history) >= 3:
                    return {"done": "all three done"}
                return {"tool": "fake_tool", "args": {}}

            snapshot = _ChangingSnapshot()

            async def _obs():
                return snapshot()

            with mock.patch.object(rg, "_known_tools",
                                   return_value=None), \
                    mock.patch.object(
                        rg, "_execute_tool",
                        return_value={"status": "success",
                                      "result": "did it"}):
                resumed = asyncio.run(rg.resume_goal(
                    record["task_id"], ledger=ledger,
                    pick_action=resume_pick, observe=_obs,
                    verify_expect=_fake_verify(
                        {GOAL: ("met", "goal state observed")})))
            self.assertIn("Goal complete: all three done", resumed)
            final = self._goal_record(ledger)
            self.assertEqual(final["state"], "completed")
            self.assertEqual(final["steps_used"], 3)
            self.assertEqual(final["completed_tools"],
                             ["fake_tool"] * 3)
            self.assertEqual(
                [h["event"] for h in final["history"]],
                ["completed"] * 3)

    def test_budget_remeasured_from_resume(self):
        """Each resume starts a FRESH window: with a 1-step
        budget the goal pauses, resumes, pauses again after
        exactly one more step, and only then completes."""
        picks = []

        async def pick(goal, observation, history):
            picks.append(1)
            return {"tool": "fake_tool", "args": {}}

        snapshot = _ChangingSnapshot()

        async def _obs():
            return snapshot()

        async def resume_pick(goal, observation, history):
            picks.append(1)
            if len(picks) >= 3:
                return {"done": "done at last"}
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            self._run(pick, ledger,
                      verify_script={GOAL: ("not-met", "still not there")},
                      budgets={"goal_max_steps": 1})
            first = self._goal_record(ledger)
            self.assertEqual(first["state"], "paused")
            self.assertEqual(first["window_steps"], 1)
            self.assertEqual(first["budget_windows_used"], 1)

            # Resume 1: one step runs, then the fresh window
            # exhausts again — the budget was re-measured,
            # not carried over (a carried-over budget would
            # stop with zero steps run).
            with mock.patch.object(rg, "_goal_budget_int",
                                   side_effect=lambda n, d: 1
                                   if n == "goal_max_steps" else d), \
                    mock.patch.object(rg, "_known_tools",
                                      return_value=None), \
                    mock.patch.object(
                        rg, "_execute_tool",
                        return_value={"status": "success",
                                      "result": "did it"}):
                resumed = asyncio.run(rg.resume_goal(
                    first["task_id"], ledger=ledger,
                    pick_action=resume_pick, observe=_obs,
                    verify_expect=_fake_verify(
                        {GOAL: ("not-met", "still not there")})))
            self.assertIn("Goal stopped: step budget exhausted", resumed)
            second = self._goal_record(ledger)
            self.assertEqual(second["state"], "paused")
            self.assertEqual(second["steps_used"], 2)
            self.assertEqual(second["window_steps"], 1)
            self.assertEqual(second["budget_windows_used"], 2)
            self.assertEqual(second["resume_count"], 1)

            # Resume 2: the done pick completes it.
            with mock.patch.object(rg, "_goal_budget_int",
                                   side_effect=lambda n, d: 1
                                   if n == "goal_max_steps" else d), \
                    mock.patch.object(rg, "_known_tools",
                                      return_value=None), \
                    mock.patch.object(
                        rg, "_execute_tool",
                        return_value={"status": "success",
                                      "result": "did it"}):
                done = asyncio.run(rg.resume_goal(
                    first["task_id"], ledger=ledger,
                    pick_action=resume_pick, observe=_obs,
                    verify_expect=_fake_verify(
                        {GOAL: ("met", "goal state observed")})))
            self.assertIn("Goal complete: done at last", done)
            final = self._goal_record(ledger)
            self.assertEqual(final["state"], "completed")
            self.assertEqual(final["resume_count"], 2)
            self.assertEqual(final["budget_windows_used"], 3)

    def test_goal_that_never_started_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            result = asyncio.run(rg.run_goal(
                "", pick_action=lambda g, o, h: {"done": "x"},
                ledger=ledger))
            self.assertIn("no goal text", result)
            self.assertEqual(self._records(ledger), [])
            result = asyncio.run(rg.run_goal(
                GOAL, pick_action=None, ledger=ledger))
            self.assertIn("no pick_action", result)
            self.assertEqual(self._records(ledger), [])

    def test_terminal_goal_record_is_not_resumable(self):
        async def pick(goal, observation, history):
            return {"done": "all done"}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            self._run(pick, ledger,
                      verify_script={GOAL: ("met", "goal state observed")})
            record = self._goal_record(ledger)
            self.assertEqual(record["state"], "completed")
            with self.assertRaises(ValueError):
                asyncio.run(rg.resume_goal(
                    record["task_id"], ledger=ledger,
                    pick_action=pick))

            # A failed goal (done, not-met) is terminal too.
            with tempfile.TemporaryDirectory() as tmp2:
                ledger2 = self._ledger(tmp2)
                self._run(pick, ledger2,
                          verify_script={
                              GOAL: ("not-met", "not there")})
                record2 = self._goal_record(ledger2)
                self.assertEqual(record2["state"], "failed")
                with self.assertRaises(ValueError):
                    asyncio.run(rg.resume_goal(
                        record2["task_id"], ledger=ledger2,
                        pick_action=pick))

    def test_resume_of_unknown_goal_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            with self.assertRaises(ValueError):
                asyncio.run(rg.resume_goal(
                    "no-such-goal", ledger=ledger,
                    pick_action=lambda g, o, h: {"done": "x"}))

    def test_goal_records_are_separate_from_task_records(self):
        """list_interrupted (tasks) never lists a goal; the goal
        surfaces through list_interrupted_goals; rebuild_task
        refuses a goal record."""
        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            self._run(pick, ledger,
                      verify_script={GOAL: ("not-met", "still not there")},
                      budgets={"goal_max_steps": 1})
            record = self._goal_record(ledger)
            self.assertEqual(record["state"], "paused")
            self.assertEqual(ledger.list_interrupted(), [])
            goals = ledger.list_interrupted_goals()
            self.assertEqual(len(goals), 1)
            self.assertEqual(goals[0]["goal_id"], record["task_id"])
            self.assertEqual(goals[0]["goal_text"], GOAL)
            self.assertEqual(goals[0]["steps_used"], 1)
            with self.assertRaises(ValueError):
                ledger.rebuild_task(record)
            # And the resume state rebuilds cleanly.
            state = ledger.rebuild_goal(record)
            self.assertEqual(state["goal_text"], GOAL)
            self.assertEqual(len(state["history"]), 1)
            self.assertEqual(state["resume_count"], 1)
            self.assertEqual(state["budget_windows_used"], 2)
            self.assertEqual(state["window_steps"], 0)


if __name__ == "__main__":
    unittest.main()
