"""Round-3 section E, E-F101: the starter/suggestion chips are cut
mid-word at the right window edge.

Section-E finding E-F101: the chips row in the docked chat panel
was cut mid-word at the right edge — `Remind me in 10 mi[n]`,
`What's on my scree…` — at every width (908/1024/1280/1920), day
and night, both chip sets. The row was a QHBoxLayout sized to the
chips' true total (508 px for three starters, 664 for four
suggestions) inside a fixed-height scroll strip, so the later
chips ran past the 300–400 px panel with no visible affordance.

The fix (E-F101, two files):
1. `_ChipFlowLayout` — the row wraps onto a second line when the
   next chip would pass the row width, each line centred (the
   pre-fix look kept per line). A chip never squeezes mid-word:
   `fit_chips` still sets each chip's minimum width from live
   font metrics, but the row no longer sizes itself to the
   chips' total.
2. `_ChipScrollArea` — the strip's scroll area, now
   widgetResizable, and a heightForWidth widget: its height for
   a given width is the row's wrapped height at the viewport
   width, so the strip stands exactly as tall as the lines the
   chips need (one line where they fit, two where they wrap).
   The fix-#8 wheel filter stays installed and wired; with
   nothing to scroll (bar maximum 0) it passes the wheel
   through to the transcript.

GUI tests are Qt-only (the test_chat_ui WindowBase shape: the
real MainWindow offscreen); the source-shape pins run on both
interpreters.
"""
import ast
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

_STARTERS = ["What can you do?", "Remind me in 10 min",
             "What's on my screen?"]
_SUGGESTIONS = ["What can you do?", "What's on my screen?",
                "Take a screenshot", "Remind me in 10 min"]


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


def _settle_room(window, hour):
    """Slice-1 driver _settle_room: force the room light to its
    converged day/night wash. Synchronous."""
    window._room_hour = hour
    window._tick = 0
    for _ in range(30):
        window._tick += 1
        window._sync_room_light(0.0)


class ChipsWrapGuiTests(WindowBase):
    """The real MainWindow offscreen, at every width the window
    supports, both chip sets, day and night."""

    def _show_chips(self, has_messages, width=1280, height=750,
                    panel=400, hour=14):
        self.window.resize(width, height)
        self.window._show_page(0)
        _pump(self.app)
        split = getattr(self.window, "_chat_splitter", None)
        if split is not None:
            split.setSizes([max(320, width - panel - 12), panel])
        _pump(self.app)
        _settle_room(self.window, hour)
        _pump(self.app, 0.3)
        self.window._chat_has_messages = has_messages
        self.window._refresh_chip_row()
        _pump(self.app)

    def _chips(self):
        # Read the chips from the LAYOUT, not from visible
        # children: synchronously after the rebuild the new chips
        # exist but are not visible yet (the layout pass shows
        # them), so a visibility filter would count zero — and
        # hidden chips pending deleteLater would be counted
        # twice by findChildren.
        layout = self.window._suggest_row.layout()
        out = []
        for i in range(layout.count()):
            wdg = layout.itemAt(i).widget()
            if wdg is not None and wdg.objectName() == "chipSuggest":
                out.append(wdg)
        return out

    def _assert_all_whole(self, has_messages):
        from PySide6.QtGui import QFontMetrics
        scroll = self.window._chip_scroll
        row = self.window._suggest_row
        bar = scroll.horizontalScrollBar()
        vp_w = scroll.viewport().width()
        chips = self._chips()
        # Nothing to scroll: every chip is visible at rest.
        self.assertEqual(bar.maximum(), 0)
        self.assertEqual(row.width(), vp_w)
        self.assertEqual(row.height(),
                         row.layout().heightForWidth(vp_w))
        for c in chips:
            g = c.geometry()
            fm = QFontMetrics(c.font())
            need = fm.horizontalAdvance(c.text()) + 2 * (14 + 1)
            self.assertGreaterEqual(g.left(), 0, c.text())
            self.assertLessEqual(g.right(), vp_w, c.text())
            self.assertLessEqual(g.bottom(), row.height(), c.text())
            self.assertGreaterEqual(c.width(), need, c.text())

    @unittest.skipIf(not _QT, "Qt only")
    def test_all_chips_whole_at_every_width(self):
        # Day only; the night leg is test_all_chips_whole_day_
        # and_night below. The panel the splitter actually
        # gives (300 px at 908, not the asked 400) is what the
        # assertions read — the asked width is only the request.
        for width in (908, 1024, 1280, 1920):
            for panel in (400, 320):
                for has_messages in (False, True):
                    with self.subTest(width=width, panel=panel,
                                      has_messages=has_messages):
                        self._show_chips(
                            has_messages, width=width, panel=panel)
                        self._assert_all_whole(has_messages)

    @unittest.skipIf(not _QT, "Qt only")
    def test_all_chips_whole_day_and_night(self):
        for width, panel in ((1280, 400), (908, 320)):
            for hour in (14, 23):
                for has_messages in (False, True):
                    with self.subTest(width=width, panel=panel,
                                      hour=hour,
                                      has_messages=has_messages):
                        self._show_chips(
                            has_messages, width=width, panel=panel,
                            hour=hour)
                        self._assert_all_whole(has_messages)

    @unittest.skipIf(not _QT, "Qt only")
    def test_strip_height_follows_wrapped_content(self):
        # 1280 / 400 px panel: the starters wrap to two lines
        # (146 + 166 on line one, 172 alone on line two) and the
        # strip stands exactly two lines tall — 82 px, not the
        # old fixed 42 and not the stacked 125.
        self._show_chips(False, width=1280, panel=400)
        row = self.window._suggest_row
        lines = len({c.geometry().top() for c in self._chips()})
        self.assertEqual(lines, 2)
        self.assertEqual(row.height(), 82)
        # A 700 px panel fits all four suggestions on one line:
        # the strip collapses back to a single line, 39 px.
        self._show_chips(True, width=1920, panel=700)
        row = self.window._suggest_row
        lines = len({c.geometry().top() for c in self._chips()})
        self.assertEqual(lines, 1)
        self.assertEqual(row.height(), 39)
        # The squeezed 908 window (300 px panel): one chip per
        # line — no two chips fit side by side — and the strip
        # grows to hold them all (3 lines starters, 4 lines
        # suggestions).
        self._show_chips(False, width=908, panel=320)
        row = self.window._suggest_row
        lines = len({c.geometry().top() for c in self._chips()})
        self.assertEqual(lines, 3)
        self.assertEqual(row.height(), 125)
        self._show_chips(True, width=908, panel=320)
        row = self.window._suggest_row
        lines = len({c.geometry().top() for c in self._chips()})
        self.assertEqual(lines, 4)
        self.assertEqual(row.height(), 168)

    @unittest.skipIf(not _QT, "Qt only")
    def test_chip_texts_unchanged(self):
        for has_messages, want in ((False, _STARTERS),
                                   (True, _SUGGESTIONS)):
            with self.subTest(has_messages=has_messages):
                self._show_chips(has_messages)
                got = [c.text() for c in self._chips()]
                self.assertEqual(got, want)

    @unittest.skipIf(not _QT, "Qt only")
    def test_wheel_passes_through_at_rest(self):
        # Nothing to scroll, so a wheel over a chip leaves the
        # bar at 0 (and the event keeps propagating to the
        # transcript, the fix-#8 at-limit rule).
        self._show_chips(False)
        bar = self.window._chip_scroll.horizontalScrollBar()
        self.assertEqual(bar.maximum(), 0)
        _wheel_over(self.app, self._chips()[1], -120)
        self.assertEqual(bar.value(), 0)
        _wheel_over(self.app, self._chips()[1], 120)
        self.assertEqual(bar.value(), 0)

    @unittest.skipIf(not _QT, "Qt only")
    def test_home_frozen_control(self):
        # The v6.3 Home layout stays frozen: the orb is visible
        # and prominent, whatever the chip strip does below it.
        self._show_chips(False)
        orb = getattr(self.window, "orb", None) or getattr(
            self.window, "visualizer", None)
        self.assertIsNotNone(orb)
        self.assertTrue(orb.isVisible())
        self.assertGreaterEqual(orb.geometry().width(), 170)


class ChipsWrapSourceTests(unittest.TestCase):
    """Source-shape pins: the wrap shape, on both interpreters."""

    def test_wrap_layout_present(self):
        widgets = (ROOT / "gui" / "window_shell_assistant_widgets.py"
                   ).read_text(encoding="utf-8")
        self.assertIn("class _ChipFlowLayout", widgets)
        self.assertIn("def heightForWidth", widgets)
        self.assertIn("class _ChipScrollArea(QScrollArea)", widgets)
        shell = (ROOT / "gui" / "window_shell_assistant.py"
                 ).read_text(encoding="utf-8")
        self.assertIn("_ChipFlowLayout(self._suggest_row)", shell)
        self.assertIn("self._chip_scroll = _ChipScrollArea()", shell)
        self.assertIn("setWidgetResizable(True)", shell)
        self.assertNotIn("setFixedHeight(42)", shell)
        self.assertNotIn("setWidgetResizable(False)", shell)

    def test_no_row_width_forcing(self):
        # fit_chips must not size the row to the chips' total
        # any more — that forcing (508/664 px) is what cut the
        # later chips. The height comes from the flow layout's
        # heightForWidth, reached through updateGeometry().
        src = (ROOT / "gui" / "window_shell_assistant_widgets.py"
               ).read_text(encoding="utf-8")
        tree = ast.parse(src)
        func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) \
                    and node.name == "fit_chips":
                func = node
                break
        self.assertIsNotNone(func)
        segment = ast.get_source_segment(src, func)
        self.assertNotIn("self.resize(", segment)
        self.assertNotIn("setMinimumWidth(total)", segment)
        self.assertNotIn("self.height()", segment)
        self.assertIn("self.updateGeometry()", segment)


if __name__ == "__main__":
    unittest.main()
