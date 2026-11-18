"""Fix 9 — complex task completion: the voice path runs a
routine as a coordinator task (plan -> act -> verify ->
recover -> report), with bounded retry-on-not-met, step-
boundary pause/resume, drop-mid-task resume, and voice plan
approval for safe steps only.

Lab leg only. The real-Windows field leg — a real messy
complex task on his machine, end to end, by voice — is OWED,
not this batch.

Gap proved first (see core/brain/planner_routines.
run_routine_as_task docstring): the voice path's run_routine
tool called planner.run_routine directly — no task id, no
ledger, no receipt, no verification phase, no retry, no plan
approval, no pause/resume — while the typed path's "run
routine X" already went through TurnCoordinator.handle_turn.
Three brief premises were WRONG and are pinned here, not
rebuilt: retry-on-not-met already exists (with a 3-strike
budget), step-boundary pause/resume already exists, and the
voice-goal adoption machinery already exists (gated — see
Stage1GateTests, which pins the gate still closed).

What this batch builds (and this suite proves): the voice
routine as a coordinator task; B12 result checks judged in
the coordinator's _run_step; stop-signal honouring for
voice-routine tasks — a failsafe trip (goal-pill Stop)
cancels, a session-epoch change (drop + reconnect) pauses
with a resumable record, never cancels; the ledger's check
+ stop-flag round-trip and its cross-writer lock; the
cross-thread plan ask (request_plan_decision) and its GUI
handler/slot.
"""

import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_PKGROOT = ROOT / ".pkgroot"
if _PKGROOT.is_dir() and str(_PKGROOT) not in sys.path:
    sys.path.insert(0, str(_PKGROOT))

from core import injection  # noqa: E402
from core import plan_approval as pa  # noqa: E402
from core import session_epoch as epoch  # noqa: E402
from core import failsafe  # noqa: E402
from core.brain import task_coordinator as tc  # noqa: E402
from core.brain import task_coordinator_tail as tct  # noqa: E402
from core.brain import task_ledger as tl  # noqa: E402
from core.brain import planner_routines as pr  # noqa: E402
from core.brain.task_coordinator import StepDef  # noqa: E402

try:
    # The GUI modules import voice_assistant.* at top level,
    # which does not resolve in the lab tree — the guards
    # suite's established pattern (test_refactor_phase6_guards):
    # a temp dir holding a symlink named voice_assistant
    # pointing at the tree. The mixin under test only reads
    # timeout constants from plan_approval, so the duplicate
    # module instance this creates cannot leak state.
    _va_dir = tempfile.mkdtemp(prefix="va_pkg_")
    _va_link = os.path.join(_va_dir, "voice_assistant")
    os.symlink(str(ROOT), _va_link)
    sys.path.insert(0, _va_dir)
    from gui.window_services_confirm import ServicesConfirmMixin
    _GUI = True
except Exception:  # noqa: BLE001 — no Qt / no symlink: skip
    ServicesConfirmMixin = None
    _GUI = False


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

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


def _routine(name, actions):
    return {"name": name, "actions": actions}


class _RoutineRunnerMixin:
    """Patches run_routine_as_task's seams: the routines store
    and the ONE shared coordinator construction site
    (task_coordinator_tail._default_coordinator — patched to
    return a real TurnCoordinator with the test's fake
    executor / verifier / snapshot and the temp ledger).
    Everything else runs for real: handle_turn, the action
    loop, the verification phase, the retry budget, the plan
    hook, the receipt."""

    def _patch_routine_path(self, routine, executor=None,
                            verifier=None, snapshot=None):
        self._tmp = tempfile.TemporaryDirectory()
        self.ledger = tl.TaskLedger(
            path=pathlib.Path(self._tmp.name) / "ledger.json")
        self.executor = executor or _ScriptExecutor({})
        self.verifier = verifier
        self.snapshot = snapshot or _Snapshot()
        self._RealCoord = tc.TurnCoordinator

        def _factory(*, ledger=None, executor=None):
            return self._RealCoord(
                executor=self.executor, ledger=self.ledger,
                verifier=self.verifier, snapshot=self.snapshot)

        self._patches = [
            mock.patch.object(
                pr, "_load_routines",
                return_value=([routine], "")),
            mock.patch.object(
                tct, "_default_coordinator", new=_factory),
        ]
        for patch in self._patches:
            patch.start()

    def _unpatch_routine_path(self):
        for patch in reversed(getattr(self, "_patches", [])):
            patch.stop()
        self._patches = []
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    def _run_routine(self, name):
        return pr.run_routine_as_task(name)


# ---------------------------------------------------------------------------
# 1. voice routine as a coordinator task, end to end
# ---------------------------------------------------------------------------

class VoiceRoutineAsTaskTests(_RoutineRunnerMixin, unittest.TestCase):
    def tearDown(self):
        self._unpatch_routine_path()
        pa.clear_all()
        super().tearDown()

    def test_voice_routine_runs_plan_act_verify_recover_report(self):
        routine = _routine("morning", [
            {"tool": "read_a", "args": {}},
            {"tool": "write_b", "args": {}},
            {"tool": "read_c", "args": {}},
        ])
        self._patch_routine_path(
            routine, executor=_ScriptExecutor({}))
        result = self._run_routine("morning")
        # The receipt, not a bare "done": every step named,
        # state completed, honest tally.
        self.assertIn("as a task:", result)
        self.assertIn("State: completed", result)
        self.assertIn("read_a", result)
        self.assertIn("write_b", result)
        self.assertIn("read_c", result)
        self.assertNotIn("did not run", result)
        # The task is in the ledger, completed, with all three
        # outcomes — a drop or a restart can see it.
        records = self.ledger.all_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["state"], "completed")
        self.assertEqual(len(records[0]["outcomes"]), 3)
        self.assertEqual(self.executor.calls,
                         ["read_a", "write_b", "read_c"])

    def test_voice_routine_failure_reports_honestly_what_did_not_finish(self):
        routine = _routine("fragile", [
            {"tool": "step_one", "args": {}},
            {"tool": "step_two", "args": {}},
            {"tool": "step_three", "args": {}},
        ])
        executor = _ScriptExecutor({"step_two": [_fail("disk full")]})
        self._patch_routine_path(routine, executor=executor)
        result = self._run_routine("fragile")
        self.assertIn("State: failed", result)
        self.assertIn("step_two", result)
        self.assertIn("disk full", result)
        # The un-run step is named as remaining — never claimed.
        self.assertIn("step_three", result)
        self.assertNotIn("all 3 action(s) completed", result)
        records = self.ledger.all_records()
        self.assertEqual(records[0]["state"], "failed")
        self.assertEqual(len(records[0]["outcomes"]), 2)
        self.assertEqual(executor.calls, ["step_one", "step_two"])

    def test_voice_routine_retries_not_met_within_budget_then_succeeds(self):
        # Already-built retry (3-strike budget) now reachable
        # from the voice path: first verify not-met, then met.
        routine = _routine("checked", [
            {"tool": "step_a", "args": {},
             "expect": "the file exists"},
            {"tool": "step_b", "args": {}},
        ])
        executor = _ScriptExecutor({})
        verifier = _ScriptVerifier({
            "the file exists": [("not-met", "absent"),
                                ("met", "present")]})
        self._patch_routine_path(routine, executor=executor,
                                 verifier=verifier)
        result = self._run_routine("checked")
        self.assertIn("State: completed", result)
        # The failed step re-ran once (not forever). The
        # verification phase runs AFTER the action loop, so
        # the order is a, b, then the retried a — not a, a, b.
        self.assertEqual(executor.calls,
                         ["step_a", "step_b", "step_a"])
        self.assertIn("Retry:", result)
        self.assertIn("retried", result)
        records = self.ledger.all_records()
        retries = records[0].get("retries", [])
        self.assertEqual(len(retries), 2)
        self.assertTrue(retries[0]["retried"])
        self.assertEqual(retries[0]["strike"], 1)

    def test_voice_routine_check_failure_is_a_failure_not_a_success(self):
        # B12 result check, judged in the coordinator's
        # _run_step: the tool returned success but its result
        # does not contain what the step declared.
        routine = _routine("picky", [
            {"tool": "step_a", "args": {}},
            {"tool": "step_b", "args": {},
             "check": {"contains": "done"}},
            {"tool": "step_c", "args": {}},
        ])
        executor = _ScriptExecutor(
            {"step_b": [_ok("something else entirely")]})
        self._patch_routine_path(routine, executor=executor)
        result = self._run_routine("picky")
        self.assertIn("State: failed", result)
        self.assertIn("check_failed", result)
        self.assertIn("step_c", result)  # named as remaining
        self.assertNotIn("all 3 action(s) completed", result)
        records = self.ledger.all_records()
        self.assertEqual(records[0]["outcomes"][1]["kind"],
                         "check_failed")

    def test_voice_routine_check_passes_when_result_matches(self):
        routine = _routine("picky_ok", [
            {"tool": "step_a", "args": {},
             "check": {"contains": "done"}},
        ])
        executor = _ScriptExecutor({"step_a": [_ok("all done here")]})
        self._patch_routine_path(routine, executor=executor)
        result = self._run_routine("picky_ok")
        self.assertIn("State: completed", result)
        self.assertNotIn("check_failed", result)

    def test_voice_routine_goto_runs_as_coordinator_task(self):
        # B4 (INTENTIONAL pin reversal): a goto jump used to
        # fall back to the legacy loop ("no coordinator
        # equivalent"). The coordinator implements jumps now,
        # so a goto routine runs as a task — with the jump
        # recorded on the receipt, not a fallback note. Here
        # step_b fails and jumps back to step 1: step_a
        # re-runs and succeeds, step_b fails again, and
        # after its third consecutive failure it strikes out
        # instead of jumping again (calls a,b,a,b,a,b).
        routine = _routine("jumpy", [
            {"tool": "step_a", "args": {}},
            {"tool": "step_b", "args": {},
             "on_fail": {"goto": 1}},
        ])
        executor = _ScriptExecutor({"step_b": [_fail("mirror down")]})
        self._patch_routine_path(routine, executor=executor)
        result = self._run_routine("jumpy")
        self.assertNotIn("legacy loop", result)
        self.assertIn("State: failed", result)
        self.assertIn("↪", result)
        self.assertIn("strike limit", result)
        self.assertEqual(executor.calls,
                         ["step_a", "step_b",
                          "step_a", "step_b",
                          "step_a", "step_b"])

    def test_voice_routine_nested_run_falls_back_to_legacy_and_says_so(self):
        routine = _routine("nested", [
            {"tool": "run_routine", "args": {"name": "other"}},
        ])
        self._patch_routine_path(routine, executor=_ScriptExecutor({}))
        result = self._run_routine("nested")
        self.assertTrue(
            result.startswith(
                "Routine runs on the legacy loop: a nested"),
            result)

    def test_voice_routine_unknown_name_reports_honestly(self):
        routine = _routine("real", [{"tool": "step_a", "args": {}}])
        self._patch_routine_path(routine, executor=_ScriptExecutor({}))
        result = self._run_routine("missing")
        self.assertIn("No routine named 'missing'", result)
        self.assertEqual(self.ledger.all_records(), [])


# ---------------------------------------------------------------------------
# 2. step-boundary pause/resume (coordinator, direct)
# ---------------------------------------------------------------------------

class StepBoundaryPauseResumeTests(unittest.TestCase):
    def test_pause_at_boundary_keeps_completed_steps_and_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = tl.TaskLedger(
                path=pathlib.Path(tmp) / "ledger.json")
            executor = _ScriptExecutor({})
            coord = tc.TurnCoordinator(executor=executor, ledger=ledger)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}},
                       {"tool": "b", "args": {}}))

            def pause_after_first(task_obj, outcome):
                coord.pause_task(task_obj.id)

            receipt = coord.run_task(task, on_step=pause_after_first)
            self.assertEqual(task.state, "paused")
            self.assertEqual(len(task.outcomes), 1)
            self.assertEqual(task.outcomes[0].tool, "a")
            self.assertEqual(executor.calls, ["a"])
            # Resume from the boundary: "a" never re-runs.
            resumed = coord.resume_task(task.id)
            self.assertEqual(resumed.state, "completed")
            self.assertEqual(len(task.outcomes), 2)
            self.assertEqual(executor.calls, ["a", "b"])

    def test_failed_step_records_cause_and_keeps_completed(self):
        executor = _ScriptExecutor({"b": [_fail("boom")]})
        coord = tc.TurnCoordinator(executor=executor)
        task = coord.new_task(
            "t", "routine",
            _steps({"tool": "a", "args": {}},
                   {"tool": "b", "args": {}},
                   {"tool": "c", "args": {}}))
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.completed, 1)
        self.assertEqual(receipt.failed, 1)
        self.assertIn("c", receipt.remaining)
        self.assertIn("b", receipt.reason)
        self.assertEqual(executor.calls, ["a", "b"])


# ---------------------------------------------------------------------------
# 3. drop mid-task + stop signals (Fix 4 interaction)
# ---------------------------------------------------------------------------

class DropResumeTests(_RoutineRunnerMixin, unittest.TestCase):
    def tearDown(self):
        self._unpatch_routine_path()
        pa.clear_all()
        try:
            failsafe.reset()
        except Exception:  # noqa: BLE001 — teardown never raises
            pass
        super().tearDown()

    def test_drop_mid_routine_pauses_at_boundary_and_resumes(self):
        # The drop + human reconnect, as the lab can stage it:
        # the executor bumps the session epoch while step 1
        # runs (a reconnect bumps it at the session seam), so
        # the next step boundary sees the session moved and
        # PAUSES the task — a cancelled record is terminal
        # in the ledger and could never resume, so cancelling
        # here would lose the task exactly the way the old
        # orphaned run did.
        routine = _routine("droppy", [
            {"tool": "step_a", "args": {}},
            {"tool": "step_b", "args": {}},
        ])
        calls = []

        def dropping_executor(tool, args):
            calls.append(tool)
            if tool == "step_a":
                epoch.bump_epoch("test drop + reconnect")
            return _ok()

        self._patch_routine_path(routine, executor=dropping_executor)
        result = self._run_routine("droppy")
        self.assertIn("State: paused", result)
        self.assertIn("session restarted", result)
        records = self.ledger.all_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["state"], "paused")
        self.assertEqual(len(records[0]["outcomes"]), 1)
        self.assertEqual(records[0]["outcomes"][0]["tool"], "step_a")
        self.assertEqual(calls, ["step_a"])
        # Resume from the ledger: step_a is kept, step_b runs.
        coord = self._RealCoord(executor=_ScriptExecutor({}),
                                ledger=self.ledger)
        resumed = coord.resume_ledger_task(records[0])
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed.state, "completed")
        record = self.ledger.get(records[0]["task_id"])
        self.assertEqual(record["state"], "completed")
        self.assertEqual(len(record["outcomes"]), 2)

    def test_stop_pill_trip_halts_at_boundary(self):
        routine = _routine("stoppable", [
            {"tool": "step_a", "args": {}},
            {"tool": "step_b", "args": {}},
        ])
        calls = []

        def tripping_executor(tool, args):
            calls.append(tool)
            if tool == "step_a":
                failsafe.trip_now("test goal-pill Stop")
            return _ok()

        self._patch_routine_path(routine, executor=tripping_executor)
        try:
            result = self._run_routine("stoppable")
        finally:
            failsafe.reset()
        self.assertIn("State: cancelled", result)
        self.assertIn("failsafe", result)
        self.assertEqual(calls, ["step_a"])
        records = self.ledger.all_records()
        self.assertEqual(records[0]["state"], "cancelled")
        self.assertEqual(len(records[0]["outcomes"]), 1)

    def test_unflagged_task_ignores_epoch_bump(self):
        # The stop-signal rule is flag-gated: a task without
        # it (every existing caller, including typed turns)
        # behaves exactly as before.
        with tempfile.TemporaryDirectory() as tmp:
            ledger = tl.TaskLedger(
                path=pathlib.Path(tmp) / "ledger.json")
            calls = []

            def bumping_executor(tool, args):
                calls.append(tool)
                if tool == "a":
                    epoch.bump_epoch("test bump, unflagged task")
                return _ok()

            coord = tc.TurnCoordinator(executor=bumping_executor,
                                       ledger=ledger)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}},
                       {"tool": "b", "args": {}}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(calls, ["a", "b"])


# ---------------------------------------------------------------------------
# 4. voice plan approval (Fix 8 module, voice path)
# ---------------------------------------------------------------------------

class VoicePlanApprovalTests(_RoutineRunnerMixin, unittest.TestCase):
    def tearDown(self):
        self._unpatch_routine_path()
        pa.clear_all()
        pa.register_plan_handler(None)
        super().tearDown()

    def _plan_routine(self):
        return _routine("planned", [
            {"tool": "wallpaper_revert", "args": {}},
            {"tool": "send_email",
             "args": {"to": "a@b.c", "subject": "s", "body": "b"}},
        ])

    def _snapshotting_executor(self, live):
        """A fake executor that snapshots the plan-approval
        record live, at each step's dispatch. The approval
        dies with the task by design (Fix 8: cleared on
        terminal state), so a post-run read can never see
        it — the live snapshot is the proof it covered the
        run."""
        def execute(tool, args):
            records = self.ledger.all_records()
            if records:
                live.update(
                    pa._APPROVALS.get(records[0]["task_id"], {}))
            return _ok()
        return execute

    def test_voice_plan_approves_safe_steps_once_destructive_still_individual(self):
        seen = {}

        def fake_dialog(task, plan):
            seen["plan"] = plan
            return [0]  # the human approved the safe step only

        pa.register_plan_handler(fake_dialog)
        live = {}
        self._patch_routine_path(
            self._plan_routine(),
            executor=self._snapshotting_executor(live))
        result = self._run_routine("planned")
        self.assertIn("State: completed", result)
        plan = seen["plan"]
        self.assertEqual(
            [e["tool"] for e in plan["approvable"]],
            ["wallpaper_revert"])
        self.assertEqual(
            [e["tool"] for e in plan["still_individual"]],
            ["send_email"])
        self.assertEqual(
            plan["still_individual"][0]["reason"],
            "destructive or system action — always asks individually")
        # The approval was live during the run for the safe
        # step only (and gone afterwards, by design).
        self.assertIn(0, live)
        self.assertNotIn(1, live)
        records = self.ledger.all_records()
        self.assertEqual(
            pa._APPROVALS.get(records[0]["task_id"], {}), {})

    def test_voice_plan_handler_trying_to_approve_destructive_is_refused(self):
        # approve_plan re-validates: even a handler that
        # returns the destructive index cannot record it.
        pa.register_plan_handler(lambda task, plan: [0, 1])
        live = {}
        self._patch_routine_path(
            self._plan_routine(),
            executor=self._snapshotting_executor(live))
        real_approve = pa.approve_plan
        recorded = {}

        def recording_approve(task_id, steps, indices):
            out = real_approve(task_id, steps, indices)
            recorded["out"] = out
            return out

        with mock.patch.object(pa, "approve_plan",
                               side_effect=recording_approve):
            self._run_routine("planned")
        self.assertEqual(recorded["out"]["approved"], [0])
        self.assertEqual(
            [r["index"] for r in recorded["out"]["refused"]], [1])
        self.assertIn(0, live)
        self.assertNotIn(1, live)

    def test_voice_plan_no_handler_asks_each_time(self):
        # No GUI handler registered (headless): no dialog, no
        # approval, the task still runs and asks per step.
        self._patch_routine_path(self._plan_routine(),
                                 executor=_ScriptExecutor({}))
        result = self._run_routine("planned")
        self.assertIn("State: completed", result)
        records = self.ledger.all_records()
        self.assertEqual(pa._APPROVALS.get(records[0]["task_id"], {}),
                         {})

    def test_request_plan_decision_timeout_asks_each_time(self):
        def slow_handler(task, plan):
            time.sleep(0.3)
            return [0]

        pa.register_plan_handler(slow_handler)
        task = tc.Task("t", "routine", _steps(
            {"tool": "wallpaper_revert", "args": {}}))
        decided = pa.request_plan_decision(task, timeout_s=0.05)
        self.assertEqual(decided, [])

    def test_request_plan_decision_no_handler_asks_each_time(self):
        pa.register_plan_handler(None)
        task = tc.Task("t", "routine", _steps(
            {"tool": "wallpaper_revert", "args": {}}))
        self.assertEqual(pa.request_plan_decision(task), [])


# ---------------------------------------------------------------------------
# 5. ledger round-trip: check + stop flag, and no cross-writer clobber
# ---------------------------------------------------------------------------

class LedgerRoundTripTests(unittest.TestCase):
    def test_check_and_stop_flag_survive_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = tl.TaskLedger(
                path=pathlib.Path(tmp) / "ledger.json")
            coord = tc.TurnCoordinator(executor=_ScriptExecutor({}),
                                       ledger=ledger)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {},
                        "check": {"contains": "done"}},
                       {"tool": "b", "args": {}}))
            task._honor_stop_signals = True
            # A pending record is not resumable by design
            # (rebuild refuses non-interrupted states), so
            # pause first — the realistic interrupted state.
            coord.pause_task(task.id)
            coord._ledger_write(task)
            record = ledger.get(task.id)
            self.assertEqual(record["state"], "paused")
            self.assertEqual(record["steps"][0]["check"],
                             {"contains": "done"})
            self.assertTrue(record["honor_stop_signals"])
            rebuilt = ledger.rebuild_task(record)
            self.assertEqual(rebuilt.steps[0].check,
                             {"contains": "done"})
            self.assertTrue(rebuilt._honor_stop_signals)

    def test_two_ledger_writers_do_not_clobber_each_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "ledger.json"
            first = tl.TaskLedger(path=path)
            second = tl.TaskLedger(path=path)
            coord_a = tc.TurnCoordinator(executor=_ScriptExecutor({}),
                                         ledger=first)
            coord_b = tc.TurnCoordinator(executor=_ScriptExecutor({}),
                                         ledger=second)
            task_a = coord_a.new_task("a", "routine",
                                      _steps({"tool": "a", "args": {}}))
            task_b = coord_b.new_task("b", "routine",
                                      _steps({"tool": "b", "args": {}}))
            # Each writer sees the other's record: no stale
            # cache, no last-write-wins loss.
            self.assertIsNotNone(first.get(task_b.id))
            self.assertIsNotNone(second.get(task_a.id))
            stored = first.all_records()
            self.assertEqual({r["task_id"] for r in stored},
                             {task_a.id, task_b.id})
            # Interleaved transitions still land on both sides.
            coord_a.run_task(task_a)
            coord_b.run_task(task_b)
            self.assertEqual(first.get(task_a.id)["state"], "completed")
            self.assertEqual(second.get(task_b.id)["state"], "completed")
            self.assertEqual(first.get(task_b.id)["state"], "completed")
            self.assertEqual(second.get(task_a.id)["state"], "completed")


# ---------------------------------------------------------------------------
# 6. guards intact: Fix 4 budgets, D4/C-14, Fix 8 eligibility, Stage 1 gate
# ---------------------------------------------------------------------------

class GuardsIntactTests(unittest.TestCase):
    def test_fix4_budgets_unchanged(self):
        # Fix 9 consumes these, never retunes them. Static
        # pins (the owning modules need Qt to import).
        status = (ROOT / "gui" / "window_session_status.py").read_text(
            encoding="utf-8")
        self.assertIn("_RECONNECT_EPISODE_BUDGET = 3", status)
        self.assertIn("_RECONNECT_EPISODE_WINDOW_S = 600.0", status)
        helpers = (ROOT / "gui" / "window_widgets_helpers.py").read_text(
            encoding="utf-8")
        self.assertIn("_RECONNECT_DELAYS = (2, 5, 15, 30)", helpers)

    def test_plan_approval_still_refuses_tainted_calls(self):
        # D4 / C-14: a content-prompted call is never
        # plan-approved, even with a recorded approval.
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "new_note.txt"
            args = {"filepath": str(dest), "content": "hi"}
            task = tc.Task("t", "routine", _steps(
                {"tool": "write_file", "args": args}))
            pa.approve_plan(task.id, task.steps, [0])
            task._index = 0
            token = pa.begin_step_dispatch(task, task.steps[0],
                                           dict(args))
            try:
                # "web page" is an external-content source, so
                # arrival alone soft-taints (v5.46) — no
                # injection-pattern text needed for the pin.
                injection.note_untrusted(
                    "web page", "some page text")
                self.assertTrue(injection.tainted())
                self.assertIsNone(
                    pa.covering_call("write_file", dict(args)))
                injection.clear_taint(force=True)
                self.assertIsNotNone(
                    pa.covering_call("write_file", dict(args)))
            finally:
                pa.end_step_dispatch(token)
                injection.clear_taint(force=True)
                pa.clear_all()

    def test_fix8_eligibility_unchanged(self):
        # A brand-new single-destination writer is still
        # approvable; a send step is still not.
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "new_note.txt"
            task = tc.Task("t", "routine", _steps(
                {"tool": "write_file",
                 "args": {"filepath": str(dest), "content": "hi"}},
                {"tool": "send_email",
                 "args": {"to": "a@b.c", "subject": "s",
                          "body": "b"}}))
            plan = pa.preview(task)
            self.assertEqual(
                [e["tool"] for e in plan["approvable"]],
                ["write_file"])
            self.assertEqual(
                [e["tool"] for e in plan["still_individual"]],
                ["send_email"])

    def test_stage1_gate_still_closed(self):
        # Fix 9 improves the general complex-task code; it
        # does NOT lift the Stage 1 hold and does not feed
        # the companion pair into the voice bridge.
        from core.voice import voice_text_bridge as bridge
        from core import brain_capabilities as bc
        gate = bridge.VOICE_FREEFORM_COMPANION_GATE
        self.assertIsInstance(gate, str)
        self.assertIn("GATED, not built", gate)
        self.assertFalse(bc.can_freeform_goal_now("gemini_live"))
        self.assertIsNone(bridge.get_text_brain())
        source = (ROOT / "core" / "brain" / "planner_routines.py"
                  ).read_text(encoding="utf-8")
        self.assertNotIn("voice_text_bridge", source)
        self.assertNotIn("text_brain", source)
        self.assertNotIn("VOICE_FREEFORM_COMPANION_GATE", source)

    def test_retry_budget_still_three_strikes(self):
        from core.brain import task_coordinator_classify as tcc
        self.assertEqual(tcc._RETRY_STRIKE_LIMIT, 3)


# ---------------------------------------------------------------------------
# 7. GUI handler/slot for the voice plan ask (Qt only)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_GUI, "Qt/GUI imports unavailable")
class GuiPlanApprovalTests(unittest.TestCase):
    def setUp(self):
        pa.clear_all()
        pa.register_plan_handler(None)
        self._dialog_patch = None

    def tearDown(self):
        if self._dialog_patch is not None:
            try:
                self._dialog_patch.stop()
            except Exception:  # noqa: BLE001 — teardown never raises
                pass
            self._dialog_patch = None
        pa.clear_all()
        pa.register_plan_handler(None)

    def _window(self, dialog_result):
        """A bare mixin host: the real handler + slot, a fake
        signal bridge that delivers synchronously (the GUI
        thread in production), and a patched dialog."""
        host = SimpleNamespace()
        mixin = ServicesConfirmMixin
        for name in ("_plan_approval_handler",
                     "_on_plan_approval_requested"):
            setattr(host, name,
                    getattr(mixin, name).__get__(host, type(host)))

        class _Signal:
            def __init__(self, owner):
                self.owner = owner

            def emit(self, task, plan):
                self.owner.emitted.append((task, plan))
                self.owner._on_plan_approval_requested(task, plan)

        host._plan_approval_lock = threading.Lock()
        host._plan_approval_event = None
        host._plan_approval_result = []
        host._plan_approval_outcome = "ask_each_time"
        host.emitted = []
        host.signals = SimpleNamespace(
            plan_approval_requested=_Signal(host))
        # R5: the slot presents through the _ex form, which
        # reports the outcome — patch that, not the []-only
        # wrapper. dialog_result is the (indices, outcome)
        # pair the real dialog returns.
        self._dialog_patch = mock.patch(
            "gui.plan_approval_dialog.present_plan_approval_dialog_ex",
            return_value=dialog_result)
        self._dialog_patch.start()
        return host

    def test_handler_marshals_to_gui_slot_and_returns_indices(self):
        host = self._window(([0], "approved"))
        task = tc.Task("t", "routine", _steps(
            {"tool": "wallpaper_revert", "args": {}}))
        plan = pa.preview(task)
        # R5: the handler returns the dict form {"approved",
        # "outcome"} — the indices ride inside it, and the
        # coordinator reads the outcome (a timeout pauses).
        self.assertEqual(
            host._plan_approval_handler(task, plan),
            {"approved": [0], "outcome": "approved"})
        self.assertEqual(len(host.emitted), 1)
        self.assertIs(host.emitted[0][0], task)
        self.assertIs(host.emitted[0][1], plan)

    def test_broken_dialog_asks_each_time(self):
        host = self._window(([], "approved"))
        self._dialog_patch.stop()
        with mock.patch(
                "gui.plan_approval_dialog.present_plan_approval_dialog_ex",
                side_effect=RuntimeError("dialog broken")):
            task = tc.Task("t", "routine", _steps(
                {"tool": "wallpaper_revert", "args": {}}))
            # R5: a broken dialog reports "error" — the
            # coordinator still runs step by step on it,
            # exactly as on "ask each time"; only the name
            # changed, from [] to the dict form.
            self.assertEqual(
                host._plan_approval_handler(task, pa.preview(task)),
                {"approved": [], "outcome": "error"})


if __name__ == "__main__":
    unittest.main()
