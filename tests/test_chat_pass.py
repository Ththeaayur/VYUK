"""Chat pass (2026-11-17) — the sidebar and the chat panel back to
v6.3 exactly, plus the chip-row freeze fix.

The owner ruled the v6.3 look is the baseline and only decluttering
was ever wanted: the sidebar is v6.3 (seven buttons, section labels
CORE/MISSION/ARCHIVE/SYSTEM, Skills Ctrl+K-only, the privacy card),
the chat panel's floor is 300 again (Build 1's raise to 380 and its
compact tier are gone), and the docked strip Build 3 added stays.
The chip row froze at 4 px tall in v6.3 itself (fit_chips resized
to self.height() before the row had any geometry), so the starter
chips never painted — that is fixed here, not reverted.

Source tests are Qt-free (AST over the shell files + the
breakpoints module loaded from its file); window tests use the
real MainWindow offscreen via the chat-ui WindowBase harness.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    WindowBase = object  # type: ignore[assignment]
    _QT = False


def _gui_source(name):
    return (ROOT / "gui" / name).read_text(encoding="utf-8")


def _load_breakpoints():
    spec = importlib.util.spec_from_file_location(
        "chat_pass_breakpoints", ROOT / "gui" / "breakpoints.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BP = _load_breakpoints()


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found")


def _assign_value(func_node, name):
    for node in ast.walk(func_node):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
                if isinstance(target, ast.Attribute) \
                        and target.attr == name:
                    return node.value
    raise AssertionError(f"assignment {name!r} not found")


class TestChatPassSidebarSource(unittest.TestCase):
    def test_nav_defs_are_the_v63_seven(self):
        src = _gui_source("window_shell_sidebar.py")
        tree = ast.parse(src)
        func = _func(tree, "_build_sidebar")
        defs = ast.literal_eval(_assign_value(func, "_nav_defs"))
        self.assertEqual(defs, [("Home", "spark", 0),
                                ("Activity", "clock", 1),
                                ("Memory", "database", 2),
                                ("System Pulse", "pulse", 5),
                                ("Senses", "senses", 6),
                                ("Doctor", "check", 7),
                                ("Settings", "sliders", 4)])
        indices = [e.value for e in
                   _assign_value(func, "_nav_page_indices").elts
                   if isinstance(e, ast.Constant)]
        self.assertEqual(indices, [0, 1, 2, 5, 6, 7, 4])
        labels = [label for label, _, _ in defs]
        self.assertNotIn("Skills", labels,
                         "Skills must not have a sidebar button")

    def test_section_labels_present_in_order(self):
        src = _gui_source("window_shell_sidebar.py")
        tree = ast.parse(src)
        func = _func(tree, "_build_sidebar")
        segment = ast.get_source_segment(src, func)
        order = ['_core_label = QLabel("CORE")',
                 "layout.addWidget(self.nav_buttons[0])",
                 '_mission_label = QLabel("MISSION")',
                 "layout.addWidget(self.nav_buttons[1])",
                 '_archive_label = QLabel("ARCHIVE")',
                 "layout.addWidget(self.nav_buttons[2])",
                 '_system_label = QLabel("SYSTEM")',
                 "for btn in self.nav_buttons[3:6]:",
                 "layout.addStretch()",
                 "layout.addWidget(self.nav_buttons[6])"]
        positions = [segment.index(marker) for marker in order]
        self.assertEqual(positions, sorted(positions),
                         "sidebar order is not CORE -> MISSION -> "
                         "ARCHIVE -> SYSTEM -> stretch -> footer "
                         "Settings")
        for label in ("CORE", "MISSION", "ARCHIVE", "SYSTEM"):
            self.assertIn(f'QLabel("{label}")', segment)

    def test_privacy_card_present_not_a_line(self):
        src = _gui_source("window_shell_sidebar.py")
        self.assertIn('setObjectName("privacyCard")', src)
        self.assertIn('"Private by design"', src)
        self.assertIn("Memory stays on this device. The mic is off "
                      "until you talk to VYUK", src)
        self.assertNotIn("privacy_lock", src,
                         "the Build 3 one-line privacy_lock survives")


class TestChatPassChatGeometrySource(unittest.TestCase):
    def test_breakpoints_are_v63_two_tiers(self):
        self.assertEqual(BP.CHAT_FLOOR, 300)
        self.assertEqual(BP.COMFORT_WIDTH, 1008)
        for gone in ("CHAT_FLOOR_COMPACT", "COMPACT_WIDTH",
                     "chat_floor_for"):
            self.assertFalse(hasattr(BP, gone),
                             f"Build 1 leftover {gone} survives")
        self.assertEqual(BP.tier_for(1007), "narrow")
        self.assertEqual(BP.tier_for(1008), "comfort")
        self.assertEqual(BP.chat_target(720, 400, 1008), 300)

    def test_chat_panel_floor_is_300_in_source(self):
        src = _gui_source("window_shell_assistant.py")
        tree = ast.parse(src)
        func = _func(tree, "_build_assistant_page")
        floors = set()
        for call in ast.walk(func):
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "setMinimumWidth"
                    and len(call.args) == 1
                    and isinstance(call.args[0], ast.Constant)):
                floors.add(call.args[0].value)
        self.assertIn(300, floors, "chat floor is not 300")
        self.assertNotIn(380, floors,
                         "the Build 1 chat floor 380 survives")
        self.assertIn(420, floors, "stage floor moved")
        self.assertNotIn("chat_floor_for", src,
                         "the Build 1 compact-tier helper survives")

    def test_chip_row_sizes_from_its_layout(self):
        # E-F101 supersedes the fix-#8 shape this pin was written
        # for: the row no longer sizes itself to the chips' total
        # (that forcing — 508/664 px — is what cut the later
        # chips at the panel edge). The row wraps now, and its
        # height comes from the flow layout's heightForWidth.
        # fit_chips must not size the row at all: no resize, no
        # minimum width forced on the row, and no self.height()
        # read at construction (the freeze this pin was written
        # for — at construction the row has no geometry yet, so
        # height() is the bare margins, 4 px).
        src = _gui_source("window_shell_assistant_widgets.py")
        tree = ast.parse(src)
        func = _func(tree, "fit_chips")
        segment = ast.get_source_segment(src, func)
        self.assertNotIn("self.resize(", segment)
        self.assertNotIn("setMinimumWidth(total)", segment)
        self.assertNotIn("self.height()", segment)
        self.assertIn("class _ChipFlowLayout", src)
        self.assertIn("def heightForWidth", src)


@unittest.skipUnless(_QT, "Qt unavailable")
class TestChatPassWindow(WindowBase):
    def _show_home(self):
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — boot may already be skipped
            pass
        self.window.resize(1280, 800)
        self.window.show()
        self.app.processEvents()

    def test_sidebar_is_v63_on_the_live_window(self):
        self._show_home()
        labels = [b.text() for b in self.window.nav_buttons]
        self.assertEqual(labels, ["Home", "Activity", "Memory",
                                  "System Pulse", "Senses", "Doctor",
                                  "Settings"])
        from PySide6.QtWidgets import QLabel, QFrame

        inner = self.window.nav_buttons[0].parentWidget()
        seen = {lbl.text() for lbl in inner.findChildren(QLabel)}
        for label in ("CORE", "MISSION", "ARCHIVE", "SYSTEM"):
            self.assertIn(label, seen,
                          "a v6.3 section label is missing")
        card = self.window.findChild(QFrame, "privacyCard")
        self.assertIsNotNone(card, "the v6.3 privacy card is missing")
        texts = {lbl.text() for lbl in card.findChildren(QLabel)}
        self.assertIn("Private by design", texts)
        self.assertIsNone(getattr(self.window, "privacy_lock", None))

    def test_chat_panel_minimum_width_is_300(self):
        self._show_home()
        self.assertEqual(self.window.chat_panel.minimumWidth(), 300)

    def test_starter_chips_paint(self):
        # The freeze regression: on an empty conversation the
        # starter chips must have real height and be visible —
        # in v6.3 they were 0 px tall and the strip above the
        # composer showed as dead space.
        self._show_home()
        row = getattr(self.window, "_suggest_row", None)
        self.assertIsNotNone(row)
        self.app.processEvents()
        self.assertGreater(row.height(), 0,
                           "the chip row has no height")
        from PySide6.QtWidgets import QPushButton

        chips = [w for w in row.findChildren(QPushButton)
                 if w.objectName() == "chipSuggest"]
        self.assertGreater(len(chips), 0, "no starter chips at all")
        for chip in chips:
            self.assertGreater(chip.height(), 0,
                               f"chip {chip.text()!r} is 0 px tall")
            self.assertTrue(chip.isVisible(),
                            f"chip {chip.text()!r} is not visible")


if __name__ == "__main__":
    unittest.main()
