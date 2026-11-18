"""Fix 9 lab gaps (R5, 2026-11-06): the plan-approval
dialog's 50 s timeout against a real human pause, and
multi-monitor / wallpaper steps inside routines.

Lab leg only. The real-Windows field leg — a real human
pause at the plan dialog on his machine, and a routine
wallpaper step on his real multi-monitor setup — is OWED,
not this batch.

Gaps proved first (from the tree):

1. A plan-dialog timeout collapsed into the same [] as an
   explicit "Ask me each time": request_plan_decision
   returns [] for a timeout, a refusal, a missing handler
   and a handler error alike, and the coordinator ran the
   task step by step — each step popping its own confirm
   dialog, each with its own 60 s default-deny timeout —
   into a human who was not there. No record anywhere said
   a timeout happened. The stale comment in plan_approval
   ("a late answer ... applies only to steps not yet
   dispatched") was also wrong: request_plan_decision reads
   the handler's result only while the wait is open, so a
   late answer was discarded, not applied.
2. A routine wallpaper step naming a gone monitor did not
   fail the routine — it reported SUCCESS for a wallpaper
   that was never set: display_control's set_wallpaper leg
   returns the plain string "Wallpaper not set:
   unknown-monitor: ...", "unknown-monitor" is in NO
   refusal marker, so the funnel returns it as
   {"status": "success", "result": <that sentence>} (probe,
   2026-11-06), and the coordinator recorded the step as a
   success.

What this batch builds (and this suite proves):

* ONE timeout rule: a timeout is a third answer, distinct
  from an explicit refusal. The dialog, the GUI handler
  and request_plan_decision_ex all report the outcome
  ("approved" / "ask_each_time" / "timeout" /
  "no_handler" / "error"); the coordinator pauses the task
  before any step runs on a timeout — nothing ran, nothing
  approved, no retry strike, no drop-budget burn — with
  the pause named in the receipt, the ledger and one
  activity line. Convergence is N=1 by construction:
  resume never re-invokes the plan hook, so no second
  plan timeout is possible.
* ONE monitor-gone rule: a wallpaper set naming a gone
  monitor pauses the task resumably (a _pending_monitor
  record, ledger-serialized beside _pending_approval —
  deliberately NOT _pending_approval, whose deny path
  would fail the step) with the honest sentence; resume
  re-runs the step, which re-resolves the monitor fresh.
  The revert slot (data/wallpaper_previous.json v2)
  survives pause/resume untouched.
* B1 (2026-11-06): the same pause now covers the WHOLE set
  family — every reason (art-missing, could-not-save-previous,
  could-not-read-current, system-call-refused, system-call-failed,
  read-back-mismatch, windows-only), the legacy save /
  system-call sentences, and the wallpaper_apply "not applied"
  sentences — on both executor paths and both shapes (a
  success-carried sentence, or the funnel-shaped failure dict
  the handlers now return). The R5 pin that other failures
  did NOT pause is intentionally reversed by this batch.
"""

import json
import os
import pathlib
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

from core import plan_approval as pa  # noqa: E402
from core import wallpaper as wp  # noqa: E402
from core.brain import task_coordinator as tc  # noqa: E402
from core.brain import task_ledger as tl  # noqa: E402
from core.brain.task_coordinator import StepDef  # noqa: E402
from core.brain.task_coordinator_refusal import (  # noqa: E402
    _detect_wallpaper_monitor_gone)

try:
    from PySide6.QtWidgets import QApplication  # noqa: E402
    from gui.plan_approval_dialog import (  # noqa: E402
        present_plan_approval_dialog,
        present_plan_approval_dialog_ex)
    _GUI = True
except Exception:  # noqa: BLE001 — no Qt: skip dialog tests
    QApplication = None
    present_plan_approval_dialog = None
    present_plan_approval_dialog_ex = None
    _GUI = False


def _ok(result="done"):
    return {"status": "success", "result": result}


def _fail(message="simulated failure"):
    return {"status": "failure", "message": message}


def _steps(*mappings):
    return [StepDef.from_mapping(m) for m in mappings]


class _ScriptExecutor:
    """Per-tool scripted results; records every call."""

    def __init__(self, script):
        # script: {tool: [result, ...]}; exhausted tools repeat
        # their last result.
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, tool, args):
        self.calls.append((tool, dict(args or {})))
        results = self.script.get(tool)
        if not results:
            return _ok()
        if len(results) > 1:
            return results.pop(0)
        return results[0]


_GONE = ("Wallpaper not set: unknown-monitor: no monitor named "
         "second monitor (monitors now: primary DISPLAY1).")
_GONE_REFUSED = {"status": "refused", "message": _GONE}
_SET_OK = ("Wallpaper set to /tmp/x.png on primary (saved setting "
           "read back to confirm).")


def _wallpaper_steps():
    return _steps(
        {"tool": "display_control",
         "args": {"action": "set_wallpaper", "path": "/tmp/x.png",
                  "monitor": "second monitor"},
         "label": "Set wallpaper on second monitor"})


# ---------------------------------------------------------------------------
# 1. the timeout is a third answer (core)
# ---------------------------------------------------------------------------

class PlanTimeoutOutcomeTests(unittest.TestCase):
    def tearDown(self):
        pa.register_plan_handler(None)
        pa.clear_all()
        super().tearDown()

    def _task(self):
        return tc.Task("t", "routine", _steps(
            {"tool": "wallpaper_revert", "args": {}}))

    def test_slow_handler_reports_timeout_and_plain_still_asks(self):
        def slow_handler(task, plan):
            time.sleep(0.3)
            return [0]

        pa.register_plan_handler(slow_handler)
        task = self._task()
        # The _ex form names the timeout; the [] contract of
        # the plain form is unchanged (existing pin).
        self.assertEqual(
            pa.request_plan_decision_ex(task, timeout_s=0.05),
            ([], "timeout"))
        self.assertEqual(
            pa.request_plan_decision(task, timeout_s=0.05), [])

    def test_dict_handler_timeout_outcome_read(self):
        pa.register_plan_handler(
            lambda task, plan: {"approved": [], "outcome": "timeout"})
        self.assertEqual(
            pa.request_plan_decision_ex(self._task()),
            ([], "timeout"))

    def test_timeout_with_indices_discards_them(self):
        # A timeout approves nothing, whatever indices rode
        # along with it — a lying handler cannot smuggle an
        # approval through a timeout.
        pa.register_plan_handler(
            lambda task, plan: {"approved": [0], "outcome": "timeout"})
        self.assertEqual(
            pa.request_plan_decision_ex(self._task()),
            ([], "timeout"))

    def test_explicit_answers_keep_their_outcomes(self):
        pa.register_plan_handler(lambda task, plan: [])
        self.assertEqual(
            pa.request_plan_decision_ex(self._task()),
            ([], "ask_each_time"))
        pa.register_plan_handler(lambda task, plan: [0])
        self.assertEqual(
            pa.request_plan_decision_ex(self._task()),
            ([0], "approved"))
        pa.register_plan_handler(
            lambda task, plan: {"approved": [], "outcome": "ask_each_time"})
        self.assertEqual(
            pa.request_plan_decision_ex(self._task()),
            ([], "ask_each_time"))

    def test_no_handler_and_handler_error_named(self):
        pa.register_plan_handler(None)
        self.assertEqual(
            pa.request_plan_decision_ex(self._task()),
            ([], "no_handler"))

        def boom(task, plan):
            raise RuntimeError("boom")

        pa.register_plan_handler(boom)
        self.assertEqual(
            pa.request_plan_decision_ex(self._task()),
            ([], "error"))


# ---------------------------------------------------------------------------
# 2. a plan timeout pauses the task before any step runs
# ---------------------------------------------------------------------------

class PlanTimeoutPauseTests(unittest.TestCase):
    def tearDown(self):
        pa.register_plan_handler(None)
        pa.clear_all()
        super().tearDown()

    def _coord(self, tmp, executor):
        ledger = tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")
        return tc.TurnCoordinator(executor=executor, ledger=ledger), ledger

    def _hook(self, outcome, indices=None, calls=None):
        def hook(task, preview):
            if calls is not None:
                calls.append(outcome)
            return {"approved": list(indices or []), "outcome": outcome}
        return hook

    def _routine_steps(self):
        return [{"tool": "wallpaper_revert", "args": {},
                 "label": "Revert wallpaper"}]

    def test_timeout_pauses_with_nothing_run_and_no_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({})
            coord, _ledger = self._coord(tmp, executor)
            result = coord.handle_turn(
                "run routine morning", routine_names=("morning",),
                routine_steps_for=lambda _n: self._routine_steps(),
                plan_hook=self._hook("timeout"))
            task = result["task"]
            receipt = result["receipt"]
            self.assertEqual(task.state, "paused")
            self.assertEqual(receipt.state, "paused")
            self.assertEqual(receipt.attempted, 0)
            self.assertEqual(receipt.completed, 0)
            self.assertEqual(executor.calls, [])
            self.assertIn("timed out", receipt.reason)
            self.assertIn("nothing ran", receipt.reason)
            # No approval exists: the step is not covered, so
            # the digest re-verification has nothing stale to
            # honour after the timeout.
            self.assertIsNone(pa.begin_step_dispatch(
                task, task.steps[0], {}))
            # No retry strike and no verification record: a
            # human pause is not a failure.
            self.assertEqual(task.verification, [])
            self.assertEqual(coord._retry_strikes(task, 0), 0)

    def test_timeout_pause_is_in_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({})
            coord, _ledger = self._coord(tmp, executor)
            result = coord.handle_turn(
                "run routine morning", routine_names=("morning",),
                routine_steps_for=lambda _n: self._routine_steps(),
                plan_hook=self._hook("timeout"))
            task_id = result["task"].id
            second = tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")
            record = second.get(task_id)
            self.assertIsNotNone(record)
            self.assertEqual(record.get("state"), "paused")
            self.assertIn("timed out", record.get("reason", ""))

    def test_timeout_pause_writes_one_activity_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            from core.brain import task_coordinator_lifecycle as tcl
            fake_root = pathlib.Path(tmp) / "fake_root"
            # The writer climbs three parents from __file__
            # (core/brain/… -> tree root), so the fake file
            # must sit three levels deep for data/ to land
            # inside the temp dir.
            (fake_root / "core" / "brain").mkdir(parents=True)
            executor = _ScriptExecutor({})
            coord, _ledger = self._coord(tmp, executor)
            with mock.patch.object(
                    tcl, "__file__",
                    str(fake_root / "core" / "brain"
                        / "task_coordinator_lifecycle.py")):
                coord.handle_turn(
                    "run routine morning", routine_names=("morning",),
                    routine_steps_for=lambda _n: self._routine_steps(),
                    plan_hook=self._hook("timeout"))
            lines = (fake_root / "data" / "activity.log").read_text(
                encoding="utf-8").splitlines()
            timed = [ln for ln in lines
                     if "plan approval timed out" in ln]
            self.assertEqual(len(timed), 1)
            self.assertIn("plan_approval", timed[0])

    def test_explicit_ask_each_time_runs_step_by_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({})
            coord, _ledger = self._coord(tmp, executor)
            result = coord.handle_turn(
                "run routine morning", routine_names=("morning",),
                routine_steps_for=lambda _n: self._routine_steps(),
                plan_hook=self._hook("ask_each_time"))
            self.assertEqual(result["receipt"].state, "completed")
            self.assertEqual(len(executor.calls), 1)

    def test_legacy_list_hook_still_runs(self):
        # A hook returning a plain list keeps the old meaning:
        # [] is "ask each time", NOT a timeout — only the dict
        # form can report one.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({})
            coord, _ledger = self._coord(tmp, executor)
            result = coord.handle_turn(
                "run routine morning", routine_names=("morning",),
                routine_steps_for=lambda _n: self._routine_steps(),
                plan_hook=lambda task, preview: [])
            self.assertEqual(result["receipt"].state, "completed")

    def test_resume_after_timeout_runs_without_reasking_the_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor({})
            coord, _ledger = self._coord(tmp, executor)
            calls = []
            result = coord.handle_turn(
                "run routine morning", routine_names=("morning",),
                routine_steps_for=lambda _n: self._routine_steps(),
                plan_hook=self._hook("timeout", calls=calls))
            task = result["task"]
            self.assertEqual(task.state, "paused")
            resumed = coord.resume_task(task.id)
            self.assertEqual(resumed.state, "completed")
            self.assertEqual(len(executor.calls), 1)
            # The hook was asked exactly once, in handle_turn:
            # resume never re-invokes it, so no second plan
            # timeout is possible — convergence N=1.
            self.assertEqual(calls, ["timeout"])
            # And the resumed run carried no stale approval.
            self.assertIsNone(pa.begin_step_dispatch(
                task, task.steps[0], {}))


# ---------------------------------------------------------------------------
# 3. the dialog reports its own ending (Qt)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_GUI, "Qt unavailable")
class PlanDialogExTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def _preview(self):
        return {
            "approvable": [
                {"index": 0, "tool": "write_file",
                 "label": "Write note.txt",
                 "summary": "note.txt — 9 characters"},
                {"index": 2, "tool": "wallpaper_revert",
                 "label": "Revert wallpaper", "summary": ""},
            ],
            "still_individual": [],
            "no_ask": 0,
        }

    def test_silence_timeout_reports_timeout(self):
        # A 10 ms silence timeout: the dialog rejects itself
        # and _ex names it a timeout — doing nothing never
        # approves, and it is not "ask each time" either.
        self.assertEqual(
            present_plan_approval_dialog_ex(
                None, "Morning plan", self._preview(), timeout_s=0.01),
            ([], "timeout"))
        # The plain form's [] contract is unchanged.
        self.assertEqual(
            present_plan_approval_dialog(
                None, "Morning plan", self._preview(), timeout_s=0.01),
            [])

    def test_broken_dialog_reports_error(self):
        with mock.patch(
                "gui.plan_approval_dialog.build_plan_approval_dialog",
                side_effect=RuntimeError("boom")):
            self.assertEqual(
                present_plan_approval_dialog_ex(
                    None, "Morning plan", self._preview()),
                ([], "error"))

    def tearDown(self):
        # Closed dialogs are never destroyed by the product
        # (see _click_dialog_button), so sweep any leftover
        # plan dialog out of topLevelWidgets() before the
        # next test — otherwise a later test's click can
        # land on a hidden dialog from this one.
        from PySide6.QtWidgets import QApplication
        for widget in QApplication.topLevelWidgets():
            if hasattr(widget, "_plan_btn_approve"):
                widget.deleteLater()
        QApplication.processEvents()
        super().tearDown()

    def _click_dialog_button(self, attr):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        def _click():
            for widget in QApplication.topLevelWidgets():
                # Only the dialog on screen is live: a closed
                # dialog is never destroyed (no
                # WA_DeleteOnClose, and the silence timer's
                # closure keeps the wrapper alive), so an
                # earlier test's HIDDEN dialog still sits in
                # topLevelWidgets() carrying the same button
                # attributes — and topLevelWidgets() order is
                # not creation order, so the first match is
                # sometimes that stale dialog. Clicking it
                # rejects an already-finished dialog and the
                # live one times out unanswered. The live
                # modal dialog is the only visible one.
                if not widget.isVisible():
                    continue
                button = getattr(widget, attr, None)
                if button is not None:
                    button.click()
                    return

        QTimer.singleShot(150, _click)

    def test_ask_each_time_button_reports_ask_each_time(self):
        self._click_dialog_button("_plan_btn_ask_each")
        self.assertEqual(
            present_plan_approval_dialog_ex(
                None, "Morning plan", self._preview(), timeout_s=60),
            ([], "ask_each_time"))

    def test_approve_button_reports_approved(self):
        self._click_dialog_button("_plan_btn_approve")
        self.assertEqual(
            present_plan_approval_dialog_ex(
                None, "Morning plan", self._preview(), timeout_s=60),
            ([0, 2], "approved"))


# ---------------------------------------------------------------------------
# 4. a gone monitor pauses the routine, never claims success
# ---------------------------------------------------------------------------

class MonitorGoneTests(unittest.TestCase):
    def tearDown(self):
        pa.clear_all()
        super().tearDown()

    def _coord(self, tmp, executor):
        ledger = tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")
        return tc.TurnCoordinator(executor=executor, ledger=ledger), ledger

    def test_gone_monitor_as_success_pauses_instead(self):
        # The proved shape: the funnel returns the refusal
        # sentence as a SUCCESS payload. Without the detector
        # the step is recorded successful for a wallpaper
        # that was never set.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"display_control": [_ok(_GONE)]})
            coord, _ledger = self._coord(tmp, executor)
            task = coord.new_task("t", "routine", _wallpaper_steps())
            receipt = coord.run_task(task)
            self.assertEqual(task.state, "paused")
            self.assertEqual(receipt.state, "paused")
            self.assertEqual(receipt.attempted, 0)
            self.assertEqual(len(executor.calls), 1)
            pending = getattr(task, "_pending_monitor", None)
            self.assertIsInstance(pending, dict)
            self.assertEqual(pending["tool"], "display_control")
            self.assertIn("no longer connected", pending["sentence"])
            self.assertIn("DISPLAY1", pending["sentence"])
            self.assertIn("no longer connected", receipt.reason)
            # It is not an approval question: nothing to
            # resolve, and the approval record is empty.
            self.assertIsNone(coord.pending_approval(task.id))
            self.assertIsNone(coord.resolve_pending(
                task.id, approved=True))

    def test_gone_monitor_as_refused_dict_also_pauses(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"display_control": [dict(_GONE_REFUSED)]})
            coord, _ledger = self._coord(tmp, executor)
            task = coord.new_task("t", "routine", _wallpaper_steps())
            receipt = coord.run_task(task)
            self.assertEqual(task.state, "paused")
            self.assertIsNotNone(getattr(task, "_pending_monitor", None))

    def test_resume_after_reconnect_completes_and_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"display_control": [_ok(_GONE), _ok(_SET_OK)]})
            coord, _ledger = self._coord(tmp, executor)
            task = coord.new_task("t", "routine", _wallpaper_steps())
            self.assertEqual(coord.run_task(task).state, "paused")
            resumed = coord.resume_task(task.id)
            self.assertEqual(resumed.state, "completed")
            self.assertEqual(len(executor.calls), 2)
            self.assertEqual(len(task.outcomes), 1)
            self.assertEqual(task.outcomes[0].status, "success")
            self.assertIsNone(getattr(task, "_pending_monitor", None))

    def test_monitor_pause_survives_ledger_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"display_control": [_ok(_GONE), _ok(_SET_OK)]})
            coord, _ledger = self._coord(tmp, executor)
            task = coord.new_task("t", "routine", _wallpaper_steps())
            self.assertEqual(coord.run_task(task).state, "paused")
            second = tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")
            record = second.get(task.id)
            self.assertIsNotNone(record)
            pending = record.get("pending_monitor")
            self.assertIsInstance(pending, dict)
            self.assertIn("no longer connected", pending["sentence"])
            # A fresh coordinator rebuilds the paused task
            # with its reason intact, then resumes it.
            coord_b = tc.TurnCoordinator(executor=executor, ledger=second)
            rebuilt = second.rebuild_task(record)
            coord_b._tasks[rebuilt.id] = rebuilt
            self.assertIn("no longer connected",
                          rebuilt.receipt().reason)
            resumed = coord_b.resume_task(rebuilt.id)
            self.assertEqual(resumed.state, "completed")

    def test_cancel_clears_the_monitor_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"display_control": [_ok(_GONE)]})
            coord, _ledger = self._coord(tmp, executor)
            task = coord.new_task("t", "routine", _wallpaper_steps())
            self.assertEqual(coord.run_task(task).state, "paused")
            self.assertTrue(coord.cancel_task(task.id))
            self.assertIsNone(getattr(task, "_pending_monitor", None))

    def test_other_wallpaper_failure_now_pauses(self):
        # B1 (2026-11-06, INTENTIONAL — this pin changes): the
        # detector is no longer narrow. An image-not-found
        # failure is the art-missing reason, so it pauses the
        # task resumably exactly like a gone monitor — the
        # human puts the file in place and resumes.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"display_control": [
                    _ok("Wallpaper image not found: /tmp/x.png")]})
            coord, _ledger = self._coord(tmp, executor)
            task = coord.new_task("t", "routine", _wallpaper_steps())
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "paused")
            pending = getattr(task, "_pending_monitor", None)
            self.assertIsNotNone(pending)
            self.assertIn("art-missing", pending["sentence"])

    def test_revert_on_gone_monitor_does_not_pause(self):
        # Revert already reports a gone monitor honestly in
        # its own message ("nothing was reverted") — it is
        # not a set, and pausing on it would be wrong.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"wallpaper_revert": [_ok(
                    "That screen is no longer connected, so nothing "
                    "was reverted for it. The other screens were not "
                    "touched — name one of them to revert it.")]})
            coord, _ledger = self._coord(tmp, executor)
            task = coord.new_task(
                "t", "routine",
                _steps({"tool": "wallpaper_revert", "args": {},
                        "label": "Revert wallpaper"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertIsNone(getattr(task, "_pending_monitor", None))

    def test_detector_unit_shapes(self):
        gone = _detect_wallpaper_monitor_gone
        self.assertIsNotNone(gone(
            "display_control", {"status": "success", "result": _GONE}))
        self.assertIsNotNone(gone(
            "set_wallpaper", {"status": "refused", "message": _GONE}))
        self.assertIsNotNone(gone(
            "wallpaper_apply", {"status": "success", "result": _GONE}))
        self.assertIsNone(gone(
            "display_control", {"status": "success", "result": _SET_OK}))
        self.assertIsNone(gone(
            "wallpaper_revert", {"status": "success", "result": _GONE}))
        # B1 (INTENTIONAL — this pin changes): an image-not-found
        # failure is the art-missing reason, so it pauses now.
        self.assertIsNotNone(gone(
            "display_control",
            {"status": "success",
             "result": "Wallpaper image not found: /tmp/x.png"}))
        self.assertIsNone(gone("other_tool",
                               {"status": "success", "result": _GONE}))


# ---------------------------------------------------------------------------
# 4b. B1: EVERY wallpaper set failure pauses, on both executor paths
# ---------------------------------------------------------------------------
# The whole reason set, on both shapes (success-carried sentence and
# funnel-shaped failure dict), for each of the three set tools.
# A genuinely successful set still completes; a resume after the
# human fixes the cause re-runs the step and completes.

_B1_CASES = (
    # (sentence, reason named in the pause sentence)
    ("Wallpaper not set: unknown-monitor: no monitor named second "
     "monitor (monitors now: primary DISPLAY1).", "no longer connected"),
    ("Wallpaper not set: art-missing.", "art-missing"),
    ("Wallpaper not set: could-not-save-previous.",
     "could-not-save-previous"),
    ("Wallpaper not set: could-not-read-current.",
     "could-not-read-current"),
    ("Wallpaper not set: system-call-refused.", "system-call-refused"),
    ("Wallpaper not set: system-call-failed: RuntimeError.",
     "system-call-failed"),
    ("Wallpaper not set: read-back-mismatch.", "read-back-mismatch"),
    ("Wallpaper not set: windows-only.", "windows-only"),
    ("Could not save the current wallpaper before changing it "
     "(could-not-save-previous), so the desktop was left unchanged.",
     "could-not-save-previous"),
    ("Windows refused the wallpaper change "
     "(SystemParametersInfo returned 0).", "system-call-refused"),
    ("Could not set the wallpaper: boom", "system-call-failed"),
    ("Windows accepted the wallpaper change for '/tmp/x.png', but the "
     "saved desktop setting now reads 'C:\\\\old.png' instead — check "
     "the desktop to see what actually applied.", "read-back-mismatch"),
    ("Wallpaper not applied: system-call-refused.",
     "system-call-refused"),
    ("Wallpaper not applied for state day on DISPLAY1: Windows still "
     "shows C:\\\\old.png, not /tmp/x.png.", "read-back-mismatch"),
    ("Wallpaper image not found: /tmp/x.png", "art-missing"),
    ("That wallpaper file doesn't exist: /tmp/x.png", "art-missing"),
)


def _b1_steps(tool):
    if tool == "display_control":
        return _steps(
            {"tool": "display_control",
             "args": {"action": "set_wallpaper", "path": "/tmp/x.png",
                      "monitor": "second monitor"},
             "label": "Set wallpaper on second monitor"})
    if tool == "set_wallpaper":
        return _steps(
            {"tool": "set_wallpaper",
             "args": {"path": "/tmp/x.png", "style": "fill",
                      "monitor": "second monitor"},
             "label": "Set wallpaper on second monitor"})
    return _steps(
        {"tool": "wallpaper_apply",
         "args": {"opt_in": True, "hour": 10},
         "label": "Apply wallpaper for the hour"})


class WallpaperFailureFamilyTests(unittest.TestCase):
    def tearDown(self):
        pa.clear_all()
        super().tearDown()

    def _coord(self, tmp, executor):
        ledger = tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")
        return tc.TurnCoordinator(executor=executor, ledger=ledger), ledger

    def test_every_reason_pauses_on_both_paths_and_both_shapes(self):
        for tool in ("display_control", "set_wallpaper",
                     "wallpaper_apply"):
            for sentence, reason in _B1_CASES:
                for shape in ("success-string", "failure-dict"):
                    with self.subTest(tool=tool, reason=reason,
                                      shape=shape):
                        if shape == "success-string":
                            out = _ok(sentence)
                        else:
                            out = {"status": "failure",
                                   "kind": "wallpaper_not_set",
                                   "message": sentence}
                        with tempfile.TemporaryDirectory() as tmp:
                            executor = _ScriptExecutor({tool: [out]})
                            coord, _ledger = self._coord(tmp, executor)
                            task = coord.new_task(
                                "t", "routine", _b1_steps(tool))
                            receipt = coord.run_task(task)
                            self.assertEqual(receipt.state, "paused")
                            pending = getattr(task, "_pending_monitor",
                                              None)
                            self.assertIsNotNone(pending)
                            self.assertEqual(pending["tool"], tool)
                            self.assertIn(reason, pending["sentence"])
                            self.assertIn(reason, receipt.reason)

    def test_genuine_success_still_completes_on_all_three_tools(self):
        for tool in ("display_control", "set_wallpaper",
                     "wallpaper_apply"):
            with self.subTest(tool=tool):
                with tempfile.TemporaryDirectory() as tmp:
                    executor = _ScriptExecutor({tool: [_ok(_SET_OK)]})
                    coord, _ledger = self._coord(tmp, executor)
                    task = coord.new_task("t", "routine",
                                          _b1_steps(tool))
                    receipt = coord.run_task(task)
                    self.assertEqual(receipt.state, "completed")
                    self.assertIsNone(
                        getattr(task, "_pending_monitor", None))

    def test_resume_after_fix_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"display_control": [
                    {"status": "failure", "kind": "wallpaper_not_set",
                     "message": "Wallpaper not set: art-missing."},
                    _ok(_SET_OK)]})
            coord, _ledger = self._coord(tmp, executor)
            task = coord.new_task("t", "routine", _wallpaper_steps())
            self.assertEqual(coord.run_task(task).state, "paused")
            resumed = coord.resume_task(task.id)
            self.assertEqual(resumed.state, "completed")
            self.assertEqual(len(executor.calls), 2)
            self.assertIsNone(getattr(task, "_pending_monitor", None))

    def test_failure_script_appendix_does_not_leak_into_pause(self):
        # The funnel appends " I tried '<tool>' with ..." to
        # failure messages; the pause sentence must name the
        # reason, not the appendix.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"display_control": [
                    {"status": "failure", "kind": "wallpaper_not_set",
                     "message": "Wallpaper not set: art-missing. "
                     "I tried 'display_control' with path=/tmp/x.png. "
                     "I don't have another way to try that."}]})
            coord, _ledger = self._coord(tmp, executor)
            task = coord.new_task("t", "routine", _wallpaper_steps())
            self.assertEqual(coord.run_task(task).state, "paused")
            sentence = getattr(task, "_pending_monitor")["sentence"]
            self.assertIn("art-missing", sentence)
            self.assertNotIn("I tried", sentence)


# ---------------------------------------------------------------------------
# 5. the revert slot survives a monitor pause + resume untouched
# ---------------------------------------------------------------------------

class RevertSlotSurvivalTests(unittest.TestCase):
    def test_slot_bytes_unchanged_across_pause_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            slot = pathlib.Path(tmp) / "wallpaper_previous.json"
            record = {
                "version": 2,
                "monitors": {
                    "DISPLAY1": {
                        "image": "C:\\old\\wall.png",
                        "style": "10", "tile": "0",
                        "kind": "image", "color": "",
                        "slideshow": None,
                    },
                },
            }
            slot.write_text(json.dumps(record), encoding="utf-8")
            before = slot.read_bytes()
            old_override = wp._PATH_OVERRIDE
            wp._PATH_OVERRIDE = slot
            try:
                executor = _ScriptExecutor(
                    {"display_control": [_ok(_SET_OK), _ok(_GONE),
                                         _ok(_SET_OK)]})
                ledger = tl.TaskLedger(
                    path=pathlib.Path(tmp) / "ledger.json")
                coord = tc.TurnCoordinator(executor=executor,
                                           ledger=ledger)
                steps = _steps(
                    {"tool": "display_control",
                     "args": {"action": "set_wallpaper",
                              "path": "/tmp/a.png",
                              "monitor": "primary"},
                     "label": "Set wallpaper on primary"},
                    {"tool": "display_control",
                     "args": {"action": "set_wallpaper",
                              "path": "/tmp/b.png",
                              "monitor": "second monitor"},
                     "label": "Set wallpaper on second monitor"})
                task = coord.new_task("t", "routine", steps)
                self.assertEqual(coord.run_task(task).state, "paused")
                # The pause touched nothing in the slot.
                self.assertEqual(slot.read_bytes(), before)
                resumed = coord.resume_task(task.id)
                self.assertEqual(resumed.state, "completed")
                self.assertEqual(slot.read_bytes(), before)
                # And the slot is still usable afterwards:
                # the saved previous for DISPLAY1 reads back.
                saved = wp.load_previous_wallpaper_for("DISPLAY1")
                self.assertIsNotNone(saved)
                self.assertEqual(saved["image"], "C:\\old\\wall.png")
            finally:
                wp._PATH_OVERRIDE = old_override


if __name__ == "__main__":
    unittest.main()
