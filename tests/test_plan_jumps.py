"""B4 — plan steps can jump: a failed step branches to a named
recovery step (on_fail {"goto": n}) instead of only halt/skip.

Lab leg only. The real-Windows field leg — a real complex task
that recovers via a jump on his machine — is OWED, not this batch.

Gap proved first (from the tree): StepDef.from_mapping rejected
every non-halt/skip on_fail ("goto jumps are not implemented",
core/brain/task_coordinator_steps.py:63), and
run_routine_as_task fell back to the legacy loop for any goto
routine — so a jumping plan lost the ledger, the receipt, the
verification phase, plan approval and pause/resume (Fix 9's
whole point). The legacy loop itself already implements goto
(planner_routines.run_routine: budget len(actions)*3+10,
transcript ↪ lines) — this batch ports that semantics into the
coordinator through its own machinery, it does not invent a
second engine.

What this batch builds (and this suite proves): ONE jump rule —
a failed step jumps to its named step, recorded in the ledger +
receipt + one Activity line, bounded twice (per-step strikes at
the retry limit 3, global budget len(steps)*3+10 — the legacy
formula, one home), surviving pause/resume and ledger rebuild,
with the target's plan-approval digest checked at dispatch
exactly like any step, and a jump to a D4/C-14 step refused
outright (the target never runs). The verification phase's
strike-out does NOT jump (its bounded retry is the recovery
for an unmet expectation; re-entering the action loop from the
phase would break the retry-resume markers) — that boundary is
pinned here, not built.
"""

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

from core import plan_approval as pa  # noqa: E402
from core.brain import task_coordinator as tc  # noqa: E402
from core.brain import task_ledger as tl  # noqa: E402
from core.brain.task_coordinator import StepDef  # noqa: E402
from core.brain.task_coordinator_steps import (  # noqa: E402
    format_receipt, max_jumps, validate_step_jumps)


def _ok(result="done"):
    return {"status": "success", "result": result}


def _fail(message="simulated failure"):
    return {"status": "failure", "message": message}


class _ScriptExecutor:
    """Per-tool scripted results; records every call."""

    def __init__(self, script):
        # script: {tool: [result, ...]}; exhausted tools repeat
        # their last result.
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


class _ScriptVerifier:
    """Per-expect scripted verdicts; records every call."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, expect_text, before, after, result_text):
        self.calls.append(expect_text)
        verdicts = self.script.get(expect_text)
        if not verdicts:
            return ("unverifiable", "no script")
        if len(verdicts) > 1:
            return verdicts.pop(0)
        return verdicts[0]


class _Snapshot:
    def __call__(self):
        return {}


def _steps(*mappings):
    return [StepDef.from_mapping(m) for m in mappings]


def _coord(tmp, executor, verifier=None):
    ledger = tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")
    coord = tc.TurnCoordinator(executor=executor, ledger=ledger,
                               verifier=verifier, snapshot=_Snapshot())
    return coord, ledger


class JumpBasicsTests(unittest.TestCase):
    def tearDown(self):
        pa.clear_all()
        super().tearDown()

    def test_failed_step_jumps_to_named_recovery_and_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"download": [_fail("mirror down")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "download", "args": {},
                        "label": "download file",
                        "on_fail": {"goto": 2}},
                       {"tool": "mirror", "args": {},
                        "label": "try the mirror"},
                       {"tool": "notify", "args": {},
                        "label": "tell the user"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(executor.calls,
                             ["download", "mirror", "notify"])
            self.assertEqual(len(receipt.jumps), 1)
            event = receipt.jumps[0]
            self.assertEqual(event["from_index"], 0)
            self.assertEqual(event["to_index"], 1)
            self.assertTrue(event["jumped"])
            self.assertIn("mirror down", event["reason"])
            self.assertEqual(event["strike"], 1)
            text = format_receipt(receipt)
            self.assertIn("↪ download file", text)
            self.assertIn("jumping to step 2", text)
            self.assertIn("try the mirror", text)

    def test_forward_jump_skips_the_middle_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"a": [_fail("a broke")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {},
                        "on_fail": {"goto": 3}},
                       {"tool": "b", "args": {}},
                       {"tool": "c", "args": {}}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(executor.calls, ["a", "c"])
            self.assertEqual(len(receipt.jumps), 1)
            self.assertEqual(receipt.jumps[0]["to_index"], 2)

    def test_when_probe_failure_jumps(self):
        # A failed when-probe follows on_fail exactly like a
        # failed action: the step never runs, the jump does.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"probe": [_fail("not there")], "recover": [_ok()]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "act", "args": {},
                        "when": {"tool": "probe", "args": {}},
                        "on_fail": {"goto": 2}},
                       {"tool": "recover", "args": {}}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(executor.calls, ["probe", "recover"])
            self.assertEqual(len(receipt.jumps), 1)

    def test_check_failed_step_jumps(self):
        # A B12 result check that fails is a failure too: the
        # step jumps to its recovery instead of halting.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"act": [_ok("wrong text")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "act", "args": {},
                        "check": {"contains": "expected text"},
                        "on_fail": {"goto": 2}},
                       {"tool": "recover", "args": {}}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(executor.calls, ["act", "recover"])
            self.assertEqual(len(receipt.jumps), 1)
            self.assertIn("check failed", receipt.jumps[0]["reason"])

    def test_jump_keeps_verification_positionally_aligned(self):
        # The recovery step's expectation is verified in the
        # phase against ITS outcome, not the failed step's:
        # outcomes stay positionally aligned through a jump.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"a": [_fail("a broke")]})
            verifier = _ScriptVerifier(
                {"mirror landed": [("met", "file is there")]})
            coord, _ledger = _coord(tmp, executor, verifier=verifier)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {},
                        "on_fail": {"goto": 2}},
                       {"tool": "mirror", "args": {},
                        "expect": "mirror landed"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(len(receipt.verification), 1)
            self.assertEqual(receipt.verification[0]["verdict"], "met")
            self.assertEqual(receipt.verification[0]["label"], "mirror")


class JumpBoundsTests(unittest.TestCase):
    def tearDown(self):
        pa.clear_all()
        super().tearDown()

    def test_self_jump_strikes_out_at_retry_limit(self):
        # One step, failing forever, jumping to itself: it
        # strikes out after 3 consecutive failures (the
        # retry-strike limit), not after the global budget.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"a": [_fail("still down")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {},
                        "on_fail": {"goto": 1}}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertEqual(executor.calls, ["a", "a", "a"])
            self.assertEqual(len(receipt.jumps), 3)
            self.assertEqual([e["jumped"] for e in receipt.jumps],
                             [True, True, False])
            self.assertEqual([e["strike"] for e in receipt.jumps],
                             [1, 2, 3])
            self.assertIn("strike limit", receipt.reason)
            self.assertIn("strike limit", receipt.jumps[-1]["reason"])

    def test_two_step_loop_strikes_out_before_budget(self):
        # A <-> B, both failing: each step strikes out on
        # its own third failure, long before the global
        # budget (3*2+10 = 16) is anywhere near spent.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"a": [_fail("a down")], "b": [_fail("b down")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {},
                        "on_fail": {"goto": 2}},
                       {"tool": "b", "args": {},
                        "on_fail": {"goto": 1}}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertEqual(executor.calls,
                             ["a", "b", "a", "b", "a"])
            self.assertEqual(len(receipt.jumps), 5)
            self.assertEqual([e["jumped"] for e in receipt.jumps],
                             [True, True, True, True, False])
            self.assertIn("strike limit", receipt.reason)

    def test_success_resets_a_steps_strikes(self):
        # The reset rule, at its mechanism: a step that
        # failed twice, then succeeds, carries no strikes
        # into its next run — only consecutive failures
        # count (the planner's reset-on-met rule).
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"a": [_ok()]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}}))
            task._jump_strikes = {0: 2}
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(task._jump_strikes, {})

    def test_max_jumps_formula_matches_legacy(self):
        # The global budget is the legacy loop's formula,
        # one home: len(steps)*3+10.
        self.assertEqual(max_jumps(3), 19)
        self.assertEqual(max_jumps(1), 13)
        self.assertEqual(max_jumps(0), 10)

    def test_jump_budget_refusal(self):
        # Strikes bind first for every failure-driven loop
        # (a loop must repeat some step, and a step that
        # fails 3 times in a row strikes out), so the global
        # budget is a backstop, not the primary bound. Its
        # refusal path is proved here with a seeded jump
        # history: 16 jumps already taken in a 2-step plan
        # (budget 2*3+10 = 16) means the next jump is refused
        # with "budget spent" and the target never runs.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"a": [_fail("a broke")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {},
                        "on_fail": {"goto": 2}},
                       {"tool": "b", "args": {}}))
            task._jumps = [{
                "from_index": 0, "from_tool": "a",
                "from_label": "a", "reason": "seeded",
                "to_index": 1, "to_label": "b",
                "strike": 1, "jumped": True} for _ in range(16)]
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertEqual(executor.calls, ["a"])
            self.assertEqual(len(receipt.jumps), 17)
            self.assertFalse(receipt.jumps[-1]["jumped"])
            self.assertIn("budget spent", receipt.jumps[-1]["reason"])
            self.assertIn("budget spent", receipt.reason)


class JumpApprovalTests(unittest.TestCase):
    def tearDown(self):
        pa.clear_all()
        super().tearDown()

    def test_jump_target_changed_after_approval_is_not_covered(self):
        # The jump runs the target step (control flow is not
        # approval), but the plan-approval digest is checked
        # at dispatch exactly like any step: args changed
        # after approval mean no coverage — the funnel would
        # pop individually for it. The token is observed live
        # inside the executor via pa.current(). The steps use
        # real approvable tools (wallpaper_apply /
        # wallpaper_revert — the Fix 8 precedent) so the
        # approval itself is real, not a fake-tool artifact.
        with tempfile.TemporaryDirectory() as tmp:
            seen = []

            def execute(tool, args):
                seen.append((tool, pa.current()))
                if len(seen) == 1:
                    return _fail("first set failed")
                return _ok()

            coord, _ledger = _coord(tmp, execute)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "wallpaper_apply",
                        "args": {"image": "a.jpg"},
                        "on_fail": {"goto": 2}},
                       {"tool": "wallpaper_revert", "args": {},
                        "label": "revert"},
                       {"tool": "wallpaper_apply",
                        "args": {"image": "b.jpg"}}))
            out = pa.approve_plan(task.id, task.steps, [0, 1, 2])
            self.assertEqual(out["approved"], [0, 1, 2])
            # The recovery step's args change after approval.
            task.steps[1].args["image"] = "temp.jpg"
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(len(seen), 3)
            # Step 0 ran covered; the jumped-to step ran
            # uncovered (its digest no longer matches); the
            # last step ran covered again.
            self.assertEqual(seen[0][0], "wallpaper_apply")
            self.assertIsNotNone(seen[0][1])
            self.assertEqual(seen[1][0], "wallpaper_revert")
            self.assertIsNone(seen[1][1])
            self.assertEqual(seen[2][0], "wallpaper_apply")
            self.assertIsNotNone(seen[2][1])

    def test_jump_to_unapproved_step_is_not_covered(self):
        # Only step 0 was approved; the jump target (step 1)
        # was never approved, so it runs uncovered — the jump
        # confers no coverage of its own.
        with tempfile.TemporaryDirectory() as tmp:
            seen = []

            def execute(tool, args):
                seen.append((tool, pa.current()))
                if len(seen) == 1:
                    return _fail("first set failed")
                return _ok()

            coord, _ledger = _coord(tmp, execute)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "wallpaper_apply",
                        "args": {"image": "a.jpg"},
                        "on_fail": {"goto": 2}},
                       {"tool": "wallpaper_revert", "args": {}}))
            out = pa.approve_plan(task.id, task.steps, [0])
            self.assertEqual(out["approved"], [0])
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(len(seen), 2)
            self.assertIsNotNone(seen[0][1])
            self.assertEqual(seen[1][0], "wallpaper_revert")
            self.assertIsNone(seen[1][1])


class JumpRefusalTests(unittest.TestCase):
    def tearDown(self):
        pa.clear_all()
        super().tearDown()

    def test_jump_to_send_step_does_not_run_it(self):
        # D4/C-14: a jump to a send step is refused outright
        # — the target never runs, the task halts honestly,
        # and the refusal is recorded (not counted as a jump).
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"download": [_fail("down")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "download", "args": {},
                        "on_fail": {"goto": 2}},
                       {"tool": "send_email", "args": {}},
                       {"tool": "notify", "args": {}}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertEqual(executor.calls, ["download"])
            self.assertEqual(len(receipt.jumps), 1)
            self.assertFalse(receipt.jumps[0]["jumped"])
            self.assertIn("send", receipt.jumps[0]["reason"])
            self.assertIn("refused", receipt.reason)

    def test_jump_to_irreversible_step_does_not_run_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"download": [_fail("down")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "download", "args": {},
                        "on_fail": {"goto": 2}},
                       {"tool": "shred_file", "args": {}}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertEqual(executor.calls, ["download"])
            self.assertFalse(receipt.jumps[0]["jumped"])

    def test_jump_refusal_does_not_burn_the_budget(self):
        # A refused jump is recorded but not counted: the
        # task halts on the refusal, and no jump was taken.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"download": [_fail("down")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "download", "args": {},
                        "on_fail": {"goto": 2}},
                       {"tool": "send_email", "args": {}}))
            coord.run_task(task)
            taken = [e for e in task._jumps if e.get("jumped")]
            self.assertEqual(taken, [])


class JumpValidationTests(unittest.TestCase):
    def test_goto_shape_validation(self):
        # The canonical shape is {"goto": n}, n >= 1, the
        # only key. Anything else is rejected at build time.
        for bad in ({"goto": 0}, {"goto": -1}, {"goto": "2"},
                    {"goto": 1, "extra": 2}, {"jump": 1}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                StepDef.from_mapping({"tool": "a", "args": {},
                                      "on_fail": bad})
        with self.assertRaises(ValueError):
            StepDef.from_mapping({"tool": "a", "args": {},
                                  "on_fail": "goto"})
        good = StepDef.from_mapping({"tool": "a", "args": {},
                                     "on_fail": {"goto": 2}})
        self.assertEqual(good.on_fail, "goto")
        self.assertEqual(good.goto, 1)

    def test_goto_out_of_range_rejected_at_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({})
            coord, _ledger = _coord(tmp, executor)
            with self.assertRaises(ValueError):
                coord.new_task(
                    "t", "routine",
                    _steps({"tool": "a", "args": {},
                            "on_fail": {"goto": 99}},
                           {"tool": "b", "args": {}}))
            # Directly: the plan-level validator names it.
            with self.assertRaises(ValueError):
                validate_step_jumps(_steps(
                    {"tool": "a", "args": {}, "on_fail": {"goto": 3}},
                    {"tool": "b", "args": {}}))

    def test_handle_turn_rejects_out_of_range_goto(self):
        # handle_turn records a failed task with the
        # validation error named, never runs a jump to
        # nowhere.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({})
            coord, _ledger = _coord(tmp, executor)
            result = coord.handle_turn(
                "run routine bad", routine_names=("bad",),
                routine_steps_for=lambda _n: [
                    {"tool": "a", "args": {}, "on_fail": {"goto": 5}},
                    {"tool": "b", "args": {}}])
            self.assertTrue(result["handled"])
            self.assertEqual(result["receipt"].state, "failed")
            self.assertIn("goto 5", result["receipt"].reason)


class JumpResumeTests(unittest.TestCase):
    def tearDown(self):
        pa.clear_all()
        super().tearDown()

    def test_jump_survives_pause_and_resume(self):
        # Pause after the recovery step completes: the
        # resumed task continues from the next step with the
        # jump count intact — nothing re-runs.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"download": [_fail("down")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "download", "args": {},
                        "on_fail": {"goto": 2}},
                       {"tool": "mirror", "args": {}},
                       {"tool": "notify", "args": {}}))

            def pause_after_mirror(task_obj, outcome):
                if outcome.tool == "mirror":
                    coord.pause_task(task_obj.id)

            receipt = coord.run_task(task, on_step=pause_after_mirror)
            self.assertEqual(task.state, "paused")
            self.assertEqual(len(task._jumps), 1)
            self.assertEqual(task._jump_strikes, {0: 1})
            resumed = coord.resume_task(task.id)
            self.assertEqual(resumed.state, "completed")
            self.assertEqual(executor.calls,
                             ["download", "mirror", "notify"])
            self.assertEqual(len(task._jumps), 1)

    def test_jump_survives_ledger_rebuild_mid_jump(self):
        # The strongest resume case: the task pauses between
        # the jump decision and the target's execution (the
        # executor pauses it while the failing step runs).
        # The ledger record must resume AT the jump target
        # with the jump history and strike count intact — a
        # crash here grants no fresh jump and loses no jump.
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = pathlib.Path(tmp) / "ledger.json"
            ledger = tl.TaskLedger(path=ledger_path)
            holder = {}

            def pausing_executor(tool, args):
                if tool == "download":
                    coord.pause_task(holder["task"].id)
                    return _fail("down")
                return _ok()

            coord = tc.TurnCoordinator(executor=pausing_executor,
                                       ledger=ledger)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "download", "args": {},
                        "on_fail": {"goto": 2}},
                       {"tool": "mirror", "args": {}},
                       {"tool": "notify", "args": {}}))
            holder["task"] = task
            receipt = coord.run_task(task)
            self.assertEqual(task.state, "paused")
            self.assertEqual(task._index, 1)
            self.assertEqual(len(task._jumps), 1)
            record = ledger.get(task.id)
            self.assertIsNotNone(record)
            self.assertEqual(record.get("index"), 1)
            self.assertEqual(len(record.get("jumps") or []), 1)
            self.assertEqual(record.get("jump_strikes"),
                             [{"step_index": 0, "strikes": 1}])
            self.assertEqual(record.get("steps")[0].get("goto"), 2)
            # A fresh coordinator rebuilds from the record
            # alone and resumes at the mirror — download
            # never re-runs, the jump is not re-taken.
            calls = []

            def clean_executor(tool, args):
                calls.append(tool)
                return _ok()

            coord2 = tc.TurnCoordinator(executor=clean_executor,
                                        ledger=tl.TaskLedger(
                                            path=ledger_path))
            resumed = coord2.resume_ledger_task(record)
            self.assertEqual(resumed.state, "completed")
            self.assertEqual(calls, ["mirror", "notify"])
            self.assertEqual(len(resumed.jumps), 1)
            self.assertTrue(resumed.jumps[0]["jumped"])

    def test_old_record_without_jumps_rebuilds_by_walk(self):
        # Backward compatibility: a record with no jump
        # history rebuilds exactly as before (outcome walk).
        # The task is paused mid-run (not failed — terminal
        # records are history, never resumable).
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}},
                       {"tool": "b", "args": {}},
                       {"tool": "c", "args": {}}))

            def pause_after_a(task_obj, outcome):
                if outcome.tool == "a":
                    coord.pause_task(task_obj.id)

            coord.run_task(task, on_step=pause_after_a)
            self.assertEqual(task.state, "paused")
            record = _ledger.get(task.id)
            self.assertEqual(record.get("jumps"), [])
            self.assertEqual(record.get("jump_strikes"), [])
            rebuilt = tl.TaskLedger().rebuild_task(record)
            self.assertEqual(rebuilt._index, 1)
            self.assertEqual(rebuilt._jumps, [])
            self.assertEqual(rebuilt._jump_strikes, {})


class JumpActivityTests(unittest.TestCase):
    def tearDown(self):
        pa.clear_all()
        super().tearDown()

    def test_jump_writes_one_activity_line(self):
        # The Activity line is written through the real
        # writer (its __file__-derived path patched to a
        # fake tree, the plan-timeout test's pattern): one
        # line per jump taken, named plan_jump.
        with tempfile.TemporaryDirectory() as tmp:
            from core.brain import task_coordinator_lifecycle as tcl
            fake_root = pathlib.Path(tmp) / "fake_root"
            (fake_root / "core" / "brain").mkdir(parents=True)
            executor = _ScriptExecutor({"download": [_fail("down")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "download", "args": {},
                        "on_fail": {"goto": 2}},
                       {"tool": "mirror", "args": {}}))
            with mock.patch.object(
                    tcl, "__file__",
                    str(fake_root / "core" / "brain"
                        / "task_coordinator_lifecycle.py")):
                coord.run_task(task)
            lines = (fake_root / "data" / "activity.log").read_text(
                encoding="utf-8").splitlines()
            jumped = [ln for ln in lines if "plan_jump" in ln]
            self.assertEqual(len(jumped), 1)
            self.assertIn("jumping to step 2", jumped[0])

    def test_handle_turn_runs_goto_routine_as_task(self):
        # The coordinator path for a saved routine with a
        # goto: handle_turn builds it as a task (no legacy
        # fallback), and the jump runs inside the task.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"a": [_fail("a broke")]})
            coord, _ledger = _coord(tmp, executor)
            result = coord.handle_turn(
                "run routine jumpy", routine_names=("jumpy",),
                routine_steps_for=lambda _n: [
                    {"tool": "a", "args": {}, "on_fail": {"goto": 2}},
                    {"tool": "b", "args": {}}])
            self.assertTrue(result["handled"])
            self.assertEqual(result["receipt"].state, "completed")
            self.assertEqual(executor.calls, ["a", "b"])
            self.assertEqual(len(result["receipt"].jumps), 1)


class JumpRefusalDoesNotJumpPhaseTests(unittest.TestCase):
    def tearDown(self):
        pa.clear_all()
        super().tearDown()

    def test_verification_strike_out_does_not_jump(self):
        # Boundary, pinned not built: a step whose action
        # SUCCEEDED but whose expectation is not met 3 times
        # fails the task in the verification phase — it does
        # NOT jump to its goto target. The phase's bounded
        # retry is the recovery for an unmet expectation.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"a": [_ok("wrong")]})
            verifier = _ScriptVerifier(
                {"file landed": [("not-met", "no file")]})
            coord, _ledger = _coord(tmp, executor, verifier=verifier)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {},
                        "expect": "file landed",
                        "on_fail": {"goto": 2}},
                       {"tool": "b", "args": {}}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            # a once in the action loop, b once, then the
            # phase's two bounded retry re-runs of a (the
            # third not-met verdict strikes out) — no jump
            # anywhere in that sequence.
            self.assertEqual(executor.calls, ["a", "b", "a", "a"])
            self.assertEqual(receipt.jumps, [])


if __name__ == "__main__":
    unittest.main()
