"""Round-3 section E, E-F206: System Pulse has a horizontal scrollbar
at the 908 minimum width.

Section-E finding E-F206: Pulse at the 908 window minimum shows a
3 px horizontal scrollbar (viewport 680, page sizeHint 683) — cosmetic,
but a scrollbar for 3 px at the minimum width. Absent at 1024+.

Measured driver (probe_f206_measure.py / probe_geom.py, not assumed):
the footnote QLabel `bars_note` (gui/pulse_page.py) — "Live bars,
sampled once a second while this page is visible. System probes only
— no news or entity cards here." — has NO wordWrap, so its
sizeHint/minimumSizeHint is the full unwrapped text width (611 px).
PulsePage minHint 627 = 611 + 16 (root margins); shell minHint 683 =
627 + 56 (shell margins 28+28). Every meter row is far narrower
(max 285); the Overview panel has no minimum width. The overflow
appears exactly when the VERTICAL scrollbar shows and steals 8 px:
window 908x750 -> viewport 688, hmax=0; window 908x600 (or the
908x500 minimum) -> viewport 680, hmax=3.

The fix (E-F206, one product file, one line): `bars_note` wraps
(`setWordWrap(True)`) instead of forcing the page wide. The note's
minimum drops to its widest word (48 px); the shell minimum drops
683 -> 489; at wide widths the line stays whole (it only wraps
when narrower than the text). Copy, meters, values, plot and the
row behaviour are untouched.

GUI tests are Qt-only (the test_chat_ui WindowBase shape: the
real MainWindow offscreen), gated the same way as the E-F202/E-F203/
E-F205 suites (skipped on the sys interpreter, which has no
PySide6). Two source pins run on both interpreters: the note wraps
and the copy is unchanged, so the footnote cannot silently go back
to unwrapped.
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

NOTE_TEXT = ("Live bars, sampled once a second while this page is visible. "
             "System probes only — no news or entity cards here.")


def _pump(app, secs=0.4):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


class TestNoteWraps(unittest.TestCase):
    """Source pins (both interpreters): the E-F206 footnote wraps
    and its copy is unchanged."""

    def test_bars_note_set_word_wrap(self):
        src = (ROOT / "gui" / "pulse_page.py").read_text(encoding="utf-8")
        self.assertIn("self.bars_note.setWordWrap(True)", src)
        self.assertIn("E-F206", src)

    def test_bars_note_text_unchanged(self):
        src = (ROOT / "gui" / "pulse_page.py").read_text(encoding="utf-8")
        self.assertIn(
            '"Live bars, sampled once a second while this page is visible. "',
            src)
        self.assertIn(
            '"System probes only — no news or entity cards here."', src)


@unittest.skipUnless(_QT, "Qt-only: needs the real MainWindow offscreen")
class TestPulseNoHScroll(WindowBase):
    """The Pulse page fits without a horizontal scrollbar at every
    supported width, tall and short (E-F206)."""

    def _show_pulse(self):
        self.window._show_page(5)  # Pulse (stack: 0 chat 1 act 2 mem 3 sk 4 set 5 pulse)
        _pump(self.app, 0.6)

    def _measure(self):
        from PySide6.QtWidgets import QScrollArea
        scroll = self.window.pages.widget(5)
        self.assertIsInstance(scroll, QScrollArea)
        bar = scroll.horizontalScrollBar()
        shell = scroll.widget()
        return {
            "hmax": bar.maximum(),
            "viewport": scroll.viewport().width(),
            "vbar": scroll.verticalScrollBar().isVisible(),
            "shell_min": shell.minimumSizeHint().width(),
            "note_min": self.window.pulse_page.bars_note.minimumSizeHint().width(),
        }

    def test_no_hscroll_908_short_filed_condition(self):
        # The filed condition: 908 wide AND short enough that the
        # vertical scrollbar shows (viewport 680). Pre-fix hmax=3.
        self._show_pulse()
        self.window.resize(908, 600)
        _pump(self.app, 0.6)
        m = self._measure()
        self.assertTrue(m["vbar"], "vertical scrollbar must show here")
        self.assertEqual(m["viewport"], 680)
        self.assertEqual(m["hmax"], 0, m)

    def test_no_hscroll_all_widths_tall_and_short(self):
        self._show_pulse()
        for width in (908, 1024, 1280, 1920):
            for height in (750, 600):
                self.window.resize(width, height)
                _pump(self.app, 0.6)
                m = self._measure()
                self.assertEqual(
                    m["hmax"], 0,
                    f"{width}x{height}: hmax={m['hmax']} "
                    f"viewport={m['viewport']} vbar={m['vbar']}")

    def test_note_wraps_and_page_min_fits(self):
        self._show_pulse()
        self.window.resize(908, 600)
        _pump(self.app, 0.6)
        pulse = self.window.pulse_page
        self.assertTrue(pulse.bars_note.wordWrap())
        self.assertEqual(pulse.bars_note.text(), NOTE_TEXT)
        m = self._measure()
        self.assertLessEqual(m["shell_min"], 680, m)
        self.assertLess(m["note_min"], 100, m)

    def test_rows_and_values_intact(self):
        # The fix touches nothing but the footnote: rows, meters
        # and live values behave exactly as before.
        self._show_pulse()
        pulse = self.window.pulse_page
        keys = [r.key for r in pulse._rows]
        self.assertEqual(keys, ["cpu", "mem", "disk", "net",
                                "battery", "uptime", "procs"])
        self.assertEqual(len(pulse.dials), 5)
        pulse.sample_now()
        _pump(self.app, 0.3)
        for row in pulse._rows:
            self.assertNotEqual(row.value_label.text(), "")


if __name__ == "__main__":
    unittest.main()
