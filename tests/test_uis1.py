"""UI Slice 1 — IA change (2026-10-30, complexity doc CX-01,
owner-approved): sidebar Home / Activity / Memory + Settings
footer + CORE / MISSION / ARCHIVE / SYSTEM labels; Skills via palette; stack indices
unchanged.

LAB HONESTY NOTE: the GUI modules import the frozen
``voice_assistant.*`` package names and build real Qt widgets,
so they cannot be imported or driven in this lab (PySide6
Essentials only, no QtWebEngine, no real GUI driving). This
suite therefore verifies STRUCTURE + ROUTING by AST over the
real files, following the safe-batch precedent. It proves the
navigation wiring and the mapping, NOT rendered appearance:
field proof on real Windows is still owed (navigate EVERY
destination, keyboard-only pass, Activity tabs with real
reminders/plans/sessions).
"""

import ast
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent
GUI = TREE / "gui"


def _source(name):
    return (GUI / name).read_text(encoding="utf-8")


# INTENTIONAL (refactor P1 step 5): window_shell.py split verbatim
# into panel mixins; the geometry pins span frame + sidebar, so read
# the shell surface concatenated. Assertions unchanged.
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
    return "".join(_source(n) for n in SHELL_FILES)


def _core_source(name):
    return (TREE / "core" / name).read_text(encoding="utf-8")


def _tree(name):
    return ast.parse(_source(name))


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found")


def _const_list(node):
    """An ast.List of constant tuples -> python values, or None."""
    if not isinstance(node, ast.List):
        return None
    out = []
    for elt in node.elts:
        if not isinstance(elt, ast.Tuple):
            return None
        vals = []
        for v in elt.elts:
            if not isinstance(v, ast.Constant):
                return None
            vals.append(v.value)
        out.append(tuple(vals))
    return out


def _assign_value(func_node, name):
    for node in ast.walk(func_node):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
                if isinstance(target, ast.Attribute) \
                        and target.attr == name:
                    return node.value
    raise AssertionError(f"assignment to {name!r} not found")


def _calls(func_node):
    return [n for n in ast.walk(func_node) if isinstance(n, ast.Call)]


def _attr_calls(func_node, attr):
    return [c for c in _calls(func_node)
            if isinstance(c.func, ast.Attribute) and c.func.attr == attr]


EXPECTED_DEFS = [("Home", "spark", 0),
                 ("Activity", "clock", 1),
                 ("Memory", "database", 2),
                 # UI Phase 2 (2026-11-25): Skills gains its own
                 # sidebar button ("shapes", stack 3).
                 ("Skills", "shapes", 3),
                 ("System Pulse", "pulse", 5),
                 ("Senses", "senses", 6),
                 # UI Phase 2 (2026-11-25): Doctor / Contacts /
                 # Calls move to their Phosphor icons.
                 ("Doctor", "heartbeat", 7),
                 # Stage 0 features batch 1 (Group D): the views —
                 # Calendar / Inbox / Contacts / Calls (stack 8–11),
                 # before the footer Settings.
                 ("Calendar", "grid", 8),
                 ("Inbox", "send", 9),
                 ("Contacts", "address-book", 10),
                 ("Calls", "phone-call", 11),
                 # UI Phase 5 (2026-11-27): Notes / Finance /
                 # Devices join as workspace buttons (stack 12–14),
                 # before the footer Settings.
                 ("Notes", "note", 12),
                 ("Finance", "currency-dollar", 13),
                 ("Devices", "house", 14),
                 ("Settings", "sliders", 4)]


class TestSidebarStructure(unittest.TestCase):
    def test_nav_defs_exact_mapping(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        func = _func(_tree("window_shell_sidebar.py"), "_build_sidebar")
        defs = _const_list(_assign_value(func, "_nav_defs"))
        self.assertEqual(defs, EXPECTED_DEFS)

    def test_nav_indices_and_icons_aligned(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        func = _func(_tree("window_shell_sidebar.py"), "_build_sidebar")
        raw = _assign_value(func, "_nav_page_indices")
        indices = [e.value for e in raw.elts
                   if isinstance(e, ast.Constant)]
        # Stage 0 features batch 1 (Group D): the four views take
        # stack 8–11; the frozen 0–7 map is unchanged.
        # UI Phase 2 (2026-11-25): Skills takes stack 3.
        # UI Phase 5 (2026-11-27): Notes / Finance / Devices take
        # stack 12–14; Settings stays the footer (stack 4).
        self.assertEqual(indices, [0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11,
                                   12, 13, 14, 4])
        icons = _assign_value(func, "_nav_icon_names")
        icon_vals = [e.value for e in icons.elts
                     if isinstance(e, ast.Constant)]
        self.assertEqual(icon_vals, ["spark", "clock", "database",
                                     "shapes", "pulse", "senses",
                                     "heartbeat", "grid", "send",
                                     "address-book", "phone-call",
                                     "note", "currency-dollar",
                                     "house", "sliders"])
        self.assertEqual(len(indices), len(icon_vals))
        self.assertEqual(len(indices), len(EXPECTED_DEFS))
        # Every def's page matches the indices list at its position.
        for i, (_, _, page) in enumerate(EXPECTED_DEFS):
            self.assertEqual(indices[i], page)

    def test_layout_order_sections_footer(self):
        # Restore 2026-11-01 (owner-delegated): the §9.2 section
        # labels inside the same 220 px sidebar — labels only,
        # same buttons, same order, same stack indices. The old
        # "Advanced / Explore" label is replaced by SYSTEM.
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        src = _source("window_shell_sidebar.py")
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        func = _func(_tree("window_shell_sidebar.py"), "_build_sidebar")
        segment = ast.get_source_segment(src, func)
        # Code lines only (bare substrings would match the header
        # comment and the brand row's addStretch first).
        order = ['_core_label = QLabel("CORE")',
                 "layout.addWidget(self.nav_buttons[0])",
                 '_mission_label = QLabel("MISSION")',
                 "layout.addWidget(self.nav_buttons[1])",
                 '_archive_label = QLabel("ARCHIVE")',
                 "layout.addWidget(self.nav_buttons[2])",
                 # UI Phase 2 (2026-11-25): CAPABILITIES for Skills.
                 '_capabilities_label = QLabel("CAPABILITIES")',
                 "layout.addWidget(self.nav_buttons[3])",
                 '_system_label = QLabel("SYSTEM")',
                 "for btn in self.nav_buttons[4:7]:",
                 # Stage 0 features batch 1 (Group D): the VIEWS
                 # section sits between SYSTEM and the stretch.
                 # INTENTIONAL UPDATE (UI Phase 3, 2026-11-25): the
                 # section is WORKSPACES now.
                 # INTENTIONAL UPDATE (UI Phase 5, 2026-11-27): the
                 # section holds seven workspace buttons now
                 # (Calendar / Inbox / Contacts / Calls / Notes /
                 # Finance / Devices, nav_buttons[7:14]).
                 '_views_label = QLabel("WORKSPACES")',
                 "for btn in self.nav_buttons[7:14]:",
                 "layout.addStretch()",
                 "layout.addWidget(self.nav_buttons[14])"]
        positions = [segment.index(marker) for marker in order]
        self.assertEqual(positions, sorted(positions),
                         "sidebar layout order is not CORE -> "
                         "MISSION -> ARCHIVE -> CAPABILITIES -> SYSTEM -> "
                         "VIEWS -> stretch -> footer Settings")
        self.assertNotIn('QLabel("Advanced / Explore")', segment,
                         "the old Advanced / Explore label survives")
        self.assertNotIn("_adv_label", segment,
                         "the old Advanced / Explore label variable "
                         "survives")
        self.assertIn('QLabel("CAPABILITIES")', segment,
                      "CAPABILITIES groups the Skills button since "
                      "UI Phase 2")

    def test_skills_button_present_no_renames(self):
        # UI Phase 2 (2026-11-25): Skills HAS a sidebar button
        # now (nav_buttons[3], CAPABILITIES) — the S1 palette-only
        # shape is gone. The internal names stay unrenamed.
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        func = _func(_tree("window_shell_sidebar.py"), "_build_sidebar")
        defs = _const_list(_assign_value(func, "_nav_defs"))
        labels = [label for label, _, _ in defs]
        self.assertIn("Skills", labels,
                      "Skills must have a sidebar button since UI Phase 2")
        for kept in ("System Pulse", "Senses", "Doctor"):
            self.assertIn(kept, labels,
                          f"internal name {kept!r} was renamed or lost")
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        segment = ast.get_source_segment(_source("window_shell_sidebar.py"), func)
        # UI Phase 2 (2026-11-25): "Capabilities" leaves the held
        # list — it is now the adopted CAPABILITIES section name,
        # not an untested CX-06 rename.
        for held in ("Task history", "Automations",
                     "Device status", "Diagnostics"):
            self.assertNotIn(held, segment,
                             f"CX-06 rename {held!r} applied without "
                             "user testing")

    def test_settings_is_footer_not_primary(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        func = _func(_tree("window_shell_sidebar.py"), "_build_sidebar")
        defs = _const_list(_assign_value(func, "_nav_defs"))
        self.assertEqual(defs[-1][0], "Settings")
        self.assertEqual(defs[:3],
                         EXPECTED_DEFS[:3],
                         "primary list is not exactly Home/Activity/Memory")


class TestStackUnchanged(unittest.TestCase):
    def test_stack_order_unchanged(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        # INTENTIONAL UPDATE (Stage 0 features batch 1, 2026-11-22):
        # the four views pages join the stack at indices 8-11
        # (calendar, inbox, contacts, calls) — indices 0-7 are
        # FROZEN (Home, Activity, Memory, Skills, Settings,
        # System Pulse, Senses, Doctor), in the same order.
        # INTENTIONAL UPDATE (UI Phase 5, 2026-11-27): Notes /
        # Finance / Devices join at indices 12-14, in that order.
        func = _func(_tree("window_shell_sidebar.py"), "_build_ui")
        builders = []
        for call in _calls(func):
            if isinstance(call.func, ast.Attribute) \
                    and call.func.attr == "addWidget" \
                    and call.args \
                    and isinstance(call.args[0], ast.Call) \
                    and isinstance(call.args[0].func, ast.Attribute) \
                    and call.args[0].func.attr.endswith("_page"):
                builders.append(call.args[0].func.attr)
        self.assertEqual(builders,
                         ["_build_assistant_page",
                          "_build_activity_page",
                          "_build_memory_page",
                          "_build_skills_page",
                          "_build_settings_page",
                          "_build_pulse_page",
                          "_build_senses_page",
                          "_build_doctor_page",
                          "_build_calendar_page",
                          "_build_inbox_page",
                          "_build_contacts_page",
                          "_build_calls_page",
                          "_build_notes_page",
                          "_build_finance_page",
                          "_build_devices_page"])

    def test_skills_page_not_deleted(self):
        # GUI split #8 INTENTIONAL: _build_skills_page moved verbatim to
        # window_pages_memory.py. Assertion unchanged.
        _func(_tree("window_pages_memory.py"), "_build_skills_page")


class TestActivityPage(unittest.TestCase):
    def test_activity_tabs(self):
        # GUI split #8 INTENTIONAL: _build_activity_page moved verbatim to
        # window_pages_activity.py. Assertion unchanged.
        func = _func(_tree("window_pages_activity.py"), "_build_activity_page")
        dump = ast.dump(func)
        self.assertIn("QTabWidget", dump)
        tabs = []
        for call in _attr_calls(func, "addTab"):
            if len(call.args) >= 2 \
                    and isinstance(call.args[1], ast.Constant):
                tabs.append(call.args[1].value)
        self.assertEqual(tabs, ["In progress", "Scheduled", "History",
                                "Mission Log"])
        self.assertIn("_build_reminders_page", dump)
        for attr in ("_activity_tabs", "_activity_in_progress_list",
                     "_activity_history_list", "_mission_log_list",
                     "_mission_log_detail"):
            self.assertIn(attr, dump, f"{attr} not set on Activity page")

    def test_scheduled_preserves_safe_batch(self):
        # Mirrors the safe-batch pins so S1 cannot silently break
        # them: scroll area + widget names + corrected subtitle.
        # GUI split #8 INTENTIONAL: _build_reminders_page moved verbatim to
        # window_pages_activity.py. Assertion unchanged.
        func = _func(_tree("window_pages_activity.py"), "_build_reminders_page")
        dump = ast.dump(func)
        self.assertIn("QScrollArea", dump)
        resizable = [c for c in _attr_calls(func, "setWidgetResizable")
                     if len(c.args) == 1
                     and isinstance(c.args[0], ast.Constant)
                     and c.args[0].value is True]
        self.assertTrue(resizable, "no setWidgetResizable(True) call")
        self.assertTrue(_attr_calls(func, "setWidget"),
                        "scroll area never given a content widget")
        assigned = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Attribute) \
                    and isinstance(node.value, ast.Name) \
                    and node.value.id == "self":
                assigned.add(node.attr)
        # UI rethink Build 2: the old summary label is gone —
        # its line IS the Scheduled headline now, so the
        # attribute no longer exists to be assigned.
        for name in ("reminders_list",
                     "input_reminder_msg", "input_reminder_min",
                     "chk_reminder_at", "input_reminder_h",
                     "input_reminder_m", "spin_brief_hour",
                     "routines_container", "plans_container"):
            self.assertIn(name, assigned, f"{name} lost in restructure")
        self.assertNotIn("live only in this session", dump)
        self.assertIn("survive a restart", dump)

    def test_select_activity_tab(self):
        # GUI split #8 INTENTIONAL: _select_activity_tab moved verbatim to
        # window_pages_activity.py. Assertion unchanged.
        func = _func(_tree("window_pages_activity.py"), "_select_activity_tab")
        dump = ast.dump(func)
        self.assertIn("tabText", dump)
        self.assertIn("setCurrentIndex", dump)

    def test_show_page_refreshes_activity(self):
        # GUI split #9 INTENTIONAL: _show_page lives in the nav module now.
        func = _func(_tree("window_chat_nav.py"), "_show_page")
        dump = ast.dump(func)
        for call in ("_refresh_routines", "_refresh_plans",
                     "_refresh_activity_tabs"):
            self.assertIn(call, dump, f"{call} not called on page show")

    def test_refresh_activity_tabs_reads_ledger(self):
        # GUI split #12 batch 2 INTENTIONAL: _refresh_activity_tabs
        # moved verbatim to window_memory_activity.py. Assertion unchanged.
        func = _func(_tree("window_memory_activity.py"), "_refresh_activity_tabs")
        dump = ast.dump(func)
        self.assertIn("TaskLedger", dump)
        self.assertIn("all_records", dump)
        for state in ("running", "paused", "completed", "failed",
                      "cancelled"):
            self.assertIn(state, dump, f"state {state!r} not handled")
        self.assertIn("Task history is unavailable right now.", dump)


class TestRoutesPreserved(unittest.TestCase):
    def test_palette_rows(self):
        # INTENTIONAL (refactor P1 step 5): CommandPalette moved verbatim
        # to window_widgets_palette.py. Assertions unchanged.
        func = _func(_tree("window_widgets_palette.py"), "_build_commands")
        rows = set()
        for call in _calls(func):
            if isinstance(call.func, ast.Name) \
                    and call.func.id == "go" \
                    and len(call.args) == 2 \
                    and all(isinstance(a, ast.Constant)
                            for a in call.args):
                rows.add((call.args[0].value, call.args[1].value))
        for expected in ((0, "Go to Home"), (0, "Go to Assistant"),
                         (1, "Go to Activity"),
                         (4, "Go to Settings"), (5, "Go to Pulse"),
                         (6, "Go to Senses"), (7, "Go to Doctor"),
                         # Stage 0 features batch 1 (Group D).
                         (8, "Go to Calendar"), (9, "Go to Inbox"),
                         (10, "Go to Contacts"), (11, "Go to Calls")):
            self.assertIn(expected, rows, f"palette row {expected} missing")
        # UI Phase 2 (2026-11-25): the Skills row is no longer a
        # literal go(3, "Go to Skills") — its label carries the
        # live capability count ("Go to Skills — 772 capabilities"),
        # and a "Browse all capabilities" row (page_3_browse) opens
        # the Centre directly.
        dump = ast.dump(func)
        self.assertIn("Go to Skills — ", dump)
        self.assertIn("capabilities", dump)
        self.assertIn("Browse all capabilities", dump)
        self.assertIn("page_3_browse", dump)
        self.assertIn("Go to Reminders", dump)
        self.assertIn("_go_reminders_scheduled", dump)
        self.assertIn("page_1_scheduled", dump)
        alias = _func(_tree("window_widgets_palette.py"),
                      "_go_reminders_scheduled")
        alias_dump = ast.dump(alias)
        self.assertIn("_show_page", alias_dump)
        self.assertIn("_select_activity_tab", alias_dump)
        self.assertIn("Scheduled", alias_dump)

    def test_other_routes_untouched(self):
        # INTENTIONAL (refactor P1 step 5): the Settings-card route
        # moved verbatim to window_settings_advcards.py. Assertion unchanged.
        self.assertIn("_show_page(7)", _source("window_settings_advcards.py"),
                      "Doctor Settings-card route lost")
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        shell = _source("window_shell_tray.py")
        self.assertIn("act_doctor", shell)
        self.assertIn("_show_page(7)", shell,
                      "tray Doctor route lost")
        self.assertIn("act_settings", shell)
        self.assertIn("_show_page(4)", shell,
                      "tray Settings route lost")

    def test_loop_rows_unchanged(self):
        # INTENTIONAL (Phase 2 B2-1 rename): filename moved with its module (was "command_palette_w4.py").
        src = _core_source("command_palette.py")
        # INTENTIONAL (world-view removal, 2026-11-05): the
        # '"target_page": 8' row is gone with the feature — loop
        # targets are 7/4 only now.
        for target in ('"target_page": 7', '"target_page": 4'):
            self.assertIn(target, src, f"loop row {target} changed")


class TestScopePins(unittest.TestCase):
    def test_orb_hero_untouched(self):
        # S1 must not fold, minimize, or collapse the orb/hero:
        # the standing verdict's own code comment must survive.
        # (The comment wraps across lines, so pin both halves.)
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        shell = _source("window_shell_assistant.py")
        self.assertIn("no longer folds", shell)
        self.assertIn("away mid-conversation", shell)

    def test_geometry_untouched(self):
        # INTENTIONAL (UIS5): S1 pinned geometry as "a later
        # slice (S5), not S1" — S5 is that slice, and it changed
        # the window geometry deliberately: the old 960 minimum
        # forgot the 56 px page margins (true floor 1008), and
        # the old 1080 default clamped the chat to 372 instead
        # of the frozen 400 (true default 1108). INTENTIONAL
        # (UI breakpoints slice, 2026-10-30): the 1008 floor is
        # now the COMFORT tier, not the hard minimum — the hard
        # minimum is the narrow derivation (908, 500), and the
        # default is clamped to the available geometry (still
        # exactly 1108x750 on a big screen). Sidebar width is
        # unchanged at 220, still pinned.
        shell = _shell_source()
        self.assertIn("setMinimumSize(*WINDOW_MINIMUM)", shell)
        self.assertIn("default_window_size(", shell)
        self.assertIn("setFixedWidth(220)", shell)
