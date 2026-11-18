"""B19 — parallel steps: declared independent steps run together
through the SAME _run_step, each judged on its own, the group
completing only when every branch has completed.

Lab leg only. The real-Windows field leg — a real complex task
on his machine where independent steps run together and finish
in the time of the longest step — is OWED, not this batch.

Gap proved first (from the tree): the coordinator ran every
step one after another (run_task dispatched one step at a time,
core/brain/task_coordinator_execution.py:331-420 pre-B19), and
StepDef had no independence field at all — the plan could not
say "these two steps are independent". Grep for
gather|ThreadPool|ProcessPool|concurrent|parallel across the
coordinator + planner files returned zero hits, and no funnel
worker pool existed anywhere in core/ (only an unrelated
max_workers=1 in watchers_network_system.py:338) — so B19
creates the pool where it is used, per group, and destroys it
at the join.

What this batch builds (and this suite proves): ONE design —
a declared parallel group. Steps sharing one non-empty
``parallel`` label form a group (maximal consecutive run);
the group runs together through the same _run_step (one path,
not two); each branch's outcome is judged independently (its
own expect, its own landing verdict, its own strike); a failed
branch never cancels its siblings; the group's outcome is the
set of its steps' outcomes, never a single merged verdict.
Independence is DECLARED, never inferred (two steps touching
the same file run sequentially unless the plan labels them);
the default is sequential. A parallel group never contains a
consequential step (validate_parallel_groups refuses the plan
at build, via the tree's own D4/C-14 set — never a copy).
Maximum group size is 4 (MAX_PARALLEL_STEPS).

Rejected alternatives (recorded in code, not built):
inferring independence from tool names; running ALL steps in
parallel by default; a second dispatch path; merging the
group's outcomes into one verdict; guessing/auto-answering;
a long-lived shared pool; re-running completed siblings on
resume (the skip rule).
"""

import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest

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
    MAX_PARALLEL_STEPS, parallel_groups, validate_parallel_groups)


def _ok(result="done"):
    return {"status": "success", "result": result}


def _fail(message="simulated failure"):
    return {"status": "failure", "message": message}


class _ScriptExecutor:
    """Per-tool scripted results; records every call + its args."""

    def __init__(self, script=None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls = []  # tool names, in call order
        self.args = []  # (tool, args dict) per call

    def __call__(self, tool, args):
        self.calls.append(tool)
        self.args.append((tool, dict(args)))
        results = self.script.get(tool)
        if not results:
            return _ok()
        if len(results) > 1:
            return results.pop(0)
        return results[0]


class _InstrumentedExecutor(_ScriptExecutor):
    """Scripted executor that also proves overlap.

    Records entry/exit times per call, the max number of
    concurrently active calls, and — for the tools named in
    barrier_tools — waits on a barrier with a timeout, so a
    sequential run breaks the barrier instead of hanging
    the suite (and the test fails on the overlap assertion,
    not on a deadlock)."""

    def __init__(self, script=None, sleep=None, barrier=None,
                 barrier_tools=()):
        super().__init__(script)
        self.sleep = dict(sleep or {})
        self.barrier = barrier
        self.barrier_tools = set(barrier_tools)
        self.entries = []  # (tool, monotonic, thread name)
        self.exits = []  # (tool, monotonic, thread name)
        self.barrier_broken = False
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0

    def __call__(self, tool, args):
        with self._lock:
            self.calls.append(tool)
            self.args.append((tool, dict(args)))
            self.entries.append(
                (tool, time.monotonic(), threading.current_thread().name))
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            if self.barrier is not None and tool in self.barrier_tools:
                try:
                    self.barrier.wait(timeout=5)
                except threading.BrokenBarrierError:
                    self.barrier_broken = True
            time.sleep(self.sleep.get(tool, 0))
            results = self.script.get(tool)
            if not results:
                return _ok()
            if len(results) > 1:
                return results.pop(0)
            return results[0]
        finally:
            with self._lock:
                self.exits.append(
                    (tool, time.monotonic(),
                     threading.current_thread().name))
                self._active -= 1


class _ScriptVerifier:
    """Per-expect scripted verdicts; records every call."""

    def __init__(self, script=None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
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


# The §7 engine confirm stop, as the funnel renders it
# (task_coordinator_refusal._detect_s7_confirmation_stop):
# a menu_action success-carrying string starting "Menu path ",
# with " refused: " and the C-14 phrase.
_C14_STOP = ("Menu path File > Delete refused: destructive action "
             "— confirm it first (C-14 approve-first)")
# The R5/B1 monitor-gone sentence, as the set family renders it.
_GONE = ("Wallpaper not set: unknown-monitor: no monitor named "
         "'second'")


# ---------------------------------------------------------------------------
# 1. declaration + validation
# ---------------------------------------------------------------------------

class DeclarationTests(unittest.TestCase):
    def tearDown(self):
        pa.clear_all()
        super().tearDown()

    def test_parallel_label_parsed(self):
        step = StepDef.from_mapping(
            {"tool": "read_file", "args": {}, "parallel": "g1"})
        self.assertEqual(step.parallel, "g1")
        plain = StepDef.from_mapping({"tool": "read_file", "args": {}})
        self.assertEqual(plain.parallel, "")

    def test_non_string_parallel_label_refused(self):
        with self.assertRaises(ValueError):
            StepDef.from_mapping(
                {"tool": "read_file", "args": {}, "parallel": 3})

    def test_parallel_groups_maximal_runs(self):
        steps = _steps(
            {"tool": "a", "args": {}, "parallel": "x"},
            {"tool": "b", "args": {}, "parallel": "x"},
            {"tool": "c", "args": {}, "parallel": "y"},
            {"tool": "d", "args": {}, "parallel": "y"},
            {"tool": "e", "args": {}},
            {"tool": "f", "args": {}, "parallel": "x"},
            {"tool": "g", "args": {}, "parallel": "x"})
        # Two runs with the same label are TWO groups.
        self.assertEqual(parallel_groups(steps),
                         [[0, 1], [2, 3], [5, 6]])
        self.assertEqual(MAX_PARALLEL_STEPS, 4)

    def test_group_of_one_refused_at_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            coord, _ledger = _coord(tmp, _ScriptExecutor())
            with self.assertRaises(ValueError):
                coord.new_task(
                    "t", "routine",
                    _steps({"tool": "a", "args": {}, "parallel": "solo"},
                           {"tool": "b", "args": {}}))

    def test_group_over_four_refused_at_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            coord, _ledger = _coord(tmp, _ScriptExecutor())
            steps = _steps(*[
                {"tool": f"t{i}", "args": {}, "parallel": "big"}
                for i in range(5)])
            with self.assertRaises(ValueError):
                coord.new_task("t", "routine", steps)
            # Four is the bound, and it builds.
            four = _steps(*[
                {"tool": f"t{i}", "args": {}, "parallel": "ok"}
                for i in range(4)])
            task = coord.new_task("t", "routine", four)
            self.assertEqual(len(task.steps), 4)

    def test_consequential_step_in_group_refused(self):
        # A parallel group never contains a consequential
        # step — refused at build, never silently downgraded
        # to sequential (a silent downgrade would hide a
        # planner error). The refusal uses the tree's own
        # D4/C-14 set via plan_approval.is_jump_refused.
        for tool in ("send_email", "shred_file", "paste_text",
                     "run_command"):
            with self.subTest(tool=tool), \
                    tempfile.TemporaryDirectory() as tmp:
                coord, _ledger = _coord(tmp, _ScriptExecutor())
                with self.assertRaises(ValueError):
                    coord.new_task(
                        "t", "routine",
                        _steps({"tool": tool, "args": {},
                                "parallel": "g"},
                               {"tool": "read_file", "args": {},
                                "parallel": "g"}))

    def test_validate_parallel_groups_direct(self):
        good = _steps({"tool": "a", "args": {}, "parallel": "g"},
                      {"tool": "b", "args": {}, "parallel": "g"})
        validate_parallel_groups(good)  # no raise
        plain = _steps({"tool": "a", "args": {}},
                       {"tool": "b", "args": {}})
        validate_parallel_groups(plain)  # no group: no raise


# ---------------------------------------------------------------------------
# 2. the group runs together, each branch judged on its own
# ---------------------------------------------------------------------------

class ParallelExecutionTests(unittest.TestCase):
    def tearDown(self):
        pa.clear_all()
        super().tearDown()

    def test_group_branches_overlap(self):
        # Overlap is proved by a barrier, not wall-clock: both
        # branches must be inside the executor at the same time
        # (each waits for the other), on different threads.
        with tempfile.TemporaryDirectory() as tmp:
            barrier = threading.Barrier(2)
            executor = _InstrumentedExecutor(
                barrier=barrier, barrier_tools={"a", "b"})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}, "parallel": "g"},
                       {"tool": "b", "args": {}, "parallel": "g"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertFalse(executor.barrier_broken,
                             "branches never overlapped — the group "
                             "ran sequentially")
            entries = {t: tm for t, tm, _ in executor.entries}
            exits = {t: tm for t, tm, _ in executor.exits}
            # Each branch entered before the other exited.
            self.assertLess(entries["a"], exits["b"])
            self.assertLess(entries["b"], exits["a"])
            threads = {t for _, _, t in executor.entries}
            self.assertEqual(len(threads), 2)
            self.assertEqual(sorted(executor.calls), ["a", "b"])

    def test_group_wall_clock_is_longest_not_sum(self):
        # Loose wall-clock check beside the barrier proof:
        # two 0.5 s branches finish well under their 1.0 s sum.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _InstrumentedExecutor(
                sleep={"a": 0.5, "b": 0.5})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}, "parallel": "g"},
                       {"tool": "b", "args": {}, "parallel": "g"}))
            started = time.monotonic()
            receipt = coord.run_task(task)
            elapsed = time.monotonic() - started
            self.assertEqual(receipt.state, "completed")
            self.assertLess(elapsed, 0.8,
                            f"group took {elapsed:.2f}s for two 0.5s "
                            "branches — the sum, not the longest")

    def test_max_concurrency_two_in_group_one_without_label(self):
        # Independence is DECLARED, never inferred: the same
        # two tools, the same executor — labelled, they run
        # two-at-a-time; unlabelled, they run one-at-a-time.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _InstrumentedExecutor(sleep={"a": 0.2, "b": 0.2})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}, "parallel": "g"},
                       {"tool": "b", "args": {}, "parallel": "g"}))
            coord.run_task(task)
            self.assertEqual(executor.max_active, 2)
        with tempfile.TemporaryDirectory() as tmp:
            executor = _InstrumentedExecutor(sleep={"a": 0.2, "b": 0.2})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}},
                       {"tool": "b", "args": {}}))
            coord.run_task(task)
            self.assertEqual(executor.max_active, 1)
            self.assertEqual(executor.calls, ["a", "b"])

    def test_each_branch_judged_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"b": [_fail("boom")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}, "parallel": "g"},
                       {"tool": "b", "args": {}, "parallel": "g"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertEqual(receipt.completed, 1)
            self.assertEqual(receipt.failed, 1)
            self.assertEqual(
                [(s["tool"], s["status"]) for s in receipt.steps],
                [("a", "success"), ("b", "failure")])
            self.assertIn("b", receipt.reason)
            # Both branches ran: the failure did not cancel its
            # sibling, and the sibling's outcome is its own.
            self.assertEqual(sorted(executor.calls), ["a", "b"])

    def test_group_outcome_is_the_set_not_one_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"b": [_fail("boom")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}, "parallel": "g"},
                       {"tool": "b", "args": {}, "parallel": "g"},
                       {"tool": "c", "args": {}, "parallel": "g"}))
            receipt = coord.run_task(task)
            self.assertEqual(
                [(s["tool"], s["status"]) for s in receipt.steps],
                [("a", "success"), ("b", "failure"),
                 ("c", "success")])
            self.assertEqual(receipt.completed, 2)
            self.assertEqual(receipt.failed, 1)
            # Three outcomes, not one merged verdict.
            self.assertEqual(receipt.attempted, 3)

    def test_failed_branch_does_not_cancel_slow_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _InstrumentedExecutor(
                script={"a": [_fail("fast boom")]}, sleep={"b": 0.3})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}, "parallel": "g"},
                       {"tool": "b", "args": {}, "parallel": "g"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertEqual(
                [(s["tool"], s["status"]) for s in receipt.steps],
                [("a", "failure"), ("b", "success")])
            # The slow sibling ran to completion after the
            # fast failure — it was not cancelled.
            self.assertEqual(len(executor.calls), 2)

    def test_each_branch_own_expect_judged(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor()
            verifier = _ScriptVerifier({
                "alpha": [("met", "seen")],
                "beta": [("met", "seen")]})
            coord, _ledger = _coord(tmp, executor, verifier=verifier)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}, "parallel": "g",
                        "expect": "alpha"},
                       {"tool": "b", "args": {}, "parallel": "g",
                        "expect": "beta"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            # Two verification entries — one per step's own
            # expect — never one merged group verdict.
            self.assertEqual(len(receipt.verification), 2)
            self.assertEqual(
                sorted(v["expect"] for v in receipt.verification),
                ["alpha", "beta"])
            self.assertTrue(all(v["verdict"] == "met"
                                for v in receipt.verification))

    def test_landing_verdict_per_branch(self):
        # B13: a landing_failed kind on one branch fails that
        # branch only — its sibling's verdict is its own.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({
                "a": [{"status": "success", "result": "clicked",
                       "outcome_kind": "landing_failed",
                       "message": "click missed the target"}]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}, "parallel": "g"},
                       {"tool": "b", "args": {}, "parallel": "g"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertEqual(
                [(s["tool"], s["status"], s["kind"])
                 for s in receipt.steps],
                [("a", "failure", "landing_failed"),
                 ("b", "success", "")])

    def test_no_parallel_group_behaves_as_today(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor()
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}},
                       {"tool": "b", "args": {}},
                       {"tool": "c", "args": {}}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(executor.calls, ["a", "b", "c"])
            self.assertEqual(
                [s["status"] for s in receipt.steps],
                ["success", "success", "success"])
            self.assertEqual(receipt.completed, 3)
            self.assertEqual(receipt.failed, 0)

    def test_jump_on_failed_group_branch(self):
        # B4 through the group: the failed branch jumps to its
        # named recovery step; the sibling's outcome survives
        # the rewind (it sits before the jump target).
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"a": [_fail("boom")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}, "parallel": "g",
                        "on_fail": {"goto": 3}},
                       {"tool": "b", "args": {}, "parallel": "g"},
                       {"tool": "recover", "args": {}}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(len(receipt.jumps), 1)
            self.assertTrue(receipt.jumps[0]["jumped"])
            self.assertEqual(receipt.jumps[0]["from_label"], "a")
            self.assertEqual(receipt.jumps[0]["to_label"], "recover")
            self.assertEqual(
                [s["tool"] for s in receipt.steps],
                ["a", "b", "recover"])
            self.assertEqual(
                [s["status"] for s in receipt.steps],
                ["failure", "success", "success"])
            # The group's call order is nondeterministic (two
            # threads); the recovery runs after the group.
            self.assertEqual(sorted(executor.calls),
                             ["a", "b", "recover"])
            self.assertEqual(executor.calls[-1], "recover")

    def test_group_strike_recorded_on_failed_branch(self):
        # A failed branch's strike is recorded at ITS index
        # (the jump machinery reads task._index, which the
        # join sets to the failed branch before jumping).
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"b": [_fail("boom")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}, "parallel": "g"},
                       {"tool": "b", "args": {}, "parallel": "g",
                        "on_fail": {"goto": 3}},
                       {"tool": "recover", "args": {}}))
            # The recovery step fails too, so the task halts
            # after the jump — the strike on branch 1 must be
            # visible in the jump event.
            executor.script["recover"] = [_fail("also boom")]
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertEqual(len(receipt.jumps), 1)
            self.assertTrue(receipt.jumps[0]["jumped"])
            self.assertEqual(receipt.jumps[0]["strike"], 1)
            self.assertEqual(receipt.jumps[0]["from_index"], 1)


# ---------------------------------------------------------------------------
# 3. pending inside a group: pause, resume, skip-completed
# ---------------------------------------------------------------------------

class PendingGroupTests(unittest.TestCase):
    def tearDown(self):
        pa.clear_all()
        super().tearDown()

    def test_ask_branch_pauses_sibling_recorded_resume_skips_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor()
            coord, ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "read_file", "args": {},
                        "parallel": "g",
                        "ask": {"question": "which file?",
                                "options": ["blue", "red"],
                                "answer_key": "choice"}},
                       {"tool": "b", "args": {}, "parallel": "g"},
                       {"tool": "c", "args": {}}))
            receipt = coord.run_task(task)
            # Paused on the question; the sibling's outcome is
            # recorded, the ask step holds a pending placeholder.
            self.assertEqual(receipt.state, "paused")
            self.assertEqual(receipt.failed, 0)
            self.assertEqual(
                [(s["tool"], s["status"]) for s in receipt.steps],
                [("read_file", "pending"), ("b", "success")])
            pending = task._pending_question
            self.assertIsNotNone(pending)
            self.assertEqual(pending["step_index"], 0)
            self.assertEqual(pending["question"], "which file?")
            # The ask step never dispatched while unanswered.
            self.assertEqual(executor.calls, ["b"])
            # Answer, resume: the ask step runs with the answer
            # injected, the completed sibling is NEVER re-run,
            # and the later step runs.
            resumed = coord.resolve_question(task.id, "blue")
            self.assertEqual(resumed.state, "completed")
            self.assertEqual(
                [(s["tool"], s["status"]) for s in resumed.steps],
                [("read_file", "success"), ("b", "success"),
                 ("c", "success")])
            self.assertEqual(executor.calls, ["b", "read_file", "c"])
            read_args = [a for t, a in executor.args
                         if t == "read_file"]
            self.assertEqual(len(read_args), 1)
            self.assertEqual(read_args[0].get("choice"), "blue")

    def test_failure_wins_over_pending(self):
        # A failed branch halts the task; a pending question
        # on another branch is moot — no pending record is
        # installed, and the branch would pend again only if
        # the loop re-reached it (after a jump).
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({"b": [_fail("boom")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "read_file", "args": {},
                        "parallel": "g",
                        "ask": {"question": "which file?",
                                "answer_key": "choice"}},
                       {"tool": "b", "args": {}, "parallel": "g"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertEqual(receipt.failed, 1)
            self.assertIsNone(task._pending_question)
            self.assertEqual(
                [(s["tool"], s["status"]) for s in receipt.steps],
                [("read_file", "pending"), ("b", "failure")])
            # The failed-reason branch never names the pending
            # placeholder — it names the failure.
            self.assertIn("b", receipt.reason)

    def test_monitor_branch_pauses_and_resume_skips_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({
                "wallpaper_apply": [
                    _ok(_GONE),  # gone monitor on the first run
                    _ok("applied")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "wallpaper_apply", "args": {},
                        "parallel": "g"},
                       {"tool": "b", "args": {}, "parallel": "g"},
                       {"tool": "c", "args": {}}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "paused")
            self.assertIsNotNone(task._pending_monitor)
            self.assertEqual(task._pending_monitor["step_index"], 0)
            self.assertEqual(
                [(s["tool"], s["status"]) for s in receipt.steps],
                [("wallpaper_apply", "pending"), ("b", "success")])
            # The group's call order is nondeterministic (two
            # threads); both branches ran once.
            self.assertEqual(sorted(executor.calls),
                             ["b", "wallpaper_apply"])
            resumed = coord.resume_task(task.id)
            self.assertEqual(resumed.state, "completed")
            self.assertEqual(
                [(s["tool"], s["status"]) for s in resumed.steps],
                [("wallpaper_apply", "success"), ("b", "success"),
                 ("c", "success")])
            # The sibling ran once; the wallpaper step ran twice
            # (gone, then applied); the later step ran once.
            self.assertEqual(executor.calls.count("b"), 1)
            self.assertEqual(executor.calls.count("wallpaper_apply"), 2)
            self.assertEqual(executor.calls.count("c"), 1)

    def test_approval_deny_skip_continues_to_next_step(self):
        # B19 fix in resolve_pending: a group branch's pending
        # placeholder is NOT the retry-resume case — denying
        # with on_fail=skip continues from the NEXT step (the
        # skip rule advances past the completed sibling), it
        # does not jump to verification.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({
                "menu_action": [_ok(_C14_STOP)]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "menu_action",
                        "args": {"action": "invoke",
                                 "path": "File > Delete"},
                        "parallel": "g", "on_fail": "skip"},
                       {"tool": "b", "args": {}, "parallel": "g"},
                       {"tool": "c", "args": {}}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "paused")
            self.assertIsNotNone(task._pending_approval)
            self.assertEqual(task._pending_approval["step_index"], 0)
            self.assertEqual(
                [(s["tool"], s["status"]) for s in receipt.steps],
                [("menu_action", "pending"), ("b", "success")])
            denied = coord.resolve_pending(task.id, approved=False)
            self.assertEqual(denied.state, "completed")
            self.assertEqual(
                [(s["tool"], s["status"]) for s in denied.steps],
                [("menu_action", "skipped"), ("b", "success"),
                 ("c", "success")])
            # The later step ran (not skipped by a jump to
            # verification); the sibling ran once.
            self.assertEqual(executor.calls.count("c"), 1)
            self.assertEqual(executor.calls.count("b"), 1)

    def test_approval_approve_reissues_branch(self):
        # The one-shot re-issue reaches the branch at its own
        # index: the approved step re-runs with its confirm
        # flag, the sibling is not re-run, the task completes.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({
                "menu_action": [_ok(_C14_STOP), _ok("invoked")]})
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "menu_action",
                        "args": {"action": "invoke",
                                 "path": "File > Delete"},
                        "parallel": "g"},
                       {"tool": "b", "args": {}, "parallel": "g"},
                       {"tool": "c", "args": {}}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "paused")
            approved = coord.resolve_pending(task.id, approved=True)
            self.assertEqual(approved.state, "completed")
            self.assertEqual(
                [(s["tool"], s["status"]) for s in approved.steps],
                [("menu_action", "success"), ("b", "success"),
                 ("c", "success")])
            self.assertEqual(executor.calls.count("menu_action"), 2)
            self.assertEqual(executor.calls.count("b"), 1)
            menu_args = [a for t, a in executor.args
                         if t == "menu_action"]
            self.assertTrue(menu_args[1].get("confirmed"),
                            "the re-issue must carry the confirm flag")

    def test_paused_group_rebuilds_from_ledger(self):
        # The ledger round-trip: the parallel labels survive,
        # the rebuild index stops at the pending placeholder
        # (the sibling's success does not advance past it),
        # and the pending question record is restored.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor()
            coord, ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "read_file", "args": {},
                        "parallel": "g",
                        "ask": {"question": "which file?",
                                "answer_key": "choice"}},
                       {"tool": "b", "args": {}, "parallel": "g"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "paused")
            records = ledger.all_records()
            self.assertEqual(len(records), 1)
            rebuilt = tl.TaskLedger().rebuild_task(records[0])
            self.assertEqual(
                [s.parallel for s in rebuilt.steps], ["g", "g"])
            self.assertEqual(rebuilt._index, 0)
            self.assertEqual(
                [(o.tool, o.status) for o in rebuilt.outcomes],
                [("read_file", "pending"), ("b", "success")])
            self.assertIsNotNone(rebuilt._pending_question)
            self.assertEqual(
                rebuilt._pending_question["question"], "which file?")


# ---------------------------------------------------------------------------
# 4. receipt shape
# ---------------------------------------------------------------------------

class ReceiptTests(unittest.TestCase):
    def tearDown(self):
        pa.clear_all()
        super().tearDown()

    def test_receipt_shape_unchanged_for_parallel_task(self):
        # A parallel task's receipt has exactly the same keys
        # as a sequential task's (the B14/B15 pin shape) — the
        # group adds no receipt field and drops none.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor()
            coord, _ledger = _coord(tmp, executor)
            seq = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}},
                       {"tool": "b", "args": {}}))
            seq_receipt = coord.run_task(seq)
            par = coord.new_task(
                "t", "routine",
                _steps({"tool": "a", "args": {}, "parallel": "g"},
                       {"tool": "b", "args": {}, "parallel": "g"}))
            par_receipt = coord.run_task(par)
            self.assertEqual(set(par_receipt.as_dict().keys()),
                             set(seq_receipt.as_dict().keys()))

    def test_pending_placeholder_not_counted_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor()
            coord, _ledger = _coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "read_file", "args": {},
                        "parallel": "g",
                        "ask": {"question": "which file?",
                                "answer_key": "choice"}},
                       {"tool": "b", "args": {}, "parallel": "g"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "paused")
            self.assertEqual(receipt.failed, 0)
            self.assertEqual(receipt.attempted, 2)


if __name__ == "__main__":
    unittest.main()
