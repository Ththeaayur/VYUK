"""§7 consumers batch 9: GUI reconcile surface for the action journal.

The journal funnel tools (batch 2) existed without a screen; this
suite pins the screen: attention ordering, evidence shown
verbatim, corruption loud, reconcile append-only through the
core path, compensation order + first-failure-stop through the
surface helper, the facts-then-authorization flow (denial and
cancel write nothing), and the hard rules — no history rewrite,
no handle re-resolution, no voice path into reconcile.

Pure tests run on both interpreters. Qt tests need PySide6
(the .venv-qt interpreter); on an interpreter without it they
skip, following the programme's standing optional-dependency
skips. All stores are temp files — no test touches the real
data/ journal.
"""

import pathlib
import re
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
             handle_id=None, stamp=None, compensations=None,
             manual_steps=None, summary=""):
    attempt = store.record_attempt(
        tool=tool, args={"x": tool}, handle_id=handle_id,
        snapshot_stamp=stamp, compensations=compensations,
        manual_steps=manual_steps)
    rid = str(attempt["receipt_id"])
    if dispatched:
        store.mark_dispatched(rid)
    if outcome is not None:
        store.record_outcome(rid, status=outcome, summary=summary,
                             dispatched=True)
    return rid


def _seed(tmpdir):
    """unknown + done-with-comp + retryable + done-plain."""
    store = _store(tmpdir)
    unknown = _attempt(store, "menu_action", dispatched=True,
                       handle_id="h-unknown", stamp="s-unknown")
    done_comp = _attempt(
        store, "file_dialog_action", outcome="failed",
        summary="dialog failed",
        compensations=[{"tool": "undo_a", "args": {},
                        "note": "first declared"},
                       {"tool": "undo_b", "args": {},
                        "note": "second declared"}])
    retryable = _attempt(store, "monitor_action")
    done_plain = _attempt(store, "adapter_action", outcome="succeeded",
                          summary="resolved")
    return store, {"unknown": unknown, "done_comp": done_comp,
                   "retryable": retryable, "done_plain": done_plain}


class TestAttentionOrdering(unittest.TestCase):
    def test_unknown_first_then_comp_then_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(tmp)
            result = js.journal_attention(
                store_path=pathlib.Path(tmp) / "receipts.jsonl")
            self.assertTrue(result["ok"])
            classes = [r["class"] for r in result["rows"]]
            self.assertEqual(classes[0], "unknown")
            # Done-with-comp before the rest; rest-group order
            # is newest-first (timestamp ties make exact rest
            # order nondeterministic, so assert the set).
            tools = [r["tool"] for r in result["rows"]]
            self.assertEqual(tools[0], "menu_action")
            self.assertEqual(tools[1], "file_dialog_action")
            self.assertEqual(set(tools[2:]),
                             {"adapter_action", "monitor_action"})
            self.assertEqual(result["counts"]["unknown"], 1)
            self.assertEqual(result["total"], 4)

    def test_cap_fifty_rows_counts_cover_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            for i in range(55):
                _attempt(store, "monitor_action", outcome="succeeded")
            result = js.journal_attention(
                store_path=pathlib.Path(tmp) / "receipts.jsonl")
            self.assertEqual(len(result["rows"]), 50)
            self.assertEqual(result["total"], 55)

    def test_missing_file_is_empty_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = js.journal_attention(
                store_path=pathlib.Path(tmp) / "nope.jsonl")
            self.assertTrue(result["ok"])
            self.assertEqual(result["rows"], [])

    def test_row_fields_verbatim_compensations_reversed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(tmp)
            result = js.journal_attention(
                store_path=pathlib.Path(tmp) / "receipts.jsonl")
            by_tool = {r["tool"]: r for r in result["rows"]}
            unknown = by_tool["menu_action"]
            self.assertEqual(unknown["handle_id"], "h-unknown")
            self.assertEqual(unknown["snapshot_stamp"], "s-unknown")
            # An unknown entry never recorded an outcome, so its
            # evidence is genuinely empty — the row must show
            # that, not invent a summary.
            self.assertEqual(unknown["evidence"], "")
            self.assertNotIn("Evidence:", js.journal_row_text(unknown))
            done_comp = by_tool["file_dialog_action"]
            self.assertEqual(done_comp["compensation_status"],
                             "declared")
            # Reverse declaration order: second declared first.
            self.assertEqual(
                [s["tool"] for s in done_comp["compensations"]],
                ["undo_b", "undo_a"])
            plain = by_tool["adapter_action"]
            self.assertEqual(plain["compensation_status"],
                             "no_undo_available")
            self.assertTrue(plain["manual_steps"])

    def test_corrupt_is_loud_never_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = _seed(tmp)
            path = pathlib.Path(tmp) / "receipts.jsonl"
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("{not json\n")
            result = js.journal_attention(store_path=path)
            self.assertFalse(result["ok"])
            self.assertNotIn("rows", result)
            self.assertIn(str(path), result["error"])
            self.assertTrue(result["repair_steps"])


class TestReconcileAppendOnly(unittest.TestCase):
    def test_happened_false_failed_and_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, ids = _seed(tmp)
            path = pathlib.Path(tmp) / "receipts.jsonl"
            before = path.read_bytes()
            result = js.reconcile_entry(
                ids["unknown"], False, "checked: file absent",
                store_path=path)
            self.assertEqual(result["new_outcome"], "failed")
            after = path.read_bytes()
            self.assertTrue(after.startswith(before))
            self.assertEqual(len(after.splitlines()),
                             len(before.splitlines()) + 1)
            # The entry leaves the unknown class.
            fresh = js.journal_attention(store_path=path)
            by_id = {r["entry_id"]: r for r in fresh["rows"]}
            self.assertEqual(by_id[ids["unknown"]]["class"], "done")
            self.assertEqual(by_id[ids["unknown"]]["outcome"],
                             "failed")

    def test_happened_true_succeeded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, ids = _seed(tmp)
            result = js.reconcile_entry(
                ids["unknown"], True, "saw the change",
                store_path=pathlib.Path(tmp) / "receipts.jsonl")
            self.assertEqual(result["new_outcome"], "succeeded")

    def test_refusals_write_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, ids = _seed(tmp)
            path = pathlib.Path(tmp) / "receipts.jsonl"
            before = path.read_bytes()
            for args in ((("no-such-id", False, "x")),
                         ((ids["done_plain"], True, "x")),
                         ((ids["unknown"], False, "   ")),
                         ((ids["unknown"], "yes", "x"))):
                with self.assertRaises(ValueError):
                    js.reconcile_entry(*args, store_path=path)
            self.assertEqual(path.read_bytes(), before)


class TestCompensateThroughSurface(unittest.TestCase):
    def test_reverse_order_first_failure_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, ids = _seed(tmp)
            path = pathlib.Path(tmp) / "receipts.jsonl"
            ran = []

            def executor(step):
                ran.append(step["tool"])
                if step["tool"] == "undo_b":
                    return {"status": "failed"}
                return {"status": "succeeded"}

            result = js.compensate_entry(
                ids["done_comp"], executor=executor, store_path=path)
            self.assertEqual(result["status"], "stopped")
            # Reverse order: undo_b first, then it fails — undo_a
            # never executes.
            self.assertEqual(ran, ["undo_b"])
            self.assertEqual(result["stopped_at"]["tool"], "undo_b")

    def test_no_declaration_never_calls_executor(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, ids = _seed(tmp)
            path = pathlib.Path(tmp) / "receipts.jsonl"
            calls = []
            result = js.compensate_entry(
                ids["done_plain"], executor=calls.append,
                store_path=path)
            self.assertEqual(result["status"], "no_undo_available")
            self.assertEqual(calls, [])
            self.assertTrue(result["manual_steps"])

    def test_compensate_helper_writes_no_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, ids = _seed(tmp)
            path = pathlib.Path(tmp) / "receipts.jsonl"
            before = path.read_bytes()
            js.compensate_entry(ids["done_comp"],
                                executor=lambda s: {"status": "succeeded"},
                                store_path=path)
            self.assertEqual(path.read_bytes(), before)

    def test_validate_input(self):
        self.assertIsNone(js.validate_reconcile_input(True, "saw it"))
        self.assertIsNotNone(js.validate_reconcile_input("yes", "saw it"))
        self.assertIsNotNone(js.validate_reconcile_input(True, "  "))
        self.assertIsNotNone(js.validate_reconcile_input(None, "saw it"))


class TestHardRules(unittest.TestCase):
    def test_no_handle_reresolution_imports(self):
        source = pathlib.Path(js.__file__).read_text(encoding="utf-8")
        self.assertNotIn("target_handles", source)
        self.assertNotIn("perception_adapter", source)

    def test_no_voice_path_into_reconcile(self):
        gui_dir = pathlib.Path(js.__file__).resolve().parent
        for path in gui_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            # GUI split #12 batch 2 INTENTIONAL: the
            # _reconcile_selected_journal_entry calls moved verbatim
            # from the skipped window_memory.py into
            # window_memory_activity.py; the no-new-caller rule is
            # unchanged, only the file holding the calls moved.
            if path.name in ("journal_surface.py", "window_memory.py",
                            "window_memory_activity.py"):
                continue
            self.assertNotIn("reconcile_entry", text,
                             f"{path.name} must not call reconcile_entry")
            # T-6 (debug pass): the bare substring "present_reconcile"
            # false-positives on the file that DEFINES
            # present_reconcile_dialog (gui/pending_reconcile.py) —
            # a word-boundary call-site check for the real function
            # name, skipping the defining file itself (its internal
            # call is the implementation, not a new caller).
            if path.name != "pending_reconcile.py":
                self.assertIsNone(
                    re.search(r"present_reconcile_dialog\s*\(", text),
                    f"{path.name} must not call present_reconcile_dialog")
        voice_hook = gui_dir / "window_services.py"
        if voice_hook.exists():
            text = voice_hook.read_text(encoding="utf-8")
            self.assertNotIn("journal_surface", text)


@unittest.skipUnless(_QT, "Qt tests need PySide6 (.venv-qt)")
class TestQtSurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_refresh_fills_order_label_and_roles(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QListWidget, QLabel

        with tempfile.TemporaryDirectory() as tmp:
            _seed(tmp)
            path = pathlib.Path(tmp) / "receipts.jsonl"
            lst = QListWidget()
            label = QLabel()
            result = js.refresh_journal_list(lst, label_widget=label,
                                             store_path=path)
            self.assertTrue(result["ok"])
            self.assertEqual(lst.count(), 4)
            first = lst.item(0).data(Qt.ItemDataRole.UserRole)
            self.assertEqual(first["class"], "unknown")
            self.assertIn("(1)", label.text())
            texts = [lst.item(i).text() for i in range(lst.count())]
            self.assertTrue(texts[0].startswith("menu_action"))

    def test_refresh_corrupt_loud_row(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QListWidget, QLabel

        with tempfile.TemporaryDirectory() as tmp:
            _seed(tmp)
            path = pathlib.Path(tmp) / "receipts.jsonl"
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("{broken\n")
            lst = QListWidget()
            label = QLabel()
            result = js.refresh_journal_list(lst, label_widget=label,
                                             store_path=path)
            self.assertFalse(result["ok"])
            self.assertEqual(lst.count(), 1)
            # Screen 3 Activity: the corrupt row is a widget row
            # — its text lives on the widget's line label, not on
            # the item (an item text would double-paint behind
            # the widget). The row still surfaces "unreadable"
            # as text; the item carries the corrupt marker.
            self.assertEqual(lst.item(0).text(), "")
            widget = lst.itemWidget(lst.item(0))
            line = [lbl for lbl in widget.findChildren(QLabel)
                    if lbl.objectName() == "activityError"]
            self.assertEqual(len(line), 1)
            self.assertIn("unreadable", line[0].text().lower())
            data = lst.item(0).data(Qt.ItemDataRole.UserRole)
            self.assertTrue(data["corrupt"])
            self.assertTrue(data["repair_steps"])
            self.assertIn("unreadable", label.text().lower())

    def test_refresh_unexpected_failure_is_widget_row_without_path(
            self):
        # Field-check fix #8 (U5 #32): the unexpected-failure
        # fallback is a §2f error-line widget like the corrupt
        # row — and its cause never carries the absolute path
        # the raised error may hold. The returned error keeps
        # the raw string for logging; only the row face is
        # sanitized.
        from PySide6.QtWidgets import QListWidget, QLabel

        with tempfile.TemporaryDirectory() as tmp:
            lst = QListWidget()
            label = QLabel()
            with mock.patch.object(
                    js, "journal_attention",
                    side_effect=RuntimeError(
                        "boom at /home/hatch/data/"
                        "tool_receipts.jsonl")):
                result = js.refresh_journal_list(
                    lst, label_widget=label,
                    store_path=pathlib.Path(tmp) / "receipts.jsonl")
            self.assertFalse(result["ok"])
            self.assertTrue(result["unexpected"])
            self.assertIn("/home/hatch/data/tool_receipts.jsonl",
                          result["error"])
            self.assertEqual(lst.count(), 1)
            self.assertEqual(lst.item(0).text(), "")
            widget = lst.itemWidget(lst.item(0))
            self.assertIsNotNone(
                widget, "unavailable row is not a widget row")
            dots = [lbl for lbl in widget.findChildren(QLabel)
                    if lbl.objectName() == "activityErrorDot"]
            self.assertEqual(len(dots), 1)
            self.assertEqual(dots[0].text(), "●")
            lines = [lbl for lbl in widget.findChildren(QLabel)
                     if lbl.objectName() == "activityError"]
            self.assertEqual(len(lines), 1)
            text = lines[0].text()
            self.assertIn("Journal is unavailable right now", text)
            self.assertIn("Reopen Activity to try again.", text)
            self.assertIn("tool_receipts.jsonl", text)
            self.assertNotIn("/home/", text)

    def test_refresh_empty(self):
        from PySide6.QtWidgets import QListWidget, QLabel

        with tempfile.TemporaryDirectory() as tmp:
            lst = QListWidget()
            label = QLabel()
            js.refresh_journal_list(
                lst, label_widget=label,
                store_path=pathlib.Path(tmp) / "nope.jsonl")
            self.assertEqual(lst.count(), 1)
            self.assertIn("No journal entries", lst.item(0).text())

    def test_input_dialog_wiring(self):
        from PySide6.QtWidgets import QWidget

        parent = QWidget()
        row = {"tool": "menu_action", "evidence": "invoked",
               "class": "unknown", "entry_id": "r1"}
        dlg = js._build_reconcile_input_dialog(parent, row)
        # Nothing chosen, no text: OK disabled.
        self.assertFalse(dlg.ok_button.isEnabled())
        dlg.happened_group.button(1).setChecked(True)
        self.assertFalse(dlg.ok_button.isEnabled())
        dlg.verification_edit.setText("saw the tab close")
        self.assertTrue(dlg.ok_button.isEnabled())
        self.assertEqual(js._read_reconcile_input_dialog(dlg),
                         (True, "saw the tab close"))
        dlg.happened_group.button(0).setChecked(True)
        self.assertEqual(js._read_reconcile_input_dialog(dlg),
                         (False, "saw the tab close"))
        parent.deleteLater()

    def _pump(self, check, timeout_s=3.0):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self.app.processEvents()
            if check():
                return True
            time.sleep(0.05)
        return check()

    def test_present_reconcile_approval_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, ids = _seed(tmp)
            path = pathlib.Path(tmp) / "receipts.jsonl"
            window = QWidget()
            row = {"tool": "menu_action", "evidence": "invoked",
                   "class": "unknown", "entry_id": ids["unknown"]}
            refreshed = []
            with mock.patch.object(
                    js, "ask_reconcile_input",
                    return_value=(False, "checked: file absent")), \
                    mock.patch(
                        "gui.pending_approval.present_pending_dialog",
                        return_value=True):
                self.assertTrue(js.present_reconcile(
                    window, row, store_path=path,
                    refresh=lambda: refreshed.append(True)))
            self.assertTrue(self._pump(lambda: bool(refreshed)))
            fresh = js.journal_attention(store_path=path)
            by_id = {r["entry_id"]: r for r in fresh["rows"]}
            self.assertEqual(by_id[ids["unknown"]]["outcome"], "failed")
            window.deleteLater()

    def test_present_reconcile_denial_and_cancel_write_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, ids = _seed(tmp)
            path = pathlib.Path(tmp) / "receipts.jsonl"
            before = path.read_bytes()
            window = QWidget()
            row = {"tool": "menu_action", "evidence": "invoked",
                   "class": "unknown", "entry_id": ids["unknown"]}
            with mock.patch.object(
                    js, "ask_reconcile_input",
                    return_value=(True, "saw it")), \
                    mock.patch(
                        "gui.pending_approval.present_pending_dialog",
                        return_value=False):
                self.assertFalse(js.present_reconcile(
                    window, row, store_path=path, refresh=lambda: None))
            self.assertEqual(path.read_bytes(), before)
            with mock.patch.object(js, "ask_reconcile_input",
                                   return_value=None), \
                    mock.patch(
                        "gui.pending_approval.present_pending_dialog",
                        side_effect=AssertionError("must not be called")):
                self.assertFalse(js.present_reconcile(
                    window, row, store_path=path, refresh=lambda: None))
            self.assertEqual(path.read_bytes(), before)
            # Non-unknown rows never reach a dialog.
            done_row = dict(row, entry_id=ids["done_plain"],
                            **{"class": "done"})
            with mock.patch.object(
                    js, "ask_reconcile_input",
                    side_effect=AssertionError("must not be called")):
                self.assertFalse(js.present_reconcile(
                    window, done_row, store_path=path,
                    refresh=lambda: None))
            self.assertEqual(path.read_bytes(), before)
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
