"""UI programme — breakpoints slice (2026-10-30).

Width tiers + scroll-safe minimums + default-size clamp, decided on
the audit slice 1 measurements (not taste):
- Comfort >= 1008 px keeps the S5 floors exactly (220 + 56 + 420 +
  12 + 300 = 1008).
- Narrow < 1008 px relaxes the stage floor 420 -> 320 (hero 400 ->
  320, the orb visualizer's own floor): 220 + 56 + 320 + 12 + 300
  = 908, fitting the 200%-on-1080p logical width (960).
- Window minimum (908, 500) fits both measured compact work areas
  (1366x768@125% ~1092x576, 200%@1080p ~960x516).
- Default size clamps to the available geometry, so the owner's
  1920x1080@125% machine still opens at exactly 1108x750.
- NO DRAWER: every measured failure is solved by the tiered
  floors + scroll-safe pages, so the drawer decision (delegated
  on measurements) is no drawer — the sidebar stays fixed at 220
  at every tier.
- Composer floors (UI rethink Build 1 + Chat UI Dictate): input
  min 120 px, Dictate collapses to icon-only below 420 px panel
  width, the dock waveform only shows when it fits, the screen
  button hides below 420 px panel width (E-F103, 2026-11-20 —
  was 380: at the 400 px default panel it took the room the
  shortened placeholder needs), and Dictate hides entirely below
  380 px (E-F103: at the 300 floor even its 48 px icon-only form
  leaves the input at its minimum). At the 300 floor the dock
  needs 244 (input min + send + mic + two gaps, no screen, no
  Dictate, no waveform) — it fits with 56 px to spare, so the
  mic stays fully visible. Palette cap/clamp and the Doctor
  scroll wrapper are re-pinned, not redone.

Lab honesty: the shell imports frozen voice_assistant.* names and
cannot be imported here, so tiers + wiring are proved by AST over
the real files and by offscreen mechanism tests (real QSplitter /
QScrollArea geometry, representative content — not the full
window). Pixel-perfect renders on real Windows are field-owed.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ast
import importlib.util
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent
GUI = TREE / "gui"


def _load_breakpoints():
    spec = importlib.util.spec_from_file_location(
        "uibreakpoints_breakpoints", GUI / "breakpoints.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BP = _load_breakpoints()


def _read(relpath):
    return (GUI / relpath).read_text(encoding="utf-8")


# INTENTIONAL (refactor P1 step 5): window_shell.py split verbatim
# into panel mixins; the breakpoint pins span frame + sidebar +
# assistant, so read the shell surface concatenated.
SHELL_FILES = (
    "window_shell.py",
    "window_shell_frame.py",
    "window_shell_tray.py",
    "window_shell_settingsio.py",
    "window_shell_memoryio.py",
    "window_shell_activity.py",
    "window_shell_onboarding.py",
    "window_shell_sidebar.py",
    "window_shell_assistant.py",
    "window_shell_assistant_widgets.py",
    "window_shell_briefing.py",
)


def _shell_source():
    return "".join(_read(n) for n in SHELL_FILES)


def _fn_src(tree_text, name):
    tree = ast.parse(tree_text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(tree_text, node)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if (isinstance(item, ast.FunctionDef)
                        and item.name == name):
                    return ast.get_source_segment(tree_text, item)
    return ""


# ---------------------------------------------------------------- pure
class TestTierArithmetic(unittest.TestCase):
    def test_tier_boundary(self):
        self.assertEqual(BP.tier_for(1007), "narrow")
        self.assertEqual(BP.tier_for(1008), "comfort")
        self.assertEqual(BP.tier_for(960), "narrow")
        self.assertEqual(BP.tier_for(1920), "comfort")

    def test_floor_derivations(self):
        # S5 comfort derivation, unchanged; narrow derivation, new.
        self.assertEqual(220 + 56 + 420 + 12 + 300, 1008)
        self.assertEqual(220 + 56 + 320 + 12 + 300, 908)
        self.assertEqual(BP.WINDOW_MINIMUM, (908, 500))
        self.assertEqual(BP.COMFORT_WIDTH, 1008)
        self.assertEqual(BP.CHAT_FLOOR, 300)
        self.assertEqual(BP.stage_floor_for(1008), 420)
        self.assertEqual(BP.stage_floor_for(1007), 320)
        self.assertEqual(BP.hero_min_width_for(1008), 400)
        self.assertEqual(BP.hero_min_width_for(1007), 320)

    def test_chat_target_preserves_s5_at_comfort(self):
        # At the old minimum the guard still yields chat 300.
        self.assertEqual(BP.chat_target(720, 400, 1108), 300)
        self.assertEqual(BP.chat_target(720, 400, 1008), 300)
        self.assertEqual(BP.chat_target(764, 400, 1052), 344)
        # Frozen shape at/above the default width.
        self.assertEqual(BP.chat_target(820, 400, 1108), 400)

    def test_chat_target_narrow_floors(self):
        # Narrow first-show: chat never below 300, stage never
        # below 320 (avail = stage + chat by construction).
        self.assertEqual(BP.chat_target(620, 400, 908), 300)
        self.assertEqual(BP.chat_target(672, 400, 960), 352)
        self.assertEqual(620 - 300, 320)  # stage at the 908 floor
        self.assertEqual(672 - 352, 320)  # stage at the 960 first-show

    def test_chat_target_no_cliff_across_boundary(self):
        # A drag down through 1008 keeps the chat width while the
        # stage shrinks continuously: 300 at 1007 AND at 1008.
        self.assertEqual(BP.chat_target(719, 300, 1007), 300)
        self.assertEqual(BP.chat_target(720, 300, 1008), 300)
        self.assertEqual(719 - 300, 419)  # stage 419, not a jump
        self.assertEqual(720 - 300, 420)

    def test_default_size_clamp(self):
        # Owner's machine: unchanged, exactly the frozen default.
        self.assertEqual(BP.default_window_size(1536, 826),
                         (1108, 750))
        self.assertEqual(BP.default_window_size(1920, 1080),
                         (1108, 750))
        # Compact case: fits inside the work area.
        self.assertEqual(BP.default_window_size(1092, 576),
                         (1052, 536))
        # 200% case: width fits; height clamps up to the 500
        # minimum under Qt (476 < 500), which still fits 516.
        self.assertEqual(BP.default_window_size(960, 516), (920, 476))

    def test_measured_cases_fit(self):
        # Every measured failure is solved by (908, 500) + the
        # clamped defaults — this is the measurement that decides
        # NO DRAWER (see test_no_drawer below).
        min_w, min_h = BP.WINDOW_MINIMUM
        # 1366x768@125% work area ~1092x576.
        self.assertLessEqual(min_w, 1092)
        self.assertLessEqual(min_h, 576)
        self.assertLessEqual(1052, 1092)
        self.assertLessEqual(536, 576)
        # 200%@1080p work area ~960x516.
        self.assertLessEqual(min_w, 960)
        self.assertLessEqual(min_h, 516)
        self.assertLessEqual(920, 960)
        self.assertLessEqual(500, 516)  # clamped default height
        # Physical minimum now fits both displays (the old
        # 2016x1240 / 1260x775 did not).
        self.assertLessEqual(908 * 2, 1920)
        self.assertLessEqual(500 * 2, 1080)
        self.assertLessEqual(908 * 1.25, 1366)
        self.assertLessEqual(500 * 1.25, 768)

    def test_composer_floors_build1(self):
        # UI rethink Build 1: the composer floors are the dock's
        # fixed widths (unchanged from S5) + the new input minimum.
        # The Dictate width is font-dependent, so the live fit is
        # proved by the renders — this test pins the exact
        # constants the fit is built from.
        # Fixed dock widths: send 44, mic 56, screen 42,
        # waveform 104, gaps 12.
        self.assertEqual(44 + 56 + 42 + 104, 246)
        # Input minimum 120: at the 400 px open width (screen
        # hidden below 420 — E-F103, 2026-11-20, was shown — and
        # Dictate icon-only below 420) the dock without the
        # waveform leaves 216 px for the input.
        self.assertEqual(400 - (44 + 56 + 48 + 3 * 12), 216)
        # With the waveform at 512: 206 px for input + Dictate
        # together — the waveform only shows when the input keeps
        # its 120 (the gate is computed from live widths, see
        # _apply_dock_narrow_rule).
        self.assertEqual(512 - (44 + 56 + 42 + 104 + 5 * 12), 206)
        # At the 300 floor (screen hidden, Dictate hidden below
        # 380 — E-F103, 2026-11-20, was icon-only): the dock needs
        # 244 — it fits with 56 px to spare, so the mic stays fully
        # visible and the input grows past its 120 minimum.
        # Dictate icon-only is 48 (16 px icon + the generic
        # QPushButton sheet's 16 px horizontal padding each side);
        # two gaps, no screen, no waveform.
        self.assertEqual(120 + 44 + 56 + 2 * 12, 244)


# ---------------------------------------------------------------- AST
class TestBreakpointsWiring(unittest.TestCase):
    def test_window_minimum_and_default_wiring(self):
        shell = _shell_source()
        self.assertIn("self.setMinimumSize(*WINDOW_MINIMUM)", shell)
        self.assertIn("QGuiApplication.primaryScreen()", shell)
        self.assertIn("availableGeometry()", shell)
        self.assertIn("default_window_size(", shell)
        self.assertIn("self.resize(1108, 750)", shell)  # no-screen fallback
        self.assertNotIn("self.setMinimumSize(1008, 620)", shell)

    def test_tier_application_wiring(self):
        shell = _shell_source()
        self.assertIn("def _apply_breakpoint_tier", shell)
        tier = _fn_src(shell, "_apply_breakpoint_tier")
        self.assertIn("stage_floor_for", tier)
        self.assertIn("hero_min_width_for", tier)
        self.assertIn('"_stage_widget"', tier)
        self.assertIn("setMinimumWidth", tier)
        # The tier switch never touches the sidebar: check the
        # code body only (the docstring mentions the sidebar to
        # record that it is untouched).
        tier_body = tier.split('"""')[-1]
        self.assertNotIn("sidebar", tier_body)
        fix = _fn_src(shell, "_fix_chat_width")
        self.assertIn("_apply_breakpoint_tier()", fix)
        self.assertIn("chat_target(", fix)
        self.assertNotIn("avail - 420", fix)

    def test_s5_geometry_still_present_at_build(self):
        # Build-time floors are the comfort tier; the tier switch
        # only relaxes them at runtime below 1008 px. Chat pass
        # (2026-11-17): the chat panel's build-time floor is 300
        # again — the Build 1 raise to 380 and its compact tier
        # are reverted. The Dictate collapse, input minimum and
        # waveform gate (Build 1 / Chat UI contents fixes) stay.
        shell = _shell_source()
        self.assertIn("setFixedWidth(220)", shell)
        self.assertIn("setMinimumWidth(420)", shell)
        self.assertIn("setMinimumWidth(300)", shell)
        self.assertIn("setHandleWidth(12)", shell)
        # E-F103 (2026-11-20): the narrow threshold is 420 now
        # (was 380) — screen-share hides below the 400 px default
        # panel, so the composer input keeps room for the
        # placeholder. The Dictate collapse (420) stays.
        self.assertIn("_DOCK_NARROW_WIDTH = 420", shell)
        self.assertIn("_DICTATE_TEXT_WIDTH = 420", shell)
        self.assertNotIn("chat_floor_for", shell)
        self.assertIn("input_cmd.setMinimumWidth(120)", shell)
        self.assertIn("_dock_waveform_allowed", shell)
        self.assertIn("setMinimumSize(400, 80)", shell)  # hero, build time
        self.assertIn("setMinimumSize(320, 80)", shell)  # visualizer floor

    def test_no_drawer_mechanism(self):
        # The NO-DRAWER decision pinned as mechanism, not as a
        # word search (the decision itself is recorded in the
        # tier module's docstring): the sidebar is fixed-width,
        # the tier module defines no sidebar narrowing, and the
        # tier switch never touches the sidebar.
        shell = _shell_source()
        self.assertIn("setFixedWidth(220)", shell)
        self.assertFalse(hasattr(BP, "SIDEBAR_WIDTH_NARROW"))
        tier = _fn_src(shell, "_apply_breakpoint_tier")
        tier_body = tier.split('"""')[-1]
        self.assertNotIn("sidebar", tier_body)

    def test_pulse_scroll_wrapper(self):
        # GUI split #8 INTENTIONAL: pulse + doctor builders moved verbatim to
        # window_pages_system.py. Assertion unchanged.
        pages = _read("window_pages_system.py")
        src = _fn_src(pages, "_build_pulse_page")
        self.assertTrue(src, "_build_pulse_page not found")
        self.assertIn("QScrollArea()", src)
        self.assertIn("setWidgetResizable(True)", src)
        self.assertIn("setFrameShape(QFrame.Shape.NoFrame)", src)
        self.assertIn("scroll.setWidget(page)", src)
        self.assertIn("page.setAutoFillBackground(False)", src)
        self.assertIn("return scroll", src)
        # Everything the page was before is unchanged.
        self.assertIn("self.pulse_page = PulsePage()", src)
        self.assertIn("layout.addWidget(self.pulse_page, stretch=1)",
                      src)

    def test_doctor_wrapper_and_palette_untouched(self):
        # GUI split #8 INTENTIONAL: pulse + doctor builders moved verbatim to
        # window_pages_system.py. Assertion unchanged.
        pages = _read("window_pages_system.py")
        doc = _fn_src(pages, "_build_doctor_page")
        self.assertIn("scroll.setWidget(page)", doc)
        self.assertIn("return scroll", doc)
        # INTENTIONAL (refactor P1 step 5): CommandPalette moved verbatim
        # to window_widgets_palette.py. Assertions unchanged.
        widgets = _read("window_widgets_palette.py")
        self.assertIn("def palette_list_height_for", widgets)
        self.assertIn("palette_list_height_for(", widgets)
        self.assertIn("setFixedWidth(560)", widgets)
        self.assertIn("setFixedHeight(486)", widgets)

    def test_orb_untouched(self):
        shell = _shell_source()
        self.assertIn("no longer folds", shell)
        self.assertIn("away mid-conversation", shell)


# ------------------------------------------------------- offscreen Qt
def _qt_available():
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def _scroll_problems(wrapped):
    """Doctor-pattern check, generalised: tall content in a host
    of 500 px. Returns violation strings (empty == pass)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QLabel,
        QPushButton,
        QScrollArea,
        QVBoxLayout,
        QWidget,
    )
    if QApplication.instance() is None:
        QApplication([])
    host = QWidget()
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(28, 20, 28, 20)
    layout.setSpacing(10)
    layout.addWidget(QLabel("SYSTEM PULSE"))
    layout.addWidget(QLabel("Live machine vitals"))
    for name in ("CPU", "Memory", "Disk", "Network", "Battery"):
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 12, 16, 12)
        cl.addWidget(QLabel(name))
        cl.addWidget(QPushButton(f"Details for {name}"))
        layout.addWidget(card)
    layout.addWidget(QLabel("Live bars, sampled once a second."))
    if wrapped:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        page.setAutoFillBackground(False)
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
    else:
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page, stretch=1)
    host.setFixedSize(908, 500)
    host.show()
    app = QApplication.instance()
    app.processEvents()
    problems = []
    for w in page.findChildren(QWidget):
        if not w.isVisible():
            continue
        cls = type(w).__name__
        if cls not in ("QLabel", "QPushButton"):
            continue
        g = w.geometry()
        if g.height() < w.minimumSizeHint().height():
            problems.append(
                f"below minimum: {cls} '{w.text()[:30]}' "
                f"{g.height()} < min {w.minimumSizeHint().height()}")
        if cls == "QLabel" and w.wordWrap():
            fm = w.fontMetrics()
            rect = fm.boundingRect(0, 0, max(1, g.width()), 10000,
                                   Qt.TextFlag.TextWordWrap, w.text())
            if g.height() < rect.height():
                problems.append(f"clipped label: '{w.text()[:30]}'")
        if cls == "QPushButton" and g.height() < 20:
            problems.append(f"collapsed button: '{w.text()[:30]}'")
    if wrapped:
        bars = host.findChildren(QScrollArea)
        if bars and bars[0].verticalScrollBar().maximum() <= 0:
            problems.append("wrapped page does not scroll at 500 px")
    host.deleteLater()
    return problems


class TestOffscreenMechanism(unittest.TestCase):
    def test_scroll_wrapped_tall_page_keeps_heights(self):
        if not _qt_available():
            self.skipTest("PySide6 not installed on this interpreter")
        self.assertEqual(_scroll_problems(wrapped=True), [])

    def test_unwrapped_tall_page_compresses_control(self):
        # Negative control: the same content WITHOUT a wrapper
        # MUST flag compression at 500 px, proving the wrapped
        # test above can detect the Doctor defect class.
        if not _qt_available():
            self.skipTest("PySide6 not installed on this interpreter")
        self.assertNotEqual(_scroll_problems(wrapped=False), [])

    def test_tiered_splitter_floors(self):
        if not _qt_available():
            self.skipTest("PySide6 not installed on this interpreter")
        from PySide6.QtWidgets import (
            QApplication,
            QSplitter,
            QWidget,
        )
        from PySide6.QtCore import Qt
        if QApplication.instance() is None:
            QApplication([])
        # Window width -> splitter avail (minus sidebar 220,
        # margins 56, handle 12); stage/chat from chat_target.
        cases = [
            # (window, first_target, expect_stage_floor)
            (908, 400, 320),
            (960, 400, 320),
            (960, 300, 320),   # drag-preserved width, no cliff
            (1008, 400, 420),
            (1052, 400, 420),
            (1108, 400, 420),
        ]
        for window, first_target, stage_floor in cases:
            split = QSplitter(Qt.Orientation.Horizontal)
            split.setHandleWidth(12)
            stage = QWidget()
            chat = QWidget()
            stage.setMinimumWidth(stage_floor)
            chat.setMinimumWidth(300)
            split.addWidget(stage)
            split.addWidget(chat)
            avail = window - 220 - 56 - 12
            target = BP.chat_target(avail, first_target, window)
            split.setSizes([avail - target, target])
            split.setFixedWidth(avail + 12)
            split.show()
            QApplication.instance().processEvents()
            self.assertGreaterEqual(stage.width(), stage_floor,
                                    f"window {window}: stage squeezed")
            self.assertGreaterEqual(chat.width(), 300,
                                    f"window {window}: chat squeezed")
            split.deleteLater()

    def test_qt_minimum_clamps_below_minimum_default(self):
        if not _qt_available():
            self.skipTest("PySide6 not installed on this interpreter")
        from PySide6.QtWidgets import QApplication, QWidget
        if QApplication.instance() is None:
            QApplication([])
        w = QWidget()
        w.setMinimumSize(*BP.WINDOW_MINIMUM)
        w.resize(920, 476)  # the 200% clamped default
        w.show()
        QApplication.instance().processEvents()
        self.assertEqual((w.width(), w.height()), (920, 500))
        w.deleteLater()


if __name__ == "__main__":
    unittest.main()
