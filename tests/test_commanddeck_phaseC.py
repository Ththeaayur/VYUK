"""Command Deck Phase C tests (guide §10 Phase C, §11.3, §11.4).

Pure tests run on both interpreters. Qt tests skip unless PySide6
imports and a QApplication can be created (same pattern as Phase B).

What is pinned here:
* the pure design-system modules (palette composition, motion
  tokens, HUD profiles);
* the shape rule: no numeric border-radius above 4 px outside the
  circle exemptions and the per-phase allowlist of unmigrated
  pages (which shrinks in D/E/G — it never grows);
* the RADIUS constants: 2 px everywhere except the Phase D window
  remainder and the two circle constants;
* every Phase C component: panel chrome, dialog deny-default +
  details toggle + parentless construction, toast, backdrop cache,
  segment arithmetic, switch hit area, tabs, pill severities,
  bus-driven mission rail + trace, radar filtering without
  dispatch, drawer pin, telemetry NOT MEASURED, empty EXAMPLE tag,
  live orb thumbnails for all nine styles;
* the package stays importable without Qt (lazy widget imports).
"""

import re
import sys
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TREE))

from gui.design_system import motion, palette, profiles  # noqa: E402
from gui.style import (  # noqa: E402
    DISPLAY_FONT_STACK,
    MONO_FONT_STACK,
    first_font_family,
)

try:
    from PySide6.QtWidgets import QApplication, QPushButton
    from PySide6.QtCore import QTimer

    _QT_IMPORTABLE = True
except Exception:
    _QT_IMPORTABLE = False


def _qt_available():
    if not _QT_IMPORTABLE:
        return False
    try:
        if QApplication.instance() is None:
            QApplication([])
        return True
    except Exception:
        return False


QT_AVAILABLE = _qt_available()


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ---------------------------------------------------------------- pure: palette
class PaletteTests(unittest.TestCase):
    def test_parse_hex(self):
        self.assertEqual(palette.parse_color("#35D6FF"), (53, 214, 255, 255))

    def test_parse_functional_float_alpha(self):
        self.assertEqual(
            palette.parse_color("rgba(53, 214, 255, 0.5)"), (53, 214, 255, 128)
        )

    def test_parse_functional_int_alpha(self):
        self.assertEqual(
            palette.parse_color("rgba(53, 214, 255, 28)"), (53, 214, 255, 28)
        )

    def test_parse_rejects_junk(self):
        with self.assertRaises(ValueError):
            palette.parse_color("not-a-colour")

    def test_css_color_is_exact(self):
        self.assertEqual(palette.css_color(53, 214, 255, 128), "rgba(53, 214, 255, 128)")

    def test_with_alpha(self):
        self.assertEqual(
            palette.with_alpha("#35D6FF", 44), "rgba(53, 214, 255, 44)"
        )

    def test_hex_of(self):
        self.assertEqual(palette.hex_of("#35D6FF"), "#35d6ff")

    def test_lighten_toward_white(self):
        # 12% toward white from near-black stays dark, moves up.
        out = palette.lighten("#04090E", 0.12)
        self.assertEqual(out, "#22272b")

    def test_first_font_family(self):
        # The stacks are single QSS strings, so stack[0] is a quote
        # character, not a family (Phase C render finding: the panel
        # eyebrow rendered as outline glyphs from an empty family).
        self.assertEqual(first_font_family(DISPLAY_FONT_STACK), "Orbitron")
        self.assertEqual(first_font_family(MONO_FONT_STACK), "Share Tech Mono")
        self.assertEqual(first_font_family("'Sora', sans-serif"), "Sora")

    def test_no_literal_values_in_source(self):
        # The guard test owns this scan; here pin the construction
        # trick itself: css_color must never contain a literal value.
        src = (TREE / "gui" / "design_system" / "palette.py").read_text()
        self.assertNotIn("35D6FF", src)
        self.assertNotIn("35, 214, 255", src)


# ---------------------------------------------------------------- pure: motion
class MotionTests(unittest.TestCase):
    def test_token_table_values(self):
        self.assertEqual(motion.HUD_SNAP_MS, 120)
        self.assertEqual(motion.SCAN_WIPE_MS, 120)
        self.assertEqual(motion.LOCK_ON_MS, 240)
        self.assertEqual(motion.LABEL_DECODE_MS, 180)
        self.assertEqual(motion.STATUS_FLICKER_MS, 80)
        self.assertEqual(motion.CURSOR_TRAIL_MS, 400)
        self.assertEqual(motion.IRIS_SUMMON_MS, 200)
        self.assertEqual(motion.TOAST_MS, 5000)
        self.assertEqual(motion.FRAME_BUDGET_MS, 20)
        self.assertEqual(motion.BLINK_MAX_HZ, 3)

    def test_frame_rates_ordered(self):
        self.assertLess(motion.FPS_BATTERY_SAVER, motion.FPS_IDLE)
        self.assertLess(motion.FPS_IDLE, motion.FPS_ACTIVE)
        self.assertLess(motion.FPS_ACTIVE, motion.FPS_CAP)

    def test_chamfer_and_tick(self):
        self.assertEqual(motion.CHAMFER_PX, 8)
        self.assertEqual(motion.TICK_PX, 8)


# ---------------------------------------------------------------- pure: profiles
class ProfileTests(unittest.TestCase):
    def test_three_profiles(self):
        self.assertEqual(set(profiles.PROFILES), {"Cinematic", "Professional", "Minimal"})
        self.assertTrue(profiles.PROFILES["Cinematic"].backdrop)
        self.assertTrue(profiles.PROFILES["Cinematic"].scanlines)
        self.assertFalse(profiles.PROFILES["Professional"].scanlines)
        self.assertFalse(profiles.PROFILES["Minimal"].backdrop)

    def test_resolution(self):
        self.assertEqual(profiles.DEFAULT_PROFILE, "Cinematic")
        self.assertEqual(
            profiles.resolve_profile("Cinematic", False, True), "Minimal"
        )  # Contrast forces Minimal
        self.assertEqual(
            profiles.resolve_profile("Cinematic", True, False), "Professional"
        )  # power-saver forces Professional
        self.assertEqual(
            profiles.resolve_profile("Minimal", False, False), "Minimal"
        )
        self.assertEqual(
            profiles.resolve_profile("Unknown", False, False), "Cinematic"
        )


# ---------------------------------------------------------------- pure: shape rule
# Files whose numeric radii are NOT yet migrated (their phase owns
# them). This list shrinks in D/E/G and never grows.
SHAPE_ALLOWLIST_FILES = {
    "onboarding.py",  # Phase G first run
}
# Numeric circle radii that stay: send button (22) and radio/slider
# indicators (9) in style_sheet.py (the QSS template home since GUI
# split #6 moved the template verbatim out of style.py). Template-var
# circles (mic 28, icon 21) are covered by the RADIUS constant test below.
# INTENTIONAL (GUI split #6): CIRCLE_EXEMPT repointed style.py ->
# style_sheet.py; the literals moved verbatim with the template.
CIRCLE_EXEMPT = {("style_sheet.py", 22), ("style_sheet.py", 9)}
# P-7 (debug pass): the floating status strip's own radius —
# status_strip.py is a floating always-on-top window, so its
# 8 px corners are intentional (not a Phase C page that forgot
# to migrate). A precise (file, value) exemption, like the
# circles above — it exempts exactly this one radius, nowhere
# else.
STRIP_EXEMPT = {("status_strip.py", 8)}
_RADIUS_RE = re.compile(r"RADIUS_([A-Z_]+)\s*=\s*(\d+)")
_QSS_RADIUS_RE = re.compile(r"border-radius:\s*(\d+)px")


class ShapeTests(unittest.TestCase):
    def test_radius_constants(self):
        # INTENTIONAL (GUI split #6): the RADIUS_* constants moved
        # verbatim from gui/style.py to gui/style_tokens.py.
        text = (TREE / "gui" / "style_tokens.py").read_text()
        values = dict(_RADIUS_RE.findall(text))
        # Phase D (D13): the shell silhouette is painted by
        # AppShell (chamfer TL/BR + 6 px arcs); the token tracks
        # the arc radius. Circles pinned exactly.
        self.assertEqual(values.get("WINDOW"), "6")
        self.assertEqual(values.get("PILL_MIC"), "28")
        self.assertEqual(values.get("PILL_ICON"), "21")
        # Everything else is a 2 px cut corner in Phase C.
        for name in (
            "DIALOG", "CARD", "BTN", "SM", "PILL", "PILL_SM", "CHIP", "DOCK",
        ):
            self.assertEqual(values.get(name), "2", name)

    def test_no_large_numeric_radius_outside_allowlist(self):
        offenders = []
        for path in sorted((TREE / "gui").rglob("*.py")):
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                for match in _QSS_RADIUS_RE.finditer(line):
                    value = int(match.group(1))
                    if value <= 4:
                        continue
                    if path.name in SHAPE_ALLOWLIST_FILES:
                        continue
                    if (path.name, value) in CIRCLE_EXEMPT:
                        continue
                    if (path.name, value) in STRIP_EXEMPT:
                        continue
                    offenders.append(f"{path.name}:{lineno} {value}px :: {line.strip()[:90]}")
        self.assertEqual(offenders, [])


# ---------------------------------------------------------------- pure: lazy package
class LazyPackageTests(unittest.TestCase):
    def test_pure_import_pulls_no_qt(self):
        # This module already imported design_system.pure modules;
        # assert the package itself declares lazy widget names.
        import gui.design_system as package

        for name in ("HudPanel", "HudDialog", "HudToast", "SegmentBar",
                     "HudSwitch", "HudTabs", "StatusPill", "MissionRail",
                     "ActionTrace", "CommandRadar", "ContextDrawer",
                     "TelemetryPanel", "EmptyState", "OrbThumbnail"):
            self.assertIn(name, package._LAZY)


# ---------------------------------------------------------------- Qt components
@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class PanelTests(unittest.TestCase):
    def test_content_layout_and_edges(self):
        from gui.design_system.panels import HudPanel, chamfered_path
        from PySide6.QtCore import QRect

        panel = HudPanel()
        panel.set_edge("amber")
        panel.set_edge("danger")
        panel.set_edge("line")
        panel.set_edge("subtle")
        layout = panel.content_layout()
        self.assertIs(layout, panel.content_layout())
        self.assertEqual(layout.contentsMargins().left(), 16)
        path = chamfered_path(QRect(0, 0, 100, 60), 8)
        self.assertFalse(path.isEmpty())
        # The eyebrow label carries a real family (never the empty
        # family that rendered as outline glyphs in lab renders).
        from PySide6.QtWidgets import QLabel

        eyed = HudPanel(eyebrow="// MISSION")
        labels = eyed.findChildren(QLabel)
        self.assertEqual(labels[0].text(), "// MISSION")
        self.assertEqual(labels[0].font().family(), "Orbitron")
        panel.resize(200, 120)
        panel.show()
        _app().processEvents()
        self.assertFalse(panel.grab().isNull())


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class DialogTests(unittest.TestCase):
    def test_deny_default_buttons(self):
        from gui.design_system.dialogs import HudDialog

        dialog = HudDialog(None, title="Confirm")
        dialog.add_title("Confirm")
        dialog.add_body("Do it?")
        buttons = dialog.add_buttons([
            ("yes", "Yes", "accept", False, False),
            ("no", "No", "reject", True, False),
        ])
        self.assertTrue(buttons["no"].isDefault())
        self.assertFalse(buttons["yes"].isDefault())
        buttons["yes"].click()
        self.assertEqual(dialog.result(), 1)
        buttons["no"].click()
        self.assertEqual(dialog.result(), 0)

    def test_details_toggle_starts_hidden(self):
        from gui.design_system.dialogs import HudDialog

        dialog = HudDialog(None, title="Details")
        toggle, label = dialog.add_details("long details")
        self.assertTrue(label.isHidden())
        toggle.click()
        self.assertFalse(label.isHidden())

    def test_parentless_construction(self):
        from gui.design_system.dialogs import HudDialog

        dialog = HudDialog(None, title="Already running")
        self.assertIsNone(dialog.parentWidget())
        self.assertTrue(dialog.isModal())
        self.assertGreaterEqual(dialog.minimumWidth(), 480)

    def test_confirm_exec_clicks_no(self):
        from gui.design_system.dialogs import HudDialog

        def _click_no():
            app = _app()
            modal = app.activeModalWidget()
            if modal is None:
                for widget in app.topLevelWidgets():
                    if isinstance(widget, HudDialog) and widget.isVisible():
                        modal = widget
                        break
            if modal is not None:
                for button in modal.findChildren(QPushButton):
                    if button.text() == "No":
                        button.click()
                        return
                modal.reject()

        QTimer.singleShot(150, _click_no)
        QTimer.singleShot(3000, lambda: _app().closeAllWindows())
        result = HudDialog.confirm(None, "Confirm", "Do it?")
        self.assertFalse(result)

    def test_information_exec_clicks_ok(self):
        from gui.design_system.dialogs import HudDialog

        seen = []

        def _click_ok():
            app = _app()
            modal = app.activeModalWidget()
            if modal is not None:
                seen.append(True)
                for button in modal.findChildren(QPushButton):
                    if button.text() == "OK":
                        button.click()
                        return
                modal.reject()

        QTimer.singleShot(150, _click_ok)
        QTimer.singleShot(3000, lambda: _app().closeAllWindows())
        result = HudDialog.information(None, "Notice", "Something happened.")
        self.assertIsNone(result)
        self.assertEqual(seen, [True])


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class ToastTests(unittest.TestCase):
    def test_attention_and_timer(self):
        from gui.design_system.toasts import HudToast
        from gui.design_system.motion import TOAST_MS

        toast = HudToast(None, "Saved", attention=True)
        self.assertTrue(toast.attention)
        self.assertEqual(toast.text, "Saved")
        self.assertTrue(toast._timer.isSingleShot())
        self.assertEqual(toast._timer.interval(), TOAST_MS)


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class BackdropTests(unittest.TestCase):
    def test_cache_tracks_resize_once_shown(self):
        from gui.design_system.backdrop import HudBackdrop

        backdrop = HudBackdrop()
        backdrop.show()
        backdrop.resize(400, 300)
        _app().processEvents()
        size = backdrop.cache_size
        self.assertIsNotNone(size)
        self.assertEqual((size.width(), size.height()), (400, 300))
        backdrop.resize(500, 400)
        _app().processEvents()
        size = backdrop.cache_size
        self.assertEqual((size.width(), size.height()), (500, 400))


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class SegmentTests(unittest.TestCase):
    def test_percent_arithmetic(self):
        from gui.design_system.progress import SegmentBar

        bar = SegmentBar()
        bar.set_progress(3, 4)
        self.assertEqual(bar.percent_text, "75%")
        self.assertEqual(bar._label.font().family(), "Share Tech Mono")
        bar.set_progress(1, 3)
        self.assertEqual(bar.percent_text, "33%")
        bar.set_progress(0, 0)
        self.assertEqual(bar.percent_text, "0%")


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class SwitchTests(unittest.TestCase):
    def test_hit_area_and_toggle(self):
        from gui.design_system.switches import HudSwitch

        switch = HudSwitch(checked=False)
        self.assertGreaterEqual(switch.minimumWidth(), 40)
        self.assertGreaterEqual(switch.minimumHeight(), 40)
        switch.click()
        self.assertTrue(switch.isChecked())
        switch.click()
        self.assertFalse(switch.isChecked())


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class TabsTests(unittest.TestCase):
    def test_tabs(self):
        from gui.design_system.tabs import HudTabs

        tabs = HudTabs()
        tabs.addTab("One")
        tabs.addTab("Two")
        self.assertEqual(tabs.count(), 2)


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class PillTests(unittest.TestCase):
    def test_severity_glyphs(self):
        from gui.design_system.pills import StatusPill

        expected = {
            "neutral": "○", "info": "○", "success": "◆",
            "warning": "▲", "danger": "■", "offline": "○",
        }
        for severity, glyph in expected.items():
            pill = StatusPill(text=severity, severity=severity)
            self.assertEqual(pill.glyph, glyph)
            self.assertEqual(pill.severity, severity)

    def test_unknown_severity_falls_back_neutral(self):
        from gui.design_system.pills import StatusPill

        pill = StatusPill(text="?", severity="mystery")
        self.assertEqual(pill.severity, "neutral")


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class MissionTests(unittest.TestCase):
    def test_bus_drives_rail_and_trace(self):
        from gui.design_system.mission import ActionTrace, MissionRail
        from gui.state_bus import (
            MissionState, StateBus, StepEvent, ToolFinished, ToolStarted, Verified,
        )

        bus = StateBus()
        rail = MissionRail(bus=bus)
        trace = ActionTrace(bus=bus)
        self.assertEqual(rail.goal_text, "No active mission")
        self.assertEqual(trace.row_count, 0)
        bus.emit(MissionState(goal="Test goal", phase="act",
                              target_app="Editor", target_window="main",
                              permission="granted", step=2, steps_total=5))
        bus.emit(StepEvent(n=3, m=5, label="open file"))
        bus.emit(ToolStarted(kind="click", target="Save"))
        bus.emit(ToolFinished(status="success"))
        bus.emit(Verified(evidence="file exists"))
        bus.drain()
        self.assertEqual(rail.goal_text, "Test goal")
        self.assertIn("click", rail.step_text)
        self.assertEqual(trace.row_count, 4)
        self.assertTrue(trace.row_text(0).startswith("PLAN ›"))
        self.assertTrue(trace.row_text(1).startswith("ACTION ›"))
        self.assertTrue(trace.row_text(2).startswith("OUTCOME ›"))
        self.assertTrue(trace.row_text(3).startswith("VERIFICATION ›"))


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class RadarTests(unittest.TestCase):
    def test_filtering_and_no_dispatch(self):
        from gui.design_system.radar import CommandRadar
        from gui.style import SURFACE_1

        radar = CommandRadar()
        self.assertFalse(hasattr(radar, "dispatched"))
        # A top-level dialog gets no sheet from the main window:
        # the radar must carry its own dark sheet, never light.
        self.assertIn(SURFACE_1, radar.styleSheet())
        radar.set_items([
            {"label": "Open Settings", "scope": "PAGE", "preview": "p1",
             "action": lambda: None},
            {"label": "Run tool", "scope": "TOOL", "preview": "p2"},
        ])
        # The callable is dropped: stored items hold data only.
        self.assertEqual(set(radar._items[0]), {"label", "scope", "preview"})
        self.assertEqual(radar.visible_count, 2)
        radar._search.setText("settings")
        self.assertEqual(radar.visible_count, 1)
        self.assertEqual(radar.preview_text, "p1")
        radar._search.setText("")
        radar._chip_buttons["TOOL"].setChecked(False)
        self.assertEqual(radar.visible_count, 1)

    def test_enter_previews_only(self):
        from gui.design_system.radar import CommandRadar

        radar = CommandRadar()
        previewed = []
        radar.previewed.connect(previewed.append)
        radar.set_items([{"label": "Open Settings", "scope": "PAGE", "preview": "p1"}])
        radar._results.setCurrentRow(0)
        radar._on_activated(None)
        self.assertEqual(previewed, ["Open Settings"])


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class DrawerTests(unittest.TestCase):
    def test_pin(self):
        from gui.design_system.drawer import ContextDrawer

        drawer = ContextDrawer(title="Detail")
        self.assertFalse(drawer.pinned)
        toggled = []
        drawer.pin_toggled.connect(toggled.append)
        drawer.set_pinned(True)
        self.assertTrue(drawer.pinned)
        self.assertEqual(toggled, [True])


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class TelemetryTests(unittest.TestCase):
    def test_sourced_and_unsourced(self):
        from gui.design_system.telemetry import TelemetryPanel

        panel = TelemetryPanel()
        panel.add_metric("cpu", "42", "%", source="psutil", trend="up",
                         updated_at="10:00")
        panel.add_metric("link", "1.2", "ms", source="")
        self.assertEqual(panel.metric_value_text("cpu"), "42 %")
        self.assertEqual(panel.metric_value_text("link"), "NOT MEASURED ms")
        self.assertEqual(panel.metric_source_text("link"), "NOT MEASURED")
        self.assertEqual(panel.row_count, 2)


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class EmptyTests(unittest.TestCase):
    def test_example_tag_and_action(self):
        from gui.design_system.empty import EmptyState

        state = EmptyState(sentence="Nothing here", suggested_task="Try a mission",
                           action_label="Start", example=True)
        self.assertTrue(state.example_shown)
        self.assertTrue(state.has_action)
        self.assertEqual(state.sentence_text, "Nothing here")
        plain = EmptyState(sentence="Empty")
        self.assertFalse(plain.example_shown)
        self.assertFalse(plain.has_action)


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class OrbThumbTests(unittest.TestCase):
    def test_all_styles_render(self):
        from core.app_settings import ORB_STYLES
        from gui.design_system.orbthumb import OrbThumbnail

        for style in ORB_STYLES:
            thumb = OrbThumbnail(style=style)
            pixmap = thumb.pixmap
            self.assertIsNotNone(pixmap)
            self.assertFalse(pixmap.isNull(), style)
            self.assertEqual(pixmap.width(), pixmap.height(), style)

    def test_unknown_style_raises(self):
        from gui.design_system.orbthumb import OrbThumbnail

        with self.assertRaises(ValueError):
            OrbThumbnail(style="Apex")


if __name__ == "__main__":
    unittest.main()
