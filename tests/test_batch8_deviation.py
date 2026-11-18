"""Batch-8 deviation decision — context-menu "verified" contract.

Batch 8 disclosed: "verified" is a fresh backend tree read, not a
perception-snapshot cross-check. This suite pins the decision made
2026-11-03 under the code-rulings delegation (commercial cool
scifi JARVIS):

- TRIGGER verification: ACCEPTED as the fresh backend tree read.
  A perception-snapshot cross-check is not an available
  alternative: the snapshot predates the popup (the popup does
  not exist until the trigger creates it) and covers the
  foreground window, while a popup is often a separate
  top-level window — so there is nothing in the snapshot to
  cross-check against. The engine docstring already states this
  honestly; this suite pins the contract's legs: present ->
  verified, unreadable -> opened-without-verification (never a
  claimed success), empty -> unexposed refusal.
- INVOKE binding: the walk uses the TRIGGER's menu node, never
  a re-derived current menu. The old invoke code re-read
  backend.current_context_menu() — whatever Menu the foreground
  walk finds first — and would walk a different menu than the
  triggered one when a second Menu is present. A same-named
  item in the wrong menu (a "Delete" in another target's menu)
  still passes the walk's exact-name match, and a path approval
  names the path, not the menu, so C-14 does not save that
  case. Binding by construction (walk the trigger's node)
  closes the substitution: no node is plumbed through public
  results (backend-private keys never leak into receipts), no
  node-equality semantics are needed, and there is no
  compare-then-walk window.

Technique: self-contained fakes mirroring the batch-8 suite
(fake perception backend + real perception_adapter.observe),
with one addition the batch-8 fake lacks: SEPARATE trigger and
current menus, so the re-derivation seam is exercisable at all
(the batch-8 fake returns the trigger's node from
current_context_menu, which is why the seam was invisible).
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.desktop import context_menus as cm  # Batch-8 deviation decision 2026-11-03 INTENTIONAL: context_menus in core/desktop/
from core.desktop import perception_adapter as pa  # Batch-8 deviation decision 2026-11-03 INTENTIONAL: perception_adapter in core/desktop/

FG = {"hwnd": 111, "pid": 42, "title": "Notes", "proc": "notes.exe"}
BOUNDS = (0, 0, 800, 600)

CONTROLS = [
    {"id": 1, "name": "report.txt", "control_type": "TreeItem",
     "rect": (10, 10, 200, 30), "source": "walk"},
    {"id": 2, "name": "notes.md", "control_type": "TreeItem",
     "rect": (10, 40, 200, 60), "source": "walk"},
]


def _cnode(name, key, children=None, enabled=True):
    return {"key": key, "name": name, "control_type": "MenuItem",
            "automation_id": key, "enabled": enabled,
            "children": children or []}


def _trigger_tree():
    return [
        _cnode("Open", "open"),
        _cnode("Delete", "delete"),
    ]


def _current_tree():
    # A DIFFERENT menu that shares the "Delete" name: the
    # substitution case. Its Delete key differs, so the test
    # can tell which menu was acted on.
    return [
        _cnode("Format", "format"),
        _cnode("Delete", "delete-other"),
    ]


class FakePerceptionBackend:
    def __init__(self, fg=None, controls=None):
        self.fg = dict(fg) if fg is not None else dict(FG)
        self.control_rows = list(controls) if controls is not None \
            else list(CONTROLS)

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
        return list(self.control_rows), ""

    def ocr_words(self, capture):
        return [], ""

    def pixels(self, capture):
        return {}, ""


class DeviationBackend:
    """Context protocol with separate trigger/current menus.

    trigger_tree is what the trigger opens; current_tree is what
    current_context_menu returns. When current_tree is None, the
    current menu IS the trigger menu (the batch-8 fake's
    behaviour). When current_node is None, no menu is current.
    """

    def __init__(self, trigger_tree=None, current_tree=None,
                 current_node_present=True):
        self.trigger_tree = (trigger_tree if trigger_tree is not None
                             else _trigger_tree())
        self.current_tree = current_tree
        self.trigger_node = {"key": "ctx-trigger", "name": "",
                             "control_type": "Menu"}
        self.current_node = ({"key": "ctx-current", "name": "",
                              "control_type": "Menu"}
                             if current_node_present else None)
        self.fg = dict(FG)
        self.stamp = 1
        self.invoked = []
        self.tree_calls = 0
        self.tree_fail = False
        self.tree_fail_on_call = None
        self.tree_empty = False
        self.invoke_result = (True, False, "")

    def foreground_identity(self):
        return dict(self.fg)

    def bounds(self):
        return BOUNDS

    def open_context_menu(self, target):
        return self.trigger_node, ""

    def current_context_menu(self):
        if self.current_node is None:
            return None, "no context menu open"
        return self.current_node, ""

    def context_menu_tree(self, node):
        self.tree_calls += 1
        if self.tree_fail:
            raise RuntimeError("tree read boom")
        if (self.tree_fail_on_call is not None
                and self.tree_calls >= self.tree_fail_on_call):
            raise RuntimeError("tree read boom")
        if self.tree_empty:
            return ([], self.stamp, "")
        key = node.get("key") if isinstance(node, dict) else None
        if key == "ctx-trigger":
            return (list(self.trigger_tree), self.stamp, "")
        if key == "ctx-current":
            tree = (self.current_tree if self.current_tree is not None
                    else self.trigger_tree)
            return (list(tree), self.stamp, "")
        raise RuntimeError("unknown context menu node")

    def expand(self, node):
        return (list(node.get("children") or []), self.stamp, "")

    def invoke(self, node):
        self.invoked.append(node.get("key"))
        return self.invoke_result


def _snapshot():
    return pa.observe(backend=FakePerceptionBackend())


class TestTriggerContract(unittest.TestCase):
    def test_trigger_verified_is_fresh_live_read(self):
        backend = DeviationBackend()
        first = cm.open_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["verification"], "verified")
        self.assertEqual([i["name"] for i in first["items"]],
                         ["Open", "Delete"])
        # The read is live, not cached: change the tree, trigger
        # again, and the new listing reflects the new tree.
        backend.trigger_tree = [_cnode("Open", "open"),
                                _cnode("Rename", "rename")]
        second = cm.open_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertEqual([i["name"] for i in second["items"]],
                         ["Open", "Rename"])

    def test_trigger_unreadable_tree_reports_without_verification(self):
        backend = DeviationBackend()
        backend.tree_fail = True
        result = cm.open_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["verification"],
                         "opened-without-verification")
        self.assertEqual(result["items"], [])

    def test_trigger_empty_tree_refuses_as_unexposed(self):
        backend = DeviationBackend()
        backend.tree_empty = True
        result = cm.open_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "menu-unexposed")


class TestInvokeBinding(unittest.TestCase):
    def test_invoke_refuses_when_trigger_unverified(self):
        backend = DeviationBackend()
        backend.tree_fail = True
        result = cm.invoke_context_path(
            "Delete", "report.txt", snapshot=_snapshot(),
            backend=backend, confirmed=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "menu-unavailable")
        self.assertEqual(backend.invoked, [])

    def test_invoke_walks_trigger_menu_not_current_menu(self):
        # The substitution case: the current menu shares the
        # "Delete" name but is a different menu. The walk must
        # act on the trigger's Delete (key "delete"), never the
        # current menu's Delete (key "delete-other").
        backend = DeviationBackend(current_tree=_current_tree())
        result = cm.invoke_context_path(
            "Delete", "report.txt", snapshot=_snapshot(),
            backend=backend, confirmed=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "invoked")
        self.assertEqual(backend.invoked, ["delete"])

    def test_invoke_ignores_absent_current_menu(self):
        # No menu is "current", but the trigger's menu is intact:
        # invoke must still walk the triggered menu, since it
        # no longer depends on current_context_menu at all.
        backend = DeviationBackend(current_node_present=False)
        result = cm.invoke_context_path(
            "Open", "report.txt", snapshot=_snapshot(),
            backend=backend)
        self.assertTrue(result["ok"], result)
        self.assertEqual(backend.invoked, ["open"])

    def test_invoke_refuses_when_trigger_menu_vanishes(self):
        # The trigger's list read succeeds (call 1); the walk's
        # read fails (call 2): the menu vanished mid-call, so
        # the walk refuses honestly and invokes nothing.
        backend = DeviationBackend()
        backend.tree_fail_on_call = 2
        result = cm.invoke_context_path(
            "Delete", "report.txt", snapshot=_snapshot(),
            backend=backend, confirmed=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "menu-unavailable")
        self.assertEqual(backend.invoked, [])

    def test_invoke_delete_without_confirm_stops_in_mismatch_case(self):
        # C-14 survives the binding change: an unconfirmed
        # destructive leaf stops even when the current menu
        # differs from the triggered one.
        backend = DeviationBackend(current_tree=_current_tree())
        result = cm.invoke_context_path(
            "Delete", "report.txt", snapshot=_snapshot(),
            backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "confirm-required")
        self.assertEqual(backend.invoked, [])


class TestContractHonesty(unittest.TestCase):
    def test_docstring_states_fresh_read_not_snapshot_crosscheck(self):
        # Pins the accepted trigger contract in words: the
        # engine must keep saying what verify actually does.
        doc = " ".join((cm.__doc__ or "").split())
        self.assertIn("does NOT cross-check against a perception "
                      "snapshot", doc)
        self.assertIn("Verify fresh, never from pre-trigger state",
                      doc)

    def test_list_still_reads_current_menu_by_design(self):
        # Scope guard: the binding change is invoke-only. List
        # reads the CURRENTLY open menu by design, so with a
        # differing current menu it lists the current tree.
        backend = DeviationBackend(current_tree=_current_tree())
        triggered = cm.open_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertTrue(triggered["ok"], triggered)
        listed = cm.list_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertTrue(listed["ok"], listed)
        self.assertEqual([i["name"] for i in listed["items"]],
                         ["Format", "Delete"])


if __name__ == "__main__":
    unittest.main()
