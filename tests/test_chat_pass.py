"""Chat pass (2026-11-17) — the sidebar and the chat panel back to
v6.3 exactly, plus the chip-row freeze fix.

The owner ruled the v6.3 look is the baseline and only decluttering
was ever wanted: the sidebar is v6.3 (fifteen buttons since UI
Phase 5 — Notes / Finance / Devices under WORKSPACES — section
labels CORE/MISSION/ARCHIVE/CAPABILITIES/SYSTEM/WORKSPACES,
Skills with its own button under CAPABILITIES since UI Phase 2,
the privacy card),
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
    def test_nav_defs_are_the_workspaces_fifteen(self):
        # Stage 0 features batch 1 (Group D): the v6.3 seven plus
        # the four views — Calendar / Inbox / Contacts / Calls
        # (stack 8–11), before the footer Settings. UI Phase 2
        # (2026-11-25): Skills gains its own sidebar button
        # ("shapes", stack 3) under a new CAPABILITIES section,
        # and Doctor / Contacts / Calls move to their Phosphor
        # icons (heartbeat / address-book / phone-call).
        # UI Phase 5 (2026-11-27): Notes / Finance / Devices
        # join as workspace buttons (stack 12–14), before the
        # footer Settings — fifteen buttons now.
        src = _gui_source("window_shell_sidebar.py")
        tree = ast.parse(src)
        func = _func(tree, "_build_sidebar")
        defs = ast.literal_eval(_assign_value(func, "_nav_defs"))
        self.assertEqual(defs, [("Home", "spark", 0),
                                ("Activity", "clock", 1),
                                ("Memory", "database", 2),
                                ("Skills", "shapes", 3),
                                ("System Pulse", "pulse", 5),
                                ("Senses", "senses", 6),
                                ("Doctor", "heartbeat", 7),
                                ("Calendar", "grid", 8),
                                ("Inbox", "send", 9),
                                ("Contacts", "address-book", 10),
                                ("Calls", "phone-call", 11),
                                ("Notes", "note", 12),
                                ("Finance", "currency-dollar", 13),
                                ("Devices", "house", 14),
                                ("Settings", "sliders", 4)])
        indices = [e.value for e in
                   _assign_value(func, "_nav_page_indices").elts
                   if isinstance(e, ast.Constant)]
        self.assertEqual(indices, [0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11,
                                   12, 13, 14, 4])
        labels = [label for label, _, _ in defs]
        self.assertIn("Skills", labels,
                      "Skills must have a sidebar button since UI Phase 2")

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
                 # UI Phase 2 (2026-11-25): CAPABILITIES for Skills.
                 '_capabilities_label = QLabel("CAPABILITIES")',
                 "layout.addWidget(self.nav_buttons[3])",
                 '_system_label = QLabel("SYSTEM")',
                 "for btn in self.nav_buttons[4:7]:",
                 # Stage 0 features batch 1 (Group D): VIEWS.
                 # INTENTIONAL UPDATE (UI Phase 3, 2026-11-25): the
                 # section is WORKSPACES now — the four views are
                 # usable workspaces, not read-only views.
                 # INTENTIONAL UPDATE (UI Phase 5, 2026-11-27): the
                 # section holds seven workspace buttons now
                 # (nav_buttons[7:14]); Settings is the footer at
                 # nav_buttons[14].
                 '_views_label = QLabel("WORKSPACES")',
                 "for btn in self.nav_buttons[7:14]:",
                 "layout.addStretch()",
                 "layout.addWidget(self.nav_buttons[14])"]
        positions = [segment.index(marker) for marker in order]
        self.assertEqual(positions, sorted(positions),
                         "sidebar order is not CORE -> MISSION -> "
                         "ARCHIVE -> CAPABILITIES -> SYSTEM -> "
                         "WORKSPACES -> stretch -> footer Settings")
        for label in ("CORE", "MISSION", "ARCHIVE", "CAPABILITIES",
                      "SYSTEM", "WORKSPACES"):
            self.assertIn(f'QLabel("{label}")', segment)

    def test_privacy_card_present_not_a_line(self):
        src = _gui_source("window_shell_sidebar.py")
        self.assertIn('setObjectName("privacyCard")', src)
        self.assertIn('"Private by design"', src)
        # Loophole batch 8 (L-31): the body reads the shared
        # sentence from core/privacy_copy.py (PRIVACY_PROMISE) —
        # "Memory stays on this device" hid that a cloud turn
        # sends the facts she needs to the cloud brain.
        self.assertIn("privacy_copy.PRIVACY_PROMISE", src)
        self.assertNotIn("Memory stays on this device. The mic is off",
                         src)
        self.assertNotIn("privacy_lock", src,
                         "the Build 3 one-line privacy_lock survives")


class TestChatPassChatGeometrySource(unittest.TestCase):
    def test_breakpoints_are_v63_three_tiers(self):
        # Stage 0 features batch 5 supersedes the old two-tier
        # pin: the compact tier below 908 exists now (sidebar
        # rail 64, stage 240, hero 240, chat floor 240) — the
        # Build 1 leftovers it used to assert gone are the
        # compact tier's own names. Comfort/narrow behaviour
        # is unchanged.
        self.assertEqual(BP.CHAT_FLOOR, 300)
        self.assertEqual(BP.CHAT_FLOOR_COMPACT, 240)
        self.assertEqual(BP.COMPACT_WIDTH, 908)
        self.assertEqual(BP.COMFORT_WIDTH, 1008)
        self.assertTrue(callable(BP.chat_floor_for))
        self.assertEqual(BP.tier_for(907), "compact")
        self.assertEqual(BP.tier_for(908), "narrow")
        self.assertEqual(BP.tier_for(1007), "narrow")
        self.assertEqual(BP.tier_for(1008), "comfort")
        self.assertEqual(BP.chat_target(720, 400, 1008), 300)
        self.assertEqual(BP.chat_target(508, 300, 640), 268)

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
        # INTENTIONAL UPDATE (Stage 0 features batch 1, 2026-11-22):
        # eleven buttons, not seven — the four views (Calendar,
        # Inbox, Contacts, Calls) join before Settings under a
        # new VIEWS section label; the v6.3 seven keep their
        # order and Settings stays last.
        # INTENTIONAL UPDATE (UI Phase 2, 2026-11-25): twelve
        # buttons — Skills joins under a new CAPABILITIES section
        # label between ARCHIVE and SYSTEM.
        # INTENTIONAL UPDATE (UI Phase 5, 2026-11-27): fifteen
        # buttons — Notes, Finance and Devices join as first-class
        # workspaces before Settings under the WORKSPACES section.
        self._show_home()
        labels = [b.text() for b in self.window.nav_buttons]
        self.assertEqual(labels, ["Home", "Activity", "Memory",
                                  "Skills", "System Pulse", "Senses",
                                  "Doctor", "Calendar", "Inbox",
                                  "Contacts", "Calls", "Notes",
                                  "Finance", "Devices", "Settings"])
        from PySide6.QtWidgets import QLabel, QFrame

        inner = self.window.nav_buttons[0].parentWidget()
        seen = {lbl.text() for lbl in inner.findChildren(QLabel)}
        for label in ("CORE", "MISSION", "ARCHIVE", "CAPABILITIES",
                      "SYSTEM", "WORKSPACES"):
            self.assertIn(label, seen,
                          "a v6.3 section label is missing")
        card = self.window.findChild(QFrame, "privacyCard")
        self.assertIsNotNone(card, "the v6.3 privacy card is missing")
        texts = {lbl.text() for lbl in card.findChildren(QLabel)}
        self.assertIn("Private by design", texts)
        self.assertIsNone(getattr(self.window, "privacy_lock", None))

    def test_chat_panel_minimum_width_is_230(self):
        # UI upgrade Phase 1 §5 (screenshot-size ruling,
        # 2026-11-24): the chat is no longer frozen at 400/300 —
        # at 1280 it is ~230 px (sidebar 170, HUD 868, chat 230),
        # so the panel minimum is 230.
        self._show_home()
        self.assertEqual(self.window.chat_panel.minimumWidth(), 230)

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
