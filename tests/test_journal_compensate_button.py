"""§7 follow-up: GUI Compensate button for the journal reconcile
surface.

Batch 9 deliberately scoped the button out (the window had no
funnel-executor hook); this suite pins the wiring that closes
that cut: the button's shared gate predicate, the authorization-
dialog-then-funnel-leg flow (denial runs nothing, approval
passes confirmed=True only from the dialog), the honest
outcome display incl. the failure branch, the real leg's own
confirmation gate, and the hard rule — no voice path into
compensate.

Pure tests run on both interpreters. Qt tests need PySide6
(the .venv-qt interpreter); on an interpreter without it they
skip, following the programme's standing optional-dependency
skips. All stores are temp files — no test touches the real
data/ journal. The MainWindow mixin (window_memory) cannot be
imported in this lab (its chain needs PortAudio, absent here),
so the button-enable predicate is pinned at its single source
(journal_surface.compensable_row, shared by the updater and
the flow) and the render script shows the wired button; the
mixin methods themselves are thin selection gatherers over
that predicate, mirroring the batch-9 Reconcile wiring.
"""

import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# voice_assistant.* alias (batch-7 precedent): gui modules import
# voice_assistant.core.*, which is this tree's core/. Submodule
# aliases too: without them a lazy import would load a SECOND
# copy of the module instead of the one the tests patch.
import core as _core_pkg
import core.desktop.action_journal as _aj_mod  # INTENTIONAL (Phase 3 S7a-2): moved to core/desktop/
import core.tool_receipts as _tr_mod
import gui as _gui_pkg
import gui.pending_approval as _pa_mod

if "voice_assistant" not in sys.modules:
    import types

    _va = types.ModuleType("voice_assistant")
    _va.__path__ = [str(ROOT)]
    sys.modules["voice_assistant"] = _va
sys.modules.setdefault("voice_assistant.core", _core_pkg)
sys.modules.setdefault("voice_assistant.gui", _gui_pkg)
sys.modules.setdefault("voice_assistant.core.desktop.action_journal", _aj_mod)
sys.modules.setdefault("voice_assistant.core.tool_receipts", _tr_mod)
sys.modules.setdefault("voice_assistant.gui.pending_approval", _pa_mod)

from core.desktop import action_journal as aj  # INTENTIONAL (Phase 3 S7a-2): moved to core/desktop/  # noqa: E402
from core import tool_receipts as tr  # noqa: E402
from gui import journal_surface as js  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtCore import Qt
    _QT = True
except Exception:  # noqa: BLE001 — system python has no Qt
    _QT = False


def _store(tmpdir, name="receipts.jsonl"):
    return tr.ReceiptStore(path=pathlib.Path(tmpdir) / name)


def _attempt(store, tool, *, dispatched=False, outcome=None,
             compensations=None, summary=""):
    attempt = store.record_attempt(
        tool=tool, args={"x": tool}, compensations=compensations)
    rid = str(attempt["receipt_id"])
    if dispatched:
        store.mark_dispatched(rid)
    if outcome is not None:
        store.record_outcome(rid, status=outcome, summary=summary,
                             dispatched=True)
    return rid


def _declared_row(entry_id, tool="file_dialog_action"):
    return {"entry_id": entry_id, "tool": tool,
            "evidence": "dialog failed",
            "class": "done",
            "compensation_status": "declared",
            "compensations": [{"tool": "undo_a", "args": {},
                               "note": "first declared"},
                              {"tool": "undo_b", "args": {},
                               "note": "second declared"}]}


class TestCompensableRow(unittest.TestCase):
    def test_declared_row_is_compensable(self):
        self.assertTrue(js.compensable_row(_declared_row("abc")))

    def test_no_declaration_is_not_compensable(self):
        row = _declared_row("abc")
        row["compensation_status"] = "no_undo_available"
        row["compensations"] = []
        self.assertFalse(js.compensable_row(row))

    def test_missing_id_is_not_compensable(self):
        row = _declared_row("")
        self.assertFalse(js.compensable_row(row))

    def test_non_dict_is_not_compensable(self):
        self.assertFalse(js.compensable_row(None))
        self.assertFalse(js.compensable_row("declared"))
        self.assertFalse(js.compensable_row([]))


class TestPresentCompensateGuards(unittest.TestCase):
    def test_non_compensable_rows_never_reach_a_dialog(self):
        rows = [None, {}, {"entry_id": "x"},
                _declared_row(""),
                dict(_declared_row("x"),
                     compensation_status="no_undo_available")]
        for row in rows:
            with mock.patch(
                    "gui.pending_approval.present_pending_dialog",
                    side_effect=AssertionError("must not be called")):
                self.assertFalse(js.present_compensate(
                    None, row, refresh=lambda: None))


class TestVoiceCannotCompensate(unittest.TestCase):
    def test_no_voice_path_into_compensate(self):
        gui_dir = pathlib.Path(js.__file__).resolve().parent
        for path in gui_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            # GUI split #12 batch 2 INTENTIONAL: the
            # _compensate_selected_journal_entry calls moved verbatim
            # from the skipped window_memory.py into
            # window_memory_activity.py; the no-new-caller rule is
            # unchanged, only the file holding the calls moved.
            if path.name in ("journal_surface.py", "window_memory.py",
                            "window_memory_activity.py"):
                continue
            self.assertNotIn("present_compensate", text,
                             f"{path.name} must not call "
                             "present_compensate")
        voice_hook = gui_dir / "window_services.py"
        if voice_hook.exists():
            text = voice_hook.read_text(encoding="utf-8")
            self.assertNotIn("journal_surface", text)
            self.assertNotIn("present_compensate", text)
            self.assertNotIn("compensate_entry", text)


class TestRealLegConfirmationGate(unittest.TestCase):
    def test_unconfirmed_leg_writes_nothing(self):
        from core import confirm_gate
        from core.funnel.tools_registry import tools

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "receipts.jsonl"
            store = _store(tmp)
            rid = _attempt(
                store, "file_dialog_action", outcome="failed",
                compensations=[{"tool": "undo_a", "args": {},
                                "note": "first"}],
                summary="dialog failed")
            before = path.read_bytes()
            # GAPS-23 (F-V7): through the funnel — the gate asks
            # first (approved here); the handler's OWN stop
            # (confirmed=False) is what this test pins.
            confirm_gate.register_handler(lambda n, s: True)
            try:
                result = tools.execute(
                    "journal_action",
                    {"action": "compensate", "receipt_id": rid,
                     "store_path": str(path), "confirmed": False,
                     "confirmed_by": "user"})["result"]
            finally:
                confirm_gate.clear_handler()
            self.assertIn("needs_confirmation", result)
            self.assertEqual(path.read_bytes(), before)

    def test_confirmed_leg_reports_stop_honestly(self):
        from core import confirm_gate
        from core.funnel.tools_registry import tools

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "receipts.jsonl"
            store = _store(tmp)
            rid = _attempt(
                store, "file_dialog_action", outcome="failed",
                compensations=[{"tool": "undo_b", "args": {},
                                "note": "second"},
                               {"tool": "undo_a", "args": {},
                                "note": "first"}],
                summary="dialog failed")
            # GAPS-23 (F-V7): through the funnel (gate approved).
            confirm_gate.register_handler(lambda n, s: True)
            try:
                result = tools.execute(
                    "journal_action",
                    {"action": "compensate", "receipt_id": rid,
                     "store_path": str(path), "confirmed": True,
                     "confirmed_by": "user"})["result"]
            finally:
                confirm_gate.clear_handler()
            # Reverse declaration order: undo_a runs first and
            # is not a real tool, so the run stops there.
            self.assertIn("stopped", result.lower())
            self.assertIn("undo_a", result)

    def test_unknown_id_is_refused(self):
        from core import confirm_gate
        from core.funnel.tools_registry import tools

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "receipts.jsonl"
            _store(tmp)
            # GAPS-23 (F-V7): through the funnel (gate approved).
            confirm_gate.register_handler(lambda n, s: True)
            try:
                result = tools.execute(
                    "journal_action",
                    {"action": "compensate",
                     "receipt_id": "no-such-id",
                     "store_path": str(path), "confirmed": True,
                     "confirmed_by": "user"})["result"]
            finally:
                confirm_gate.clear_handler()
            self.assertIn("No journal entry", result)


@unittest.skipUnless(_QT, "Qt tests need PySide6 (.venv-qt)")
class TestQtCompensateFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _pump(self, check, timeout_s=3.0):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self.app.processEvents()
            if check():
                return True
            time.sleep(0.05)
        return check()

    def test_approval_invokes_leg_with_confirmed_and_shows_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "receipts.jsonl"
            store = _store(tmp)
            rid = _attempt(
                store, "file_dialog_action", outcome="failed",
                compensations=[{"tool": "undo_a", "args": {},
                                "note": "first"}],
                summary="dialog failed")
            window = QWidget()
            row = _declared_row(rid)
            calls = []
            outcomes = []
            refreshed = []

            def _fake_leg(action, *, receipt_id=None, store_path=None,
                          confirmed=False, confirmed_by=""):
                calls.append({"action": action,
                              "receipt_id": receipt_id,
                              "store_path": store_path,
                              "confirmed": confirmed,
                              "confirmed_by": confirmed_by})
                return ("Compensation complete for entry %s: "
                        "1 step(s) verified." % receipt_id)

            with mock.patch(
                    "gui.pending_approval.present_pending_dialog",
                    return_value=True), \
                    mock.patch.object(
                        js, "show_compensate_outcome",
                        side_effect=lambda w, t: outcomes.append(t)):
                self.assertTrue(js.present_compensate(
                    window, row, store_path=path,
                    refresh=lambda: refreshed.append(True),
                    leg=_fake_leg))
                self.assertTrue(self._pump(
                    lambda: bool(outcomes) and bool(refreshed)))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["action"], "compensate")
            self.assertEqual(calls[0]["receipt_id"], rid)
            self.assertIs(calls[0]["store_path"], path)
            self.assertIs(calls[0]["confirmed"], True)
            self.assertEqual(calls[0]["confirmed_by"], "user")
            self.assertEqual(
                outcomes[0],
                "Compensation complete for entry %s: "
                "1 step(s) verified." % rid)
            window.deleteLater()

    def test_denial_runs_nothing_but_refreshes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "receipts.jsonl"
            store = _store(tmp)
            rid = _attempt(
                store, "file_dialog_action", outcome="failed",
                compensations=[{"tool": "undo_a", "args": {},
                                "note": "first"}],
                summary="dialog failed")
            before = path.read_bytes()
            window = QWidget()
            row = _declared_row(rid)
            refreshed = []
            with mock.patch(
                    "gui.pending_approval.present_pending_dialog",
                    return_value=False), \
                    mock.patch.object(
                        js, "show_compensate_outcome",
                        side_effect=AssertionError(
                            "no outcome without approval")):
                self.assertFalse(js.present_compensate(
                    window, row, store_path=path,
                    refresh=lambda: refreshed.append(True),
                    leg=mock.Mock(
                        side_effect=AssertionError(
                            "leg must not be called"))))
            self.assertEqual(refreshed, [True])
            self.assertEqual(path.read_bytes(), before)
            window.deleteLater()

    def test_failure_outcome_is_shown_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "receipts.jsonl"
            store = _store(tmp)
            rid = _attempt(
                store, "file_dialog_action", outcome="failed",
                compensations=[{"tool": "undo_a", "args": {},
                                "note": "first"}],
                summary="dialog failed")
            window = QWidget()
            row = _declared_row(rid)
            outcomes = []
            stopped = ("Compensation stopped for entry %s: step "
                       "not verified (status 'unavailable')\n"
                       "Stopped at: undo_a" % rid)

            def _fake_leg(action, **kwargs):
                return stopped

            with mock.patch(
                    "gui.pending_approval.present_pending_dialog",
                    return_value=True), \
                    mock.patch.object(
                        js, "show_compensate_outcome",
                        side_effect=lambda w, t: outcomes.append(t)):
                self.assertTrue(js.present_compensate(
                    window, row, store_path=path,
                    refresh=lambda: None, leg=_fake_leg))
                self.assertTrue(self._pump(lambda: bool(outcomes)))
            self.assertEqual(outcomes[0], stopped)
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
