"""C4 enumeration pins (2026-11-05).

C4 RULING: verification runs BEFORE the approval dialog wherever
the path both verifies and asks — verify_before_confirm is the
primitive (refusal means no dialog is shown).

ENUMERATION (derived from code reads 2026-11-05; the parent
verifies it from disk):

WIRED verify_before_confirm call sites: NONE. No live ask-path
has BOTH a typed control-existence target AND a fresh
observation available at dialog time. Wiring the primitive into
any path below would spuriously refuse legitimate calls — the
adapters refuse (never pass through) when the fresh observation
is missing. That is why this file pins the enumeration instead
of a wiring: a future observation supplier (menu-tree / DOM /
handle supplier in the funnel) is what would make a path
wireable, and these pins force that future work to confront
the enumeration rather than wire blindly.

1. Funnel confirm dialog (native block + shared _ask_human;
   layer-1 voice popup / layer-2 GUI handler) — ASKS for five
   of the six Family-B tools (all but click_by_mark, which is
   not confirm-gated) but carries only tool name + args view:
   no menu tree, no DOM, no handle observation. verify(
   menu_item, {}) refuses "menu-unavailable"; verify(handle,
   {}) refuses "target-not-present". So the funnel CANNOT take
   the primitive. Protection: handlers re-prove at dispatch
   (Family-B live engine resolution), C3 on re-issue.
   (adapter_action is confirm-gated but UNCLASSIFIED by
   family_of — a §7 engine tool whose adapter resolution is
   its own proof, not a verifier target; see the pin.)
2. Funnel confirm for Family-C tools — NO DIALOG EXISTS:
   cursor_control / mouse_control / window_control are NOT
   in _CONFIRM_GATE_TOOLS, so no funnel dialog ever asks for
   them. Not a C4 site (nothing to precede).
3. Funnel confirm for Family-A tools — NO DIALOG EXISTS:
   uia_click_control / click_element / click_text are NOT in
   _CONFIRM_GATE_TOOLS. Family-A re-scans at dispatch.
4. Funnel confirm for non-family tools (the other confirm-set
   members: file/write/system actions) — args are paths /
   commands / text, not control-existence targets. No typed
   target, so no C4 site. (A file-existence pre-check would
   also be WRONG for create actions, where absence is the
   normal case.)
5. §7 engine confirm stops -> pending GUI ->
   resolve_pending — the brief's STATED EXCEPTION. The pending
   dialog shows a STORED record; the GUI has no fresh
   observation supplier, so no pre-dialog verification is
   possible there. C4's TIMING still holds in substance: the
   engine resolves live before stopping (menu invoke_path
   walks the live tree, then C-14 stops), and re-issue
   re-proves (C3 for Family-C, handler re-proof for
   Family-B, re-scan for Family-A). Pinned by the existing
   C3 tests, not duplicated here.
6. Browser gates (dangerous click/submit, uninspectable,
   raw point) — string targets only (target + identity
   string, no typed target dict); the handler resolves a live
   Playwright locator at dispatch (locators re-resolve at
   action time); the raw-point verdict has no element at all;
   browser tools are unclassified by family_of. Not C4 sites.
7. Capture guard / mic / trust asks — capture kinds and
   trust decisions, not control-existence targets. N/A.
8. Second-opinion pause — not a target-approval dialog (it
   shows two answers); resume is C3-protected for Family-C.
   N/A.
9. Coordinator _run_step first-run dispatch — shows NO
   dialog (dialogs happen in the funnel/handlers). A
   pre-dispatch verification there would be C1 (verify
   before act), not C4 — out of scope for this batch.

UNBLOCKING CONDITION: a funnel/GUI observation supplier
(menu-tree / DOM / handle observation available at dialog
time) would make path 1 wireable. Until then, any wiring
attempt must update this enumeration, not bypass it.

Headless by construction: no test needs a screen, a UIA walk,
or a live DOM. The handle test mints in-process and verifies
in the same test, so the session is consistent by
construction (no begin_session anywhere in this file).
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.desktop import control_existence as cev
from core.desktop import target_handles as th
from core.funnel import tools_gate_confirm as _gate_confirm

_CONFIRM = _gate_confirm._CONFIRM_GATE_TOOLS

_FAMILY_A = ("uia_click_control", "click_element", "click_text")
_FAMILY_B = ("menu_action", "file_dialog_action",
             "system_surface_action", "drag_drop_action",
             "context_menu_action")
_FAMILY_B_NO_DIALOG = ("click_by_mark",)
_FAMILY_C = ("cursor_control", "mouse_control", "window_control")
_BROWSER_TOOLS = ("browser_control", "browser_read", "browser_click",
                  "browser_type", "browser_select", "browser_press",
                  "open_url")


class C4FunnelDialogTests(unittest.TestCase):
    def test_family_c_tools_have_no_funnel_dialog(self):
        # INTENTIONAL (C4 enumeration path 2): Family-C tools
        # never reach the funnel confirm dialog, so there is no
        # dialog for verification to precede. If a Family-C tool
        # is ever added to the confirm set, this pin forces the
        # C4 question to be answered at that time.
        for tool in _FAMILY_C:
            with self.subTest(tool=tool):
                self.assertNotIn(tool, _CONFIRM)

    def test_family_a_tools_have_no_funnel_dialog(self):
        # INTENTIONAL (C4 enumeration path 3): Family-A tools
        # never reach the funnel confirm dialog; the fresh
        # lookup at dispatch IS their verification.
        for tool in _FAMILY_A:
            with self.subTest(tool=tool):
                self.assertNotIn(tool, _CONFIRM)

    def test_family_b_tools_do_ask_in_funnel(self):
        # INTENTIONAL (C4 enumeration path 1, first half): the
        # five dialog-reaching Family-B tools DO ask in the
        # funnel dialog — so the funnel dialog is a real
        # ask-path for UI targets, and the second half (no
        # observation supplier) is what blocks the primitive,
        # not the absence of a dialog. click_by_mark is the
        # sixth Family-B tool and is NOT confirm-gated (see
        # the next test).
        for tool in _FAMILY_B:
            with self.subTest(tool=tool):
                self.assertIn(tool, _CONFIRM)

    def test_click_by_mark_has_no_funnel_dialog(self):
        # INTENTIONAL (C4 enumeration): click_by_mark is
        # Family-B but is NOT confirm-gated, so it has no
        # funnel dialog either — no C4 site.
        for tool in _FAMILY_B_NO_DIALOG:
            with self.subTest(tool=tool):
                self.assertNotIn(tool, _CONFIRM)

    def test_adapter_action_unclassified_but_confirm_gated(self):
        # INTENTIONAL (C4 enumeration): adapter_action is a
        # §7 engine tool and IS confirm-gated, but family_of
        # returns "unknown" — it has no verifier family. Its
        # adapter resolution is its own proof (a confirm-gated
        # resolution renders a §7 stop, then re-resolves on
        # re-issue); there is no typed verifier target here,
        # so it is not a C4 site either.
        self.assertEqual(cev.family_of("adapter_action"), "unknown")
        self.assertIn("adapter_action", _CONFIRM)


class C4ObservationSupplierTests(unittest.TestCase):
    def test_menu_item_without_fresh_tree_refuses(self):
        # INTENTIONAL (C4 enumeration path 1, second half):
        # the menu adapter REQUIRES a fresh tree; the funnel
        # has no tree supplier, so calling the primitive there
        # would refuse every confirm-gated menu call, fresh
        # or not. This pin proves the block is structural.
        result = cev.verify(
            {"kind": "menu_item", "path": ["File", "Open"],
             "description": "menu 'File > Open'"},
            {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "menu-unavailable")

    def test_handle_without_observation_refuses(self):
        # INTENTIONAL (C4 enumeration path 1, second half):
        # the handle adapter resolves against a SUPPLIED
        # observation; with none offered it refuses
        # "target-not-present" even for a freshly minted
        # handle. The funnel has no observation supplier, so
        # it cannot verify handles before the dialog.
        handle = th.mint_window({"hwnd": 123, "title": "Doc"},
                                "window 'Doc'")
        result = cev.verify(
            {"kind": "handle", "handle_id": handle["id"]},
            {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "target-not-present")

    def test_browser_element_without_fresh_dom_refuses(self):
        # INTENTIONAL (C4 enumeration path 6): the browser
        # adapter needs fresh DOM state (present/visible);
        # with none supplied it refuses "target-gone". The
        # browser gates carry string targets, not typed
        # targets with fresh DOM state, so they cannot take
        # the primitive either.
        result = cev.verify(
            {"kind": "browser_element",
             "description": "browser element",
             "selector": "#pay"},
            {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "target-gone")


class C4ClassificationTests(unittest.TestCase):
    def test_browser_tools_unclassified(self):
        # INTENTIONAL (C4 enumeration path 6): browser tools
        # are not in any verifier family, so no verifier path
        # covers their gates — the gates are string-target
        # classification gates, not control-existence paths.
        for tool in _BROWSER_TOOLS:
            with self.subTest(tool=tool):
                self.assertEqual(cev.family_of(tool), "unknown")

    def test_families_classified(self):
        # INTENTIONAL (C4 enumeration): pins the family mapping
        # the enumeration relies on — A re-scans, B re-proves,
        # C takes the geometry floor.
        for tool in _FAMILY_A:
            with self.subTest(tool=tool):
                self.assertEqual(cev.family_of(tool), "A")
        for tool in _FAMILY_B:
            with self.subTest(tool=tool):
                self.assertEqual(cev.family_of(tool), "B")
        for tool in _FAMILY_C:
            with self.subTest(tool=tool):
                self.assertEqual(cev.family_of(tool), "C")


if __name__ == "__main__":
    unittest.main()
