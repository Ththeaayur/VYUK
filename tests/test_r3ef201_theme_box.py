"""Round-3 section E, E-F201: the Theme-packs JSON box is a solid
WHITE box on the dark Settings page.

Section-E finding E-F201: Settings -> Appearance -> Theme packs ->
the "Paste theme-pack JSON here..." editor is a solid white
rectangle in the middle of the dark page — the only white surface
anywhere in Settings, placeholder barely-readable grey-on-white.
Cause: a bare QPlainTextEdit (gui/window_settings_advcards.py:116,
self._w9_pack_input) with no stylesheet — the global sheet styles
only the QLineEdit family (gui/style_sheet.py:333, no QPlainTextEdit
rule). The sibling Voice card hit the same trap and was hand-fixed
(gui/window_settings_voice.py:76-79); the Theme-packs box never got
the same treatment. It is the only bare QPlainTextEdit in the
Settings builders.

The fix (E-F201, one product file): the box carries the same
hand-fix the Voice card carries — byte-identical stylesheet
(dark INPUT_BG well, TEXT colour, focus ring), same tokens, so
the two boxes match. Behaviour is untouched: placeholder, maximum
height and text round-trip are exactly as before. The app has ONE
dark chrome — there is no light/day theme in v6.3.0 — so the
variable is width (908 / 1024 / 1280 / 1920).

GUI tests are Qt-only (the test_chat_ui WindowBase shape: the
real MainWindow offscreen), gated the same way as rethink Build
3's TestStripDerivation (skipped on the sys interpreter, which
has no PySide6). One source pin runs on both interpreters: it
reads the product file as text and pins the hand-fix in place,
so the box cannot drift back to bare silently.

Harness trap (also in repro_f201.py): WindowBase's _install_shim
stubs ``voice_assistant.core.memory.memory`` with only
``memory_store`` — no ``MemoryStore`` — so the lazy gui_facade
resolution of ``w9_wiring`` fails during ``_build_adv_cards1``
and the whole Theme-packs card is skipped silently by its
try/except (``_w9_pack_input`` never exists). Every Qt test
therefore synthesizes the ``voice_assistant`` package (the shim's
own first lines) and pre-imports the w9 chain BEFORE
WindowBase.setUp() installs the shim; the already-imported
modules survive the shim in sys.modules and the card builds for
real. Without the pre-import the tests fail on the box missing
for a harness reason, not a product reason.

The boot overlay dims the whole window while it ticks, so the
tests dismiss it with the real user action
(window._skip_boot_sequence(), the Escape skip) before reading
pixels; the box's own paint is overlay-independent either way.
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

# Style tokens (gui/style_tokens.py) — the same values the Voice
# card's hand-fix mirrors from the sheet's input rule.
INPUT_BG = "rgba(0, 0, 0, 96)"
TEXT = "#E6FBFF"


def _pump(app, secs=0.4):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def _mean_luminance(box):
    """Mean luminance of the box's rendered interior, sampled
    below the placeholder line (the bottom half is pure
    background). The dark well reads 0.0 by this method (the
    semi-transparent INPUT_BG over a transparent grab pixmap);
    a bare box reads 255.0."""
    pix = box.grab()
    img = pix.toImage()
    w, h = img.width(), img.height()
    total = 0
    count = 0
    for x in range(int(w * 0.10), int(w * 0.90), 8):
        for y in range(int(h * 0.60), int(h * 0.85), 4):
            c = img.pixelColor(x, y)
            total += (c.red() + c.green() + c.blue()) / 3.0
            count += 1
    return total / count if count else None


class TestBoxInSource(unittest.TestCase):
    """The hand-fix is present in the product source — pinned at
    source level so it runs on both interpreters."""

    def test_pack_box_carries_dark_well_in_source(self):
        src = (ROOT / "gui" / "window_settings_advcards.py").read_text(
            encoding="utf-8")
        self.assertIn("self._w9_pack_input.setStyleSheet(", src)
        self.assertIn("INPUT_BG", src)
        self.assertIn("TEXT", src)

    def test_voice_box_hand_fix_still_present(self):
        src = (ROOT / "gui" / "window_settings_voice.py").read_text(
            encoding="utf-8")
        self.assertIn("self.input_pronunciations.setStyleSheet(", src)
        self.assertIn("INPUT_BG", src)


@unittest.skipUnless(_QT, "Qt-only: the real MainWindow offscreen")
class TestThemePackBox(unittest.TestCase):
    """The Theme-packs box reads dark on the real Settings page."""

    def setUp(self):
        # Harness trap (see module docstring): synthesize the
        # voice_assistant package and pre-import the w9 chain
        # BEFORE WindowBase installs its shim, or the card is
        # skipped silently and the box never exists.
        import types as _types

        _pkg = _types.ModuleType("voice_assistant")
        _pkg.__path__ = [str(ROOT)]
        sys.modules["voice_assistant"] = _pkg
        _core_pkg = _types.ModuleType("voice_assistant.core")
        _core_pkg.__path__ = [str(ROOT / "core")]
        sys.modules["voice_assistant.core"] = _core_pkg
        from voice_assistant.core.gui_facade import w9_wiring  # noqa: F401

        self._base = WindowBase()
        self._base.setUp()
        self.window = self._base.window
        self.app = self._base.app
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — overlay best-effort
            pass
        _pump(self.app, 0.4)
        self.window.resize(1280, 750)
        try:
            self.window._show_page(4)
        except Exception:  # noqa: BLE001 — page switch best-effort
            pass
        _pump(self.app, 0.6)

    def tearDown(self):
        try:
            self._base.tearDown()
        except Exception:  # noqa: BLE001 — teardown best-effort
            pass

    def test_pack_box_present(self):
        from PySide6.QtWidgets import QPlainTextEdit

        box = getattr(self.window, "_w9_pack_input", None)
        self.assertIsInstance(box, QPlainTextEdit)

    def test_pack_box_carries_dark_stylesheet(self):
        box = self.window._w9_pack_input
        self.assertIn(INPUT_BG, box.styleSheet())
        self.assertIn(TEXT, box.styleSheet())

    def test_pack_box_matches_voice_box(self):
        box = self.window._w9_pack_input
        voice = self.window.input_pronunciations
        self.assertEqual(box.styleSheet(), voice.styleSheet())

    def test_pack_box_behaviour_unchanged(self):
        box = self.window._w9_pack_input
        self.assertEqual(box.placeholderText(),
                         "Paste theme-pack JSON here…")
        self.assertEqual(box.maximumHeight(), 90)
        box.setPlainText('{"name": "probe", "accent": "#35D6FF"}')
        self.assertEqual(box.toPlainText(),
                         '{"name": "probe", "accent": "#35D6FF"}')
        box.clear()

    def test_voice_box_unchanged(self):
        voice = self.window.input_pronunciations
        self.assertIn(INPUT_BG, voice.styleSheet())
        self.assertIn(TEXT, voice.styleSheet())

    def test_pack_box_renders_dark_at_widths(self):
        box = self.window._w9_pack_input
        for width in (908, 1024, 1280, 1920):
            with self.subTest(width=width):
                self.window.resize(width, 750)
                try:
                    self.window._show_page(4)
                except Exception:  # noqa: BLE001
                    pass
                _pump(self.app, 0.6)
                lum = _mean_luminance(box)
                self.assertIsNotNone(lum)
                self.assertLess(lum, 60,
                                f"mean luminance {lum} at width {width}")


if __name__ == "__main__":
    unittest.main()
