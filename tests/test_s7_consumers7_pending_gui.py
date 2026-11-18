"""§7 consumers batch 7: GUI pending-approval presentation.

The coordinator protocol (batch 6) pauses a task with a pending
record; this suite pins the GUI layer that makes it visible:
the pending → dialog mapping, the Activity row state, the
dialog outcomes (Allow / Don't allow / silence timeout /
fail-closed fallback), the live-first + rebuild resolve paths,
the queue sequencing, and the hard rule that voice never
resolves a coordinator pending approval.

Pure tests run on both interpreters. Qt tests need PySide6
(the .venv-qt interpreter); on an interpreter without it they
skip, following the programme's standing optional-dependency
skips. The real S-1 dialog builder is exercised through the
same stub technique as test_chatfix (heavy core modules
stubbed in sys.modules before importing gui.window_services);
the builder itself is never copied or reimplemented here.
"""

import ast
import pathlib
import sys
import threading
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# voice_assistant.* alias (test_chatfix precedent): gui modules
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


def _record(state="paused", pending=PENDING, task_id="t1"):
    record = {
        "task_id": task_id,
        "title": "T",
        "state": state,
        "steps": [{"tool": "menu_action", "label": "Close tab",
                   "args": {"action": "list"}}],
        "outcomes": [],
    }
    if pending is not None:
        record["pending_approval"] = dict(pending)
    return record


class TestMapping(unittest.TestCase):
    def test_summary_carries_tool_label_sentence_flag(self):
        tool, summary = pa.pending_approval_summary(PENDING)
        self.assertEqual(tool, "menu_action")
        self.assertIn("Close tab", summary)
        self.assertIn(PENDING["sentence"], summary)
        self.assertIn("confirmed", summary)

    def test_summary_degrades_without_inventing(self):
        tool, summary = pa.pending_approval_summary({"tool": "x"})
        self.assertEqual(tool, "x")
        self.assertIn("x", summary)  # label falls back to tool
        self.assertNotIn("Confirm flag", summary)
        tool, summary = pa.pending_approval_summary(None)
        self.assertEqual(tool, "")
        self.assertEqual(summary, "Step: ")

    def test_filter_keeps_only_offerable(self):
        summaries = [
            {"task_id": "a", "pending_approval": dict(PENDING)},
            {"task_id": "b"},  # no pending
            {"task_id": "c", "pending_approval": {"tool": ""}},
            {"task_id": "d", "pending_approval": "not-a-dict"},
            {"pending_approval": dict(PENDING)},  # no task id
            "not-a-dict",
        ]
        out = pa.interrupted_pending_approvals(summaries)
        self.assertEqual([t for t, _ in out], ["a"])
        self.assertEqual(out[0][1], PENDING)
        # The returned copy is detached from the summary.
        out[0][1]["tool"] = "mutated"
        self.assertEqual(summaries[0]["pending_approval"]["tool"],
                         "menu_action")


class TestRenderTask(unittest.TestCase):
    def test_paused_with_pending_is_needs_approval(self):
        rendered = task_state.render_task(_record())
        self.assertEqual(rendered["state"], "Needs approval")
        self.assertEqual(rendered["action"],
                         "Waiting on approval: Close tab")
        self.assertIn(PENDING["sentence"], rendered["rationale"])
        self.assertIn("Pending approval: menu_action",
                      rendered["details_text"])
        self.assertTrue(rendered["paused"])

    def test_paused_without_pending_is_running(self):
        rendered = task_state.render_task(_record(pending=None))
        self.assertEqual(rendered["state"], "Running")
        self.assertTrue(rendered["paused"])

    def test_running_gate_hint_unchanged(self):
        record = _record(state="running", pending=None)
        rendered = task_state.render_task(
            record, pending_tool="menu_action",
            pending_summary="gate summary")
        self.assertEqual(rendered["state"], "Needs approval")
        self.assertIn("gate summary", rendered["rationale"])

    def test_running_without_hint_is_running(self):
        rendered = task_state.render_task(
            _record(state="running", pending=None))
        self.assertEqual(rendered["state"], "Running")

    def test_terminal_states_unaffected(self):
        self.assertEqual(
            task_state.render_task(
                _record(state="completed", pending=PENDING))["state"],
            "Done")
        self.assertEqual(
            task_state.render_task(
                _record(state="failed", pending=PENDING))["state"],
            "Failed")


class _FakeCoordinator:
    def __init__(self, tasks):
        # tasks: {task_id: pending-or-None}
        self._tasks = dict(tasks)
        self.resolved = []

    def get_task(self, task_id):
        if task_id == "raise":
            raise RuntimeError("boom")
        return object() if task_id in self._tasks else None

    def pending_approval(self, task_id):
        return self._tasks.get(task_id)

    def resolve_pending(self, task_id, *, approved):
        self.resolved.append((task_id, approved))
        return {"task_id": task_id, "approved": approved}


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
        self.resolved = []
        self._pending = None
        _FakeRebuiltCoordinator.instances.append(self)

    def resume_ledger_task(self, record, *, ledger=None, on_step=None):
        self.resumed.append(record["task_id"])
        # A rebuilt pending task re-stops: pending survives.
        self._pending = record.get("pending_approval")
        return {"task_id": record["task_id"], "state": "paused"}

    def pending_approval(self, task_id):
        return self._pending

    def resolve_pending(self, task_id, *, approved):
        self.resolved.append((task_id, approved))
        return {"task_id": task_id, "approved": approved}


class TestResolvePendingAnswer(unittest.TestCase):
    def test_live_first(self):
        live = _FakeCoordinator({"t1": dict(PENDING)})
        out = pa.resolve_pending_answer(
            "t1", True, live_coordinator=live, ledger=_FakeLedger({}))
        self.assertEqual(out, {"task_id": "t1", "approved": True})
        self.assertEqual(live.resolved, [("t1", True)])

    def test_live_stale_resolves_nothing(self):
        live = _FakeCoordinator({"t1": None})
        out = pa.resolve_pending_answer(
            "t1", True, live_coordinator=live,
            ledger=_FakeLedger({"t1": _record()}))
        self.assertIsNone(out)
        self.assertEqual(live.resolved, [])

    def test_live_lookup_failure_never_rebuilds(self):
        live = _FakeCoordinator({})
        out = pa.resolve_pending_answer(
            "raise", True, live_coordinator=live,
            ledger=_FakeLedger({"raise": _record(task_id="raise")}))
        self.assertIsNone(out)

    def test_rebuild_path(self):
        _FakeRebuiltCoordinator.instances.clear()
        with mock.patch.object(tc, "TurnCoordinator",
                               _FakeRebuiltCoordinator):
            out = pa.resolve_pending_answer(
                "t9", False, live_coordinator=None,
                ledger=_FakeLedger({"t9": _record(task_id="t9")}))
        self.assertEqual(out, {"task_id": "t9", "approved": False})
        rebuilt = _FakeRebuiltCoordinator.instances[-1]
        self.assertEqual(rebuilt.resumed, ["t9"])
        self.assertEqual(rebuilt.resolved, [("t9", False)])

    def test_rebuild_without_record_is_none(self):
        with mock.patch.object(tc, "TurnCoordinator",
                               _FakeRebuiltCoordinator):
            out = pa.resolve_pending_answer(
                "nope", True, live_coordinator=None,
                ledger=_FakeLedger({}))
        self.assertIsNone(out)

    def test_no_ledger_is_none(self):
        out = pa.resolve_pending_answer(
            "t1", True, live_coordinator=None, ledger=None)
        self.assertIsNone(out)


class TestVoiceNeverResolves(unittest.TestCase):
    def test_gui_resolve_callers_are_presenter_or_gate(self):
        """The only gui/ production caller of a coordinator
        resolve is this presenter; every other resolve_pending
        call in gui/ is confirm_gate's (the funnel layer).
        A voice path can therefore only ever resolve a gate
        request, never a coordinator pending approval."""
        gui_dir = ROOT / "gui"
        offenders = []
        for path in sorted(gui_dir.rglob("*.py")):
            if "test" in path.name:
                continue
            text = path.read_text()
            for line in text.splitlines():
                if "resolve_pending(" not in line:
                    continue
                if path.name == "pending_approval.py":
                    continue
                if "confirm_gate.resolve_pending" in line:
                    continue
                offenders.append(f"{path.name}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_voice_hook_resolves_gate_only(self):
        # GUI split #10 batch 1 INTENTIONAL: _maybe_confirm_reply moved
        # verbatim to window_services_confirm.py. Assertion unchanged.
        text = (ROOT / "gui" / "window_services_confirm.py").read_text()
        start = text.index("def _maybe_confirm_reply")
        end = text.index("\n    def ", start + 1)
        method = text[start:end]
        self.assertNotIn("task_coordinator", method)
        for line in method.splitlines():
            if "resolve_pending(" in line:
                self.assertIn("confirm_gate.resolve_pending", line)


# ---- Qt tests: skip without PySide6 ---------------------------------------
try:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget

    _QT = True
except Exception:  # noqa: BLE001 — optional dependency absence
    _QT = False


def _stub_window_services():
    """Import gui.window_services with heavy core modules stubbed
    (probe-verified): only the dialog builders are exercised.

    The stubs are removed again right after the import: the
    builder already bound what it needs, and leaving stub
    modules in sys.modules would pollute later suites in the
    same process (a later import of voice_assistant.core.*
    would resolve to a stub instead of the real module).
    """
    import core as core_pkg  # noqa: F401 — keeps the alias visible

    _added = []
    # Phase 3 B2h: brain hub moved to core/brain/brain.py INTENTIONAL: stub key follows the hub move;
    # Phase 3 V1a-1 INTENTIONAL: audio moved to core/voice/audio.py, so audio maps to the voice path;
    # Phase 3 V1a-2 INTENTIONAL: wake_word moved to core/voice/wake_word.py; all other leaves stay flat.
    for name in ("audio", "clipboard_monitor", "config", "hotkeys",
                 "brain", "scheduler", "wake_word"):
        # Phase 3 D3 INTENTIONAL: clipboard_monitor + hotkeys moved
        # to core/desktop/ — the key computation follows the move
        # (previously it still built the flat keys while the
        # assignments below already used the desktop keys, so the
        # first desktop assignment raised KeyError and every Qt
        # test in this suite skipped vacuously).
        full = ("voice_assistant.core.brain.brain" if name == "brain"
                       else "voice_assistant.core.voice.audio" if name == "audio"
                       else "voice_assistant.core.voice.wake_word" if name == "wake_word"
                       else "voice_assistant.core.desktop.clipboard_monitor" if name == "clipboard_monitor"
                       else "voice_assistant.core.desktop.hotkeys" if name == "hotkeys"
                       else "voice_assistant.core." + name)
        if full not in sys.modules:
            sys.modules[full] = types.ModuleType(full)
            _added.append(full)
    sys.modules["voice_assistant.core.voice.audio"].audio_manager = object()  # Phase 3 V1a-1 INTENTIONAL: stub follows audio move
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
class TestPresentDialog(unittest.TestCase):
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

        return Stub()

    def _click(self, window, text):
        dlg = window.captured
        for btn in dlg.findChildren(QPushButton):
            if btn.text() == text:
                btn.click()
                return True
        return False

    def test_allow_once_returns_true(self):
        window = self._stub_window()
        QTimer.singleShot(
            0, lambda: self._click(window, "Allow once"))
        self.assertTrue(pa.present_pending_dialog(window, PENDING))

    def test_dont_allow_returns_false(self):
        window = self._stub_window()
        QTimer.singleShot(
            0, lambda: self._click(window, "Don't allow"))
        self.assertFalse(pa.present_pending_dialog(window, PENDING))

    def test_silence_timeout_denies(self):
        window = self._stub_window()
        # No click scheduled: the presenter's own timer rejects.
        self.assertFalse(
            pa.present_pending_dialog(window, PENDING, timeout_s=0.5))

    def test_dialog_shows_pending_fields(self):
        window = self._stub_window()
        # The safe-button contract holds while the dialog is
        # shown: Qt clears isDefault() once exec() returns, so
        # the default is recorded from inside the modal loop,
        # before the click that closes it.
        seen = {}

        def _record_and_click():
            dlg = window.captured
            deny = next(b for b in dlg.findChildren(QPushButton)
                        if b.text() == "Don't allow")
            seen["default"] = deny.isDefault()
            deny.click()

        QTimer.singleShot(0, _record_and_click)
        pa.present_pending_dialog(window, PENDING)
        self.assertTrue(seen.get("default"))
        headline = window._confirm_headline_label.text()
        details = window._confirm_details_label.text()
        self.assertTrue(headline)
        self.assertIn("menu_action", details)
        self.assertIn("Close tab", details)
        self.assertIn(PENDING["sentence"], details)
        # §7 tools are confirm-gate tools, hence never
        # allowlistable: no "Always allow this kind" button on
        # this dialog (the Details toggle is not a decision).
        dlg = window.captured
        texts = [b.text() for b in dlg.findChildren(QPushButton)]
        self.assertNotIn("Always allow this kind", texts)
        self.assertIn("Don't allow", texts)
        self.assertIn("Allow once", texts)

    def test_plain_fallback_still_asks(self):
        window = self._stub_window()

        def _boom(tool, summary):
            raise RuntimeError("designed builder broken")

        window._build_confirm_dialog = _boom
        QTimer.singleShot(
            0, lambda: self._click(window, "Allow once"))
        self.assertTrue(pa.present_pending_dialog(window, PENDING))

    def test_broken_both_builders_deny(self):
        window = self._stub_window()
        window._build_confirm_dialog = lambda *a: (_ for _ in ()).throw(
            RuntimeError("designed broken"))
        window._build_plain_confirm_dialog = lambda *a: (
            _ for _ in ()).throw(RuntimeError("plain broken"))
        self.assertFalse(pa.present_pending_dialog(window, PENDING))


@unittest.skipUnless(_QT, "PySide6 unavailable")
class TestReviewQueue(unittest.TestCase):
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

    def _wait(self, predicate, timeout_s=10.0):
        from PySide6.QtCore import QEventLoop
        from PySide6.QtTest import QTest
        waited = 0.0
        while not predicate() and waited < timeout_s:
            QTest.qWait(50)
            waited += 0.05
        return predicate()

    def test_queue_resolves_in_order_then_refreshes(self):
        window = self._stub_window()
        resolved = []
        # Cancel-wiring INTENTIONAL: the Review queue presents
        # via present_pending_dialog_with_cancel (the dialog
        # WITH the Cancel task button), not the plain
        # presenter — patching the plain name left this test
        # clicking nothing (both dialogs hit the 60 s silence
        # timeout) once the module-stub fix let Qt tests run.
        real_present = pa.present_pending_dialog_with_cancel

        def _fake_resolve(task_id, approved, **kw):
            resolved.append((task_id, approved))
            return {"task_id": task_id}

        clicks = ["Allow once", "Don't allow"]
        state = {"i": 0}

        def _present(w, pending, **kw):
            text = clicks[state["i"]]
            state["i"] += 1
            QTimer.singleShot(
                0, lambda: self._click(w, text))
            return real_present(w, pending)

        with mock.patch.object(pa, "resolve_pending_answer",
                               _fake_resolve):
            with mock.patch.object(pa,
                                   "present_pending_dialog_with_cancel",
                                   _present):
                pa.review_pending_queue(
                    window,
                    [("t1", dict(PENDING)),
                     ("t2", dict(PENDING, label="Delete file"))],
                    live_coordinator=None, ledger=None,
                    refresh=window._refresh, poll_ms=100)
                done = self._wait(lambda: window.refreshed >= 1)
        self.assertTrue(done)
        self.assertEqual(resolved, [("t1", True), ("t2", False)])

    def _click(self, window, text):
        dlg = window.captured
        for btn in dlg.findChildren(QPushButton):
            if btn.text() == text:
                btn.click()
                return True
        return False


if __name__ == "__main__":
    unittest.main()
