"""C7 approval-dialog crop display tests (2026-11-05, INTENTIONAL).

Dialog half of C7 ("Verify the spot, not the snapshot"): the
S-1 dialog shows the STORED crop — the pixels the spot was
read from — when the action carries one, and no image region
at all otherwise. Gating is by SUPPLY, not by tool name, and
the caption never claims the crop is fresh (C4 enumeration
stands: ask-paths have no fresh-observation supplier at dialog
time, so this slot shows stored pixels only).

HONEST SCOPE NOTE (do not "fix" by inventing attribution): the
producer core exists (vision retains the SENT image in a
single slot; point_crop cuts region grids; the C3 re-issue
target and pending records attach a crop when the step names
the retained capture — see tests/test_c7_crop_producer.py).
But NO live step carries a source_capture_id today — the
brain-plan stamp point is a brain-session-layer design
decision, still owed — so live dialogs still render the
honest fallback until steps carry ids. The
pending-threading tests prove the stored-record path carries
a record's crop through to the dialog.
"""

import unittest

from gui.crop_grid import stored_crop_grayscale_bytes

try:
    from PySide6.QtWidgets import QApplication as _QA
    _QT = True
except Exception:  # noqa: BLE001 — PySide6 absent on sys
    _QA = None
    _QT = False


import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_APP = None
_VA_SNAPSHOT = None


def setUpModule():
    """Mirror test_uiinteraction's voice_assistant.* alias.

    gui/window_services_confirm.py imports voice_assistant.core.*
    (pre-split paths); the tree layout is top-level core/ + gui/,
    so the alias maps voice_assistant -> tree root, .core ->
    tree/core. Snapshot discipline: restore prior entries on
    teardown so a co-run suite's cached modules survive.
    """
    global _APP, _VA_SNAPSHOT
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    if not _QT:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    tree = str(ROOT)
    pkg = types.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = types.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    audio = types.ModuleType("voice_assistant.core.voice.audio")
    audio.audio_manager = types.SimpleNamespace()
    brain = types.ModuleType("voice_assistant.core.brain.brain")
    brain.live_session = types.SimpleNamespace()
    for name, mod in (("voice_assistant", pkg),
                      ("voice_assistant.core", core),
                      ("voice_assistant.core.voice.audio", audio),
                      ("voice_assistant.core.brain.brain", brain)):
        sys.modules[name] = mod


def tearDownModule():
    global _VA_SNAPSHOT
    if _VA_SNAPSHOT is None:
        return
    for key in list(sys.modules):
        if key == "voice_assistant" or key.startswith("voice_assistant."):
            del sys.modules[key]
    sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None


def _grid(width=24, height=16):
    return [[(x * 10 + y * 7) % 256 for x in range(width)]
            for y in range(height)]


class TestCropGridBytes(unittest.TestCase):
    """Exactness contract, both interpreters (stdlib only)."""

    def test_bytes_exact(self):
        flat, width, height = stored_crop_grayscale_bytes(
            [[0, 128], [255, 64]])
        self.assertEqual(flat, bytes([0, 128, 255, 64]))
        self.assertEqual((width, height), (2, 2))

    def test_bytes_rejects(self):
        for bad in (None, [], [[]], [[1, 2], [3]],
                    [[-1, 2]], [[256, 2]], [[1.5, 2]], [[True, 2]],
                    [[1, 2], "ab"], "not-a-grid", 42):
            self.assertIsNone(
                stored_crop_grayscale_bytes(bad),
                "unusable grid must yield None, never bytes: %r"
                % (bad,))


@unittest.skipUnless(_QT, "PySide6 needed (Qt interpreter)")
class TestC7DialogCropQt(unittest.TestCase):
    """Dialog slot, Qt only (builders need PySide6)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if _QA.instance() is None:
            _QA([])

    def _pump(self):
        _QA.processEvents()

    def _pixmap_gray_bytes(self, pixmap):
        from PySide6.QtGui import QImage, qGray
        image = pixmap.toImage().convertToFormat(
            QImage.Format.Format_Grayscale8)
        vals = []
        for y in range(image.height()):
            for x in range(image.width()):
                vals.append(qGray(image.pixel(x, y)))
        return vals, image.width(), image.height()

    def _builders(self):
        from gui.window_services import ServicesMixin
        return (ServicesMixin._build_confirm_dialog,
                ServicesMixin._build_plain_confirm_dialog)

    def test_helper_pixmap_pixel_exact(self):
        from gui.window_services_confirm import stored_crop_pixmap
        grid = _grid()
        pixmap = stored_crop_pixmap(grid)
        self.assertIsNotNone(pixmap)
        vals, width, height = self._pixmap_gray_bytes(pixmap)
        self.assertEqual((width, height), (24, 16))
        self.assertEqual(vals, [v for row in grid for v in row])

    def test_large_grid_scaled_for_display(self):
        from gui.window_services_confirm import stored_crop_pixmap
        pixmap = stored_crop_pixmap(_grid(width=400, height=10))
        self.assertIsNotNone(pixmap)
        self.assertLessEqual(pixmap.width(), 360)
        self.assertLessEqual(pixmap.height(), 360)

    def test_builders_show_crop_with_exact_pixels(self):
        from PySide6.QtWidgets import QLabel, QWidget
        grid = _grid()
        for builder in self._builders():
            fake = QWidget()
            fake.show()
            self._pump()
            dlg = builder(fake, "cursor_control", "move the pointer",
                          stored_crop=grid)
            dlg.show()
            self._pump()
            label = dlg.findChild(QLabel, "c7CropLabel")
            self.assertIsNotNone(label, "crop label missing")
            vals, width, height = self._pixmap_gray_bytes(
                label.pixmap())
            self.assertEqual((width, height), (24, 16))
            self.assertEqual(vals, [v for row in grid for v in row])
            caption = dlg.findChild(QLabel, "c7CropCaption")
            self.assertIsNotNone(caption, "crop caption missing")
            text = caption.text()
            self.assertIn("Stored", text)
            lowered = text.lower()
            self.assertNotIn("live", lowered)
            self.assertNotIn("current", lowered)
            dlg.close()
            fake.close()

    def test_builders_no_crop_no_image(self):
        from PySide6.QtWidgets import QLabel, QWidget
        for builder in self._builders():
            fake = QWidget()
            fake.show()
            self._pump()
            dlg = builder(fake, "cursor_control", "move the pointer")
            dlg.show()
            self._pump()
            self.assertIsNone(dlg.findChild(QLabel, "c7CropLabel"))
            self.assertIsNone(dlg.findChild(QLabel, "c7CropCaption"))
            dlg.close()
            fake.close()

    def test_builders_unusable_crop_no_image(self):
        from PySide6.QtWidgets import QLabel, QWidget
        for builder in self._builders():
            fake = QWidget()
            fake.show()
            self._pump()
            dlg = builder(fake, "cursor_control", "move the pointer",
                          stored_crop=[[1, 2], [3]])
            dlg.show()
            self._pump()
            self.assertIsNone(dlg.findChild(QLabel, "c7CropLabel"))
            dlg.close()
            fake.close()

    def test_designed_dialog_unchanged_without_crop(self):
        from PySide6.QtWidgets import QPushButton, QWidget
        from gui.window_services import ServicesMixin
        fake = QWidget()
        fake.show()
        self._pump()
        dlg = ServicesMixin._build_confirm_dialog(
            fake, "cursor_control", "move the pointer")
        dlg.show()
        self._pump()
        defaults = [b for b in dlg.findChildren(QPushButton)
                    if b.isDefault()]
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0].text(), "Don't allow")
        self.assertIn("Action: ", fake._confirm_details_label.text())
        dlg.close()
        fake.close()

    def _bound_fake(self):
        from types import MethodType
        from PySide6.QtWidgets import QWidget
        from gui.window_services import ServicesMixin
        fake = QWidget()
        fake.show()
        self._pump()
        fake._build_confirm_dialog = MethodType(
            ServicesMixin._build_confirm_dialog, fake)
        fake._build_plain_confirm_dialog = MethodType(
            ServicesMixin._build_plain_confirm_dialog, fake)
        return fake

    def test_pending_threading_with_crop(self):
        from unittest import mock
        from PySide6.QtWidgets import QDialog, QLabel
        from gui import pending_approval as pa
        captured = []

        def _fake_exec(self):
            captured.append(self)
            return QDialog.DialogCode.Rejected

        fake = self._bound_fake()
        grid = _grid()
        with mock.patch.object(QDialog, "exec", _fake_exec):
            result = pa.present_pending_dialog(
                fake, {"tool": "cursor_control", "label": "click spot",
                       "stored_crop": grid}, timeout_s=0.05)
        self.assertFalse(result)
        self.assertEqual(len(captured), 1)
        label = captured[0].findChild(QLabel, "c7CropLabel")
        self.assertIsNotNone(label, "pending crop not threaded")
        vals, width, height = self._pixmap_gray_bytes(label.pixmap())
        self.assertEqual((width, height), (24, 16))
        self.assertEqual(vals, [v for row in grid for v in row])
        fake.close()

    def test_pending_threading_without_crop(self):
        from unittest import mock
        from PySide6.QtWidgets import QDialog, QLabel
        from gui import pending_approval as pa
        captured = []

        def _fake_exec(self):
            captured.append(self)
            return QDialog.DialogCode.Rejected

        fake = self._bound_fake()
        with mock.patch.object(QDialog, "exec", _fake_exec):
            pa.present_pending_dialog(
                fake, {"tool": "cursor_control", "label": "click spot"},
                timeout_s=0.05)
        self.assertEqual(len(captured), 1)
        self.assertIsNone(captured[0].findChild(QLabel, "c7CropLabel"))
        fake.close()

    def test_pending_old_style_stub_still_works(self):
        # REGRESSION PIN (neighbour run 1): present functions
        # used to call window builders with a stored_crop kwarg
        # unconditionally, which broke every existing stub whose
        # builders take only (tool, summary) — both builders
        # "broke" and present denied. The kwarg now passes only
        # when the record carries a crop, so the plain
        # (tool, summary) call keeps working exactly.
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QPushButton, QWidget
        from gui import pending_approval as pa
        from gui.window_services import ServicesMixin

        class Stub(QWidget):
            def __init__(self):
                super().__init__()
                self.captured = None

            def _build_confirm_dialog(self, tool, summary):
                dlg = ServicesMixin._build_confirm_dialog(
                    self, tool, summary)
                self.captured = dlg
                return dlg

            def _build_plain_confirm_dialog(self, tool, summary):
                dlg = ServicesMixin._build_plain_confirm_dialog(
                    self, tool, summary)
                self.captured = dlg
                return dlg

        window = Stub()
        window.show()
        self._pump()

        def _click():
            for btn in window.captured.findChildren(QPushButton):
                if btn.text() == "Allow once":
                    btn.click()
                    return

        QTimer.singleShot(0, _click)
        self.assertTrue(pa.present_pending_dialog(
            window, {"tool": "menu_action", "label": "Close tab"}))
        window.close()

    def test_pending_with_cancel_threading_with_crop(self):
        from unittest import mock
        from PySide6.QtWidgets import QDialog, QLabel, QPushButton
        from gui import pending_approval as pa
        captured = []

        def _fake_exec(self):
            captured.append(self)
            return QDialog.DialogCode.Rejected

        fake = self._bound_fake()
        with mock.patch.object(QDialog, "exec", _fake_exec):
            result = pa.present_pending_dialog_with_cancel(
                fake, {"tool": "cursor_control", "label": "click spot",
                       "stored_crop": _grid()}, timeout_s=0.05)
        self.assertEqual(result, "deny")
        self.assertEqual(len(captured), 1)
        dlg = captured[0]
        self.assertIsNotNone(dlg.findChild(QLabel, "c7CropLabel"))
        cancels = [b for b in dlg.findChildren(QPushButton)
                   if b.text() == "Cancel task"]
        self.assertEqual(len(cancels), 1)
        fake.close()


if __name__ == "__main__":
    unittest.main()
