"""Startup recovery offers (B16, 2026-11-07): interrupted tasks and
goals offered back after a restart, on the existing Activity surface.

Gap proved first (from the tree): the interrupted records EXIST
(B11 wrote them — coordinator tasks paused, budget-paused,
crash-interrupted goal records with state ``running``), but nothing
ever showed them after a restart. ``list_interrupted`` /
``list_interrupted_goals`` existed as readers, the Activity offer
covered pending-approval tasks only (§7 consumers batch 7) and
pending-question tasks only (B15), and zero GUI callers of
``resume_task`` / ``resume_goal`` existed — a task interrupted by
a crash or a budget stop sat in the ledger, invisible, until the
user happened to dig it out. The persistence was built; the
surfacing was not.

What this batch builds (and this suite proves):

* ONE design: on startup the app reads the ledger's interrupted
  records and offers them back — one surface, the same Activity
  offer the pending-approval tasks already use, extended to every
  interrupted kind. The offer is NEVER auto-run (a restart is not
  consent); each offered record names what it is, where it
  stopped, why it stopped, and what it spent (the B14 spend line
  where it exists); dismissing the offer never deletes the record
  (a dismissed record is offered again next startup until resumed
  or explicitly abandoned); resuming goes through the existing
  resume paths with the recorded index and history intact.
* The queues never mix: a record with a pending approval or a
  pending question already has its own offer and is never
  double-offered here.
* A goal offer carries its line plus Not now / Abandon, never a
  Resume button — resuming a goal needs the goal loop's picker,
  which the GUI cannot construct (the D1 boundary); the
  module-level resume path for goals exists and is tested with a
  fake picker.

Fakes only: stub executors, a temp ledger, fake pickers, a fake
clock for the budget pause, no real GUI except the dialog tests
(Qt-only, skipped on the system interpreter), no real cloud calls.

Lab note: gui/pending_recovery.py imports voice_assistant.core.*
at top level (the shipped install is laid out as the
voice_assistant package; the lab tree is top-level core/ + gui/).
The alias below mirrors test_mic_first_use_ask's setUpModule
alias, installed at import time — before importing
gui.pending_recovery — with the same snapshot discipline in
tearDownModule.
"""

import asyncio
import inspect
import os
import pathlib
import sys
import tempfile
import types as _types_mod
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

# --- voice_assistant.* alias (mirrors test_mic_first_use_ask) --------
_VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")}
_pkg = _types_mod.ModuleType("voice_assistant")
_pkg.__path__ = [str(ROOT)]
_core_alias = _types_mod.ModuleType("voice_assistant.core")
_core_alias.__path__ = [str(ROOT / "core")]
sys.modules["voice_assistant"] = _pkg
sys.modules["voice_assistant.core"] = _core_alias


def tearDownModule():
    global _VA_SNAPSHOT
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        del sys.modules[key]
    if _VA_SNAPSHOT is not None:
        sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None


from core.brain import planner_rungoal as rg  # noqa: E402
from core.brain import task_budget as tb  # noqa: E402
from core.brain import task_coordinator as tc  # noqa: E402
from core.brain import task_ledger as tl  # noqa: E402
from gui.pending_recovery import (  # noqa: E402
    abandon_recovery_offer,
    interrupted_recoverable,
    is_recoverable_goal,
    is_recoverable_task,
    recovery_offer_line,
    resume_recovery_offer,
    spend_line,
    startup_recovery_check,
    why_stopped,
)

try:
    from PySide6.QtWidgets import QApplication
    _HAS_QT = True
except Exception:  # noqa: BLE001 — no PySide6 on sys
    _HAS_QT = False

GOAL = "organise the downloads folder"

_GONE = ("Wallpaper not set: unknown-monitor: no monitor named "
         "second monitor (monitors now: primary DISPLAY1).")


def _ok(result="done"):
    return {"status": "success", "result": result}


class _ScriptExecutor:
    """Per-tool scripted results; records every call (B14 shape)."""

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
    """Advances the fake clock by `advance_s` on every call (B14)."""

    def __init__(self, clock, advance_s, script=None):
        super().__init__(script)
        self._clock = clock
        self._advance_s = advance_s

    def __call__(self, tool, args):
        self._clock[0] += self._advance_s
        return super().__call__(tool, args)


class _ChangingSnapshot:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return {"fg": "fake", "n": self.n, "windows": ["fake"],
                "texts": []}


def _fake_verify(script):
    def _verify(expect_text, before, after, result_text):
        return script.get(expect_text, ("unverifiable", "no script"))
    return _verify


def _task_summary(**kw):
    base = {"task_id": "t1", "title": "Do the thing", "state": "paused",
            "completed": 1, "remaining_labels": ["step b", "step c"],
            "remaining_tools": ["b", "c"], "pending_approval": None,
            "pending_question": None, "pending_monitor": None,
            "reason": "", "spend": {},
            "updated_at": "2026-11-07T00:00:00"}
    base.update(kw)
    return base


def _goal_summary(**kw):
    base = {"goal_id": "g1", "goal_text": GOAL, "kind": "goal",
            "state": "paused",
            "steps_used": 2, "window_steps": 2,
            "budget_windows_used": 1, "resume_count": 0,
            "elapsed_s": 252.0, "reason": "", "spend": {},
            "updated_at": "2026-11-07T00:00:00"}
    base.update(kw)
    return base


class _BudgetBase(unittest.TestCase):
    """Fake clock on task_budget.monotonic for the whole test (B14)."""

    def setUp(self):
        self.clock = [1000.0]
        self._patch = mock.patch.object(
            tb, "monotonic", side_effect=lambda: self.clock[0])
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _coord(self, tmp, executor):
        ledger = tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")
        return tc.TurnCoordinator(executor=executor,
                                  ledger=ledger), ledger

    def _budget_paused(self, tmp):
        """A real 3-step task paused by its time budget after step a."""
        executor = _ClockAdvancingExecutor(self.clock, 11.0)
        coord, ledger = self._coord(tmp, executor)
        task = coord.new_task(
            "Do the thing", "saved_plan",
            [tc.StepDef.from_mapping(m) for m in [
                {"tool": "a", "label": "step a"},
                {"tool": "b", "label": "step b"},
                {"tool": "c", "label": "step c"},
            ]], time_budget_s=10.0)
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "paused")
        self.assertEqual(executor.calls, ["a"])
        return coord, ledger, task, executor

    def _run_goal(self, pick, ledger, verify_script=None, budgets=None):
        """Run run_goal with a changing snapshot + fake tool (B11)."""
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


# ---------------------------------------------------------------------------
# 1. the offer covers every interrupted kind, and only those
# ---------------------------------------------------------------------------

class OfferCoverageTests(unittest.TestCase):
    def test_every_interrupted_kind_is_offered(self):
        offers = interrupted_recoverable(
            [
                _task_summary(task_id="paused-task"),
                _task_summary(task_id="running-task", state="running"),
                _task_summary(task_id="budget-task",
                              reason="Time budget spent (10s of 10s)"),
                _task_summary(task_id="monitor-task", reason="",
                              pending_monitor={
                                  "tool": "display_control",
                                  "sentence": "The second monitor is "
                                              "no longer connected."}),
                # The queues never mix: these two already have
                # their own offers (approval / question).
                _task_summary(task_id="approval-task",
                              pending_approval={"tool": "send_email"}),
                _task_summary(task_id="question-task",
                              pending_question={"question": "Which file?",
                                                "tool": "write_file"}),
                _task_summary(task_id="done-task", state="completed"),
            ],
            [
                _goal_summary(goal_id="paused-goal"),
                _goal_summary(goal_id="running-goal", state="running"),
                _goal_summary(goal_id="done-goal", state="completed"),
            ])
        self.assertEqual(
            {(o["kind"], o["id"]) for o in offers},
            {("task", "paused-task"), ("task", "running-task"),
             ("task", "budget-task"), ("task", "monitor-task"),
             ("goal", "paused-goal"), ("goal", "running-goal")})

    def test_nothing_interrupted_offers_nothing(self):
        self.assertEqual(interrupted_recoverable([], []), [])
        self.assertEqual(interrupted_recoverable(
            [_task_summary(state="completed"),
             _task_summary(task_id="t2", state="cancelled")],
            [_goal_summary(state="completed")]), [])

    def test_malformed_entries_dropped_never_offered(self):
        offers = interrupted_recoverable(
            [None, "junk", {}, {"task_id": ""},
             _task_summary(task_id="good")],
            [None, {}, {"goal_id": ""},
             _goal_summary(goal_id="good-goal")])
        self.assertEqual(
            {(o["kind"], o["id"]) for o in offers},
            {("task", "good"), ("goal", "good-goal")})

    def test_newest_first(self):
        offers = interrupted_recoverable(
            [_task_summary(task_id="old",
                           updated_at="2026-11-07T00:00:00"),
             _task_summary(task_id="new",
                           updated_at="2026-11-07T01:00:00")],
            [])
        self.assertEqual([o["id"] for o in offers], ["new", "old"])


# ---------------------------------------------------------------------------
# 2. each offer line names what / where / why / spent
# ---------------------------------------------------------------------------

class OfferLineTests(unittest.TestCase):
    def test_task_line_names_what_where_why_spent(self):
        line = recovery_offer_line("task", _task_summary(
            reason="Time budget spent (10s of 10s)",
            spend={"elapsed_s": 252.0, "cost_usd": None,
                   "cost_known": False}))
        self.assertIn("Do the thing", line)
        self.assertIn("1 of 3 step(s) done", line)
        self.assertIn("next: step b", line)
        self.assertIn("Time budget spent (10s of 10s)", line)
        self.assertIn("spent 4m 12s", line)
        self.assertIn("cost unknown", line)

    def test_goal_line_names_what_where_spent(self):
        line = recovery_offer_line("goal", _goal_summary(
            budget_windows_used=2, elapsed_s=38.0,
            reason="budget_exhausted (step)"))
        self.assertIn(GOAL, line)
        self.assertIn("2 step(s) in, budget window 2", line)
        self.assertIn("budget_exhausted (step)", line)
        self.assertIn("spent 38s", line)

    def test_crash_record_names_the_crash_first(self):
        # A crash-interrupted record (state "running") names the
        # crash first: its stored reason predates the crash.
        line = recovery_offer_line("task", _task_summary(
            state="running", reason="Time budget spent (10s of 10s)"))
        self.assertIn("the app stopped while it was running", line)
        self.assertNotIn("Time budget spent", line)
        self.assertEqual(
            why_stopped("task", _task_summary(
                state="running", reason="whatever")),
            "the app stopped while it was running")

    def test_monitor_pause_line_names_monitor_sentence(self):
        line = recovery_offer_line("task", _task_summary(
            reason="",
            pending_monitor={
                "tool": "display_control",
                "sentence": "The second monitor is no longer "
                            "connected."}))
        self.assertIn("The second monitor is no longer connected.",
                      line)

    def test_spend_line_is_honest_about_cost(self):
        self.assertEqual(spend_line({}), "")
        self.assertEqual(spend_line(None), "")
        self.assertEqual(
            spend_line({"elapsed_s": 0, "cost_usd": 0.0,
                        "cost_known": True}), "")
        self.assertEqual(
            spend_line({"elapsed_s": 65.0, "cost_usd": 0.0,
                        "cost_known": True}), "spent 1m 5s")
        self.assertEqual(
            spend_line({"elapsed_s": 65.0, "cost_known": False}),
            "spent 1m 5s · cost unknown")
        self.assertEqual(
            spend_line({"elapsed_s": 65.0, "cost_usd": 0.5,
                        "cost_known": True}),
            "spent 1m 5s · cost $0.50")
        self.assertEqual(spend_line({}, elapsed_s=38), "spent 38s")


# ---------------------------------------------------------------------------
# 3. the startup check reads the real ledger — and never auto-runs
# ---------------------------------------------------------------------------

class StartupCheckTests(_BudgetBase):
    def test_budget_paused_task_offered_and_never_auto_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            coord, ledger, task, executor = self._budget_paused(tmp)
            self.assertEqual(executor.calls, ["a"])
            offers = startup_recovery_check(ledger)
            self.assertEqual(len(offers), 1)
            self.assertEqual(offers[0]["kind"], "task")
            self.assertEqual(offers[0]["id"], task.id)
            self.assertIn("Do the thing", offers[0]["line"])
            self.assertIn("1 of 3 step(s) done", offers[0]["line"])
            self.assertIn("spent ", offers[0]["line"])
            # The check itself ran nothing: no new executor
            # call, the task still paused in the ledger.
            self.assertEqual(executor.calls, ["a"])
            self.assertEqual(task.state, "paused")
            self.assertEqual(ledger.get(task.id)["state"], "paused")

    def test_monitor_paused_task_offered_with_its_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"display_control": [_ok(_GONE)]})
            coord, ledger = self._coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                [tc.StepDef.from_mapping(m) for m in [
                    {"tool": "display_control",
                     "args": {"action": "set_wallpaper",
                              "path": "/tmp/x.png",
                              "monitor": "second monitor"},
                     "label": "Set wallpaper on second monitor"},
                    {"tool": "read_file", "args": {},
                     "label": "read the notes"},
                ]])
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "paused")
            self.assertIsNotNone(getattr(task, "_pending_monitor", None))
            offers = startup_recovery_check(ledger)
            self.assertEqual(len(offers), 1)
            self.assertEqual(offers[0]["id"], task.id)
            self.assertIn("no longer connected", offers[0]["line"])

    def test_crash_running_task_offered(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor()
            coord, ledger = self._coord(tmp, executor)
            task = coord.new_task(
                "t", "saved_plan",
                [tc.StepDef.from_mapping(m) for m in [
                    {"tool": "a", "label": "step a"},
                    {"tool": "b", "label": "step b"},
                ]])
            # What a crash leaves behind: the task started,
            # its record written, and never finished.
            task.state = "running"
            ledger.upsert_task(task)
            self.assertEqual(ledger.get(task.id)["state"], "running")
            offers = startup_recovery_check(ledger)
            self.assertEqual(len(offers), 1)
            self.assertIn("the app stopped while it was running",
                          offers[0]["line"])

    def test_question_pending_task_is_not_offered_here(self):
        # The B15 question offer already covers it; the
        # recovery offer must never double-offer it.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor()
            coord, ledger = self._coord(tmp, executor)
            task = coord.new_task(
                "t", "saved_plan",
                [tc.StepDef.from_mapping(
                    {"tool": "write_file", "args": {},
                     "ask": {"question": "Which file?",
                             "answer_key": "filename"},
                     "label": "write"})])
            self.assertEqual(coord.run_task(task).state, "paused")
            self.assertIsNotNone(coord.pending_question(task.id))
            # The ledger DOES hold the interrupted record —
            # the exclusion is in the recovery layer, not
            # because the record is missing.
            self.assertEqual(len(ledger.list_interrupted()), 1)
            self.assertEqual(startup_recovery_check(ledger), [])

    def test_approval_pending_task_is_not_offered_here(self):
        class _FakeLedger:
            def list_interrupted(self):
                return [_task_summary(
                    task_id="approval-task",
                    pending_approval={"tool": "send_email"})]

            def list_interrupted_goals(self):
                return []

        self.assertEqual(startup_recovery_check(_FakeLedger()), [])

    def test_completed_task_offers_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor()
            coord, ledger = self._coord(tmp, executor)
            task = coord.new_task(
                "t", "saved_plan",
                [tc.StepDef.from_mapping({"tool": "a"})])
            self.assertEqual(coord.run_task(task).state, "completed")
            self.assertEqual(startup_recovery_check(ledger), [])

    def test_broken_ledger_offers_nothing_never_raises(self):
        class _BrokenLedger:
            def list_interrupted(self):
                raise RuntimeError("ledger file is corrupt")

            def list_interrupted_goals(self):
                raise RuntimeError("ledger file is corrupt")

        self.assertEqual(startup_recovery_check(_BrokenLedger()), [])

    def test_startup_check_is_read_only(self):
        # The offer is a courtesy read: it never resumes,
        # abandons, upserts, or saves anything.
        src = inspect.getsource(startup_recovery_check)
        for forbidden in ("resume_ledger_task", "abandon_record",
                          "upsert", ".save(", "resume_goal",
                          "cancel_task"):
            self.assertNotIn(forbidden, src)


# ---------------------------------------------------------------------------
# 4. dismissing never deletes; abandoning removes — only then
# ---------------------------------------------------------------------------

class DismissAbandonTests(_BudgetBase):
    def test_dismissal_never_deletes_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            _coord, ledger, task, _executor = self._budget_paused(tmp)
            first = startup_recovery_check(ledger)
            second = startup_recovery_check(ledger)
            # Offered again, identical — a dismissal is "not
            # now", not an abandonment; there is no dismissal
            # list to go stale (the offer is recomputed fresh
            # from the ledger at every read).
            self.assertEqual(len(first), 1)
            self.assertEqual(
                [(o["kind"], o["id"]) for o in first],
                [(o["kind"], o["id"]) for o in second])
            record = ledger.get(task.id)
            self.assertEqual(record["state"], "paused")

    def test_abandon_removes_from_offer_only_then(self):
        with tempfile.TemporaryDirectory() as tmp:
            _coord, ledger, task, _executor = self._budget_paused(tmp)
            offer = startup_recovery_check(ledger)[0]
            self.assertTrue(abandon_recovery_offer(offer, ledger=ledger))
            record = ledger.get(task.id)
            self.assertEqual(record["state"], "cancelled")
            # The record stays as cancelled history — it is
            # simply never offered again.
            self.assertEqual(startup_recovery_check(ledger), [])
            # Abandoning twice abandons nothing.
            self.assertIsNone(
                abandon_recovery_offer(offer, ledger=ledger))
            self.assertIsNone(abandon_recovery_offer(
                {"kind": "task", "id": "no-such-task"}, ledger=ledger))
            self.assertIsNone(abandon_recovery_offer(
                {"kind": "task", "id": ""}, ledger=ledger))

    def test_abandon_refuses_terminal_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor()
            coord, ledger = self._coord(tmp, executor)
            task = coord.new_task(
                "t", "saved_plan",
                [tc.StepDef.from_mapping({"tool": "a"})])
            self.assertEqual(coord.run_task(task).state, "completed")
            self.assertFalse(ledger.abandon_record(task.id))
            self.assertEqual(
                ledger.get(task.id)["state"], "completed")


# ---------------------------------------------------------------------------
# 5. resuming goes through the existing paths, index + history intact
# ---------------------------------------------------------------------------

class ResumeTests(_BudgetBase):
    def test_resume_live_held_task_keeps_index_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            coord, ledger, task, executor = self._budget_paused(tmp)
            # 100 s pass while paused — pause time is never
            # charged (B14); the resumed run must not advance
            # the clock again (steps b/c run fast).
            self.clock[0] += 100.0
            executor._advance_s = 0.0
            offer = startup_recovery_check(ledger)[0]
            receipt = resume_recovery_offer(
                offer, live_coordinator=coord)
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.state, "completed")
            # Step a ran exactly once, before the pause; the
            # resume continued from step b, never re-ran a.
            self.assertEqual(executor.calls, ["a", "b", "c"])
            self.assertEqual(task._budget_windows, 2)
            self.assertLess(receipt.spend["elapsed_s"], 20.0)
            record = ledger.get(task.id)
            self.assertEqual(record["state"], "completed")
            self.assertEqual(len(record["outcomes"]), 3)

    def test_resume_crash_running_task_via_rebuild_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor()
            coord, ledger = self._coord(tmp, executor)
            task = coord.new_task(
                "t", "saved_plan",
                [tc.StepDef.from_mapping(m) for m in [
                    {"tool": "a", "label": "step a"},
                    {"tool": "b", "label": "step b"},
                ]])
            task.state = "running"
            ledger.upsert_task(task)
            offer = startup_recovery_check(ledger)[0]
            # The rebuild path constructs a fresh coordinator
            # bound to the ledger; the factory injects the fake
            # executor (the real default is the funnel) — the
            # seam under test is that resume_recovery_offer
            # builds it and calls resume_ledger_task with the
            # record. The factory is patched on the ALIASED
            # module (B6: tests drive the aliased module).
            import voice_assistant.core.brain.task_coordinator as vatc
            orig = vatc.TurnCoordinator

            def _factory(*, ledger=None, **kw):
                return tc.TurnCoordinator(executor=executor,
                                          ledger=ledger)

            with mock.patch.object(vatc, "TurnCoordinator", _factory):
                receipt = resume_recovery_offer(offer, ledger=ledger)
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(executor.calls, ["a", "b"])
            record = ledger.get(task.id)
            self.assertEqual(record["state"], "completed")
            self.assertEqual(len(record["outcomes"]), 2)
            self.assertIs(vatc.TurnCoordinator, orig)

    def test_resume_stale_offer_resumes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            _coord, ledger, _task, _executor = self._budget_paused(tmp)
            self.assertIsNone(resume_recovery_offer(
                {"kind": "task", "id": "no-such-task"}, ledger=ledger))
            self.assertIsNone(resume_recovery_offer(
                {"kind": "task", "id": ""}, ledger=ledger))
            # A completed record is history, not resumable.
            executor = _ScriptExecutor()
            coord, ledger2 = self._coord(tmp, executor)
            done = coord.new_task(
                "t", "saved_plan",
                [tc.StepDef.from_mapping({"tool": "a"})])
            self.assertEqual(coord.run_task(done).state, "completed")
            self.assertIsNone(resume_recovery_offer(
                {"kind": "task", "id": done.id}, ledger=ledger2))


# ---------------------------------------------------------------------------
# 6. goals: offered back, resume needs a picker, crash leaves a record
# ---------------------------------------------------------------------------

class GoalRecoveryTests(_BudgetBase):
    def _paused_goal(self, tmp):
        ledger = tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")

        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {}}

        result = self._run_goal(
            pick, ledger,
            verify_script={GOAL: ("not-met", "still not there")},
            budgets={"goal_max_steps": 1})
        self.assertIn("Goal stopped: step budget exhausted", result)
        records = [r for r in ledger.all_records()
                   if r.get("kind") == "goal"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["state"], "paused")
        return ledger, records[0]

    def test_goal_offered_back_with_what_where_why_spent(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger, record = self._paused_goal(tmp)
            offers = startup_recovery_check(ledger)
            self.assertEqual(len(offers), 1)
            self.assertEqual(offers[0]["kind"], "goal")
            self.assertEqual(offers[0]["id"], record["task_id"])
            self.assertIn(GOAL, offers[0]["line"])
            self.assertIn("1 step(s) in, budget window 1",
                          offers[0]["line"])
            self.assertIn("budget_exhausted (step)", offers[0]["line"])

    def test_goal_resume_without_picker_resumes_nothing(self):
        # The D1 boundary: a goal is not resumable from here
        # without a picker, and no resume is invented.
        with tempfile.TemporaryDirectory() as tmp:
            ledger, record = self._paused_goal(tmp)
            offer = startup_recovery_check(ledger)[0]
            self.assertIsNone(
                resume_recovery_offer(offer, ledger=ledger))
            self.assertIsNone(
                resume_recovery_offer(offer, live_coordinator=None,
                                      ledger=ledger, pick_action=None))
            self.assertEqual(
                ledger.get_goal(record["task_id"])["state"], "paused")

    def test_goal_resume_with_picker_keeps_history_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger, record = self._paused_goal(tmp)
            goal_id = record["task_id"]
            offer = startup_recovery_check(ledger)[0]
            seen_histories = []

            async def resume_pick(goal, observation, history):
                seen_histories.append(list(history))
                return {"done": "finished after resume"}

            async def _obs():
                return {"fg": "fake", "n": 99, "windows": ["fake"],
                        "texts": []}

            # resume_goal is imported lazily from the ALIASED
            # module (B6: tests drive the aliased module), so
            # the run_goal seams are patched there, mirroring
            # the B11 _run helper on the core module.
            import voice_assistant.core.brain.planner_rungoal as varg
            with mock.patch.object(
                    varg, "_goal_budget_int",
                    side_effect=lambda n, d: d), \
                    mock.patch.object(
                        varg, "_goal_budget_seconds",
                        side_effect=lambda n, d: d), \
                    mock.patch.object(varg, "_known_tools",
                                      return_value=None), \
                    mock.patch.object(
                        varg, "_execute_tool",
                        return_value={"status": "success",
                                      "result": "did it"}):
                result = resume_recovery_offer(
                    offer, ledger=ledger, pick_action=resume_pick)
            self.assertIsNotNone(result)
            self.assertIn("Resuming goal (resume 1, budget window 2",
                          result)
            self.assertIn("Goal complete: finished after resume", result)
            # The picker saw the pre-resume history intact.
            self.assertEqual(len(seen_histories), 1)
            self.assertEqual(len(seen_histories[0]), 1)
            self.assertEqual(seen_histories[0][0]["event"], "completed")
            final = ledger.get_goal(goal_id)
            self.assertEqual(final["state"], "completed")
            self.assertEqual(final["steps_used"], 1)
            self.assertEqual(final["resume_count"], 1)
            self.assertEqual(final["budget_windows_used"], 2)
            self.assertEqual(len(final["history"]), 1)

    def test_goal_crash_leaves_interrupted_record_that_is_offered(self):
        calls = []

        def _crashing_execute(tool, args):
            calls.append(1)
            if len(calls) >= 3:
                raise RuntimeError("boom: tool blew up mid-goal")
            return {"status": "success", "result": "did it"}

        async def pick(goal, observation, history):
            return {"tool": "fake_tool", "args": {}}

        async def _obs():
            return _ChangingSnapshot()()

        with tempfile.TemporaryDirectory() as tmp:
            ledger = tl.TaskLedger(
                path=pathlib.Path(tmp) / "ledger.json")
            with mock.patch.object(rg, "_known_tools",
                                   return_value=None), \
                    mock.patch.object(rg, "_execute_tool",
                                      side_effect=_crashing_execute):
                with self.assertRaises(RuntimeError):
                    asyncio.run(rg.run_goal(
                        GOAL, pick_action=pick, observe=_obs,
                        verify_expect=_fake_verify({}),
                        ledger=ledger))
            records = [r for r in ledger.all_records()
                       if r.get("kind") == "goal"]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["state"], "running")
            self.assertEqual(records[0]["steps_used"], 2)
            offers = startup_recovery_check(ledger)
            self.assertEqual(len(offers), 1)
            self.assertEqual(offers[0]["kind"], "goal")
            self.assertIn("the app stopped while it was running",
                          offers[0]["line"])


# ---------------------------------------------------------------------------
# 7. static pins: the wiring, and no voice channel into recovery
# ---------------------------------------------------------------------------

class StaticPinTests(unittest.TestCase):
    def test_startup_wiring_is_present(self):
        frame = (ROOT / "gui" / "window_shell_frame.py").read_text()
        self.assertIn(
            "QTimer.singleShot(1500, self._run_startup_recovery_check)",
            frame)
        activity = (ROOT / "gui" / "window_memory_activity.py"
                    ).read_text()
        self.assertIn("_run_startup_recovery_check", activity)
        self.assertIn("_review_recovery", activity)
        self.assertIn("_activity_recovery_btn", activity)
        pages = (ROOT / "gui" / "window_pages_activity.py").read_text()
        self.assertIn("Resume interrupted tasks", pages)
        # The startup check never pops, navigates, or runs:
        # its own source names no dialog, no navigation, no
        # resume — only the ledger read and the refresh.
        start = activity.index("def _run_startup_recovery_check")
        end = activity.index("\n    def ", start + 10)
        body = activity[start:end]
        for forbidden in ("exec(", "show(", "setCurrentIndex",
                          "setCurrentWidget", "resume_", "abandon_"):
            self.assertNotIn(forbidden, body)

    def test_no_voice_path_into_recovery(self):
        # C-14: recovery is a GUI offer, never voice-resolvable.
        # The recovery module must not reference any voice
        # resolution path.
        text = (ROOT / "gui" / "pending_recovery.py").read_text()
        for forbidden in ("voice_goal", "voice_text_bridge",
                          "_maybe_confirm_reply", "voice_client",
                          "live_client"):
            self.assertNotIn(forbidden, text)
        # And no voice-side module calls the recovery actions.
        for path in list((ROOT / "core" / "brain").glob("voice*.py")) \
                + list((ROOT / "gui").glob("window_services*.py")):
            content = path.read_text()
            self.assertNotIn("resume_recovery_offer", content,
                             f"{path.name} must not resume recovery")
            self.assertNotIn("abandon_recovery_offer", content,
                             f"{path.name} must not abandon recovery")


# ---------------------------------------------------------------------------
# 8. dialogs (Qt-only)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_QT, "no PySide6 on the system interpreter")
class RecoveryDialogTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        from PySide6.QtWidgets import QWidget
        self.window = QWidget()

    def tearDown(self):
        # R5 lesson: stale closed dialogs linger in
        # topLevelWidgets — sweep them so no later test's
        # first-match click scan can hit one.
        try:
            for widget in QApplication.topLevelWidgets():
                if hasattr(widget, "_recovery_btn_not_now"):
                    try:
                        widget.close()
                    except Exception:  # noqa: BLE001 — teardown
                        pass  # never raises.
        except Exception:  # noqa: BLE001 — teardown never raises
            pass
        try:
            self.window.close()
        except Exception:  # noqa: BLE001 — teardown never raises
            pass
        QApplication.processEvents()

    def _offer(self, kind="task"):
        return {"kind": kind, "id": "t1", "title": "Do the thing",
                "line": "Do the thing · 1 of 3 step(s) done — "
                        "next: step b · Time budget spent",
                "summary": {}}

    def _visible_dialog(self):
        for widget in QApplication.topLevelWidgets():
            if widget.isVisible() and hasattr(
                    widget, "_recovery_btn_not_now"):
                return widget
        return None

    def test_dialog_task_has_resume_and_safe_default(self):
        from gui.pending_recovery import build_recovery_dialog
        dlg = build_recovery_dialog(self.window, self._offer())
        self.assertIsNotNone(dlg._recovery_btn_resume)
        self.assertEqual(dlg._recovery_btn_resume.text(), "Resume")
        self.assertIsNotNone(dlg._recovery_btn_not_now)
        self.assertIsNotNone(dlg._recovery_btn_abandon)
        # The safe default + initial focus is Not now: resuming
        # is an explicit click, never the default, and
        # abandoning is never the default either.
        self.assertTrue(dlg._recovery_btn_not_now.isDefault())
        self.assertFalse(dlg._recovery_btn_resume.isDefault())
        self.assertFalse(dlg._recovery_btn_abandon.isDefault())
        self.assertFalse(dlg._recovery_timed_out)
        self.assertIsNone(dlg._recovery_outcome)

    def test_dialog_goal_has_no_resume_button(self):
        from gui.pending_recovery import build_recovery_dialog
        dlg = build_recovery_dialog(self.window, self._offer("goal"))
        self.assertIsNone(dlg._recovery_btn_resume)
        self.assertIsNotNone(dlg._recovery_btn_not_now)
        self.assertIsNotNone(dlg._recovery_btn_abandon)

    def test_resume_click_returns_resume(self):
        from PySide6.QtCore import QTimer
        from gui.pending_recovery import present_recovery_dialog

        def _click():
            dlg = self._visible_dialog()
            if dlg is not None and dlg._recovery_btn_resume is not None:
                dlg._recovery_btn_resume.click()

        QTimer.singleShot(50, _click)
        self.assertEqual(
            present_recovery_dialog(self.window, self._offer(),
                                    timeout_s=30), "resume")

    def test_abandon_click_returns_abandon(self):
        from PySide6.QtCore import QTimer
        from gui.pending_recovery import present_recovery_dialog

        def _click():
            dlg = self._visible_dialog()
            if dlg is not None:
                dlg._recovery_btn_abandon.click()

        QTimer.singleShot(50, _click)
        self.assertEqual(
            present_recovery_dialog(self.window, self._offer(),
                                    timeout_s=30), "abandon")

    def test_not_now_click_returns_not_now(self):
        from PySide6.QtCore import QTimer
        from gui.pending_recovery import present_recovery_dialog

        def _click():
            dlg = self._visible_dialog()
            if dlg is not None:
                dlg._recovery_btn_not_now.click()

        QTimer.singleShot(50, _click)
        self.assertEqual(
            present_recovery_dialog(self.window, self._offer(),
                                    timeout_s=30), "not_now")

    def test_silence_timeout_decides_nothing(self):
        from gui.pending_recovery import present_recovery_dialog
        self.assertEqual(
            present_recovery_dialog(self.window, self._offer(),
                                    timeout_s=0.02), "timeout")

    def test_close_decides_nothing(self):
        from PySide6.QtCore import QTimer
        from gui.pending_recovery import present_recovery_dialog

        def _close():
            dlg = self._visible_dialog()
            if dlg is not None:
                dlg.close()

        QTimer.singleShot(50, _close)
        self.assertEqual(
            present_recovery_dialog(self.window, self._offer(),
                                    timeout_s=30), "not_now")


if __name__ == "__main__":
    unittest.main()
