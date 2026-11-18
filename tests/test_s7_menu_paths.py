"""§7 item 3 — menus / toolbars (parent draft).

Drives the menu backend protocol with a fake menu tree: proves
open/walk/invoke, disabled / ambiguous / not-found / rebuilt
refusals, window move+rename survival via the item-1 engine,
snapshot anchoring via the item-2 adapter, C-14 confirm gating,
and that rectangles are never read (a moved item still
invokes). Real Windows behaviour — real menu bars, real
ExpandCollapse / Invoke patterns — stays field work and is not
claimed here.

Every test resets the handle store, so it cannot poison later
suites in the same process.
"""

import ast
import os
import unittest

from core.desktop import menu_paths as mp  # INTENTIONAL (Phase 3 S7a): menu_paths moved to core/desktop/
from core.desktop import perception_adapter as pa  # INTENTIONAL (Phase 3 S7a): perception_adapter moved to core/desktop/
from core.desktop import target_handles as th  # INTENTIONAL (Phase 3 S7a): target_handles moved to core/desktop/


FG = {"hwnd": 111, "pid": 42, "title": "Notes", "proc": "notes.exe"}
BOUNDS = (0, 0, 800, 600)


def _node(name, aid, children=None, enabled=True, checked=None,
          rect=(0, 0, 100, 20)):
    return {"name": name, "control_type": "MenuItem",
            "automation_id": aid, "enabled": enabled,
            "checked": checked, "children": list(children or []),
            "rect": rect}


def _tree():
    return [
        _node("File", "file", [
            _node("New", "new"),
            _node("Open", "open"),
            _node("Save", "save"),
            _node("Save As...", "saveas"),
            _node("Delete", "delete"),
            _node("Exit", "exit"),
        ]),
        _node("Edit", "edit", [
            _node("Undo", "undo"),
            _node("Redo", "redo", enabled=False),
        ]),
        _node("Tools", "tools", [_node("Options", "options")],
              enabled=False),
        _node("View", "view", [
            _node("Zoom", "zoom1"),
            _node("Zoom", "zoom2"),
        ]),
        _node("Help", "help", [_node("About", "about")]),
    ]


class FakePerceptionBackend:
    """Minimal item-2 backend: one fake capture, fixed foreground."""

    def __init__(self, fg=None):
        self.fg = dict(fg) if fg is not None else dict(FG)

    def foreground_identity(self):
        return dict(self.fg)

    def bounds(self):
        return BOUNDS

    def capture_block_reason(self):
        return None

    def capture(self):
        return {"fake_image": True}

    def marks(self, capture, fg, ocr_words):
        return [], ""

    def controls(self, capture, fg):
        return [], ""

    def ocr_words(self, capture):
        return [], ""

    def pixels(self, capture):
        return {}, ""


class FakeMenuBackend:
    """Fake menu tree: expand returns the node's children, invoke
    records. ``rebuild_on_expand`` simulates a menu rebuilt
    between observation and invoke: expanding bumps the stamp."""

    def __init__(self, tree=None, fg=None):
        self.tree = tree if tree is not None else _tree()
        self.fg = dict(fg) if fg is not None else dict(FG)
        self.stamp = 1
        self.tree_calls = 0
        self.expanded = []
        self.invoked = []
        self.rebuild_on_expand = None
        self.invoke_result = (True, False, "")
        self.move_rects_on_expand = False

    def foreground_identity(self):
        return dict(self.fg)

    def bounds(self):
        return BOUNDS

    def menu_tree(self):
        self.tree_calls += 1
        return (list(self.tree), self.stamp, "")

    def expand(self, node):
        self.expanded.append(node.get("name"))
        if self.rebuild_on_expand is not None:
            self.stamp += 1
            return (list(self.rebuild_on_expand), self.stamp, "")
        children = list(node.get("children") or [])
        if self.move_rects_on_expand:
            moved = []
            for child in children:
                copy = dict(child)
                copy["rect"] = (900, 900, 999, 999)
                moved.append(copy)
            return (moved, self.stamp, "")
        return (children, self.stamp, "")

    def invoke(self, node):
        self.invoked.append(node.get("name"))
        return self.invoke_result


def _snapshot(fg=None):
    return pa.observe(backend=FakePerceptionBackend(fg))


class _Base(unittest.TestCase):
    def setUp(self):
        th.reset_store()

    def tearDown(self):
        th.reset_store()


class ParseTests(_Base):
    def test_list_and_string_forms(self):
        self.assertEqual(mp.parse_path(["File", "Save As"]),
                         ["File", "Save As"])
        self.assertEqual(mp.parse_path("File → Save As"),
                         ["File", "Save As"])
        self.assertEqual(mp.parse_path("File > Save As"),
                         ["File", "Save As"])
        self.assertEqual(mp.parse_path("  "), [])
        self.assertEqual(mp.parse_path("File →  → Save"),
                         ["File", "Save"])

    def test_destructive_leaf_classification(self):
        self.assertTrue(mp.is_destructive_leaf("Delete"))
        self.assertTrue(mp.is_destructive_leaf("&Remove..."))
        self.assertTrue(mp.is_destructive_leaf("Send"))
        self.assertFalse(mp.is_destructive_leaf("Save"))
        self.assertFalse(mp.is_destructive_leaf("Save As..."))
        self.assertFalse(mp.is_destructive_leaf("Sender settings"))


class ListTests(_Base):
    def test_list_roots_with_states(self):
        result = mp.list_menu(snapshot=_snapshot(),
                              backend=FakeMenuBackend())
        self.assertTrue(result["ok"])
        self.assertIsNone(result["refusal"])
        names = [item["name"] for item in result["items"]]
        self.assertEqual(names, ["File", "Edit", "Tools", "View", "Help"])
        by_name = {item["name"]: item for item in result["items"]}
        self.assertFalse(by_name["Tools"]["enabled"])
        self.assertTrue(by_name["File"]["enabled"])
        self.assertTrue(by_name["File"]["has_children"])
        self.assertEqual(result["tree_stamp"], 1)
        self.assertTrue(result["snapshot_stamp"])
        self.assertTrue(result["window_handle_id"]
                        .startswith("th-window-"))

    def test_list_unexposed_menu_refuses(self):
        backend = FakeMenuBackend(tree=[])
        backend.menu_tree = lambda: ([], 1, "no menu bar exposed")
        result = mp.list_menu(snapshot=_snapshot(), backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "menu-unexposed")
        self.assertIn("isn't exposed", result["refusal"]["reason"])
        self.assertIn("keyboard shortcut", result["refusal"]["reason"])


class InvokeTests(_Base):
    def test_invoke_leaf_walk(self):
        backend = FakeMenuBackend()
        snap = _snapshot()
        result = mp.invoke_path(["File", "Save"],
                                snapshot=snap, backend=backend)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["invoked"])
        self.assertFalse(result["opened"])
        self.assertEqual(result["path"], ["File", "Save"])
        self.assertEqual(backend.expanded, ["File", "Save"])
        self.assertEqual(backend.invoked, ["Save"])
        self.assertEqual(result["verification"],
                         "invoked-without-verification")
        self.assertEqual(result["snapshot_stamp"], snap["stamp"])
        self.assertEqual(len(result["item_handle_ids"]), 2)
        self.assertIn("File → Save invoked",
                      mp.describe(result))

    def test_invoke_leaf_string_path(self):
        backend = FakeMenuBackend()
        result = mp.invoke_path("File → Open", snapshot=_snapshot(),
                                backend=backend)
        self.assertTrue(result["ok"])
        self.assertEqual(backend.invoked, ["Open"])

    def test_open_submenu_reports_opened_not_invoked(self):
        backend = FakeMenuBackend()
        result = mp.invoke_path(["File"], snapshot=_snapshot(),
                                backend=backend)
        self.assertTrue(result["ok"])
        self.assertTrue(result["opened"])
        self.assertFalse(result["invoked"])
        self.assertEqual(backend.invoked, [])
        names = [item["name"] for item in result["items"]]
        self.assertEqual(names, ["New", "Open", "Save", "Save As...",
                                 "Delete", "Exit"])
        self.assertEqual(result["verification"],
                         "menu opened (no action invoked)")

    def test_verified_invoke_reports_verified(self):
        backend = FakeMenuBackend()
        backend.invoke_result = (True, True, "")
        result = mp.invoke_path(["Help", "About"], snapshot=_snapshot(),
                                backend=backend)
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"], "verified")

    def test_disabled_leaf_refuses_naming_state(self):
        backend = FakeMenuBackend()
        result = mp.invoke_path(["Edit", "Redo"], snapshot=_snapshot(),
                                backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "item-disabled")
        self.assertIn("Redo", result["refusal"]["reason"])
        self.assertIn("disabled", result["refusal"]["reason"])
        self.assertEqual(backend.invoked, [])

    def test_disabled_intermediate_refuses_before_expand(self):
        backend = FakeMenuBackend()
        result = mp.invoke_path(["Tools", "Options"],
                                snapshot=_snapshot(), backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "item-disabled")
        self.assertEqual(backend.expanded, [])

    def test_ambiguous_path_lists_both_never_picks_first(self):
        backend = FakeMenuBackend()
        result = mp.invoke_path(["View", "Zoom"], snapshot=_snapshot(),
                                backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "item-ambiguous")
        self.assertIn("zoom1", result["refusal"]["reason"])
        self.assertIn("zoom2", result["refusal"]["reason"])
        self.assertEqual(backend.invoked, [])

    def test_not_found_lists_siblings(self):
        backend = FakeMenuBackend()
        result = mp.invoke_path(["File", "Print"], snapshot=_snapshot(),
                                backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "item-not-found")
        self.assertIn("Save", result["refusal"]["reason"])
        self.assertEqual(backend.invoked, [])

    def test_no_fuzzy_match_on_changed_text(self):
        backend = FakeMenuBackend()
        # "Save As" must NOT match "Save As..." and "Save" must
        # NOT match "Save As..." either.
        result = mp.invoke_path(["File", "Save As"],
                                snapshot=_snapshot(), backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "item-not-found")
        self.assertEqual(backend.invoked, [])

    def test_not_a_submenu_refuses(self):
        backend = FakeMenuBackend()
        result = mp.invoke_path(["File", "Save", "Extra"],
                                snapshot=_snapshot(), backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "not-a-submenu")
        self.assertEqual(backend.invoked, [])

    def test_rebuilt_menu_mid_walk_refuses(self):
        backend = FakeMenuBackend()
        backend.rebuild_on_expand = [_node("Other", "other")]
        result = mp.invoke_path(["File", "Save"], snapshot=_snapshot(),
                                backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "menu-rebuilt")
        self.assertEqual(backend.invoked, [])

    def test_expected_stamp_mismatch_refuses_before_walk(self):
        backend = FakeMenuBackend()
        listed = mp.list_menu(snapshot=_snapshot(), backend=backend)
        self.assertTrue(listed["ok"])
        result = mp.invoke_path(["File", "Save"], snapshot=_snapshot(),
                                backend=backend,
                                expected_tree_stamp=999)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "menu-rebuilt")
        self.assertEqual(backend.expanded, [])

    def test_window_rename_survives_on_same_hwnd(self):
        handle = th.mint_window({"hwnd": 111, "pid": 42,
                                 "title": "Notes"})
        renamed_fg = {"hwnd": 111, "pid": 42,
                      "title": "Notes — modified", "proc": "notes.exe"}
        backend = FakeMenuBackend(fg=renamed_fg)
        result = mp.invoke_path(["File", "Save"],
                                snapshot=_snapshot(), backend=backend,
                                window_handle_id=handle["id"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(backend.invoked, ["Save"])

    def test_window_replaced_same_title_refuses(self):
        handle = th.mint_window({"hwnd": 111, "pid": 42,
                                 "title": "Notes"})
        replaced_fg = {"hwnd": 222, "pid": 99, "title": "Notes",
                       "proc": "notes.exe"}
        backend = FakeMenuBackend(fg=replaced_fg)
        result = mp.invoke_path(["File", "Save"],
                                snapshot=_snapshot(fg=replaced_fg),
                                backend=backend,
                                window_handle_id=handle["id"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "window-handle-refused")
        self.assertIn("hwnd", result["refusal"]["reason"])
        self.assertEqual(backend.invoked, [])

    def test_stale_snapshot_refuses(self):
        backend = FakeMenuBackend(
            fg={"hwnd": 999, "pid": 42, "title": "Notes"})
        result = mp.invoke_path(["File", "Save"],
                                snapshot=_snapshot(), backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "snapshot-stale")
        self.assertEqual(backend.tree_calls, 0)

    def test_denied_snapshot_refuses_without_walking(self):
        backend = FakeMenuBackend()
        denied = {"stamp": "deadbeef", "capture_status": "denied",
                  "capture_reason": "capture denylisted",
                  "foreground_identity": dict(FG), "bounds": BOUNDS}
        result = mp.invoke_path(["File", "Save"], snapshot=denied,
                                backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "snapshot-unavailable")
        self.assertEqual(backend.tree_calls, 0)

    def test_unknown_window_handle_refuses(self):
        backend = FakeMenuBackend()
        result = mp.invoke_path(["File", "Save"], snapshot=_snapshot(),
                                backend=backend,
                                window_handle_id="th-window-9999")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "window-handle-refused")
        self.assertEqual(backend.invoked, [])

    def test_empty_path_refuses(self):
        backend = FakeMenuBackend()
        result = mp.invoke_path([], snapshot=_snapshot(),
                                backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "empty-path")

    def test_moved_item_invokes_on_identity_not_rect(self):
        backend = FakeMenuBackend()
        backend.move_rects_on_expand = True
        result = mp.invoke_path(["File", "Save"], snapshot=_snapshot(),
                                backend=backend)
        self.assertTrue(result["ok"], result)
        self.assertEqual(backend.invoked, ["Save"])

    def test_destructive_leaf_needs_confirmation(self):
        backend = FakeMenuBackend()
        refused = mp.invoke_path(["File", "Delete"],
                                 snapshot=_snapshot(), backend=backend)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["refusal"]["code"], "confirm-required")
        self.assertEqual(backend.invoked, [])
        allowed = mp.invoke_path(["File", "Delete"],
                                 snapshot=_snapshot(), backend=backend,
                                 confirmed=True)
        self.assertTrue(allowed["ok"])
        self.assertEqual(backend.invoked, ["Delete"])

    def test_caller_declared_effectful_needs_confirmation(self):
        backend = FakeMenuBackend()
        refused = mp.invoke_path(["File", "Save"], snapshot=_snapshot(),
                                 backend=backend, effectful=True)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["refusal"]["code"], "confirm-required")
        self.assertEqual(backend.invoked, [])
        allowed = mp.invoke_path(["File", "Save"], snapshot=_snapshot(),
                                 backend=backend, effectful=True,
                                 confirmed=True)
        self.assertTrue(allowed["ok"])

    def test_invoke_failure_reports_honestly(self):
        backend = FakeMenuBackend()
        backend.invoke_result = (False, False,
                                 "item does not expose an Invoke pattern")
        result = mp.invoke_path(["File", "Save"], snapshot=_snapshot(),
                                backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "invoke-failed")
        self.assertIn("Invoke pattern", result["refusal"]["reason"])


class ModuleHygieneTests(_Base):
    def test_stdlib_only_at_import(self):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "core", "desktop", "menu_paths.py")  # INTENTIONAL (Phase 3 S7a): menu_paths moved to core/desktop/
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        stdlib = {"hashlib", "json"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], stdlib,
                                  alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    # Relative import: the tree's own modules, imported
                    # lazily inside methods — not a new dep.
                    continue
                self.assertIn((node.module or "").split(".")[0], stdlib,
                              node.module)

    def test_item1_roundtrip_from_menu_result(self):
        backend = FakeMenuBackend()
        result = mp.invoke_path(["File", "Save"], snapshot=_snapshot(),
                                backend=backend)
        self.assertTrue(result["ok"])
        handle = th.get_handle(result["window_handle_id"])
        self.assertIsNotNone(handle)
        self.assertEqual(handle["kind"], "window")


if __name__ == "__main__":
    unittest.main()
