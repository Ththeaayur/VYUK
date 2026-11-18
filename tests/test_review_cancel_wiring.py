"""§7 consumers follow-up: coordinator task-cancel in the batch-7
Review flow (disclosure 6: dialog dismissal = deny, no separate
task-cancel button — coordinator.cancel_task existed but was
unwired there). This suite pins the wiring, not a new feature.

Cancel is not deny, and the suite proves it at the record
level: a cancelled task's ledger record carries state
"cancelled" with its pending record cleared and NO refused
outcome appended, while a denied step appends a refused
outcome (kind approval_denied) and fails (or skips) the
step. The Activity rendering then shows the cancelled state
(Partial with at least one successful step, Failed
otherwise, per gui/task_state's standing contract) and never
"Needs approval" again, and the task drops out of future
Review queues. Voice never cancels: the pins mirror batch
7's voice-never-resolves pins, extended to cancel.

Pure tests run on both interpreters. Qt tests need PySide6
(the .venv-qt interpreter); on an interpreter without it they
skip, following the programme's standing optional-dependency
skips. The real S-1 dialog builder is exercised through the
same stub technique as batch 7 (heavy core modules stubbed
in sys.modules before importing gui.window_services); the
builder itself is never copied or reimplemented here.
"""

import ast
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# voice_assistant.* alias (batch-7 precedent): gui modules
# import voice_assistant.core.*, which is this tree's core/.
# Submodule aliases too: without them a lazy
# ``from voice_assistant.core.brain.task_coordinator import ...``
# would load a SECOND copy of the module instead of the one
# the tests patch.
import core as _core_pkg
import core.brain.task_coordinator as _tc_mod  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
import core.confirm_gate as _cg_mod
import gui as _gui_pkg

if "voice_assistant" not in sys.modules:
    _va = types.ModuleType("voice_assistant")
    _va.__path__ = [str(ROOT)]
    sys.modules["voice_assistant"] = _va
sys.modules.setdefault("voice_assistant.core", _core_pkg)
sys.modules.setdefault("voice_assistant.gui", _gui_pkg)
sys.modules.setdefault("voice_assistant.core.brain.task_coordinator", _tc_mod)  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
sys.modules.setdefault("voice_assistant.core.confirm_gate", _cg_mod)

from core.brain import task_coordinator as tc  # noqa: E402  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.brain import task_ledger as tl  # noqa: E402  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from gui import pending_approval as pa  # noqa: E402
from gui import task_state  # noqa: E402

PENDING = {
    "step_index": 0,
    "tool": "menu_action",
    "label": "Close tab",
    "sentence": "Menu path refused (C-14 approve-first): "
                "'close tab' needs approval",
    "confirm_arg": "confirmed",
}

# Stop sentence read off the engine rendering (batch-6
# precedent: menu C-14 reason in menu_paths.py invoke_path).
# A fake executor returning it reproduces a genuine engine
# confirm stop through the real _detect_s7_confirmation_stop.
MENU_STOP = ("Menu path File → Delete refused: 'Delete' is an "
             "effectful menu action — confirm it first "
             "(C-14 approve-first) before it is invoked.")


def _step(tool, args, **kw):
    mapping = {"tool": tool, "args": args}
    mapping.update(kw)
    return tc.StepDef.from_mapping(mapping)


def _fake_executor(handler):
    """Executor recording calls, delegating to handler(tool, args)."""
    def execute(tool, args):
        execute.calls.append((tool, dict(args)))
        return handler(tool, args)
    execute.calls = []
    return execute


def _paused_coord(tmpdir, steps, handler):
    """A coordinator + task paused by a genuine confirm stop.

    Returns (coord, task, ledger). The pause writes the
    ledger, exactly as the run loop does on a confirm stop.
    """
    ledger = tl.TaskLedger(path=str(pathlib.Path(tmpdir) / "tasks.json"))
    executor = _fake_executor(handler)
    coord = tc.TurnCoordinator(executor=executor, ledger=ledger)
    task = coord.new_task("s7 goal", "routine", steps)
    receipt = coord.run_task(task)
    assert receipt.state == "paused", receipt.state
    assert coord.pending_approval(task.id) is not None
    return coord, task, ledger


def _record(state="paused", pending=PENDING, task_id="t1",
            outcomes=()):
    record = {
        "task_id": task_id,
        "title": "T",
        "state": state,
        "steps": [{"tool": "menu_action", "label": "Close tab",
                   "args": {"action": "list"}}],
        "outcomes": list(outcomes),
    }
    if pending is not None:
        record["pending_approval"] = dict(pending)
    return record


class TestCancelVsDenyLedger(unittest.TestCase):
    def test_cancel_writes_cancelled_no_refused_outcome(self):
        steps = [_step("menu_action",
                       {"action": "invoke", "path": "File → Delete"})]
        with tempfile.TemporaryDirectory() as tmpdir:
            coord, task, ledger = _paused_coord(
                tmpdir, steps,
                lambda t, a: {"status": "success",
                              "result": MENU_STOP})
            self.assertEqual(
                ledger.get(task.id)["state"], "paused")
            self.assertTrue(coord.cancel_task(task.id))
            record = ledger.get(task.id)
            self.assertEqual(record["state"], "cancelled")
            self.assertIsNone(record["pending_approval"])
            self.assertEqual(record["outcomes"], [])
            self.assertIsNone(coord.pending_approval(task.id))
            self.assertIsNone(
                coord.resolve_pending(task.id, approved=True))
            # Terminal: a cancelled record is history, not
            # resumable, and drops out of Review queues.
            self.assertNotIn(
                task.id,
                [r["task_id"] for r in ledger.list_interrupted()])

    def test_deny_appends_refused_outcome(self):
        steps = [_step("menu_action",
                       {"action": "invoke", "path": "File → Delete"})]
        with tempfile.TemporaryDirectory() as tmpdir:
            coord, task, ledger = _paused_coord(
                tmpdir, steps,
                lambda t, a: {"status": "success",
                              "result": MENU_STOP})
            receipt = coord.resolve_pending(task.id, approved=False)
            self.assertEqual(receipt.state, "failed")
            record = ledger.get(task.id)
            self.assertEqual(record["state"], "failed")
            self.assertEqual(len(record["outcomes"]), 1)
            self.assertEqual(record["outcomes"][0]["status"], "refused")
            self.assertEqual(record["outcomes"][0]["kind"],
                             "approval_denied")
            self.assertIsNone(record["pending_approval"])

    def test_cancel_and_deny_records_differ(self):
        # The distinction the Review flow promises the user:
        # cancel ends the task with no step refused; deny
        # refuses the step. Same setup, opposite outcomes.
        steps = [_step("menu_action",
                       {"action": "invoke", "path": "File → Delete"})]
        with tempfile.TemporaryDirectory() as tmpdir:
            coord_c, task_c, ledger_c = _paused_coord(
                tmpdir, steps,
                lambda t, a: {"status": "success",
                              "result": MENU_STOP})
            coord_c.cancel_task(task_c.id)
            rec_c = ledger_c.get(task_c.id)
        with tempfile.TemporaryDirectory() as tmpdir:
            coord_d, task_d, ledger_d = _paused_coord(
                tmpdir, steps,
                lambda t, a: {"status": "success",
                              "result": MENU_STOP})
            coord_d.resolve_pending(task_d.id, approved=False)
            rec_d = ledger_d.get(task_d.id)
        self.assertEqual(rec_c["state"], "cancelled")
        self.assertEqual(rec_c["outcomes"], [])
        self.assertEqual(rec_d["state"], "failed")
        self.assertEqual(rec_d["outcomes"][0]["status"], "refused")

    def test_cancelled_record_never_needs_approval(self):
        # Standing task_state contract: a cancelled task
        # renders Partial (>=1 success) / Failed (0 success),
        # never "Needs approval" — even if a stale pending
        # dict were still attached, state decides first.
        rendered = task_state.render_task(
            _record(state="cancelled", pending=PENDING))
        self.assertEqual(rendered["state"], "Failed")
        rendered = task_state.render_task(
            _record(state="cancelled", pending=PENDING,
                    outcomes=[{"tool": "menu_action",
                               "label": "Close tab",
                               "status": "success"}]))
        self.assertEqual(rendered["state"], "Partial")
        rendered = task_state.render_task(_record())
        self.assertEqual(rendered["state"], "Needs approval")


class _FakeCoordinator:
    def __init__(self, tasks):
        # tasks: {task_id: pending-or-None}
        self._tasks = dict(tasks)
        self.cancelled = []

    def get_task(self, task_id):
        if task_id == "raise":
            raise RuntimeError("boom")
        return object() if task_id in self._tasks else None

    def pending_approval(self, task_id):
        return self._tasks.get(task_id)

    def cancel_task(self, task_id):
        self.cancelled.append(task_id)
        return True


class _FakeLedger:
    def __init__(self, records):
        self._records = records

    def get(self, task_id):
        return self._records.get(task_id)


class _FakeRebuiltCoordinator:
    instances = []

    def __init__(self, *, ledger=None):
        self.ledger = ledger
        self.resumed = []
        self.cancelled = []
        self._pending = None
        _FakeRebuiltCoordinator.instances.append(self)

    def resume_ledger_task(self, record, *, ledger=None, on_step=None):
        self.resumed.append(record["task_id"])
        # A rebuilt pending task re-stops: pending survives.
        self._pending = record.get("pending_approval")
        return {"task_id": record["task_id"], "state": "paused"}

    def pending_approval(self, task_id):
        return self._pending

    def cancel_task(self, task_id):
        self.cancelled.append(task_id)
        return True


class TestCancelPendingAnswer(unittest.TestCase):
    def test_live_first(self):
        live = _FakeCoordinator({"t1": dict(PENDING)})
        out = pa.cancel_pending_answer(
            "t1", live_coordinator=live, ledger=_FakeLedger({}))
        self.assertTrue(out)
        self.assertEqual(live.cancelled, ["t1"])

    def test_live_stale_cancels_nothing(self):
        # The task moved on (no pending record): a stale
        # offer must never kill a task the user already
        # approved elsewhere.
        live = _FakeCoordinator({"t1": None})
        out = pa.cancel_pending_answer(
            "t1", live_coordinator=live,
            ledger=_FakeLedger({"t1": _record()}))
        self.assertIsNone(out)
        self.assertEqual(live.cancelled, [])

    def test_live_lookup_failure_never_rebuilds(self):
        live = _FakeCoordinator({})
        out = pa.cancel_pending_answer(
            "raise", live_coordinator=live,
            ledger=_FakeLedger({"raise": _record(task_id="raise")}))
        self.assertIsNone(out)
        self.assertEqual(live.cancelled, [])

    def test_rebuild_path(self):
        _FakeRebuiltCoordinator.instances.clear()
        with mock.patch.object(tc, "TurnCoordinator",
                               _FakeRebuiltCoordinator):
            out = pa.cancel_pending_answer(
                "t9", live_coordinator=None,
                ledger=_FakeLedger({"t9": _record(task_id="t9")}))
        self.assertTrue(out)
        rebuilt = _FakeRebuiltCoordinator.instances[-1]
        self.assertEqual(rebuilt.resumed, ["t9"])
        self.assertEqual(rebuilt.cancelled, ["t9"])

    def test_rebuild_settled_cancels_nothing(self):
        # Resume settled the task (no pending survives): the
        # task moved on, so the cancel must not land on it.
        class _Settled(_FakeRebuiltCoordinator):
            def resume_ledger_task(self, record, *, ledger=None,
                                   on_step=None):
                self.resumed.append(record["task_id"])
                self._pending = None
                return {"task_id": record["task_id"],
                        "state": "failed"}
        _FakeRebuiltCoordinator.instances.clear()
        with mock.patch.object(tc, "TurnCoordinator", _Settled):
            out = pa.cancel_pending_answer(
                "t9", live_coordinator=None,
                ledger=_FakeLedger({"t9": _record(task_id="t9")}))
        self.assertIsNone(out)
        self.assertEqual(
            _FakeRebuiltCoordinator.instances[-1].cancelled, [])

    def test_rebuild_without_record_is_none(self):
        with mock.patch.object(tc, "TurnCoordinator",
                               _FakeRebuiltCoordinator):
            out = pa.cancel_pending_answer(
                "nope", live_coordinator=None, ledger=_FakeLedger({}))
        self.assertIsNone(out)

    def test_rebuild_non_paused_is_none(self):
        with mock.patch.object(tc, "TurnCoordinator",
                               _FakeRebuiltCoordinator):
            out = pa.cancel_pending_answer(
                "t9", live_coordinator=None,
                ledger=_FakeLedger(
                    {"t9": _record(state="running",
                                   task_id="t9")}))
        self.assertIsNone(out)

    def test_no_ledger_is_none(self):
        live = _FakeCoordinator({})
        out = pa.cancel_pending_answer(
            "ghost", live_coordinator=live, ledger=None)
        self.assertIsNone(out)


class TestVoiceCannotCancel(unittest.TestCase):
    def test_gui_cancel_callers_are_presenter_only(self):
        """The only gui/ production caller of coordinator
        cancel is this presenter; no voice path in gui/ can
        reach cancel_task for a pending record. Mirrors
        batch 7's resolve pin, extended to cancel."""
        gui_dir = ROOT / "gui"
        offenders = []
        for path in sorted(gui_dir.rglob("*.py")):
            if "test" in path.name:
                continue
            text = path.read_text()
            for line in text.splitlines():
                if "cancel_task(" not in line:
                    continue
                if path.name == "pending_approval.py":
                    continue
                offenders.append(f"{path.name}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_presenter_cancel_calls_are_answer_path_only(self):
        """Inside the presenter, cancel_task is called only
        from cancel_pending_answer (live + rebuild) — never
        from the dialog builders or the queue timer."""
        text = (ROOT / "gui" / "pending_approval.py").read_text()
        start = text.index("def cancel_pending_answer")
        end = text.index("\ndef ", start + 1)
        body = text[start:end]
        self.assertEqual(body.count("cancel_task("), 2)

    def test_voice_hook_never_routes_to_coordinator_cancel(self):
        # GUI split #10 batch 1 INTENTIONAL: _maybe_confirm_reply moved
        # verbatim to window_services_confirm.py. Assertion unchanged.
        text = (ROOT / "gui" / "window_services_confirm.py").read_text()
        start = text.index("def _maybe_confirm_reply")
        end = text.index("\n    def ", start + 1)
        method = text[start:end]
        self.assertNotIn("task_coordinator", method)
        self.assertNotIn("cancel_task", method)
        for line in method.splitlines():
            if "resolve_pending(" in line:
                self.assertIn("confirm_gate.resolve_pending", line)

    def test_core_cancel_callers_are_coordinator_and_voice_goal(self):
        """Across core/, cancel_task is defined once (the
        coordinator) and called only by the presenter-adjacent
        voice-goal interruption path — which cancels the voice
        goal's OWN task on interruption, never answers or
        dismisses a Review offer and never resolves an
        approval. Any new core/ caller is a pin failure, not
        a silent extension."""
        offenders = []
        for path in sorted((ROOT / "core").rglob("*.py")):
            if "test" in path.name:
                continue
            text = path.read_text()
            for line in text.splitlines():
                if "cancel_task(" not in line:
                    continue
                stripped = line.strip()
                if path.name == "task_coordinator.py" and \
                        stripped.startswith("def cancel_task"):
                    continue
                # INTENTIONAL (skills split B43): cancel_task moved to the
                # lifecycle mixin module; the def there is the same single
                # definition, not a new caller — same assertion, superset.
                if path.name == "task_coordinator_lifecycle.py" and \
                        stripped.startswith("def cancel_task"):
                    continue
                if path.name == "voice_goal.py":
                    continue
                offenders.append(f"{path.name}: {stripped}")
        self.assertEqual(offenders, [])


# ---- Qt tests: skip without PySide6 ---------------------------------------
try:
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget

    _QT = True
except Exception:  # noqa: BLE001 — optional dependency absence
    _QT = False


def _stub_window_services():
    """Import gui.window_services with heavy core modules stubbed
    (batch-7 probe-verified): only the dialog builders are
    exercised. The stubs are removed again right after the
    import: the builder already bound what it needs, and
    leaving stub modules in sys.modules would pollute later
    suites in the same process (a later import of
    voice_assistant.core.* would resolve to a stub instead of
    the real module).
    """
    import core as core_pkg  # noqa: F401 — keeps the alias visible

    _added = []
    # Phase 3 B2h: brain hub moved to core/brain/brain.py INTENTIONAL: stub key follows the hub move;
    # Phase 3 V1a-1 INTENTIONAL: audio moved to core/voice/audio.py, so audio maps to the voice path;
    # Phase 3 V1a-2 INTENTIONAL: wake_word moved to core/voice/wake_word.py; all other leaves stay flat.
    for name in ("audio", "clipboard_monitor", "config",
                 "brain", "scheduler", "wake_word"):
        full = ("voice_assistant.core.brain.brain" if name == "brain"
                       else "voice_assistant.core.voice.audio" if name == "audio"
                       else "voice_assistant.core.voice.wake_word" if name == "wake_word"
                       else "voice_assistant.core." + name)
        if full not in sys.modules:
            sys.modules[full] = types.ModuleType(full)
            _added.append(full)
    sys.modules["voice_assistant.core.voice.audio"].audio_manager = object()  # Phase 3 V1a-1 INTENTIONAL: stub follows audio move
    # Review-cancel fix to the batch-7 helper (this suite only;
    # the batch-7 file itself is untouched): the helper indexed
    # "voice_assistant.core.desktop.clipboard_monitor" and
    # "...desktop.hotkeys" without creating them, and nothing
    # else creates those keys — so in a clean single-suite run
    # it raised KeyError and the whole Qt class skipped (the
    # batch-7 suite skips identically here today; verified).
    # A stub helper must create the stubs it sets attrs on.
    for _full in ("voice_assistant.core.desktop.clipboard_monitor",
                  "voice_assistant.core.desktop.hotkeys"):
        if _full not in sys.modules:
            sys.modules[_full] = types.ModuleType(_full)
            _added.append(_full)
    sys.modules[
        "voice_assistant.core.desktop.clipboard_monitor"].clipboard_monitor = (
        object())  # Phase 3 D3 INTENTIONAL: stub follows clipboard_monitor move
    sys.modules["voice_assistant.core.config"].config = object()
    sys.modules["voice_assistant.core.desktop.hotkeys"].hotkey_daemon = object()  # Phase 3 D3 INTENTIONAL: stub follows hotkeys move
    sys.modules["voice_assistant.core.brain.brain"].live_session = object()  # Phase 3 B2h: brain hub moved to core/brain/brain.py INTENTIONAL: stub follows hub move
    sys.modules["voice_assistant.core.scheduler"].scheduler = object()
    sys.modules[
        "voice_assistant.core.voice.wake_word"].wake_word_detector = object()
    try:
        from gui import window_services
        return window_services
    finally:
        for full in _added:
            sys.modules.pop(full, None)
    return window_services


@unittest.skipUnless(_QT, "PySide6 unavailable")
class TestReviewDialogCancel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        try:
            cls.ws = _stub_window_services()
        except Exception as exc:  # noqa: BLE001 — heavy import
            raise unittest.SkipTest(
                f"window_services unimportable: {exc}")

    def _stub_window(self, fail_designed=False):
        ws = self.ws

        class Stub(QWidget):
            def __init__(self):
                super().__init__()
                self.captured = None

            def _build_confirm_dialog(self, tool, summary):
                if fail_designed:
                    raise RuntimeError("designed builder broken")
                dlg = ws.ServicesMixin._build_confirm_dialog(
                    self, tool, summary)
                self.captured = dlg
                return dlg

            def _build_plain_confirm_dialog(self, tool, summary):
                dlg = ws.ServicesMixin._build_plain_confirm_dialog(
                    self, tool, summary)
                self.captured = dlg
                return dlg

        return Stub()

    def _click(self, window, text):
        dlg = window.captured
        for btn in dlg.findChildren(QPushButton):
            if btn.text() == text:
                btn.click()
                return True
        return False

    def _button_texts(self, window):
        dlg = window.captured
        return [btn.text() for btn in dlg.findChildren(QPushButton)]

    def test_cancel_button_present_and_labelled(self):
        window = self._stub_window()
        seen = {}

        def _inspect():
            seen["texts"] = self._button_texts(window)
            self._click(window, "Cancel task")

        QTimer.singleShot(0, _inspect)
        self.assertEqual(
            pa.present_pending_dialog_with_cancel(window, PENDING),
            "cancel")
        self.assertIn("Cancel task", seen["texts"])
        self.assertIn("Allow once", seen["texts"])
        self.assertIn("Don't allow", seen["texts"])

    def test_cancel_returns_cancel_not_deny(self):
        window = self._stub_window()
        QTimer.singleShot(
            0, lambda: self._click(window, "Cancel task"))
        self.assertEqual(
            pa.present_pending_dialog_with_cancel(window, PENDING),
            "cancel")

    def test_allow_returns_allow(self):
        window = self._stub_window()
        QTimer.singleShot(
            0, lambda: self._click(window, "Allow once"))
        self.assertEqual(
            pa.present_pending_dialog_with_cancel(window, PENDING),
            "allow")

    def test_dont_allow_returns_deny(self):
        window = self._stub_window()
        QTimer.singleShot(
            0, lambda: self._click(window, "Don't allow"))
        self.assertEqual(
            pa.present_pending_dialog_with_cancel(window, PENDING),
            "deny")

    def test_silence_timeout_denies_not_cancels(self):
        window = self._stub_window()
        # No click scheduled: the presenter's own timer rejects.
        self.assertEqual(
            pa.present_pending_dialog_with_cancel(
                window, PENDING, timeout_s=0.5),
            "deny")

    def test_plain_fallback_carries_cancel(self):
        window = self._stub_window(fail_designed=True)
        QTimer.singleShot(
            0, lambda: self._click(window, "Cancel task"))
        self.assertEqual(
            pa.present_pending_dialog_with_cancel(window, PENDING),
            "cancel")

    def test_journal_dialog_has_no_cancel(self):
        # The journal reconcile flow reuses the bool presenter
        # with a synthetic record and no task to cancel: it
        # must never offer the button.
        window = self._stub_window()
        seen = {}

        def _inspect():
            seen["texts"] = self._button_texts(window)
            self._click(window, "Don't allow")

        QTimer.singleShot(0, _inspect)
        self.assertFalse(pa.present_pending_dialog(window, PENDING))
        self.assertNotIn("Cancel task", seen["texts"])


@unittest.skipUnless(_QT, "PySide6 unavailable")
class TestReviewQueueCancel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        try:
            cls.ws = _stub_window_services()
        except Exception as exc:  # noqa: BLE001 — heavy import
            raise unittest.SkipTest(
                f"window_services unimportable: {exc}")

    def _stub_window(self):
        ws = self.ws

        class Stub(QWidget):
            def __init__(self):
                super().__init__()
                self.captured = None
                self.refreshed = 0

            def _build_confirm_dialog(self, tool, summary):
                dlg = ws.ServicesMixin._build_confirm_dialog(
                    self, tool, summary)
                self.captured = dlg
                return dlg

            def _build_plain_confirm_dialog(self, tool, summary):
                dlg = ws.ServicesMixin._build_plain_confirm_dialog(
                    self, tool, summary)
                self.captured = dlg
                return dlg

            def _refresh(self):
                self.refreshed += 1

        return Stub()

    def _click(self, window, text):
        dlg = window.captured
        for btn in dlg.findChildren(QPushButton):
            if btn.text() == text:
                btn.click()
                return True
        return False

    def _wait(self, predicate, timeout_s=10.0):
        waited = 0.0
        while not predicate() and waited < timeout_s:
            QTest.qWait(50)
            waited += 0.05
        return predicate()

    def test_queue_cancel_ends_task_and_refreshes(self):
        window = self._stub_window()
        steps = [_step("menu_action",
                       {"action": "invoke", "path": "File → Delete"})]
        with tempfile.TemporaryDirectory() as tmpdir:
            coord, task, ledger = _paused_coord(
                tmpdir, steps,
                lambda t, a: {"status": "success",
                              "result": MENU_STOP})
            pending = coord.pending_approval(task.id)
            QTimer.singleShot(
                0, lambda: self._click(window, "Cancel task"))
            pa.review_pending_queue(
                window, [(task.id, dict(pending))],
                live_coordinator=coord, ledger=ledger,
                refresh=window._refresh, poll_ms=100)
            done = self._wait(lambda: window.refreshed >= 1)
            self.assertTrue(done)
            record = ledger.get(task.id)
            self.assertEqual(record["state"], "cancelled")
            self.assertIsNone(record["pending_approval"])
            self.assertEqual(record["outcomes"], [])
            self.assertNotIn(
                task.id,
                [r["task_id"] for r in ledger.list_interrupted()])


if __name__ == "__main__":
    unittest.main()
