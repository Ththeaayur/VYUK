"""Round-3 section E, E-F103: the composer placeholder is truncated
to "Type, or" at every width — the mic instruction is never visible.

Section-E finding E-F103: the composer placeholder read "Type, or"
at every width (908/1024/1280/1920), day and night — the full text
is "Type, or hold the mic to speak", but the docked panel is too
narrow to show it. The mechanism, measured on the real MainWindow
offscreen (Sora 13 px, the bundled face): the placeholder is a
QTextEdit placeholder, so it wraps at the input's visible text
width and the fixed single-line height hides every line after the
first. The full copy is 195 px; the input shows ~106 px at the
400 px default panel and ~70 px at the 300 px panel floor — so
the first wrapped line is "Type, or" (or "Type, or hold" at 400),
and the mic instruction never reads. The full copy cannot fit at
908, measured, not assumed: the panel is forced to its 300 px
floor there (stage at its 320 floor, sidebar fixed), and the
input sits at its 120 px minimum.

The fix (E-F103, two product files):
1. The copy shortens to "Type, or hold mic" (112 px) — both
   actions survive, and the mic instruction survives ("hold mic"
   is the mic button's own hold-to-talk wording). "To speak" is
   the only loss, and it is redundant: holding the mic IS
   speaking, and the Home wake-word line already says "use the
   mic button".
2. The narrow rule gives the input room: screen-share hides
   below 420 px panel width (was 380), so at the 400 px default
   panel the input grows from 156 to 210 px (text 106 -> 160);
   the action stays in the header overflow menu (same handler,
   same menu entry). Dictate hides entirely below 380 px panel
   width, so at the 300 px floor the input grows from 120 to
   176 px (text 70 -> 126); dictation stays reachable via Ctrl+D
   (an event-filter shortcut, independent of the button), and the
   mic button — the primary voice control — stays visible.
   The shortened copy then reads whole at every width and every
   panel width: 14 px of margin at the tightest (908 / 300 px
   panel), 48 px at the default (1280 / 400 px panel).

GUI tests are Qt-only (the test_chat_ui WindowBase shape: the
real MainWindow offscreen); the source-shape pins run on both
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

_FULL = "Type, or hold mic"
_OLD = "Type, or hold the mic to speak"


def _pump(app, secs=0.6):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def _first_line(fm, text, text_w):
    """Word-wrap simulation: the first line Qt lays out for the
    placeholder inside text_w px."""
    words = text.split(" ")
    line = ""
    for word in words:
        cand = word if not line else line + " " + word
        if fm.horizontalAdvance(cand) <= text_w:
            line = cand
        else:
            break
    return line


class PlaceholderGuiTests(WindowBase):
    """The real MainWindow offscreen, at every width the window
    supports, at the default and dragged-narrow panel widths."""

    def _show_composer(self, width=1280, height=750, panel=400):
        self.window.resize(width, height)
        self.window._show_page(0)
        _pump(self.app)
        split = getattr(self.window, "_chat_splitter", None)
        if split is not None:
            split.setSizes([max(320, width - panel - 12), panel])
        _pump(self.app)
        # The pin alone does not re-run the narrow rule in this
        # harness (no resizeEvent fires when the size is
        # unchanged, and setSizes emits no splitterMoved) — a real
        # drag calls the rule from _on_chat_splitter_moved with
        # the live width, so run it explicitly here with the
        # settled geometry.
        self.window._apply_dock_narrow_rule()
        _pump(self.app, 0.3)

    def _assert_placeholder_fits(self):
        from PySide6.QtGui import QFontMetrics
        edit = self.window.input_cmd
        fm = QFontMetrics(edit.font())
        full = edit.placeholderText()
        text_w = (edit.viewport().width()
                  - 2 * edit.document().documentMargin())
        self.assertEqual(full, _FULL)
        self.assertLessEqual(fm.horizontalAdvance(full), text_w,
                             f"text_w={text_w:.0f} "
                             f"advance={fm.horizontalAdvance(full)}")
        self.assertEqual(_first_line(fm, full, text_w), full)

    @unittest.skipIf(not _QT, "Qt only")
    def test_placeholder_fits_at_every_width(self):
        # The panel the splitter actually gives (300 px at 908,
        # not the asked 400) is what the assertions read — the
        # asked width is only the request.
        for width in (908, 1024, 1280, 1920):
            for panel in (400, 320, 300):
                with self.subTest(width=width, panel=panel):
                    self._show_composer(width=width, panel=panel)
                    self._assert_placeholder_fits()

    @unittest.skipIf(not _QT, "Qt only")
    def test_narrow_rule_button_states(self):
        # At the 400 px default panel: screen-share hidden (the
        # E-F103 threshold move), Dictate visible and icon-only.
        self._show_composer(width=1280, panel=400)
        self.assertFalse(self.window.btn_screen.isVisible())
        self.assertTrue(self.window._btn_dictate.isVisible())
        self.assertEqual(self.window._btn_dictate.text(), "")
        # At the 300 px panel floor: Dictate hidden too, so the
        # input keeps its room; the mic button stays visible.
        self._show_composer(width=1280, panel=300)
        self.assertFalse(self.window._btn_dictate.isVisible())
        self.assertTrue(self.window.btn_mic.isVisible())
        # Dragged wide again: screen-share comes back (the button
        # is hidden, not deleted, and the overflow menu keeps the
        # same action at every width).
        self._show_composer(width=1280, panel=500)
        self.assertTrue(self.window.btn_screen.isVisible())

    @unittest.skipIf(not _QT, "Qt only")
    def test_home_frozen_control(self):
        # The v6.3 Home layout stays frozen: the orb is visible
        # and prominent, whatever the composer does below it.
        self._show_composer()
        orb = getattr(self.window, "orb", None) or getattr(
            self.window, "visualizer", None)
        self.assertIsNotNone(orb)
        self.assertTrue(orb.isVisible())
        self.assertGreaterEqual(orb.geometry().width(), 170)


class PlaceholderSourceTests(unittest.TestCase):
    """Source-shape pins: the shortened copy and the narrowed
    rule, on both interpreters."""

    def test_shortened_copy_present(self):
        shell = (ROOT / "gui" / "window_shell_assistant.py"
                 ).read_text(encoding="utf-8")
        self.assertIn(f'"{_FULL}"', shell)
        self.assertNotIn(_OLD, shell)

    def test_narrow_thresholds(self):
        frame = (ROOT / "gui" / "window_shell_frame.py"
                 ).read_text(encoding="utf-8")
        # E-F103: screen-share hides below 420 (was 380); Dictate
        # hides entirely below 380 (new).
        self.assertIn("_DOCK_NARROW_WIDTH = 420", frame)
        self.assertIn("_DICTATE_HIDE_WIDTH = 380", frame)
        self.assertNotIn("_DOCK_NARROW_WIDTH = 380", frame)
        self.assertIn("dictate.setVisible(panel_w "
                      ">= _DICTATE_HIDE_WIDTH", frame)

    def test_dictate_shortcut_independent_of_button(self):
        # Ctrl+D toggles dictation through the event filter, not
        # through the button — hiding the button below 380 px
        # does not take dictation away from keyboard users.
        bubbles = (ROOT / "gui" / "window_chat_bubbles.py"
                   ).read_text(encoding="utf-8")
        self.assertIn("Qt.Key.Key_D", bubbles)
        self.assertIn("self._chat_toggle_dictation()", bubbles)


if __name__ == "__main__":
    unittest.main()
