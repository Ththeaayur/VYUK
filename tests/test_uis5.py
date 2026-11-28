"""UI Slice 5 — responsive + keyboard QA (complexity doc slice 5).

Lab honesty (read first): GUI modules import frozen ``voice_assistant.*``
names and build real Qt widgets, so they cannot be imported or driven
here, and no Windows render exists in this lab. This suite therefore
proves STRUCTURE + ARITHMETIC by AST over the real files: geometry
constants recomputed from code, scroll evidence per page, keyboard
routes, focus rules, and gate consultation at every animation site.
It does NOT prove rendered appearance. Field proof on real Windows
is owed: sizes x scales matrix, keyboard-only pass, high-contrast
pass, reduced-motion pass, tab-focus-frame pixels.
"""

import ast
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
GUI = HERE / "gui"


def parse(relpath):
    text = (GUI / relpath).read_text(encoding="utf-8")
    return text, ast.parse(text)


# INTENTIONAL (refactor P1 step 5): window_shell.py split verbatim
# into panel mixins; method names are unique across the shell
# modules, so the concatenated parse keeps every assertion.
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


def _shell_text():
    return "".join((GUI / n).read_text(encoding="utf-8")
                   for n in SHELL_FILES)


def _shell_parse():
    text = _shell_text()
    return text, ast.parse(text)


def funcs(tree):
    """All FunctionDefs (including nested) by name."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            out.setdefault(node.name, []).append(node)
    return out


def calls_with_attr(tree, attr):
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == attr
        ):
            out.append(node)
    return out


def int_args(call):
    vals = []
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
            vals.append(arg.value)
    return vals


def enclosing_funcs(tree):
    """Map every node to its enclosing FunctionDef (or None)."""
    parent = {}

    def visit(node, fn):
        parent[node] = fn
        for child in ast.iter_child_nodes(node):
            visit(child, fn if fn is not None else (
                node if isinstance(node, ast.FunctionDef) else None))

    visit(tree, None)
    return parent


class TestGeometry(unittest.TestCase):
    def setUp(self):
        # INTENTIONAL (refactor P1 step 5): see _shell_parse above. Assertions unchanged.
        self.text, self.tree = _shell_parse()
        self.fn = funcs(self.tree)

    def _fn_src(self, name):
        node = self.fn[name][0]
        return ast.get_source_segment(self.text, node)

    def test_window_minimum_equals_floor_sum(self):
        # UIS5: sidebar 220 + page margins 56 + stage 420 +
        # handle 12 + chat 300 = 1008 == the COMFORT tier floor.
        # INTENTIONAL (UI breakpoints slice, 2026-10-30): the
        # hard window minimum is now the NARROW derivation
        # (220 + 28 + 28 + 320 + 12 + 300 = 908, height 500);
        # 1008 survives as the comfort tier, not the minimum.
        # The v5.57.1 comment computed 952 because it forgot
        # the page margins; the floor below 1008 squeezed the
        # stage/chat below their floors.
        shell = self._fn_src("_build_sidebar")
        sidebar = [int_args(c)[0] for c in calls_with_attr(
            ast.parse(shell), "setFixedWidth")]
        self.assertIn(220, sidebar)
        home = self._fn_src("_build_assistant_page")
        home_tree = ast.parse(home)
        margins = [int_args(c) for c in calls_with_attr(
            home_tree, "setContentsMargins")]
        self.assertIn([28, 16, 28, 20], margins)
        stage = [int_args(c)[0] for c in calls_with_attr(
            home_tree, "setMinimumWidth")]
        self.assertIn(420, stage)
        self.assertIn(300, stage)
        handle = [int_args(c)[0] for c in calls_with_attr(
            home_tree, "setHandleWidth")]
        self.assertIn(12, handle)
        self.assertEqual(220 + 28 + 28 + 420 + 12 + 300, 1008)
        self.assertEqual(220 + 28 + 28 + 320 + 12 + 300, 908)
        # The hard minimum is the tier constant, applied via the
        # breakpoints module (see test_uibreakpoints for the
        # full tier table).
        self.assertIn("self.setMinimumSize(*WINDOW_MINIMUM)", self.text)

    def test_default_width_achieves_frozen_chat(self):
        # UIS5: at the old 1080 default the width guard clamped
        # the chat to 372 (avail 792 - stage floor 420), not the
        # frozen 400. At 1108: 1108 - 220 - 56 - 12 = 820 =
        # stage 420 + chat 400 exactly.
        # INTENTIONAL (UI breakpoints slice, 2026-10-30): the
        # default is now clamped to the available geometry —
        # still exactly 1108x750 on a big screen (see
        # test_uibreakpoints for the clamp table), with a
        # no-screen fallback to the frozen default.
        self.assertIn("default_window_size(", self.text)
        resizes = [int_args(c) for c in calls_with_attr(
            self.tree, "resize")]
        self.assertIn([1108, 750], resizes)
        self.assertNotIn([1080, 750], resizes)
        self.assertEqual(1108 - 220 - 56 - 12, 420 + 400)

    def test_no_old_minimum_values(self):
        mins = [int_args(c) for c in calls_with_attr(
            self.tree, "setMinimumSize")]
        self.assertNotIn([960, 620], mins)

    def test_composer_input_floor(self):
        # UIS5 composer arithmetic (UI-03). Dock spacing 12;
        # send 44, mic 56, screen 42; waveform 104 fixed.
        home = self._fn_src("_build_assistant_page")
        home_tree = ast.parse(home)
        spacing = [int_args(c)[0] for c in calls_with_attr(
            home_tree, "setSpacing")]
        self.assertIn(12, spacing)
        fixed = [int_args(c) for c in calls_with_attr(
            home_tree, "setFixedSize")]
        self.assertIn([44, 44], fixed)
        self.assertIn([56, 56], fixed)
        self.assertIn([42, 42], fixed)
        # INTENTIONAL (refactor P1 step 5): WaveformWidget moved verbatim
        # to window_widgets_chatbits.py. Assertions unchanged.
        wtext, wtree = parse("window_widgets_chatbits.py")
        # Class-scoped lookup: the file has many __init__ defs.
        wave = ""
        for node in ast.walk(wtree):
            if isinstance(node, ast.ClassDef) and node.name == "WaveformWidget":
                init = next(n for n in node.body
                            if isinstance(n, ast.FunctionDef)
                            and n.name == "__init__")
                wave = ast.get_source_segment(wtext, init)
        self.assertIn("setFixedSize(104, 30)", wave)
        full = 44 + 56 + 42 + 104 + 4 * 12
        self.assertEqual(full, 294)
        narrow = 44 + 56 + 104 + 3 * 12
        self.assertEqual(narrow, 240)
        # E-F103 (2026-11-20): threshold 420 (was 380) — the full
        # dock leaves 126 px at it; at the 300 px panel floor with
        # the screen button hidden: 60 px (unchanged — the narrow
        # dock's widths did not move, only the threshold did, so
        # the input keeps room for the shortened placeholder).
        self.assertIn("_DOCK_NARROW_WIDTH = 420", self.text)
        self.assertEqual(420 - full, 126)
        self.assertEqual(300 - narrow, 60)

    def test_narrow_rule_wiring(self):
        # The rule exists, compares against the threshold, hides
        # the button, and is called from both width-change paths
        # (resize/first-show guard + splitter drags). The button
        # is stored on self, and the overflow menu keeps the
        # action reachable at every width.
        self.assertIn("def _apply_dock_narrow_rule", self.text)
        rule = self._fn_src("_apply_dock_narrow_rule")
        # The width is read once into panel_w, then compared —
        # the comparison is against the local, not a fresh read.
        self.assertIn("panel_w = panel.width()", rule)
        self.assertIn("panel_w >= _DOCK_NARROW_WIDTH", rule)
        # The screen-share button is the local `screen` (renamed
        # from `btn` in the Build 1 rewrite — same hide).
        self.assertIn("screen.setVisible", rule)
        # E-F103 (2026-11-20): Dictate hides entirely below
        # _DICTATE_HIDE_WIDTH (380) — the rule sets its text
        # (icon-only below 420) and its visibility, in that order.
        self.assertIn("_DICTATE_HIDE_WIDTH = 380", self.text)
        self.assertIn("dictate.setVisible", rule)
        self.assertIn("panel_w >= _DICTATE_HIDE_WIDTH", rule)
        fix = self._fn_src("_fix_chat_width")
        self.assertIn("_apply_dock_narrow_rule()", fix)
        moved = self._fn_src("_on_chat_splitter_moved")
        self.assertIn("_apply_dock_narrow_rule()", moved)
        self.assertIn("self.btn_screen = QPushButton()", self.text)
        menu = self._fn_src("_show_header_overflow_menu")
        self.assertIn('"Share screen"', menu)
        self.assertIn("_send_screen_capture", menu)


class TestScrollContainment(unittest.TestCase):
    def test_every_page_keeps_controls_reachable(self):
        # Per-page scroll evidence (UIS5 audit). Lists scroll
        # internally; cards stretch lists; dashboards reflow or
        # scroll; canvases/web views scroll internally.
        # GUI split #8 INTENTIONAL: pages builders moved verbatim to
        # window_pages_activity.py (activity) + window_pages_memory.py
        # (memory/skills); method names are unique across the two
        # modules, so the concatenated parse keeps every assertion.
        act_text, _ = parse("window_pages_activity.py")
        mem_text, _ = parse("window_pages_memory.py")
        pages_text = act_text + mem_text
        pages_fns = funcs(ast.parse(pages_text))
        evidence = []

        def fn_src(name):
            return ast.get_source_segment(
                pages_text, pages_fns[name][0])

        # Activity: lists scroll internally; Scheduled tab is the
        # reminders page with its own outer scroll area.
        act = fn_src("_build_activity_page")
        self.assertIn("QListWidget()", act)
        evidence.append("Activity lists")
        rem = fn_src("_build_reminders_page")
        self.assertIn("QScrollArea()", rem)
        evidence.append("Activity Scheduled scroll")
        # Memory / Skills: list inside a stretch=1 card.
        mem = fn_src("_build_memory_page")
        self.assertIn("self.memory_list = QListWidget()", mem)
        self.assertIn("layout.addWidget(card, stretch=1)", mem)
        evidence.append("Memory list")
        skl = fn_src("_build_skills_page")
        self.assertIn("self.plugin_list = QListWidget()", skl)
        # UI Phase 2 (2026-11-25): the page is a left column (view row,
        # catalogue/galaxy, plugins, rescan) beside the detail drawer,
        # so the plugins card is added to `left`, not the page layout —
        # still stretch=1, still inside the page's outer scroll area.
        self.assertIn("left.addWidget(card, stretch=1)", skl)
        evidence.append("Skills list")
        # Settings / Senses / Doctor: outer scroll areas.
        for rel in ("window_settings.py", "senses_page.py",
                    "doctor_page.py"):
            t, _ = parse(rel)
            self.assertIn("QScrollArea(", t, rel)
            evidence.append(rel + " scroll")
        # Home: chat transcript + chip strip scroll areas.
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        # E-F101: the chip strip is a _ChipScrollArea now — a
        # QScrollArea subclass whose height follows the wrapped
        # row — so the construction pin names the subclass.
        shell_text, _ = parse("window_shell_assistant.py")
        self.assertIn("self.chat_scroll = QScrollArea()", shell_text)
        self.assertIn("self._chip_scroll = _ChipScrollArea()",
                      shell_text)
        widgets_text, _ = parse("window_shell_assistant_widgets.py")
        self.assertIn("class _ChipScrollArea(QScrollArea)",
                      widgets_text)
        evidence.append("Home scrolls")
        # Pulse: v5.57 replaced the column grid (5/3/2/1) with a
        # vertical stack of bar meters — seven rows (five BarMeter
        # rows + two number-only rows), each with a live value
        # label whose width tracks the text so values never clip.
        # The page keeps controls reachable by an outer QScrollArea
        # (window_pages_system._build_pulse_page), and the footnote
        # wraps (E-F206) so no horizontal scrollbar appears at 908.
        pulse_text, _ = parse("pulse_page.py")
        for row in ('("cpu", "CPU", ACCENT)',
                    '("mem", "Memory", ACCENT)',
                    '("disk", "Disk", ACCENT)',
                    '("net", "Network", ACCENT_2)',
                    '("battery", "Battery", ACCENT_2)',
                    '("uptime", "Uptime", None)',
                    '("procs", "Processes", None)'):
            self.assertIn(row, pulse_text)
        self.assertIn("self.dials: dict[str, BarMeter]", pulse_text)
        self.assertIn("self.value_label = QLabel", pulse_text)
        self.assertIn("self.value_label.setMinimumWidth(",
                      pulse_text)
        self.assertIn("self.bars_note.setWordWrap(True)", pulse_text)
        sys_text, sys_tree = parse("window_pages_system.py")
        sys_fns = funcs(sys_tree)
        pulse_fn = ast.get_source_segment(
            sys_text, sys_fns["_build_pulse_page"][0])
        self.assertIn("scroll = QScrollArea()", pulse_fn)
        self.assertIn("scroll.setWidgetResizable(True)", pulse_fn)
        self.assertIn("scroll.setWidget(page)", pulse_fn)
        evidence.append("Pulse scroll")
        # First-open prompt + onboarding: scroll areas (re-pinned
        # from the first-open + safe batches, not redone here).
        fo_text, _ = parse("firstopen_prompt.py")
        self.assertIn("QScrollArea", fo_text)
        ob_text, _ = parse("onboarding.py")
        self.assertIn("QScrollArea", ob_text)
        self.assertNotIn("setFixedSize(780, 560)", ob_text)
        self.assertIn("availableGeometry", ob_text)
        evidence.append("First-open + onboarding scroll")
        self.assertEqual(len(evidence), 10)


class TestKeyboard(unittest.TestCase):
    def test_shortcuts_and_keys(self):
        # INTENTIONAL (refactor P1 step 5): Ctrl+K in frame, Ctrl+F in assistant; see _shell_parse above.
        shell_text, _ = _shell_parse()
        self.assertIn('QKeySequence("Ctrl+K")', shell_text)
        self.assertIn('QKeySequence("Ctrl+F")', shell_text)
        # GUI split #8 INTENTIONAL: activity builders moved verbatim to
        # window_pages_activity.py. Assertion unchanged.
        pages_text, _ = parse("window_pages_activity.py")
        self.assertIn('QKeySequence("Del")', pages_text)
        # Palette closes on Escape; composer sends on Enter and
        # recalls on Up (see _ComposerEdit wiring in shell).
        # INTENTIONAL (refactor P1 step 5): CommandPalette moved verbatim
        # to window_widgets_palette.py. Assertions unchanged.
        w_text, _ = parse("window_widgets_palette.py")
        self.assertIn("Key_Escape", w_text)
        self.assertIn("self.close()", w_text)
        # GUI split #12 batch 1 INTENTIONAL: keyPressEvent/keyReleaseEvent
        # moved verbatim to window_memory_learned.py. Assertion unchanged.
        mem_text, _ = parse("window_memory_learned.py")
        # Space is push-to-talk EXCEPT when a text field has focus
        # (typing must never trigger the mic).
        self.assertIn("isinstance(focused, (QLineEdit,))", mem_text)
        # First-open: two buttons, No is the default — keyboard
        # Enter/Esc both land on No (never an accidental Yes).
        fo_text, _ = parse("firstopen_prompt.py")
        self.assertIn("self.btn_yes = QPushButton", fo_text)
        self.assertIn("self.btn_no = QPushButton", fo_text)
        self.assertIn("self.btn_no.setDefault(True)", fo_text)
        # Onboarding: Space/Return activate (button subclass).
        # INTENTIONAL (GUI split #13 batch 1): Key_Space/Key_Return
        # handling moved verbatim with _ChoiceCard to widgets.
        # Assertion unchanged.
        ob_text, _ = parse("onboarding_widgets.py")
        self.assertIn("Key_Space", ob_text)
        self.assertIn("Key_Return", ob_text)
        # Advanced disclosure + Details + chips are real buttons.
        # INTENTIONAL (refactor P1 step 5): the disclosure button moved
        # verbatim to window_settings_groups.py. Assertion unchanged.
        st_text, _ = parse("window_settings_groups.py")
        self.assertIn('self.more_toggle_btn = QPushButton', st_text)
        ts_text, _ = parse("task_state.py")
        self.assertIn('details_btn = QPushButton("Details")', ts_text)
        # GUI split #9 INTENTIONAL: chip row lives in the nav module now.
        chat_text, _ = parse("window_chat_nav.py")
        self.assertIn("chip = QPushButton(label)", chat_text)

    def test_focus_visible(self):
        # Explicit :focus borders for the main controls; list
        # focus shows via selection; tab focus uses Qt's default
        # frame (no override, no suppression — field-verify the
        # pixels on real Windows).
        # INTENTIONAL (GUI split #6): the :focus rules live in the QSS
        # template, which moved verbatim from gui/style.py to
        # gui/style_sheet.py. Assertions unchanged.
        style_text, _ = parse("style_sheet.py")
        for rule in ("QPushButton:focus", "QPushButton#navBtn:focus",
                     "QPushButton#micBtn:focus", "QPushButton#chipBtn:focus",
                     "QPushButton#chipSuggest:focus",
                     "QPushButton#cmdPill:focus", "QLineEdit:focus",
                     "QComboBox:focus", "QSpinBox:focus", "#dockInput:focus",
                     "QCheckBox::indicator:focus"):
            self.assertIn(rule, style_text, rule)
        # outline:none is scoped (combo dropdowns, lists) — never
        # a global suppression.
        for m in re.finditer(r"outline: none;", style_text):
            window = style_text[max(0, m.start() - 400):m.start()]
            self.assertTrue("QComboBox" in window or "QListWidget" in window,
                            "unscoped outline:none near: " + window[-80:])
        self.assertNotIn("QTabBar", style_text)


class TestMotionGates(unittest.TestCase):
    def test_all_animation_sites_gated(self):
        # Complete inventory of QPropertyAnimation /
        # QParallelAnimationGroup / QSequentialAnimationGroup
        # constructions in gui/. Every site's enclosing function
        # must consult a motion gate: _animate_enabled (page
        # transitions, startup fade, bubble entrances), the
        # palette's parent-window gate, or the splash's own
        # _reduced flag (wired to _motion_reduced() at build).
        # The shell:379 hit in earlier greps was a comment, not
        # a construction — AST excludes it, so the count is 15.
        anim_types = {"QPropertyAnimation", "QParallelAnimationGroup",
                      "QSequentialAnimationGroup"}
        sites = []
        for path in sorted(GUI.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
            enclosing = enclosing_funcs(tree)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in anim_types):
                    fn = enclosing[node]
                    seg = ast.get_source_segment(
                        text, fn) if fn is not None else ""
                    sites.append((path.name, fn.name if fn else "?",
                                  seg))
        # UI upgrade Phase 1 (2026-11-24): 15 -> 16. The 16th
        # site is the Home boot moment (gui/window_home_hud.py
        # _home_boot, a 600 ms hero opacity fade). H-35 exempts
        # the boot moment from the motion audit, and it is
        # gated on reduced motion (_reduced: skipped under
        # the Minimal profile), so the gating check below
        # still holds for every site.
        self.assertEqual(len(sites), 16)
        for fname, fnname, seg in sites:
            self.assertTrue(
                "_animate_enabled" in seg
                or "_reduced" in seg
                or "_palette_animate" in seg,
                f"ungated animation site: {fname}:{fnname}")
        # The safe-batch sites are re-pinned, not assumed.
        # GUI split #9 INTENTIONAL: the animate gate lives in showEvent,
        # which lives in the nav module now.
        chat_text, _ = parse("window_chat_nav.py")
        self.assertIn("if not self._animate_enabled():", chat_text)
        # INTENTIONAL (refactor P1 step 5): CommandPalette moved verbatim
        # to window_widgets_palette.py. Assertions unchanged.
        w_text, _ = parse("window_widgets_palette.py")
        self.assertIn("_palette_animate", w_text)
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        shell_text, _ = parse("window_shell_frame.py")
        self.assertIn("reduced_motion=self._motion_reduced()",
                      shell_text)


class TestSizeMatrix(unittest.TestCase):
    def test_static_sizes(self):
        # Static half of the acceptance matrix, from the geometry
        # constants (render half is field-owed). Splitter avail =
        # width - sidebar 220 - margins 56 - handle 12; the width
        # guard clamps chat to min(400, avail - stage floor),
        # floor 300 (stage floor 420 comfort, 320 narrow).
        def chat_at(width):
            avail = width - 220 - 56 - 12
            floor = 320 if width < 1008 else 420
            return max(300, min(400, avail - floor))

        self.assertEqual(chat_at(1008), 300)  # comfort floor: exact fit
        self.assertEqual(chat_at(908), 300)  # narrow floor: exact fit
        self.assertEqual(chat_at(960), 352)  # narrow first-show
        self.assertEqual(chat_at(1280), 400)  # stage 592 >= 420
        self.assertEqual(chat_at(1920), 400)
        # 150% on a 1920x1080 display: physical minimum fits.
        self.assertLessEqual(908 * 1.5, 1920)
        self.assertLessEqual(500 * 1.5, 1080)
        # INTENTIONAL (UI breakpoints slice, 2026-10-30): the
        # 200%-on-1080p finding is SOLVED, not just pinned — the
        # new physical minimum (1816x1000) fits 1920x1080, and
        # the 1366x768@125% physical minimum (1135x625) fits
        # 1366x768. The old 2016x1240 / 1260x775 did not.
        self.assertEqual((908 * 2, 500 * 2), (1816, 1000))
        self.assertLessEqual(1000, 1080)
        self.assertLessEqual(1816, 1920)
        self.assertLessEqual(500 * 1.25, 768)
        self.assertLessEqual(908 * 1.25, 1366)


if __name__ == "__main__":
    unittest.main()
