"""Round-3 section E, E-F209: Pulse meters, the Overview line and
the Senses 24 h bars stay cyan under the Sunset theme.

The theme system only regenerates the QSS, so the three surfaces
painted from the fixed house tokens (gui/style_tokens.py: ACCENT
"#35D6FF", ACCENT_2 "#3D8BFF") never followed the theme, and
PulsePage.set_palette(highlight, accent) existed but had no callers.
Measured in the live tree: the Pulse BarMeters use ACCENT for
cpu/mem/disk and ACCENT_2 for net/battery; the Senses _TrendChart
paintEvent uses ACCENT with no setter at all; the Overview fallback
plot paints its first series with ACCENT_2.

THE FIX (5 files, no new theming system, Cyan byte-identical):
- gui/pulse_page.py: set_palette redefined as (accent, accent_2)
  — the old (highlight, accent) mapping could not fix the finding
  (CPU would have gone pale under Cyan, Disk/Battery would have
  stayed cyan under every theme). Families preserved.
- gui/senses_page.py: _TrendChart gains set_bar_color (default
  ACCENT); SensesPage.set_palette keeps its (highlight, accent)
  signature and forwards accent to both charts.
- gui/overview_panel.py: the first series takes theme accent_2
  (today's colour under Cyan); AMBER/OK keep semantic colours.
  set_palette(accent, accent_2) + set_series_color on the fallback.
- gui/window_settings_appearance.py: _apply_theme_to_painted_pages
  resolves the same theme the chrome just took and drives the
  three set_palette hooks, called from _apply_appearance after
  setStyleSheet and at the end of the Pulse/Senses builders
  (gui/window_pages_system.py) — the load-time call can run before
  the pages exist.

The disk meter's per-reading heat override (DANGER/AMBER at high
fractions) still wins over the base colour, and the DurationStrip
is out of scope (caller-supplied segment colours).

GUI tests are Qt-only (the real MainWindow offscreen), gated the
same way as the E-F202/E-F203/E-F205/E-F206/E-F207/E-F208 suites
(skipped on the sys interpreter, which has no PySide6). Source
pins run on both interpreters.

HARNESS TRAP (E-F201 lesson, bites the Overview panel too):
WindowBase's shim stubs voice_assistant.core.memory.memory with
only memory_store, so the lazy facade resolution behind
w9_wiring_proof fails at import and _build_pulse_page silently
skips the Overview panel. The Qt tests synthesize the package and
pre-import w9_wiring_proof BEFORE WindowBase.setUp(); the
already-imported modules survive the shim and the panel builds
for real. Without the pre-import every overview assertion reads
None and the test fails on a missing panel, not on a colour.
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

PULSE = ROOT / "gui" / "pulse_page.py"
SENSES = ROOT / "gui" / "senses_page.py"
OVERVIEW = ROOT / "gui" / "overview_panel.py"
APPEARANCE = ROOT / "gui" / "window_settings_appearance.py"
BUILDERS = ROOT / "gui" / "window_pages_system.py"
TOKENS = ROOT / "gui" / "style_tokens.py"
THEME = ROOT / "gui" / "style_theme.py"

CYAN = "#35D6FF"
CYAN_2 = "#3D8BFF"
SUNSET = "#FF9A7A"
SUNSET_2 = "#FF7EB6"


def _func_args(path, name):
    """Arg names of the first function called `name` in `path`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return [a.arg for a in node.args.args]
    return None


class TestFixInSource(unittest.TestCase):
    """The fix is present in the product source — pins run on
    both interpreters."""

    def test_cyan_house_tokens_unchanged(self):
        src = TOKENS.read_text(encoding="utf-8")
        self.assertIn('ACCENT = "%s"' % CYAN, src)
        self.assertIn('ACCENT_2 = "%s"' % CYAN_2, src)

    def test_pulse_set_palette_takes_accent_pair(self):
        self.assertEqual(
            _func_args(PULSE, "set_palette"),
            ["self", "accent", "accent_2"])
        src = PULSE.read_text(encoding="utf-8")
        self.assertIn("self.dials[key].set_base_color(accent)", src)
        self.assertIn("self.dials[key].set_base_color(accent_2)", src)

    def test_senses_charts_take_bar_colour(self):
        self.assertEqual(
            _func_args(SENSES, "set_bar_color"),
            ["self", "color"])
        src = SENSES.read_text(encoding="utf-8")
        self.assertIn("self._bar_color", src)
        self.assertIn(
            "for chart in (self.noise_chart, self.light_chart):", src)
        self.assertIn("chart.set_bar_color(accent)", src)

    def test_overview_set_palette_and_series_colour(self):
        self.assertEqual(
            _func_args(OVERVIEW, "set_palette"),
            ["self", "accent", "accent_2"])
        src = OVERVIEW.read_text(encoding="utf-8")
        self.assertIn("def set_series_color(self, color: str)", src)
        self.assertIn("self._series_color", src)

    def test_hook_wires_painted_pages(self):
        src = APPEARANCE.read_text(encoding="utf-8")
        self.assertIn("def _apply_theme_to_painted_pages", src)
        self.assertIn("pulse.set_palette(accent, accent_2)", src)
        self.assertIn("overview.set_palette(accent, accent_2)", src)
        self.assertIn("senses.set_palette(highlight, accent)", src)
        self.assertIn("_apply_theme_to_painted_pages(theme_arg)", src)
        built = BUILDERS.read_text(encoding="utf-8")
        self.assertEqual(
            built.count("self._apply_theme_to_painted_pages()"), 2)


def _pump(app, seconds=0.4):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def _preimport_w9():
    """Synthesize the voice_assistant package and pre-import the
    w9 chain BEFORE WindowBase installs its shim (see module
    docstring — without this the Overview panel never builds)."""
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


def _meters(window):
    dials = window.pulse_page.dials  # noqa: SLF001 — probe
    return {k: dials[k]._base_color.name().upper()  # noqa: SLF001
            for k in ("cpu", "mem", "disk", "net", "battery")}


def _charts(window):
    senses = window.senses_page
    # _bar_color is stored as a plain hex string, not a QColor.
    return {
        "noise_chart": str(senses.noise_chart._bar_color).upper(),  # noqa: SLF001
        "light_chart": str(senses.light_chart._bar_color).upper(),  # noqa: SLF001
    }


def _overview(window):
    panel = getattr(window, "_w9_overview_panel", None)
    assert panel is not None, "Overview panel never built (pre-import?)"
    # _series_color is stored as a plain hex string, not a QColor.
    return str(panel._series_color).upper()  # noqa: SLF001


@unittest.skipUnless(_QT, "Qt-only: the real MainWindow offscreen")
class TestAccentFollowsTheme(unittest.TestCase):
    """Live theme switches recolour all three surfaces."""

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
        self.window.resize(1280, 750)
        try:
            self.window._show_page(5)
        except Exception:  # noqa: BLE001 — page switch best-effort
            pass
        _pump(self.app, 0.6)
        # Start from the known default so every test is
        # order-independent (QSettings path is cached per process).
        self.window._on_theme_selected("Cyan")
        _pump(self.app, 0.4)

    def tearDown(self):
        try:
            self.window._on_theme_selected("Cyan")
        except Exception:  # noqa: BLE001 — restore best-effort
            pass
        try:
            self._base.tearDown()
        except Exception:  # noqa: BLE001 — teardown best-effort
            pass

    def test_sunset_switch_recolours_all_three_surfaces(self):
        self.window._on_theme_selected("Sunset")
        _pump(self.app, 0.5)
        self.assertEqual(_meters(self.window), {
            "cpu": SUNSET, "mem": SUNSET, "disk": SUNSET,
            "net": SUNSET_2, "battery": SUNSET_2})
        self.assertEqual(_charts(self.window), {
            "noise_chart": SUNSET, "light_chart": SUNSET})
        self.assertEqual(_overview(self.window), SUNSET_2)

    def test_cyan_is_byte_identical_to_today(self):
        self.window._on_theme_selected("Sunset")
        _pump(self.app, 0.5)
        self.window._on_theme_selected("Cyan")
        _pump(self.app, 0.5)
        self.assertEqual(_meters(self.window), {
            "cpu": CYAN, "mem": CYAN, "disk": CYAN,
            "net": CYAN_2, "battery": CYAN_2})
        self.assertEqual(_charts(self.window), {
            "noise_chart": CYAN, "light_chart": CYAN})
        self.assertEqual(_overview(self.window), CYAN_2)

    def test_heat_override_still_wins_over_base_colour(self):
        # The per-reading override (the disk heat shift's shape:
        # set_reading with an explicit colour) still wins at paint
        # time, and clearing it returns to the theme base colour.
        # The page samples live once a second while visible, and a
        # sample landing mid-probe overwrites the reading (the
        # first full-suite run failed exactly that way: the centre
        # pixel read the track, #D4EBF1) — so pause the page
        # through its real toggle first, then unpause.
        from PySide6.QtGui import QColor
        page = self.window.pulse_page
        page._on_pause_toggled(True)  # noqa: SLF001 — real toggle
        try:
            disk = page.dials["disk"]  # noqa: SLF001
            disk.set_reading(1.0, "100%", color=QColor("#FF0000"))
            _pump(self.app, 0.2)
            img = disk.grab().toImage()
            hot = img.pixelColor(max(img.width() // 2, 1),
                                 img.height() // 2).name().upper()
            self.assertEqual(hot, "#FF0000")
            disk.set_reading(1.0, "100%")
            _pump(self.app, 0.2)
            img = disk.grab().toImage()
            base = img.pixelColor(max(img.width() // 2, 1),
                                  img.height() // 2).name().upper()
            self.assertEqual(base, CYAN)
        finally:
            page._on_pause_toggled(False)  # noqa: SLF001

    def test_every_named_theme_and_custom_follow(self):
        from gui.style import THEMES as STYLE_THEMES
        for name, theme in STYLE_THEMES.items():
            self.window._on_theme_selected(name)
            _pump(self.app, 0.3)
            meters = _meters(self.window)
            self.assertEqual(meters["cpu"], theme["accent"].upper(),
                             f"{name} cpu")
            self.assertEqual(meters["net"], theme["accent_2"].upper(),
                             f"{name} net")
            self.assertEqual(_charts(self.window)["noise_chart"],
                             theme["accent"].upper(), f"{name} chart")
            self.assertEqual(_overview(self.window),
                             theme["accent_2"].upper(), f"{name} overview")
        self.window._on_theme_selected("Custom")
        _pump(self.app, 0.3)
        # Custom with no hex saved falls back through the gate to
        # the default accent; the surfaces must still take the
        # resolved theme, never stay on a stale colour.
        self.assertEqual(_meters(self.window)["cpu"],
                         _charts(self.window)["noise_chart"])


@unittest.skipUnless(_QT, "Qt-only: the real MainWindow offscreen")
class TestBootPathSunset(unittest.TestCase):
    """A fresh boot with Sunset saved already shows Sunset colours
    with no live theme switch at all."""

    def setUp(self):
        from PySide6.QtCore import QSettings as _QSettings
        _seed = _QSettings("NovaAI", "Nova")
        _seed.setValue("theme", "Sunset")
        _seed.sync()
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
        self.window.resize(1280, 750)
        try:
            self.window._show_page(5)
        except Exception:  # noqa: BLE001 — page switch best-effort
            pass
        _pump(self.app, 0.6)

    def tearDown(self):
        try:
            self.window._on_theme_selected("Cyan")
        except Exception:  # noqa: BLE001 — restore best-effort
            pass
        try:
            self._base.tearDown()
        except Exception:  # noqa: BLE001 — teardown best-effort
            pass

    def test_boot_with_sunset_saved_shows_sunset(self):
        self.assertEqual(_meters(self.window), {
            "cpu": SUNSET, "mem": SUNSET, "disk": SUNSET,
            "net": SUNSET_2, "battery": SUNSET_2})
        self.assertEqual(_charts(self.window), {
            "noise_chart": SUNSET, "light_chart": SUNSET})
        self.assertEqual(_overview(self.window), SUNSET_2)


if __name__ == "__main__":
    unittest.main()
