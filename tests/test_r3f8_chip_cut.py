"""R3F8 (A-UI5): the starter chips are cut — and worse, invisible.

Section-A finding A-UI5 (MINOR, pre-existing): the 'Remind me in 10
min' starter chip is cut at the chat panel's right edge at both
1280x800 and 1024x768. The cut was the visible half. The worse half,
found while reproducing it: the strip was built as a scroll strip —
chips keep their natural size and slide — but nothing could ever
slide it (scrollbar always off, wheel did nothing), AND every
`_refresh_chip_row` rebuild froze the row at the bare margins (4 px
tall) with 0 px chips, because `fit_chips` read the layout's
synchronously stale sizeHint/minimumSize (8 px / 4 px until the
event loop's first layout pass). So after the first reply the
follow-up chips were invisible, not just cut.

The fix (three parts, all in the chip strip):
1. `_ChipStripWheelFilter` — a wheel over the strip slides it
   horizontally (down = right, up = back); installed on the
   viewport AND on each chip, because a wheel over a button never
   leaves the button (QWidget's default wheel handler leaves the
   event accepted, so it never propagates to the scroll area).
2. `fit_chips` computes the row's width AND height from the chips'
   own sizeHints/minimum widths (correct synchronously), not from
   the layout's stale minimumSize/sizeHint — so the row is 508 px
   (3 chips) / 664 px (4 chips) by 39 px immediately after every
   rebuild, and the scroll maximum lets the last chip come fully
   into view.
3. At the scroll limit in the wheel's direction the event is NOT
   swallowed, so it keeps propagating to the chat transcript
   (the _SettingsWheelFilter rule).

GUI tests are Qt-only (the test_chat_ui WindowBase shape: the real
MainWindow offscreen); the source-shape pins run on both
interpreters.
"""
import os
import pathlib
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    WindowBase = unittest.TestCase  # type: ignore[assignment]


def _pump(app, secs=0.6):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def _wheel_over(app, widget, dy):
    """Send a real vertical wheel event over `widget` (dy<0 = down)."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    pos = QPointF(widget.rect().center())
    gpos = widget.mapToGlobal(widget.rect().center())
    ev = QWheelEvent(pos, gpos, QPoint(0, 0), QPoint(0, dy),
                     Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False,
                     Qt.MouseEventSource.MouseEventNotSynthesized)
    QApplication.sendEvent(widget, ev)
    _pump(app, 0.15)


class ChipCutGuiTests(WindowBase):
    """The real MainWindow offscreen, at both window sizes."""

    def _show_chips(self, has_messages, width=1280, height=800):
        self.window.resize(width, height)
        self.window._show_page(0)
        _pump(self.app)
        self.window._chat_has_messages = has_messages
        self.window._refresh_chip_row()
        _pump(self.app)

    def _chips(self):
        from PySide6.QtWidgets import QPushButton
        return [w for w in self.window._suggest_row.findChildren(
            QPushButton)
            if w.objectName() == "chipSuggest" and w.isVisible()]

    @unittest.skipIf(not _QT, "Qt only")
    def test_row_height_after_rebuild_empty(self):
        self._show_chips(False)
        row = self.window._suggest_row
        chips = self._chips()
        self.assertEqual(len(chips), 3)
        margins = row.layout().contentsMargins()
        want_h = (max(c.sizeHint().height() for c in chips)
                  + margins.top() + margins.bottom())
        self.assertEqual(row.height(), want_h)
        self.assertTrue(all(
            c.geometry().height() == c.sizeHint().height()
            for c in chips))

    @unittest.skipIf(not _QT, "Qt only")
    def test_row_height_after_rebuild_msgs(self):
        self._show_chips(True)
        row = self.window._suggest_row
        chips = self._chips()
        self.assertEqual(len(chips), 4)
        margins = row.layout().contentsMargins()
        want_h = (max(c.sizeHint().height() for c in chips)
                  + margins.top() + margins.bottom())
        self.assertEqual(row.height(), want_h)
        self.assertTrue(all(
            c.geometry().height() == c.sizeHint().height()
            for c in chips))

    @unittest.skipIf(not _QT, "Qt only")
    def test_row_width_true_total_immediately(self):
        # No pump between the rebuild and the read: the row must
        # already be the true total (664 px for 4 chips), not the
        # previous state's width (508 px) the stale layout gave.
        self.window.resize(1280, 800)
        self.window._show_page(0)
        _pump(self.app)
        self.window._chat_has_messages = True
        self.window._refresh_chip_row()
        row = self.window._suggest_row
        layout = row.layout()
        # Read the chips from the LAYOUT, not from visible
        # children: synchronously after the rebuild the new chips
        # exist but are not visible yet (the layout pass shows
        # them), so a visibility filter would count zero.
        chips = []
        for i in range(layout.count()):
            wdg = layout.itemAt(i).widget()
            if wdg is not None and wdg.objectName() == "chipSuggest":
                chips.append(wdg)
        self.assertEqual(len(chips), 4)
        margins = layout.contentsMargins()
        want = (margins.left() + margins.right()
                + sum(c.minimumWidth() for c in chips)
                + max(layout.spacing(), 0) * (len(chips) - 1))
        self.assertEqual(row.width(), want)
        self.assertEqual(row.minimumWidth(), want)

    @unittest.skipIf(not _QT, "Qt only")
    def test_wheel_down_slides_right_and_up_back(self):
        self._show_chips(False)
        bar = self.window._chip_scroll.horizontalScrollBar()
        chips = self._chips()
        self.assertGreater(bar.maximum(), 0)
        _wheel_over(self.app, chips[1], -120)
        self.assertGreater(bar.value(), 0)
        down = bar.value()
        _wheel_over(self.app, chips[1], 120)
        self.assertLess(bar.value(), down)

    @unittest.skipIf(not _QT, "Qt only")
    def test_first_and_last_chip_fully_visible(self):
        for has_messages in (False, True):
            with self.subTest(has_messages=has_messages):
                self._show_chips(has_messages)
                scroll = self.window._chip_scroll
                bar = scroll.horizontalScrollBar()
                vp_w = scroll.viewport().width()
                chips = self._chips()
                bar.setValue(0)
                _pump(self.app)
                self.assertGreaterEqual(chips[0].geometry().left(), 0)
                self.assertLessEqual(chips[0].geometry().right(), vp_w)
                bar.setValue(bar.maximum())
                _pump(self.app)
                last = chips[-1]
                self.assertGreaterEqual(
                    last.geometry().left() - bar.value(), 0)
                self.assertLessEqual(
                    last.geometry().right() - bar.value(), vp_w)

    @unittest.skipIf(not _QT, "Qt only")
    def test_wheel_to_the_end_reaches_maximum(self):
        # Repair round (parent-proven): the wheel must drive the
        # bar to its MAXIMUM, not stall a few px short. The first
        # build rejected an overshooting step outright, so a
        # notched wheel stalled at 200 of max 208 (empty state)
        # and the last chip stayed 3 px cut. Drive the wheel
        # until it stops moving and assert the bar is at the
        # maximum AND the last chip is fully visible — in both
        # states, at 1280x800.
        for has_messages in (False, True):
            with self.subTest(has_messages=has_messages):
                self._show_chips(has_messages)
                scroll = self.window._chip_scroll
                bar = scroll.horizontalScrollBar()
                vp_w = scroll.viewport().width()
                chips = self._chips()
                self.assertGreater(bar.maximum(), 0)
                bar.setValue(0)
                _pump(self.app)
                mid = chips[1] if len(chips) > 1 else chips[0]
                for _ in range(40):
                    before = bar.value()
                    _wheel_over(self.app, mid, -120)
                    if bar.value() == before:
                        break
                self.assertEqual(bar.value(), bar.maximum())
                last = chips[-1]
                self.assertGreaterEqual(
                    last.geometry().left() - bar.value(), 0)
                self.assertLessEqual(
                    last.geometry().right() - bar.value(), vp_w)

    @unittest.skipIf(not _QT, "Qt only")
    def test_wheel_at_limit_does_not_move_bar(self):
        # At the scroll limit the wheel is NOT swallowed, so the
        # bar stays put (and the event keeps propagating upward).
        self._show_chips(False)
        bar = self.window._chip_scroll.horizontalScrollBar()
        bar.setValue(bar.maximum())
        _pump(self.app)
        _wheel_over(self.app, self._chips()[1], -120)
        self.assertEqual(bar.value(), bar.maximum())

    @unittest.skipIf(not _QT, "Qt only")
    def test_filter_does_not_swallow_clicks(self):
        # The wheel filter returns False for anything but a
        # translatable wheel event — a press still reaches the
        # chip, so the fix can never eat a tap.
        self._show_chips(False)
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        filt = self.window._chip_wheel_filter
        chip = self._chips()[0]
        ev = QMouseEvent(QEvent.Type.MouseButtonPress,
                         QPointF(chip.rect().center()),
                         Qt.MouseButton.LeftButton,
                         Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        self.assertFalse(filt.eventFilter(chip, ev))
        self.assertIsNotNone(filt)

    @unittest.skipIf(not _QT, "Qt only")
    def test_filter_installed(self):
        self._show_chips(False)
        self.assertTrue(hasattr(self.window, "_chip_wheel_filter"))
        # Import through the voice_assistant shim identity the
        # window was built under — the top-level gui.* import is
        # a DIFFERENT module object (the dual-identity trap:
        # isinstance fails across it even though it is the same
        # class; see AGENTS.md, fix #4 lesson).
        import voice_assistant.gui.window_shell_assistant_widgets as _w
        self.assertIsInstance(self.window._chip_wheel_filter,
                              _w._ChipStripWheelFilter)


class ChipCutSourceTests(unittest.TestCase):
    """Source-shape pins: the wiring, on both interpreters."""

    def test_wheel_filter_wired(self):
        widgets = (ROOT / "gui" / "window_shell_assistant_widgets.py"
                   ).read_text(encoding="utf-8")
        self.assertIn("class _ChipStripWheelFilter", widgets)
        self.assertIn("horizontalScrollBar", widgets)
        shell = (ROOT / "gui" / "window_shell_assistant.py"
                 ).read_text(encoding="utf-8")
        self.assertIn("_ChipStripWheelFilter(self._chip_scroll)", shell)
        self.assertIn("viewport().installEventFilter", shell)

    def test_chips_carry_the_filter(self):
        nav = (ROOT / "gui" / "window_chat_nav.py"
               ).read_text(encoding="utf-8")
        self.assertIn("chip.installEventFilter", nav)
        self.assertIn("_chip_wheel_filter", nav)


if __name__ == "__main__":
    unittest.main()
