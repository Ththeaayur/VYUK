"""Round-3 section E, E-F210: Senses "Network quality" value wraps to
two lines at 908 px ("Not / measured"). Cosmetic.

Filed shape (section E, slice 2): resize to 908 wide -> Senses ->
the four state cards -> the Network quality value reads "Not /
measured" on two lines.

Mechanism (measured on the live tree, not assumed): the four state
cards share a 4-column grid at page widths >= 600 (D8 ruling,
gui/senses_page.py _relayout_state). At a 908 px window the page is
632 px wide, the grid 608 px, each card ~141 px, and the value
label inside it 115 px (12 px card margins each side). The value
face is 18 px semibold: "Not measured" advances 130 px -- 15 px
wider than the label -- so the wrapping label breaks it at the
space (label 46 px tall, two lines). At 1024+ the label is 144+
px and it fits on one line, so the wrap fires ONLY at 908. The
other quality states fit at 908 ("Measuring\u2026" 110 px,
"Unreachable" 116 px -- a single word, so it cannot wrap --
verdicts shorter), and the sibling cards' default values
("Ethernet" 79 px, "\u2014", "Idle") all fit.

THE FIX (1 line, copy only): the quality card's unmeasured value
is "No reading" (102 px, 13 px margin at 908; "reading" is the
page's own vocabulary -- "every reading arrives through a
provider", "no reading that hour"). No layout, font, or wrap
change: the same-phrase shortening "Unmeasured" was measured and
rejected (116 px against a 115 px label -- a coin flip, not a
fix), and the grid spacing plus card margins together could gain
at most ~10 px, still short of the 15 px needed. The value label
keeps wordWrap(True): long names (a Wi-Fi SSID, a "Camera: ..."
line) still wrap by design.

GUI tests are Qt-only (the real MainWindow offscreen), gated the
same way as the E-F202/E-F203/E-F205/E-F206/E-F207/E-F208/E-F209
suites (skipped on the sys interpreter, which has no PySide6).
Source pins run on both interpreters.

HARNESS TRAP (E-F201 lesson): WindowBase's shim stubs
voice_assistant.core.memory.memory with only memory_store, so the
lazy facade resolution behind w9_wiring_proof fails at import and
_build_pulse_page silently skips the Overview panel. The Qt tests
synthesize the package and pre-import w9_wiring_proof BEFORE
WindowBase.setUp(); the already-imported modules survive the shim.
(The Senses page itself does not need the panel, but the boot
path does -- without the pre-import the window never finishes
building and senses_page reads None.)
"""

import os
import pathlib
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False

SENSES = ROOT / "gui" / "senses_page.py"


class TestFixInSource(unittest.TestCase):
    """The fix is present in the product source — pins run on
    both interpreters."""

    def test_quality_default_is_no_reading(self):
        src = SENSES.read_text(encoding="utf-8")
        self.assertIn('setText("No reading")', src)
        self.assertNotIn('setText("Not measured")', src)

    def test_fix_comment_names_the_measurement(self):
        src = SENSES.read_text(encoding="utf-8")
        self.assertIn("E-F210", src)
        self.assertIn("102 px", src)
        self.assertIn("115 px", src)

    def test_other_quality_states_and_wrap_untouched(self):
        src = SENSES.read_text(encoding="utf-8")
        self.assertIn('setText("Measuring\u2026")', src)
        self.assertIn('label.setText("Unreachable")', src)
        # The fix is copy only: long names still wrap by design.
        self.assertIn("self.value_label.setWordWrap(True)", src)


def _pump(app, seconds=0.4):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def _preimport_w9():
    """Synthesize the voice_assistant package and pre-import the
    w9 chain BEFORE WindowBase installs its shim (see module
    docstring)."""
    import sys
    import types as _types

    _pkg = _types.ModuleType("voice_assistant")
    _pkg.__path__ = [str(ROOT)]
    sys.modules["voice_assistant"] = _pkg
    _core_pkg = _types.ModuleType("voice_assistant.core")
    _core_pkg.__path__ = [str(ROOT / "core")]
    sys.modules["voice_assistant.core"] = _core_pkg
    _gui_pkg = _types.ModuleType("voice_assistant.gui")
    _gui_pkg.__path__ = [str(ROOT / "gui")]
    sys.modules["voice_assistant.gui"] = _gui_pkg
    from voice_assistant.gui import w9_wiring_proof  # noqa: F401


@unittest.skipUnless(_QT, "Qt-only: the real MainWindow offscreen")
class TestQualityValueOneLine(unittest.TestCase):
    """The Network quality value reads on one line at every width,
    in every state, with the sibling cards unchanged."""

    def setUp(self):
        _preimport_w9()
        self._base = WindowBase()
        self._base.setUp()
        self.window = self._base.window
        self.app = self._base.app
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — overlay best-effort
            pass
        _pump(self.app)
        self.window._show_page(6)  # Senses
        _pump(self.app, 0.6)
        self.senses = self.window.senses_page
        self.assertIsNotNone(self.senses, "Senses page never built")

    def tearDown(self):
        try:
            self._base.tearDown()
        except Exception:  # noqa: BLE001 — teardown best-effort
            pass

    def _one_line(self, label):
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(label.font())
        return label.height() <= fm.height() + 2  # 24 px; 1 line 23

    def _resize(self, width):
        self.window.resize(width, 700)
        _pump(self.app, 0.5)
        self.senses._relayout_state(self.senses.width())
        _pump(self.app, 0.2)

    def test_quality_one_line_at_908(self):
        self._resize(908)
        qlabel = self.senses.state_labels["quality"]
        self.assertEqual(qlabel.text(), "No reading")
        self.assertTrue(self._one_line(qlabel),
                        f"value_h={qlabel.height()} at 908")

    def test_quality_one_line_at_other_widths(self):
        qlabel = self.senses.state_labels["quality"]
        for width in (1024, 1280, 1920):
            self._resize(width)
            self.assertEqual(qlabel.text(), "No reading")
            self.assertTrue(self._one_line(qlabel),
                            f"value_h={qlabel.height()} at {width}")

    def test_quality_states_one_line_at_908(self):
        self._resize(908)
        qlabel = self.senses.state_labels["quality"]
        for text in ("Measuring\u2026", "Unreachable", "Good",
                     "Measured", "\u2014"):
            qlabel.setText(text)
            _pump(self.app, 0.2)
            self.assertTrue(self._one_line(qlabel),
                            f"{text!r} wraps at 908: "
                            f"value_h={qlabel.height()}")

    def test_sibling_cards_unchanged_at_908(self):
        self._resize(908)
        # UI Phase 4 (D-P4-12): the privacy probe cannot run off
        # Windows, so the leg reads "—" (couldn't check) instead
        # of the pre-Phase-4 "Idle" default; the connection and
        # battery legs are unchanged.
        for key, want in (("connection", "Ethernet"),
                          ("battery", "\u2014"),
                          ("privacy", "\u2014")):
            lab = self.senses.state_labels[key]
            self.assertEqual(lab.text(), want)
            self.assertTrue(self._one_line(lab),
                            f"{key} wraps at 908: value_h={lab.height()}")


if __name__ == "__main__":
    unittest.main()
