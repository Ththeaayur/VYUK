"""Stage 0 features batch 5 (Group B look-and-sound, 2026-11-22):
items 9 (light theme — was MISSING), 10 (narrow window below
908 px — was MISSING), 11 (verbosity control — was MISSING),
12 (larger-text mode — was PARTIAL: the audit's "dead key" claim
is WRONG, appearance_font_scale was live; the real gaps were the
choices stopping at 120% and custom-painted text not scaling),
13 (high-contrast — already DONE, pinned here as still holding).

This suite pins the batch so it can never regress silently:

- Light is the 12th theme (was 11), with the contrast-verified
  palette (text 16.86, muted 7.14, faint 5.14 on bg); the
  stylesheet builds for Light and carries the setup-banner rules;
  the banner, kicker, HudPanel, Activity pane, Pulse rows,
  Overview backdrops, Senses text and Views panels all take the
  resolved Light theme (the first-pass renders showed every one
  of them leaking house-dark chrome).
- The compact tier (< 908): WINDOW_MINIMUM (640, 500); the shell
  minimum 64 + 56 + 240 + 12 + 240 = 612 <= 640; the chat floor
  is tiered (240 compact, 300 elsewhere) and _apply_breakpoint_tier
  wires it onto the panel — the pure-logic module always said so,
  but the panel kept its build-time 300 px hard minimum, so the
  shell minimum was 672 and the content overflowed the 640 window
  by 32 px (P21 root cause, found by the probe). The visualizer
  relaxes to the tier hero floor; the Remembered-approvals row
  wraps (FlowLayout); the Contacts empty state wraps.
- Verbosity: reply_verbosity (balanced default, env
  VYUK_REPLY_VERBOSITY) — ConfigSpec 112 fields / 107 env-backed;
  concise/detailed carry a directive, balanced carries none;
  concise caps at 256 output tokens.
- Larger text: choices run to 150% (1.0 / 1.15 / 1.3 / 1.5) and
  the stylesheet scales (13 px -> 19.5 px at 1.5); the painted
  widgets carry set_font_scale.
- Census UNMOVED (no new tools): 769 / 384 read / 385 effectful /
  169 confirm / params 2121 / grouped 766 / closed 768 /
  already-verified 319 / §2c 385 / labels 694 / desktop offered 76.

Stdlib unittest only. The theme/breakpoint/verbosity/census legs
are pure logic + AST pins and run on both interpreters; the
splitter/wrap legs are offscreen Qt mechanism tests (real
QSplitter / QListWidget geometry, not the full window) and skip
where PySide6 is absent. The real-window proof lives in the
repro probe (32/32) and the by-eye renders in stage0f5_work/.
"""

import ast
import dataclasses
import importlib.util
import json
import re
import unittest
from pathlib import Path

from core import tool_labels as _labels  # noqa: E402
from core import verbosity as _verbosity  # noqa: E402
from core.catalogue_groups_data import GROUP_MEMBERS  # noqa: E402
from core.config_spec import ConfigSpec  # noqa: E402
from core.funnel.tools_registry import tools  # noqa: E402
from gui.style import THEMES  # noqa: E402
from gui.style_theme import build_stylesheet  # noqa: E402

_TREE = Path(__file__).resolve().parent.parent
_GUI = _TREE / "gui"


def _qt_available():
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def _load_breakpoints():
    spec = importlib.util.spec_from_file_location(
        "stage0f5_breakpoints", _GUI / "breakpoints.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BP = _load_breakpoints()


def _read(relpath):
    return (_GUI / relpath).read_text(encoding="utf-8")


def _has_function(source, name):
    tree = ast.parse(source)
    return any(isinstance(node, ast.FunctionDef) and node.name == name
               for node in ast.walk(tree))


def _luminance(hex_color):
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = f(r), f(g), f(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg, bg):
    l1, l2 = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


# ----------------------------------------------------------------------
# Item 9 — Light theme
# ----------------------------------------------------------------------
class TestLightTheme(unittest.TestCase):
    def test_light_is_twelfth_theme(self):
        self.assertEqual(len(THEMES), 12)
        self.assertIn("Light", THEMES)
        self.assertEqual(
            sorted(THEMES),
            ["Amber", "Aurora", "Contrast", "Crimson", "Cyan", "Ember",
             "Graphite", "Light", "Mono", "Rose", "Sunset", "Violet"])

    def test_light_palette_exact(self):
        self.assertEqual(THEMES["Light"], {
            "accent": "#1D6FE0", "accent_2": "#2563EB",
            "highlight": "#E3F0FF", "bg": "#F7F9FC",
            "surface_1": "#FFFFFF", "surface_2": "#EDF1F7",
            "text": "#101826", "text_muted": "#4A5568",
            "text_faint": "#5F6B7A"})

    def test_light_text_contrast_aa(self):
        light = THEMES["Light"]
        bg = light["bg"]
        # Contrast-verified by computation at build time: text
        # 16.86, muted 7.14, faint 5.14 — all WCAG AA (>= 4.5).
        self.assertGreaterEqual(_contrast(light["text"], bg), 4.5)
        self.assertGreaterEqual(_contrast(light["text_muted"], bg), 4.5)
        self.assertGreaterEqual(_contrast(light["text_faint"], bg), 4.5)

    def test_light_stylesheet_builds_with_banner_rules(self):
        sheet = build_stylesheet("Light")
        self.assertGreater(len(sheet), 10000)
        self.assertIn("QFrame#setupBanner", sheet)
        self.assertIn("QProgressBar#setupBar", sheet)
        # The banner bar is the Light accent, not house cyan.
        self.assertIn("#1D6FE0", sheet)

    def test_light_stylesheet_scales_with_font_scale(self):
        base = build_stylesheet("Light", font_scale=1.0)
        scaled = build_stylesheet("Light", font_scale=1.5)
        self.assertIn("13px", base)
        self.assertIn("19.5px", scaled)
        self.assertNotIn("19.5px", base)

    def test_contrast_theme_holds(self):
        # Item 13: already DONE — pin it holding, do not rebuild.
        contrast = THEMES["Contrast"]
        self.assertEqual(contrast["bg"], "#000000")
        self.assertEqual(contrast["text"], "#FFFFFF")
        self.assertEqual(contrast["accent"], "#FFE600")

    def test_theme_application_wiring(self):
        # Every surface the first-pass renders caught leaking
        # house-dark chrome now takes the resolved theme.
        self.assertTrue(_has_function(
            _read("design_system/panels.py"), "set_theme"))
        self.assertTrue(_has_function(
            _read("window_pages_activity.py"), "_refresh_activity_theme"))
        self.assertTrue(_has_function(
            _read("pulse_page.py"), "set_palette"))
        self.assertTrue(_has_function(
            _read("overview_panel.py"), "set_palette"))
        self.assertTrue(_has_function(
            _read("senses_page.py"), "set_palette"))
        self.assertTrue(_has_function(
            _read("window_pages_views.py"), "_refresh_views_theme"))
        # The kicker no longer carries a scoped house-TEXT colour:
        # _page_shell's kicker sheet sets size + weight only (the
        # string survives only in the comment explaining the
        # removal). The Activity tab sheet legitimately sets
        # colours — from the RESOLVED theme variables.
        shell = _read("window_pages_activity.py")
        tree = ast.parse(shell)
        page_shell = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_page_shell")
        segment = ast.get_source_segment(shell, page_shell)
        for call in re.findall(r"setStyleSheet\((.*?)\)", segment, re.S):
            self.assertNotIn("color:", call)
        self.assertIn("font-weight: 600", segment)
        # The banner carries no scoped sheet of its own any more.
        sidebar = _read("window_shell_sidebar.py")
        self.assertIn('setObjectName("setupBar")', sidebar)
        self.assertNotIn("SURFACE_2", sidebar)


# ----------------------------------------------------------------------
# Item 10 — narrow window (compact tier)
# ----------------------------------------------------------------------
class TestCompactTier(unittest.TestCase):
    def test_window_minimum_is_640(self):
        self.assertEqual(BP.WINDOW_MINIMUM, (640, 500))

    def test_tier_boundaries(self):
        self.assertEqual(BP.tier_for(639), "compact")
        self.assertEqual(BP.tier_for(640), "compact")
        self.assertEqual(BP.tier_for(907), "compact")
        self.assertEqual(BP.tier_for(908), "narrow")
        self.assertEqual(BP.tier_for(1007), "narrow")
        self.assertEqual(BP.tier_for(1008), "comfort")

    def test_compact_floors(self):
        self.assertEqual(BP.stage_floor_for(640), 240)
        self.assertEqual(BP.stage_floor_for(908), 320)
        self.assertEqual(BP.stage_floor_for(1008), 420)
        self.assertEqual(BP.hero_min_width_for(640), 240)
        self.assertEqual(BP.hero_min_width_for(908), 320)
        self.assertEqual(BP.hero_min_width_for(1008), 400)
        self.assertEqual(BP.chat_floor_for(640), 240)
        self.assertEqual(BP.chat_floor_for(908), 300)
        self.assertEqual(BP.chat_floor_for(1008), 300)

    def test_compact_shell_minimum_fits_640(self):
        # 64 rail + 56 page margins + 240 stage + 12 handle +
        # 240 chat = 612 <= 640 (with the chat floor wired; the
        # unwired 300 px chat minimum made it 672 and overflowed).
        total = (BP.SIDEBAR_WIDTH_COMPACT + BP.PAGE_MARGINS
                 + BP.STAGE_FLOOR_COMPACT + BP.SPLITTER_HANDLE
                 + BP.CHAT_FLOOR_COMPACT)
        self.assertEqual(total, 612)
        self.assertLessEqual(total, BP.WINDOW_MINIMUM[0])

    def test_chat_target_at_640(self):
        # Splitter avail at 640: 640 - 64 rail - 56 margins - 12
        # handle = 508. A stored 300/400 px chat clamps to 268
        # (avail minus the 240 stage floor); the chat never drops
        # below its 240 compact floor.
        self.assertEqual(BP.chat_target(508, 300, 640), 268)
        self.assertEqual(BP.chat_target(508, 400, 640), 268)
        self.assertEqual(BP.chat_target(508, 240, 640), 240)

    def test_chat_floor_wired_onto_panel(self):
        # P21 root cause: chat_floor_for existed in the pure
        # logic but _apply_breakpoint_tier never applied it — the
        # panel kept its build-time 300 px minimum. The tier now
        # sets the panel minimum from the tier floor, and the
        # visualizer relaxes to the tier hero floor with it.
        frame = _read("window_shell_frame.py")
        self.assertIn("chat_floor_for", frame)
        self.assertIn("chat.setMinimumWidth(chat_floor_for(width))",
                      frame)
        self.assertIn(
            "visualizer.setMinimumWidth(hero_min_width_for(width))",
            frame)

    def test_approvals_row_wraps(self):
        # The five-chip Remembered-approvals row summed to a
        # 606 px minimum in one QHBoxLayout and clipped 34 px at
        # 640; it is a FlowLayout now (the E-F205 precedent).
        groups = _read("window_settings_groups.py")
        self.assertIn("from .design_system.flowlayout import FlowLayout",
                      groups)
        self.assertIn("allow_row = FlowLayout(spacing=6)", groups)

    def test_contacts_empty_state_wraps(self):
        # Re-render find: the Contacts empty-state item was a
        # 70-char single line and the list clipped it
        # mid-sentence; the Inbox list already wraps, Contacts
        # must too.
        # INTENTIONAL UPDATE (UI Phase 3, 2026-11-25): the
        # names-list is a card grid now — there is no list to
        # wrap. The empty state is a dedicated EmptyState
        # (sentence + suggested task + Add contact action), and
        # the wrap lives in that component: its sentence label
        # word-wraps by construction.
        views = _read("window_pages_views.py")
        self.assertIn(
            "self._views_contacts_empty = EmptyState(", views)
        empty = _read("design_system/empty.py")
        self.assertIn("self._sentence.setWordWrap(True)", empty)


class TestCompactSplitterMechanism(unittest.TestCase):
    def test_compact_splitter_floors_hold_at_640(self):
        if not _qt_available():
            self.skipTest("PySide6 not installed on this interpreter")
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QSplitter, QWidget
        if QApplication.instance() is None:
            QApplication([])
        # The P21 geometry, as a mechanism: splitter avail 508
        # at 640, stage floor 240, chat floor 240, target 268.
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(BP.SPLITTER_HANDLE)
        stage = QWidget()
        chat = QWidget()
        stage.setMinimumWidth(BP.stage_floor_for(640))
        chat.setMinimumWidth(BP.chat_floor_for(640))
        split.addWidget(stage)
        split.addWidget(chat)
        avail = 640 - 64 - 56 - BP.SPLITTER_HANDLE
        target = BP.chat_target(avail, 300, 640)
        split.setSizes([avail - target, target])
        split.setFixedWidth(avail + BP.SPLITTER_HANDLE)
        split.show()
        QApplication.instance().processEvents()
        self.assertGreaterEqual(stage.width(), 240)
        self.assertGreaterEqual(chat.width(), 240)
        self.assertEqual(stage.width() + chat.width(), avail)
        split.deleteLater()

    def test_contacts_list_wraps_long_item(self):
        if not _qt_available():
            self.skipTest("PySide6 not installed on this interpreter")
        from PySide6.QtWidgets import QApplication, QListWidget
        if QApplication.instance() is None:
            QApplication([])
        lst = QListWidget()
        lst.setWordWrap(True)
        lst.addItem("No contact details stored yet — ask VYUK to save "
                    "a detail for someone.")
        lst.setFixedWidth(380)
        lst.show()
        QApplication.instance().processEvents()
        item = lst.item(0)
        # Wrapped: the item needs more than one line of height.
        self.assertGreater(lst.visualItemRect(item).height(), 20)
        lst.deleteLater()


# ----------------------------------------------------------------------
# Item 11 — verbosity control
# ----------------------------------------------------------------------
class TestVerbosity(unittest.TestCase):
    def test_config_field(self):
        fields = {f.name: f for f in dataclasses.fields(ConfigSpec)}
        self.assertIn("reply_verbosity", fields)
        self.assertEqual(
            fields["reply_verbosity"].metadata.get("env"),
            "VYUK_REPLY_VERBOSITY")
        self.assertEqual(ConfigSpec().reply_verbosity, "balanced")

    def test_config_spec_counts(self):
        fields = dataclasses.fields(ConfigSpec)
        env_backed = [f for f in fields if f.metadata.get("env")]
        # Stage 0 features batch 6: 112 -> 113 fields, 107 -> 108
        # env-backed (calendar_quiet_enabled,
        # VYUK_CALENDAR_QUIET_ENABLED).
        # UI upgrade Phase 1 (2026-11-24): 113 -> 115 fields, 108 -> 110
        # env-backed (home_city, VYUK_HOME_CITY; hud_profile,
        # VYUK_HUD_PROFILE).
        self.assertEqual(len(fields), 115)
        self.assertEqual(len(env_backed), 110)

    def test_directives(self):
        self.assertNotEqual(
            _verbosity.reply_verbosity_directive("concise"), "")
        self.assertEqual(
            _verbosity.reply_verbosity_directive("balanced"), "")
        self.assertNotEqual(
            _verbosity.reply_verbosity_directive("detailed"), "")

    def test_output_caps(self):
        self.assertEqual(
            _verbosity.verbosity_max_output_tokens("concise"), 256)
        self.assertIsNone(
            _verbosity.verbosity_max_output_tokens("balanced"))
        self.assertIsNone(
            _verbosity.verbosity_max_output_tokens("detailed"))


# ----------------------------------------------------------------------
# Item 12 — larger-text mode
# ----------------------------------------------------------------------
class TestLargerText(unittest.TestCase):
    def test_choices_run_to_150(self):
        appearance = _read("window_settings_appearance.py")
        self.assertIn("(0.9, 1.0, 1.1, 1.15, 1.2, 1.3, 1.5)",
                      appearance)
        self.assertIn("1.0 / 1.15 / 1.3 / 1.5", appearance)

    def test_painted_widgets_scale(self):
        # Custom-painted text scales through set_font_scale on
        # every painted surface (the audit's dead-key claim was
        # wrong about the chrome key; the painted gap was real).
        for relpath in ("overview_panel.py", "senses_page.py",
                        "memory_web_widget.py", "skills_catalog_widget.py",
                        "skills_galaxy_canvas2d.py"):
            self.assertTrue(
                _has_function(_read(relpath), "set_font_scale"),
                f"{relpath} has no set_font_scale")

    def test_stylesheet_scales_base_sizes(self):
        # The chrome key was live (P7 proved it); the scale must
        # reach the new 150% choice, not stop at 120%.
        scaled = build_stylesheet("Light", font_scale=1.5)
        self.assertIn("19.5px", scaled)


# ----------------------------------------------------------------------
# Census unmoved (no new tools in batch 5)
# ----------------------------------------------------------------------
class TestCensusUnmoved(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.decls = {d["name"]: d for d in tools.get_declarations()}
        doc = (_TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        cls.totals = json.loads(re.search(
            r"AUDIT-TOTALS:\s*(\{[^}]*\})", doc).group(1))
        cls.counts = json.loads(re.search(
            r"AUDIT-COUNTS:\s*(\{[^}]*\})", doc).group(1))
        section_2c = doc[doc.index("## 2c."):doc.index("## 3.")]
        cls.rows_2c = re.findall(r"^\| `([a-z0-9_]+)` \|",
                                 section_2c, re.M)

    def test_totals(self):
        self.assertEqual(
            (self.totals["total"], self.totals["read"],
             self.totals["effectful"], self.totals["confirm_gated"],
             self.totals["already_verified_in_handler"]),
            (772, 386, 386, 170, 320))  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_params_grouped_closed(self):
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in self.decls.values())
        grouped = sum(len(v) for v in GROUP_MEMBERS.values())
        closed = sum(
            1 for d in self.decls.values()
            if d.get("parameters", {}).get("additionalProperties")
            is False)
        self.assertEqual(total_params, 2126)  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(grouped, 772)  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170; UI Phase 2 (2026-11-25): grouped 769 -> 772 — click_anywhere + 2 plugin tools join the catalogue
        self.assertEqual(closed, 771)  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_section_2c_rows_labels(self):
        self.assertEqual(len(self.rows_2c), 386)  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(len(set(self.rows_2c)), 386)  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(len(_labels.TOOL_FRIENDLY_NAMES), 697)  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_profiles(self):
        from core.funnel import tool_profiles as profiles  # noqa: E402
        self.assertEqual(
            len(profiles.profile_declarations(tools, "desktop")), 76)


if __name__ == "__main__":
    unittest.main()
