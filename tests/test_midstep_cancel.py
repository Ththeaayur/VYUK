"""B20 — mid-step cancellation: a Stop pressed while a step runs
ends the step promptly, honestly, and with its partial progress
intact in the ledger.

Lab leg only. The real-Windows field leg — a real long step on
his machine that ends promptly when he presses Stop — is OWED,
not this batch.

Gap proved first (from the tree): the coordinator read the stop
signal only at step boundaries (_stop_reason at
core/brain/task_coordinator_execution.py:100, called at
:289/:882/:946/:1090; run_goal's _stop_checks at
core/brain/planner_rungoal.py:428, called at :557/:691), and the
funnel's cooperative token (REL-03, core/funnel/tool_cancel.py)
existed but ZERO handlers declared it and the coordinator never
supplied one — so a `long` tool ran up to 900 s (the default in
core/funnel/tools_handlers_limits.py:152) before Stop took
effect.

What this batch builds (and this suite proves): ONE design — a
cooperative cancellation token per running step, registered
while the step runs (core/brain/task_cancel.py), cancelled by
cancel_task (typed "stop" / GUI cancel) or by the ONE failsafe
trip listener (goal pill), threaded through _execute →
_funnel_execute → registry.execute → handlers that declare
cancel_token, checked at the tool's natural yield points. A
step stopped this way records outcome "cancelled" — a third
outcome, never success, never failure — the task stops as
"cancelled" with partial progress in the ledger (B11), never
retried (B12), never a lesson (B17), never a goal check (B10).

Rejected alternatives (recorded in code, not built): killing
the step's thread mid-write (a half-applied action — the B14
failure again); a hard timeout on every step (a timeout is not
a cancellation); ignoring the stop until the step finishes
(today's behaviour — the gap itself); cancelling by deleting
the task record (partial progress must stay in the ledger).

Fakes only: no real threads killed, no real cloud calls, no
real waiting — a slow fake tool yields on its token, a drip
HTTP server paces the download, a slow copy2 paces the sync.
"""

import asyncio
import contextlib
import http.server
import inspect
import os
import pathlib
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_PKGROOT = ROOT / ".pkgroot"
if _PKGROOT.is_dir() and str(_PKGROOT) not in sys.path:
    sys.path.insert(0, str(_PKGROOT))

from core import failsafe  # noqa: E402
from core.brain import planner_routines as pr  # noqa: E402
from core.brain import planner_rungoal as rg  # noqa: E402
from core.brain import task_coordinator as tc  # noqa: E402
from core.brain import task_ledger as tl  # noqa: E402
from core.brain.task_coordinator import StepDef  # noqa: E402
from core.funnel.tool_cancel import (  # noqa: E402
    CancellationToken, ToolCancelled, run_with_cancel)
from core.funnel.tool_result import (  # noqa: E402
    NON_SUCCESS_STATUSES, cancelled_result, is_funnel_shaped,
    is_non_success)

GOAL = "organise the downloads folder"


# B-D8r: canned winget table output (same shape as
# tests/test_app_management.py) for the winget -3 block below —
# the winget tools route through the B-D8 wrapper now, so the
# fake is injected into the wrapper's backend, not patched onto
# the tools' old module-level helpers.
_SEARCH_VLC = """Name                 Id                    Version   Source
-------------------------------------------------------------------
VLC media player     VideoLAN.VLC          3.0.20    winget
"""

_LIST_VLC = """Name                 Id                    Version   Source
-------------------------------------------------------------------
VLC media player     VideoLAN.VLC          3.0.20    winget
"""

_UPGRADE_PENDING = """Name                 Id                    Version   Available Source
--------------------------------------------------------------------------------
VLC media player     VideoLAN.VLC          3.0.20    3.0.21    winget
"""


class _CancellingWinget:
    """Canned winget.exe: search + pending read fine, the mutating
    runs return the B20 -3 sentinel (the child was stopped)."""

    def __init__(self):
        self.calls = []

    def which(self):
        return True

    def run(self, args, timeout_s, cancel_token=None):
        self.calls.append(list(args))
        sub = args[1] if len(args) > 1 else ""
        if sub == "search":
            return 0, _SEARCH_VLC, ""
        if sub == "upgrade" and "--all" in args:
            return -3, "", "cancelled"
        if sub == "upgrade":
            return 0, _UPGRADE_PENDING, ""
        if sub == "install":
            return -3, "", "cancelled"
        if sub == "list":
            return 0, _LIST_VLC, ""
        return 1, "", "unknown command"


def _ok(result="done"):
    return {"status": "success", "result": result}


def _steps(*mappings):
    return [StepDef.from_mapping(m) for m in mappings]


class _BlockingExecutor:
    """A fake tool that runs until its token is cancelled, then
    returns the cancelled dict — the shape a token-aware handler
    returns when Stop is pressed mid-act. Never blocks forever:
    a wall guard (default 10 s) returns success, so a test that
    fails to cancel fails its assertions instead of hanging."""

    def __init__(self, wall_guard: float = 10):
        self.calls = []
        self.started = threading.Event()
        self.started_calls = []
        self.wall_guard = wall_guard

    def __call__(self, tool, args, cancel_token=None):
        self.calls.append(tool)
        self.started_calls.append(tool)
        self.started.set()
        deadline = time.monotonic() + self.wall_guard
        while time.monotonic() < deadline:
            if cancel_token is not None and cancel_token.cancelled:
                return cancelled_result(
                    f"{tool} stopped at the user's request")
            time.sleep(0.01)
        return _ok()


class _Snapshot:
    def __call__(self):
        return {}


class _ScriptVerifier:
    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}

    def __call__(self, expect_text, before, after, result_text):
        verdicts = self.script.get(expect_text)
        if not verdicts:
            return ("unverifiable", "no script")
        if len(verdicts) > 1:
            return verdicts.pop(0)
        return verdicts[0]


def _coordinator(executor, ledger):
    return tc.TurnCoordinator(executor=executor, ledger=ledger,
                              verifier=None, snapshot=_Snapshot())


# ---------------------------------------------------------------------------
# 1. The token and the cancelled shape
# ---------------------------------------------------------------------------

class TokenShapeTests(unittest.TestCase):
    def test_cancelled_result_is_funnel_shaped(self):
        out = cancelled_result("stopped after 2 files")
        self.assertTrue(is_funnel_shaped(out))
        self.assertTrue(is_non_success(out))
        self.assertEqual(out["status"], "cancelled")
        self.assertEqual(out["kind"], "cancelled")

    def test_cancelled_is_a_non_success_status(self):
        self.assertIn("cancelled", NON_SUCCESS_STATUSES)

    def test_token_stop_beats_deadline(self):
        token = CancellationToken()
        token.cancel(reason="deadline")
        self.assertEqual(token.cancelled_by, "deadline")
        token.cancel(reason="stop")
        self.assertEqual(token.cancelled_by, "stop")
        self.assertTrue(token.cancelled)

    def test_token_deadline_never_overwrites_stop(self):
        token = CancellationToken()
        token.cancel(reason="stop")
        token.cancel(reason="deadline")
        self.assertEqual(token.cancelled_by, "stop")

    def test_token_raise_if_cancelled(self):
        token = CancellationToken()
        token.raise_if_cancelled()  # no-op before cancel
        token.cancel()
        with self.assertRaises(ToolCancelled):
            token.raise_if_cancelled()


# ---------------------------------------------------------------------------
# 2. The funnel maps a raised ToolCancelled to cancelled, not failure
# ---------------------------------------------------------------------------

class DispatchMappingTests(unittest.TestCase):
    def test_dispatch_maps_tool_cancelled_to_cancelled(self):
        from core.funnel.tools_registry import ToolRegistry
        import uuid

        # A fresh name per run: the funnel's duplicate-call
        # guard refuses a repeat identical call whose earlier
        # receipt reads as an unknown outcome — and a
        # cancelled call's effect IS unknown (it may have
        # half-applied), so the guard correctly refuses the
        # repeat until the effect is verified. That is the
        # B12 timeout rule applied to a stop, not a bug.
        name = "b20_probe_cancel_" + uuid.uuid4().hex[:8]

        def _boom():
            raise ToolCancelled("stopped mid-write")

        reg = ToolRegistry()
        reg.register(name=name, description="probe",
                     parameters={"type": "object", "properties": {}},
                     handler=_boom)
        out = reg.execute(name, {})
        self.assertEqual(out.get("status"), "cancelled")
        self.assertEqual(out.get("kind"), "cancelled")
        # Never a handler_error: B12 would classify that
        # TRANSIENT and retry the thing the user just stopped.
        self.assertNotEqual(out.get("kind"), "handler_error")

    def test_funnel_passes_token_only_to_declaring_handlers(self):
        from core.funnel.tools_registry import ToolRegistry

        seen = {}

        def _declaring(cancel_token=None):
            seen["declaring"] = cancel_token
            return _ok()

        def _plain():
            seen["plain"] = True
            return _ok()

        reg = ToolRegistry()
        reg.register(name="b20_probe_declaring", description="probe",
                     parameters={"type": "object", "properties": {}},
                     handler=_declaring)
        reg.register(name="b20_probe_plain", description="probe",
                     parameters={"type": "object", "properties": {}},
                     handler=_plain)
        token = CancellationToken()
        out = reg.execute("b20_probe_declaring", {},
                          cancel_token=token)
        self.assertEqual(out.get("status"), "success")
        self.assertIs(seen.get("declaring"), token)
        out = reg.execute("b20_probe_plain", {}, cancel_token=token)
        self.assertEqual(out.get("status"), "success")
        self.assertTrue(seen.get("plain"))


# ---------------------------------------------------------------------------
# 3. The coordinator: a running step ends promptly on Stop
# ---------------------------------------------------------------------------

class CoordinatorCancelTests(unittest.TestCase):
    def _ledger(self, tmp):
        return tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")

    def test_step_cancelled_by_token_ends_task_cancelled(self):
        def _cancel_mid_step(tool, args, cancel_token=None):
            # The user presses Stop while the step runs: the
            # token is cancelled from outside, and the fake
            # ends at its next poll — the shape a real
            # token-aware handler returns.
            cancel_token.cancel()
            return cancelled_result(f"{tool} stopped at the request")

        with tempfile.TemporaryDirectory() as tmp:
            coord = _coordinator(_cancel_mid_step, self._ledger(tmp))
            task = coord.new_task(
                "two steps", "test", _steps(
                    {"tool": "slow_tool", "label": "slow"},
                    {"tool": "never_runs", "label": "never"}))
            receipt = coord.run_task(task)
            self.assertEqual(task.state, "cancelled")
            self.assertEqual(len(task.outcomes), 1)
            self.assertEqual(task.outcomes[0].status, "cancelled")
            self.assertEqual(receipt.failed, 0)
            self.assertIn("slow", receipt.reason)
            # Step 2 never ran.
            self.assertEqual(task.outcomes[0].tool, "slow_tool")

    def test_cancelled_step_is_never_skipped_by_on_fail_skip(self):
        def _cancel(tool, args, cancel_token=None):
            return cancelled_result("stopped")

        with tempfile.TemporaryDirectory() as tmp:
            coord = _coordinator(_cancel, self._ledger(tmp))
            task = coord.new_task(
                "skip plan", "test", _steps(
                    {"tool": "slow_tool", "label": "slow",
                     "on_fail": "skip"},
                    {"tool": "later", "label": "later"}))
            coord.run_task(task)
            self.assertEqual(task.state, "cancelled")
            self.assertEqual(task.outcomes[0].status, "cancelled")
            self.assertNotEqual(task.outcomes[0].status, "skipped")

    def test_cancelled_step_takes_no_jump_and_no_strike(self):
        def _cancel(tool, args, cancel_token=None):
            return cancelled_result("stopped")

        with tempfile.TemporaryDirectory() as tmp:
            coord = _coordinator(_cancel, self._ledger(tmp))
            task = coord.new_task(
                "jump plan", "test", _steps(
                    {"tool": "slow_tool", "label": "slow",
                     "goto": 2},
                    {"tool": "recovery", "label": "recovery"}))
            coord.run_task(task)
            self.assertEqual(task.state, "cancelled")
            self.assertEqual(len(task.outcomes), 1)
            self.assertEqual(dict(task._jump_strikes), {})

    def test_no_stop_behaves_exactly_as_today(self):
        def _ok_executor(tool, args, cancel_token=None):
            return _ok()

        with tempfile.TemporaryDirectory() as tmp:
            coord = _coordinator(_ok_executor, self._ledger(tmp))
            task = coord.new_task(
                "two steps", "test", _steps(
                    {"tool": "a", "label": "a"},
                    {"tool": "b", "label": "b"}))
            receipt = coord.run_task(task)
            self.assertEqual(task.state, "completed")
            self.assertEqual(
                [o.status for o in task.outcomes],
                ["success", "success"])
            self.assertEqual(receipt.failed, 0)

    def test_ledger_keeps_partial_progress_of_cancelled_task(self):
        def _mixed(tool, args, cancel_token=None):
            if tool == "first":
                return _ok()
            return cancelled_result("stopped during second")

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            coord = _coordinator(_mixed, ledger)
            task = coord.new_task(
                "two steps", "test", _steps(
                    {"tool": "first", "label": "first"},
                    {"tool": "second", "label": "second"}))
            coord.run_task(task)
            records = [r for r in ledger.all_records()
                       if r.get("kind") != "goal"]
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record.get("state"), "cancelled")
            outcomes = record.get("outcomes") or []
            self.assertEqual(
                [o.get("status") for o in outcomes],
                ["success", "cancelled"])

    def test_cancel_task_threads_into_running_step(self):
        # Typed "stop": cancel_task cancels the step's LIVE
        # token, and the running step ends at its next poll.
        executor = _BlockingExecutor()
        with tempfile.TemporaryDirectory() as tmp:
            coord = _coordinator(executor, self._ledger(tmp))
            task = coord.new_task(
                "two steps", "test", _steps(
                    {"tool": "slow_tool", "label": "slow"},
                    {"tool": "never_runs", "label": "never"}))
            done = threading.Event()
            result = {}

            def _run():
                result["receipt"] = coord.run_task(task)
                done.set()

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            self.assertTrue(executor.started.wait(timeout=5),
                            "the step never started")
            started = time.monotonic()
            self.assertTrue(coord.cancel_task(task.id))
            self.assertTrue(done.wait(timeout=5),
                            "Stop did not end the running step")
            self.assertLess(time.monotonic() - started, 2,
                            "the step did not end promptly on Stop")
            thread.join(timeout=2)
            self.assertEqual(task.state, "cancelled")
            self.assertEqual(task.outcomes[0].status, "cancelled")
            self.assertEqual(result["receipt"].failed, 0)
            self.assertEqual(executor.calls, ["slow_tool"])

    def test_failsafe_trip_threads_into_honoring_step_only(self):
        # Goal pill: the failsafe trip cancels the live tokens
        # of tasks that honour stop signals — and ONLY those.
        executor = _BlockingExecutor()
        with tempfile.TemporaryDirectory() as tmp:
            coord = _coordinator(executor, self._ledger(tmp))
            task = coord.new_task(
                "honoring", "test", _steps(
                    {"tool": "slow_tool", "label": "slow"}))
            task._honor_stop_signals = True
            done = threading.Event()

            def _run():
                coord.run_task(task)
                done.set()

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            try:
                self.assertTrue(executor.started.wait(timeout=5),
                                "the step never started")
                started = time.monotonic()
                failsafe.trip_now("test trip")
                self.assertTrue(done.wait(timeout=5),
                                "the trip did not end the step")
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(task.state, "cancelled")
                self.assertEqual(task.outcomes[0].status, "cancelled")
            finally:
                failsafe.reset()
                thread.join(timeout=2)

    def test_failsafe_trip_leaves_non_honoring_step_alone(self):
        # The 3 s wall guard stands in for the step finishing
        # on its own: the trip must not end it early.
        executor = _BlockingExecutor(wall_guard=3)
        with tempfile.TemporaryDirectory() as tmp:
            coord = _coordinator(executor, self._ledger(tmp))
            task = coord.new_task(
                "not honoring", "test", _steps(
                    {"tool": "slow_tool", "label": "slow"}))
            # Default: _honor_stop_signals is False.
            self.assertFalse(task._honor_stop_signals)
            done = threading.Event()

            def _run():
                coord.run_task(task)
                done.set()

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            try:
                self.assertTrue(executor.started.wait(timeout=5),
                                "the step never started")
                failsafe.trip_now("test trip")
                # The step must NOT end on the trip: it runs to
                # its wall guard, so the task completes.
                self.assertTrue(done.wait(timeout=10),
                                "the step never finished")
                self.assertEqual(task.state, "completed")
                self.assertEqual(task.outcomes[0].status, "success")
            finally:
                failsafe.reset()
                thread.join(timeout=2)

    def test_parallel_group_stop_cancels_every_branch(self):
        executor = _BlockingExecutor()
        with tempfile.TemporaryDirectory() as tmp:
            coord = _coordinator(executor, self._ledger(tmp))
            task = coord.new_task(
                "parallel", "test", _steps(
                    {"tool": "branch_a", "label": "a", "parallel": "g"},
                    {"tool": "branch_b", "label": "b", "parallel": "g"}))
            done = threading.Event()

            def _run():
                coord.run_task(task)
                done.set()

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            deadline = time.monotonic() + 5
            while (len(executor.started_calls) < 2
                   and time.monotonic() < deadline):
                time.sleep(0.01)
            self.assertEqual(sorted(executor.started_calls),
                             ["branch_a", "branch_b"])
            started = time.monotonic()
            self.assertTrue(coord.cancel_task(task.id))
            self.assertTrue(done.wait(timeout=5),
                            "Stop did not end the parallel group")
            self.assertLess(time.monotonic() - started, 2)
            thread.join(timeout=2)
            self.assertEqual(task.state, "cancelled")
            self.assertEqual(
                sorted(o.status for o in task.outcomes),
                ["cancelled", "cancelled"])

    def test_verification_retry_cancelled_is_cancelled_not_failed(self):
        calls = []

        def _executor(tool, args, cancel_token=None):
            calls.append(tool)
            if len(calls) == 1:
                return _ok()
            # The retry re-run is stopped mid-act: the token
            # it received is cancelled, and it returns the
            # cancelled shape.
            cancel_token.cancel()
            return cancelled_result("retry stopped at the request")

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            coord = tc.TurnCoordinator(
                executor=_executor, ledger=ledger,
                verifier=_ScriptVerifier(
                    {"file is there": [("not-met", "not seen")]}),
                snapshot=_Snapshot())
            task = coord.new_task(
                "verify me", "test", _steps(
                    {"tool": "make_file", "label": "make",
                     "expect": "file is there"}))
            coord.run_task(task)
            self.assertEqual(task.state, "cancelled")
            self.assertEqual(task.outcomes[0].status, "cancelled")
            self.assertEqual(task.receipt().failed, 0)
            self.assertEqual(len(calls), 2)

    def test_mission_rail_is_not_a_live_stop_path(self):
        # CORRECTION (proved from the tree, 2026-11-07): the
        # brief named three stop paths, but the mission rail
        # is NOT one — MissionRail is defined
        # (gui/design_system/mission.py) but NEVER instantiated
        # anywhere, so its stop_requested signal is connected
        # to nothing. This pins that: only cancel_task (typed
        # "stop" / GUI cancel) and the failsafe trip (goal
        # pill) thread a stop into a step. If the rail is ever
        # wired, this pin must be updated with its path.
        hits = []
        # Live product code only: tests/ mention the rail in
        # their own scaffolding, and old GUI backups inside
        # the tree are not live code — neither is walked.
        for top in ("core", "gui"):
            for path in (ROOT / top).rglob("*.py"):
                if "gui/design_system/mission.py" in str(path):
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if "MissionRail(" in text:
                    hits.append(str(path))
        self.assertEqual(hits, [])


# ---------------------------------------------------------------------------
# 4. run_goal: a cancelled step ends the goal cancelled, never retried
# ---------------------------------------------------------------------------

class RungoalCancelTests(unittest.TestCase):
    def _ledger(self, tmp):
        return tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")

    def _run(self, pick, ledger, executor, verify_script=None):
        snap_calls = {"n": 0}

        async def _pick(goal, observation, history):
            out = pick(goal, observation, history)
            if asyncio.iscoroutine(out):
                out = await out
            return out

        async def _obs():
            snap_calls["n"] += 1
            return {"fg": "fake", "n": snap_calls["n"],
                    "windows": ["fake"], "texts": []}

        def _verify(expect_text, before, after, result_text):
            script = verify_script or {}
            return script.get(expect_text,
                              ("unverifiable", "no script"))

        patches = [
            mock.patch.object(rg, "_goal_budget_int",
                              side_effect=lambda n, d: d),
            mock.patch.object(rg, "_goal_budget_seconds",
                              side_effect=lambda n, d: d),
            mock.patch.object(rg, "_known_tools",
                              return_value=None),
            mock.patch.object(rg, "_execute_tool",
                              side_effect=executor),
        ]
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            return asyncio.run(rg.run_goal(
                GOAL, pick_action=_pick, observe=_obs,
                verify_expect=_verify, ledger=ledger))

    def _goal_record(self, ledger):
        records = [r for r in ledger.all_records()
                   if r.get("kind") == "goal"]
        self.assertEqual(len(records), 1)
        return records[0]

    def test_rungoal_cancelled_step_ends_cancelled_not_failed(self):
        calls = []

        def executor(tool, args):
            calls.append(tool)
            return {"status": "cancelled", "kind": "cancelled",
                    "message": "stopped at the user's request"}

        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {"x": 1}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            result = self._run(
                pick, ledger, executor,
                verify_script={GOAL: ("met", "goal state observed")})
            self.assertIn("cancelled", result.lower())
            record = self._goal_record(ledger)
            self.assertEqual(record["state"], "cancelled")
            events = [h.get("event") for h in record["history"]]
            self.assertIn("cancelled", events)
            self.assertNotIn("retry", events)

    def test_rungoal_cancelled_step_is_not_retried(self):
        calls = []

        def executor(tool, args):
            calls.append(tool)
            if len(calls) == 1:
                return {"status": "cancelled", "kind": "cancelled",
                        "message": "stopped at the user's request"}
            return {"status": "success", "result": "did it"}

        async def pick(goal, observation, history):
            if len(calls) >= 1:
                return {"done": "all done"}
            return {"tool": "fake_tool", "args": {"x": 1}}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            self._run(pick, ledger, executor)
            # Exactly one execution: a cancelled step is never
            # retried (B12: "cancelled" is not a transient kind).
            self.assertEqual(calls, ["fake_tool"])


# ---------------------------------------------------------------------------
# 5. run_routine: a cancelled sub-step ends the routine as cancelled
# ---------------------------------------------------------------------------

class RoutineCancelTests(unittest.TestCase):
    def _routine(self, name, actions):
        return {"name": name, "actions": actions}

    def _patch_routine(self, routine, executor):
        from core.funnel.tools_registry import tools
        names = [{"name": a.get("tool", "")}
                 for a in routine.get("actions", [])]
        patches = [
            mock.patch.object(pr, "_load_routines",
                              return_value=([routine], "")),
            mock.patch.object(tools, "execute",
                              side_effect=executor),
            # run_routine reads the known-tool set from the
            # live registry's declarations; the fake tool
            # names above are not registered there.
            mock.patch.object(tools, "get_declarations",
                              return_value=names),
        ]
        for patch in patches:
            patch.start()
        return patches

    def _unpatch(self, patches):
        for patch in reversed(patches):
            patch.stop()

    def test_routine_cancelled_substep_returns_cancelled_dict(self):
        calls = []

        def executor(tool, args, cancel_token=None):
            calls.append(tool)
            if tool == "second":
                return {"status": "cancelled", "kind": "cancelled",
                        "message": "stopped at the request"}
            return _ok()

        routine = self._routine("three", [
            {"tool": "first", "args": {}},
            {"tool": "second", "args": {}},
            {"tool": "third", "args": {}},
        ])
        patches = self._patch_routine(routine, executor)
        try:
            out = pr.run_routine("three",
                                 cancel_token=CancellationToken())
        finally:
            self._unpatch(patches)
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("status"), "cancelled")
        self.assertEqual(calls, ["first", "second"])

    def test_routine_deadline_cancel_does_not_stop_routine(self):
        # A sub-step that TIMED OUT cancels the token with
        # reason "deadline" — the routine must NOT read that
        # as a user stop: the timed-out step follows its own
        # on_fail (skip), and the next step still runs.
        calls = []

        def executor(tool, args, cancel_token=None):
            calls.append(tool)
            if tool == "first":
                return {"status": "timeout", "kind": "timeout",
                        "message": "took too long"}
            return _ok()

        routine = self._routine("two", [
            {"tool": "first", "args": {}, "on_fail": "skip"},
            {"tool": "second", "args": {}},
        ])
        token = CancellationToken()
        token.cancel(reason="deadline")
        patches = self._patch_routine(routine, executor)
        try:
            out = pr.run_routine("two", cancel_token=token)
        finally:
            self._unpatch(patches)
        self.assertIsInstance(out, str)
        self.assertEqual(calls, ["first", "second"])

    def test_routine_stop_between_steps_halts_before_next(self):
        token = CancellationToken()
        calls = []

        def executor(tool, args, cancel_token=None):
            calls.append(tool)
            if tool == "first":
                token.cancel()  # the user presses Stop mid-routine
            return _ok()

        routine = self._routine("two", [
            {"tool": "first", "args": {}},
            {"tool": "second", "args": {}},
        ])
        patches = self._patch_routine(routine, executor)
        try:
            out = pr.run_routine("two", cancel_token=token)
        finally:
            self._unpatch(patches)
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("status"), "cancelled")
        self.assertEqual(calls, ["first"])


# ---------------------------------------------------------------------------
# 6. use_toolbox: a cancelled inner call returns the dict, not a string
# ---------------------------------------------------------------------------

class ToolboxCancelTests(unittest.TestCase):
    def test_use_toolbox_returns_cancelled_dict(self):
        from core.funnel import toolbox_findcall as tbf
        from core.funnel.tools_registry import tools

        def executor(name, args, cancel_token=None):
            return {"status": "cancelled", "kind": "cancelled",
                    "message": "inner tool stopped"}

        with mock.patch.object(tools, "execute",
                               side_effect=executor), \
                mock.patch.object(
                    tools, "get_declarations",
                    return_value=[{"name": "fake_tool"}]):
            out = tbf.use_toolbox(action="call", tool="fake_tool",
                                  args={})
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("status"), "cancelled")


# ---------------------------------------------------------------------------
# 7. The subprocess seam: run_with_cancel and run_command
# ---------------------------------------------------------------------------

class SubprocessCancelTests(unittest.TestCase):
    def test_run_with_cancel_returns_minus3_on_token(self):
        token = CancellationToken()
        token.cancel()
        started = time.monotonic()
        rc, _out, _err = run_with_cancel(["sleep", "30"], 30, token)
        self.assertEqual(rc, -3)
        self.assertLess(time.monotonic() - started, 2)

    def test_run_with_cancel_timeout_returns_minus2(self):
        started = time.monotonic()
        rc, _out, _err = run_with_cancel(
            ["sleep", "30"], 0.2, CancellationToken())
        self.assertEqual(rc, -2)
        self.assertLess(time.monotonic() - started, 2)

    def test_run_with_cancel_success(self):
        rc, out, _err = run_with_cancel(
            ["echo", "hi"], 5, CancellationToken())
        self.assertEqual(rc, 0)
        self.assertIn("hi", out)

    def test_run_command_cancelled_token_ends_promptly(self):
        from core.desktop.control_shell import run_command

        token = CancellationToken()
        token.cancel()
        started = time.monotonic()
        out = run_command("sleep 30", cancel_token=token)
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("status"), "cancelled")
        self.assertLess(time.monotonic() - started, 2)


# ---------------------------------------------------------------------------
# 8. The long tools check the token at their yield points
# ---------------------------------------------------------------------------

class LongToolCancelTests(unittest.TestCase):
    def test_every_long_tool_declares_cancel_token(self):
        # The 14 long tools with a yield point (the 15th,
        # windows_update_status, has none — see its docstring
        # pin below). A tool missing the parameter would never
        # receive the token at all.
        from core.desktop import control_plus_files as cpf
        from core.desktop import control_shell as cshell
        from core import skills_files2_pdf_files as s2
        from core import skills_files4_image as s4
        from core import skills_files5_watch_blur_notes as s5
        from core import skills_media_files as smf
        from core import skills_system2_devices as sys2d
        from core import skills_system3_hardware as sys3h
        from core import skills_system6_safety as sys6s
        from core import skills_system_apps_startup as sysapp
        from core.brain import planner_routines as prout
        from core.funnel import toolbox_findcall as tbf
        from core.funnel import tools_handlers_command_url as thcu

        declaring = [
            cpf.download_file, cpf.zip_control,
            cshell.run_command,
            s2.sync_folder,
            s4.batch_watermark,
            s5.folder_watch_cook,
            smf.batch_rename,
            sys2d.create_restore_point,
            sys3h.list_drivers,
            sys6s.restore_point_before_changes,
            sysapp.winget_install, sysapp.winget_upgrade_all,
            prout.run_routine,
            tbf.use_toolbox,
            thcu.ToolHandlersCommandUrlMixin._tool_run_command,
        ]
        for fn in declaring:
            with self.subTest(fn=fn.__qualname__):
                self.assertIn("cancel_token",
                              inspect.signature(fn).parameters)

    def test_windows_update_status_names_no_yield_point(self):
        from core import skills_system_bench as bench

        self.assertIn("NO-YIELD-POINT",
                      bench.windows_update_status.__doc__ or "")

    def test_download_cancel_deletes_part_and_saves_nothing(self):
        from core.desktop import control_plus_files as cpf

        class _Drip(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"x" * (64 * 1024 * 4)
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                for i in range(0, len(body), 64 * 1024):
                    self.wfile.write(body[i:i + 64 * 1024])
                    self.wfile.flush()
                    time.sleep(0.2)

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            server = socketserver.ThreadingTCPServer(
                ("127.0.0.1", 0), _Drip)
            thread = threading.Thread(target=server.serve_forever,
                                      daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                token = CancellationToken()
                dest = pathlib.Path(tmp) / "slow.bin"
                result = {}

                def _download():
                    result["out"] = cpf.download_file(
                        f"http://127.0.0.1:{port}/slow.bin",
                        destination=str(dest), cancel_token=token)

                worker = threading.Thread(target=_download, daemon=True)
                worker.start()
                time.sleep(0.5)  # let the first chunk land
                token.cancel()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive(),
                                 "the download did not end promptly")
                out = result.get("out")
                self.assertIsInstance(out, dict)
                self.assertEqual(out.get("status"), "cancelled")
                self.assertFalse(dest.exists())
                self.assertFalse(
                    dest.with_suffix(dest.suffix + ".part").exists())
            finally:
                server.shutdown()
                server.server_close()

    def test_sync_folder_cancel_mid_run(self):
        from core import skills_files2_pdf_files as s2
        import shutil

        real_copy2 = shutil.copy2

        def _slow_copy2(src, dst, **kwargs):
            time.sleep(0.1)
            return real_copy2(src, dst, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "src"
            dst = pathlib.Path(tmp) / "dst"
            src.mkdir()
            dst.mkdir()
            for i in range(10):
                (src / f"file_{i:02d}.txt").write_text("x" * 100)
            token = CancellationToken()
            result = {}

            def _sync():
                result["out"] = s2.sync_folder(
                    str(src), str(dst), cancel_token=token)

            with mock.patch.object(shutil, "copy2",
                                   side_effect=_slow_copy2):
                worker = threading.Thread(target=_sync, daemon=True)
                worker.start()
                time.sleep(0.35)
                token.cancel()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive(),
                             "the sync did not end promptly")
            out = result.get("out")
            self.assertIsInstance(out, dict)
            self.assertEqual(out.get("status"), "cancelled")
            copied = [p for p in dst.iterdir() if p.is_file()]
            self.assertGreater(len(copied), 0)
            self.assertLess(len(copied), 10)

    def test_batch_rename_precancelled_renames_nothing(self):
        from core import skills_media_files as smf

        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            for name in ("a_one.txt", "a_two.txt", "a_three.txt"):
                (base / name).write_text("x")
            token = CancellationToken()
            token.cancel()
            out = smf.batch_rename(str(base), "a_", "b_",
                                   confirm=True, cancel_token=token)
            self.assertIsInstance(out, dict)
            self.assertEqual(out.get("status"), "cancelled")
            self.assertEqual(
                sorted(p.name for p in base.iterdir()),
                ["a_one.txt", "a_three.txt", "a_two.txt"])

    def test_batch_watermark_precancelled_stamps_nothing(self):
        from core import skills_files4_image as s4
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "imgs"
            root.mkdir()
            for i in range(3):
                Image.new("RGB", (20, 20), color="white").save(
                    root / f"img_{i}.png")
            token = CancellationToken()
            token.cancel()
            out = s4.batch_watermark(str(root), "TEST",
                                     cancel_token=token)
            self.assertIsInstance(out, dict)
            self.assertEqual(out.get("status"), "cancelled")
            # The output folder is created before the loop, so
            # it exists — but it must hold no stamped image.
            out_dir = root / "watermarked"
            stamped = list(out_dir.iterdir()) if out_dir.exists() else []
            self.assertEqual(stamped, [])

    def test_zip_control_precancelled(self):
        from core.desktop import control_plus_files as cpf

        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "folder"
            src.mkdir()
            for i in range(3):
                (src / f"f_{i}.txt").write_text("x" * 100)
            token = CancellationToken()
            token.cancel()
            out = cpf.zip_control("zip", str(src),
                                  cancel_token=token)
            self.assertIsInstance(out, dict)
            self.assertEqual(out.get("status"), "cancelled")

    def test_folder_watch_cook_precancelled(self):
        from core import skills_files5_watch_blur_notes as s5

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for i in range(3):
                (root / f"note_{i}.txt").write_text("x")
            token = CancellationToken()
            token.cancel()
            out = s5.folder_watch_cook(
                "run_once", folder=str(root),
                recipe="rename_by_date", cancel_token=token)
            self.assertIsInstance(out, dict)
            self.assertEqual(out.get("status"), "cancelled")
            self.assertEqual(
                sorted(p.name for p in root.iterdir()),
                ["note_0.txt", "note_1.txt", "note_2.txt"])

    def test_subprocess_minus3_maps_to_cancelled(self):
        # The -3 sentinel from run_with_cancel reaches the
        # caller as a cancelled outcome (never a timeout,
        # never success). Patched _run/_ps_run stand in for
        # the Windows child on this Linux lab.
        from core import skills_system2_devices as sys2d
        from core import skills_system3_hardware as sys3h
        from core import skills_system6_safety as sys6s
        from core import skills_system_apps_startup as sysapp

        token = CancellationToken()
        with mock.patch.object(sys2d, "_is_windows",
                               return_value=True), \
                mock.patch.object(sys2d, "_run",
                                  return_value=(-3, "", "cancelled")):
            out = sys2d.create_restore_point(cancel_token=token)
            self.assertIsInstance(out, dict)
            self.assertEqual(out.get("status"), "cancelled")
        with mock.patch.object(sys6s, "_is_windows",
                               return_value=True), \
                mock.patch.object(sys6s, "_ps_run",
                                  return_value=(-3, "", "cancelled")):
            out = sys6s.restore_point_before_changes(
                cancel_token=token)
            self.assertIsInstance(out, dict)
            self.assertEqual(out.get("status"), "cancelled")
        with mock.patch.object(sys3h, "_is_windows",
                               return_value=True), \
                mock.patch.object(sys3h, "_ps_structured",
                                  return_value=(-3, [], "cancelled")):
            out = sys3h.list_drivers(cancel_token=token)
            self.assertIsInstance(out, dict)
            self.assertEqual(out.get("status"), "cancelled")
        # B-D8r: winget_install / winget_upgrade_all route through
        # the B-D8 wrapper (core/desktop/app_management.py) — the
        # seam moved from sysapp's module-level helpers to the
        # wrapper's injected backend. The assertions are unchanged:
        # a -3 from the child is a cancelled DICT, never a timeout
        # or success. (The fake is reset in a finally so no other
        # suite in this process sees it.)
        from core.desktop import app_management as appmgmt
        appmgmt.set_app_backend(_CancellingWinget())
        try:
            out = sysapp.winget_install("VideoLAN.VLC", confirm=True,
                                        cancel_token=token)
            self.assertIsInstance(out, dict)
            self.assertEqual(out.get("status"), "cancelled")
            out = sysapp.winget_upgrade_all(confirm=True,
                                            cancel_token=token)
            self.assertIsInstance(out, dict)
            self.assertEqual(out.get("status"), "cancelled")
        finally:
            appmgmt.reset_app_backend()


if __name__ == "__main__":
    unittest.main()
