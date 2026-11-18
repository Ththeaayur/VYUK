"""Sweep Build 4 — Flow-style command palette, Nova's own Qt pattern
(no WebEngine, no Qt, no network).

What is tested is the Qt-free logic (matching, ranking, cap,
consequence classification, registry mapping), the pattern-only
licence record, and — by source inspection, because Qt cannot be
instantiated in this shell (libEGL missing, as in earlier builds) —
the widget delta: the Globe row, the consequential-row confirmation,
and the untouched wiring (Ctrl+K shortcut + titlebar pill, no new
sidebar entry, no stack renumbering).

NOT claimed here: a rendered palette, a clicked row, or a working
palette on his machine — those need a Qt-capable run and field
proof, and this suite does not pretend otherwise.
"""

import pathlib
import unittest

from core import command_palette as palette

_ROOT = pathlib.Path(__file__).resolve().parent.parent


# INTENTIONAL (refactor P1 step 5): window_shell.py split verbatim
# into panel mixins; the shell pins span frame + sidebar, so read
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
    return "".join((_ROOT / "gui" / n).read_text() for n in SHELL_FILES)


def _entries():
    return [
        {"label": "Go to Doctor", "kind": "PAGE", "description": "",
         "action_id": "page_7", "consequential": False},
        {"label": "Toggle wake word", "kind": "SETTING", "description": "",
         "action_id": "toggle_wake", "consequential": True},
        {"label": "Toggle mute", "kind": "SETTING", "description": "",
         "action_id": "toggle_mute", "consequential": False},
        {"label": "Connect / Disconnect", "kind": "SETTING",
         "description": "", "action_id": "toggle_connection",
         "consequential": True},
        {"label": "Get Stock Quote", "kind": "TOOL",
         "description": "A stock quote summary.",
         "action_id": "tool_get_stock_quote", "consequential": False},
    ]


class RankingTests(unittest.TestCase):
    def test_exact_beats_prefix_beats_substring(self):
        entries = [
            {"label": "Settings", "kind": "PAGE", "description": "",
             "action_id": "page_4", "consequential": False},
            {"label": "Go to Settings", "kind": "PAGE", "description": "",
             "action_id": "page_4b", "consequential": False},
            {"label": "System Settings Panel", "kind": "PAGE",
             "description": "", "action_id": "page_4c",
             "consequential": False},
        ]
        ranked = palette.rank_entries(entries, "settings")
        self.assertEqual(ranked[0]["label"], "Settings")
        # Prefix ("Go to Settings" starts with a word "Settings"? No:
        # word-boundary match) must outrank a plain substring hit.
        self.assertEqual(ranked[1]["label"], "Go to Settings")
        self.assertEqual(ranked[2]["label"], "System Settings Panel")

    def test_prefix_beats_substring(self):
        entries = [
            {"label": "System Settings", "kind": "PAGE", "description": "",
             "action_id": "a", "consequential": False},
            {"label": "Settings", "kind": "PAGE", "description": "",
             "action_id": "b", "consequential": False},
        ]
        # "Settings" is exact here; use a non-exact prefix case.
        ranked = palette.rank_entries(entries, "set")
        self.assertEqual(ranked[0]["label"], "Settings")

    def test_description_match_and_no_match_dropped(self):
        ranked = palette.rank_entries(_entries(), "stock quote")
        self.assertEqual([e["label"] for e in ranked],
                         ["Get Stock Quote"])
        self.assertEqual(palette.rank_entries(_entries(), "zzzqqq"), [])

    def test_subsequence_match(self):
        self.assertTrue(palette.is_subsequence("glb", "globe"))
        self.assertFalse(palette.is_subsequence("glz", "globe"))
        ranked = palette.rank_entries(_entries(), "gtd")
        self.assertIn("Go to Doctor", [e["label"] for e in ranked])

    def test_empty_query_keeps_original_order(self):
        entries = _entries()
        ranked = palette.rank_entries(entries, "")
        self.assertEqual([e["label"] for e in ranked],
                         [e["label"] for e in entries])

    def test_cap_and_hidden_count_are_honest(self):
        entries = [{"label": f"Item {i}", "kind": "PAGE",
                    "description": "", "action_id": f"page_{i}",
                    "consequential": False} for i in range(20)]
        shown, hidden = palette.filter_entries(entries, "item")
        self.assertEqual(len(shown), 12)
        self.assertEqual(hidden, 8)
        shown2, hidden2 = palette.filter_entries(entries, "item", cap=5)
        self.assertEqual((len(shown2), hidden2), (5, 15))

    def test_score_is_none_for_no_match(self):
        self.assertIsNone(palette.score_match("zzz", "Globe"))
        self.assertIsNotNone(palette.score_match("glo", "Globe"))


class ConsequenceTests(unittest.TestCase):
    def test_consequential_rows_need_confirmation(self):
        entries = {e["action_id"]: e for e in _entries()}
        self.assertTrue(
            palette.requires_confirmation(entries["toggle_wake"]))
        self.assertTrue(
            palette.requires_confirmation(entries["toggle_connection"]))

    def test_safe_rows_do_not_need_confirmation(self):
        entries = {e["action_id"]: e for e in _entries()}
        # INTENTIONAL (world-view removal, 2026-11-05): the
        # page_8 fixture row is gone with the feature; page_7
        # (Go to Doctor) is the remaining PAGE row here.
        self.assertFalse(
            palette.requires_confirmation(entries["page_7"]))
        self.assertFalse(
            palette.requires_confirmation(entries["toggle_mute"]))
        # Tool rows are prefill-only, never direct execution.
        self.assertFalse(
            palette.requires_confirmation(entries["tool_get_stock_quote"]))

    def test_unknown_action_id_fails_closed(self):
        self.assertTrue(palette.requires_confirmation(
            {"label": "Mystery", "action_id": "mystery_row",
             "consequential": False}))


class RegistryTests(unittest.TestCase):
    def test_build_tool_entries_from_declarations(self):
        decls = [
            {"name": "get_stock_quote",
             "description": "A stock quote summary.\nSecond line."},
            {"name": "", "description": "skipped, no name"},
            {"name": "send_email", "description": "Send an email."},
        ]
        entries = palette.build_tool_entries(decls)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["label"], "Get Stock Quote")
        self.assertEqual(entries[0]["description"],
                         "A stock quote summary.")
        self.assertEqual(entries[0]["action_id"],
                         "tool_get_stock_quote")
        self.assertFalse(entries[0]["consequential"])
        # Entries carry no callable: the widget maps them to prefill.
        self.assertNotIn("action", entries[0])

    def test_humanise(self):
        self.assertEqual(palette.humanise_tool_name("get_stock_quote"),
                         "Get Stock Quote")


class LicenceAndWidgetSourceTests(unittest.TestCase):
    def test_logic_module_has_no_qt_import(self):
        # INTENTIONAL (Phase 2 B2-1 rename): path moved with its module (was "core/command_palette_w4.py").
        src = (_ROOT / "core" / "command_palette.py").read_text()
        self.assertNotIn("PySide", src)
        self.assertNotIn("PyQt", src)

    def test_flow_record_is_pattern_only(self):
        record = (_ROOT / "gui" / "assets"
                  / "FLOW_LICENCE_READ.md").read_text()
        self.assertIn("PATTERN USE ONLY", record.upper())
        self.assertIn("No Flow Launcher code is copied", record)
        self.assertIn("No Flow Launcher binary is bundled", record)
        self.assertIn("MIT", record)
        # No Flow code or binary anywhere in the new module.
        # INTENTIONAL (Phase 2 B2-1 rename): path moved with its module (was "core/command_palette_w4.py").
        logic = (_ROOT / "core" / "command_palette.py").read_text()
        self.assertNotIn("Flow-Launcher", logic)
        self.assertNotIn("flow_launcher", logic.lower().replace(
            "flow launcher", ""))

    def test_widget_has_confirm_gate(self):
        # INTENTIONAL (world-view removal, 2026-11-05): the globe
        # row is gone with the feature; the confirm gate below is
        # the generic assertion this test still pins.
        # INTENTIONAL (refactor P1 step 5): CommandPalette moved verbatim
        # to window_widgets_palette.py. Assertions unchanged.
        src = (_ROOT / "gui" / "window_widgets_palette.py").read_text()
        self.assertIn("toggle_connection", src)
        self.assertIn("toggle_wake", src)
        self.assertIn("HudDialog.confirm", src)
        self.assertNotIn("default=True", src)

    # INTENTIONAL (S1, 2026-10-30): renamed — the old name
    # ("no_new_sidebar_no_renumber") described the pre-S1 freeze,
    # and S1 deliberately restructures the sidebar. What this test
    # still pins is unchanged: Ctrl+K opens the palette, and the
    # STACK is not renumbered (only the button->page map moved:
    # Settings to the footer position, Doctor gaining a button in
    # S1, Skills gaining one in rethink Build 3 and losing it
    # again in the chat pass — the map is [0, 1, 2, 5, 6, 7, 4],
    # the v6.3 value, proven at backup_release_v630_pre
    # gui/window_shell_sidebar.py:295).
    def test_wiring_sidebar_s1_remap_stack_unchanged(self):
        shell = _shell_source()
        self.assertIn('QKeySequence("Ctrl+K")', shell)
        self.assertIn("_show_command_palette", shell)
        self.assertIn("_nav_page_indices = [0, 1, 2, 5, 6, 7, 4]",
                      shell)
        # GUI split #9 INTENTIONAL: CommandPalette lives in the nav module now.
        chat = (_ROOT / "gui" / "window_chat_nav.py").read_text()
        self.assertIn("CommandPalette", chat)
        self.assertIn("Open the command palette (Ctrl+K)", shell)


if __name__ == "__main__":
    unittest.main()
