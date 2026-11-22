"""Command Deck restore (2026-11-01, owner-delegated) — pins for
the two restores onto the settled Phase E home, and for what was
deliberately NOT restored.

Scope: (1) sidebar section labels CORE / MISSION / ARCHIVE /
SYSTEM inside the existing 220 px sidebar — labels only, same
buttons, same order, same stack indices; (2) the guide's Mission
Log as a fourth Activity tab, rows through gui/task_state.py,
state segments + time filter, selection shows the evidence trace.

Deliberately NOT restored (owner's no-clutter constraint): the
icon rail, the transcript drawer, S8 corner telemetry, any hero
composition change, the wake ring, the LOCAL readout. This suite
pins the absence of the rail/hero artifacts from the shell.

LAB HONESTY NOTE: same AST-over-real-files precedent as uis1 —
this proves structure + routing, NOT rendered appearance. Renders
are verified separately under commanddeck_evidence/restore/.
"""

import ast
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _source(name):
    return (ROOT / "gui" / name).read_text(encoding="utf-8")


# INTENTIONAL (refactor P1 step 5): window_shell.py split verbatim
# into panel mixins; the shell surface is these files together.
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


def _tree(name):
    return ast.parse(_source(name))


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found")


def _segment(name, func_name):
    src = _source(name)
    return ast.get_source_segment(src, _func(_tree(name), func_name))


class SidebarRestoreTests(unittest.TestCase):
    def test_section_labels_present(self):
        # Chat pass (2026-11-17, owner ruling: the sidebar is
        # v6.3 exactly): the §9.2 section labels are BACK — the
        # rail is eleven nav items in labelled groups (the v6.3
        # seven plus the Stage 0 features batch 1 views), as
        # restored 2026-11-01 and as shipped in v6.3 (proven at
        # backup_release_v630_pre gui/window_shell_sidebar.py
        # :330-345). This suite's own scope is the labels restore;
        # Build 3 flipped the test to assert absence, the chat
        # pass flips it back.
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        segment = _segment("window_shell_sidebar.py", "_build_sidebar")
        for label in ('_core_label = QLabel("CORE")',
                      '_mission_label = QLabel("MISSION")',
                      '_archive_label = QLabel("ARCHIVE")',
                      '_system_label = QLabel("SYSTEM")',
                      # Stage 0 features batch 1 (Group D).
                      '_views_label = QLabel("VIEWS")'):
            self.assertIn(label, segment,
                          "a v6.3 section label is missing")
        self.assertNotIn('QLabel("CAPABILITIES")', segment,
                         "CAPABILITIES has no sidebar button to group")

    def test_sidebar_frame_and_indices_unchanged(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        # Chat pass (2026-11-17): the map is the v6.3 seven-button
        # map again — Skills has no sidebar button, so index 3 is
        # gone (proven at backup_release_v630_pre
        # gui/window_shell_sidebar.py:295). Stage 0 features
        # batch 1 (Group D) adds the four views at stack 8–11.
        segment = _segment("window_shell_sidebar.py", "_build_sidebar")
        self.assertIn("sidebar.setFixedWidth(220)", segment)
        self.assertIn("_nav_page_indices = [0, 1, 2, 5, 6, 7, 8, 9, 10, 11, 4]",
                      segment)

    def test_no_capabilities_label_no_skills_button(self):
        # Chat pass (2026-11-17): still no CAPABILITIES label —
        # and the Build 3 Skills button is GONE (Skills is
        # Ctrl+K-only as in v6.3; proven: no Skills def in
        # backup_release_v630_pre gui/window_shell_sidebar.py).
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        segment = _segment("window_shell_sidebar.py", "_build_sidebar")
        self.assertNotIn('QLabel("CAPABILITIES")', segment)
        self.assertNotIn('("Skills", "grid", 3, "Capabilities")',
                         segment)


class NotRestoredTests(unittest.TestCase):
    def test_no_rail_no_hero_panel_no_drawer_artifacts(self):
        # INTENTIONAL (refactor P1 step 5): the negatives must hold
        # across the whole shell surface, so read it concatenated.
        shell = _shell_source()
        self.assertNotIn("railSection", shell,
                         "the §9.2 icon rail is not restored")
        self.assertNotIn("hero_mission_panel", shell,
                         "the hero Mission panel is not restored")
        self.assertNotIn("railMode", shell,
                         "the rail footer readout is not restored")


class MissionLogTests(unittest.TestCase):
    def test_mission_log_tab_and_controls(self):
        # GUI split #8 INTENTIONAL: _build_activity_page moved verbatim to
        # window_pages_activity.py. Assertion unchanged.
        segment = _segment("window_pages_activity.py", "_build_activity_page")
        self.assertIn('"Mission Log"', segment)
        for state in ("All", "Running", "Done", "Failed", "Partial"):
            self.assertIn(f'"{state}"', segment,
                          f"state segment {state!r} missing")
        for item in ("All time", "Last 7 days", "Last 30 days"):
            self.assertIn(f'"{item}"', segment,
                          f"time filter {item!r} missing")
        self.assertIn('"missionLogDetail"', segment)
        # Render-found: a bare QTextEdit uses Qt's light palette
        # (unreadable pale trace on white) — the detail pane
        # must carry the dark scoped stylesheet.
        self.assertIn("_mission_log_detail.setStyleSheet(", segment)
        self.assertIn("QTextEdit", segment)
        for handler in ("_set_mission_log_filter",
                        "_set_mission_log_time_filter",
                        "_on_mission_log_selected"):
            self.assertIsNotNone(
        # GUI split #8 INTENTIONAL: mission-log handlers moved verbatim to
                # window_pages_activity.py. Assertion unchanged.
                _func(_tree("window_pages_activity.py"), handler),
                f"{handler} missing")

    def test_mission_log_filled_from_ledger_via_task_state(self):
        # GUI split #12 batch 2 INTENTIONAL: _refresh_activity_tabs
        # moved verbatim to window_memory_activity.py. Assertion unchanged.
        segment = _segment("window_memory_activity.py", "_refresh_activity_tabs")
        for marker in ("_mission_log_list", "render_task",
                       "add_task_row", "ItemDataRole.UserRole",
                       "No missions match this filter.",
                       "Mission log is unavailable right now."):
            self.assertIn(marker, segment,
                          f"Mission Log fill marker {marker!r} missing")
        # The Running segment covers the Needs approval variant.
        self.assertIn('"Needs approval"', segment)


if __name__ == "__main__":
    unittest.main()
