"""§7 consumers batch 8 — context-menu trigger (item-3 follow-up).

Item 3 shipped menu-bar roots only; ``core.desktop.context_menus``
opens a target's context menu through an exposed path and
walks it with the item-3 engine itself. This suite proves:

- engine: trigger opens + fresh-tree verification, trigger
  failures honest (unsupported backend, no open, empty tree,
  unreadable tree), list reads an already-open menu only,
  target resolution (exact name, ambiguous, not-found,
  mark-handle ok/stale/gone, control-handle guidance,
  unknown handle, missing target), snapshot/window
  re-proof, invoke reuse (exact-name, disabled, ambiguous,
  not-found + siblings, rebuilt, not-a-submenu, C-14
  stop-then-resolve with zero invokes, Save exclusion,
  invoked-without-verification honesty, invoke-failed).
- VS Code "delete file": the adapter phrase resolves
  confirm-gated with the explorer_context_menu quirk, and
  the context engine drives a Delete path for a target
  (engine-level + funnel-level).
- funnel: registration + count 655 (stale-pin sweep: INTENTIONAL — 653 -> 654: wallpaper_apply; wallpaper-revert batch (2026-11-05): INTENTIONAL — 654 -> 655: wallpaper_revert), gate legs (trigger /
  invoke gated, list free), approved paths reach the
  engine, C-14 stop-then-resolve through the funnel with
  zero invokes.
- coordinator: the batch-8 refusal shapes rewrite to
  context_menu_refused, the C-14 stop shape pauses with
  confirm_arg "confirmed" (batch-6 protocol), success
  lines pass through untouched, and a refused / stopped
  context-menu step behaves through a real coordinator run.

Technique (no product seam): the engine's default-backend
factory is patched to return fakes, and item-2 snapshots
are built through the real perception adapter over a fake
perception backend carrying the target controls. Adapter
tests use the real registry with the real landed adapters
(reset per test, exactly like the adapter suites).
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.desktop import app_adapters as aa  # INTENTIONAL (Phase 3 S7a-2): moved to core/desktop/
from core import confirm_gate
from core.desktop import context_menus as cm  # INTENTIONAL (Phase 3 S7a-2): moved to core/desktop/
from core import injection
from core.desktop import perception_adapter as pa  # INTENTIONAL (Phase 3 S7a): perception_adapter moved to core/desktop/
from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.desktop import target_handles as th  # INTENTIONAL (Phase 3 S7a): target_handles moved to core/desktop/
from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.desktop import vscode_adapter as va  # INTENTIONAL (Phase 3 S7b): moved to core/desktop/
from core.config import config as app_config

TOOLS = tools

FG = {"hwnd": 111, "pid": 42, "title": "Notes", "proc": "notes.exe"}
BOUNDS = (0, 0, 800, 600)

CONTROLS = [
    {"id": 1, "name": "report.txt", "control_type": "TreeItem",
     "rect": (10, 10, 200, 30), "source": "walk"},
    {"id": 2, "name": "notes.md", "control_type": "TreeItem",
     "rect": (10, 40, 200, 60), "source": "walk"},
    {"id": 3, "name": "src", "control_type": "TreeItem",
     "rect": (10, 70, 200, 90), "source": "walk"},
]


def _cnode(name, key, children=None, enabled=True):
    return {"key": key, "name": name, "control_type": "MenuItem",
            "automation_id": key, "enabled": enabled,
            "children": children or []}


def _ctx_tree():
    return [
        _cnode("Open", "open"),
        _cnode("Rename", "rename"),
        _cnode("Delete", "delete"),
        _cnode("Open With", "openwith", [
            _cnode("Notepad", "notepad"),
            _cnode("WordPad", "wordpad"),
        ]),
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


class FakeContextBackend:
    """Full context protocol over a canned menu tree."""

    def __init__(self, tree=None, fg=None):
        self.tree = tree if tree is not None else _ctx_tree()
        self.fg = dict(fg) if fg is not None else dict(FG)
        self.stamp = 1
        self.open_node = None
        self.triggered = []
        self.invoked = []
        self.expanded = []
        self.open_returns_none = False
        self.tree_fail = False
        self.tree_empty = False
        self.rebuild_on_expand = None
        self.invoke_result = (True, False, "")

    def foreground_identity(self):
        return dict(self.fg)

    def bounds(self):
        return BOUNDS

    def open_context_menu(self, target):
        self.triggered.append(target.get("name"))
        if self.open_returns_none:
            return None, "no menu appeared"
        if self.open_node is None:
            self.open_node = {"key": "ctx", "name": "",
                              "control_type": "Menu"}
        return self.open_node, ""

    def current_context_menu(self):
        if self.open_node is None:
            return None, "no context menu open"
        return self.open_node, ""

    def context_menu_tree(self, node):
        if self.tree_fail:
            raise RuntimeError("tree read boom")
        if self.tree_empty:
            return ([], self.stamp, "")
        return (list(self.tree), self.stamp, "")

    def expand(self, node):
        self.expanded.append(node.get("name"))
        if self.rebuild_on_expand is not None:
            self.stamp += 1
            return (list(self.rebuild_on_expand), self.stamp, "")
        return (list(node.get("children") or []), self.stamp, "")

    def invoke(self, node):
        self.invoked.append(node.get("name"))
        return self.invoke_result


class MenuOnlyBackend:
    """A menu-bar-only backend: no context methods at all."""

    def __init__(self, fg=None):
        self.fg = dict(fg) if fg is not None else dict(FG)

    def foreground_identity(self):
        return dict(self.fg)

    def bounds(self):
        return BOUNDS

    def expand(self, node):
        return (list(node.get("children") or []), 1, "")

    def invoke(self, node):
        return (True, False, "")


def _snapshot(fg=None, controls=None):
    return pa.observe(backend=FakePerceptionBackend(fg, controls))


def _snapshot_with_controls(controls):
    return _snapshot(controls=controls)


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------


class TestRegistration(unittest.TestCase):
    def test_context_menu_tool_registered_and_count_is_757(self):  # INTENTIONAL — Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan
        names = [d["name"] for d in TOOLS.get_declarations()]
        # §7 consumers batch 8: INTENTIONAL — 1 tool added
        # (652 -> 653): context_menu_action. Stale-pin sweep: INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (count proof: registry 655, no duplicates); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(names), 757, "registry must be 754 + 1")  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan
        self.assertIn("context_menu_action", names)


# ----------------------------------------------------------------------
# Engine: trigger
# ----------------------------------------------------------------------


class TestTrigger(unittest.TestCase):
    def test_trigger_opens_and_verifies(self):
        backend = FakeContextBackend()
        result = cm.open_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "opened")
        self.assertEqual(result["verification"], "verified")
        self.assertEqual(result["target_name"], "report.txt")
        self.assertEqual([i["name"] for i in result["items"]],
                         ["Open", "Rename", "Delete", "Open With"])
        self.assertEqual(backend.triggered, ["report.txt"])

    def test_trigger_describe(self):
        backend = FakeContextBackend()
        result = cm.open_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        line = cm.describe(result)
        self.assertIn("Context menu for 'report.txt' opened", line)
        self.assertIn("verified", line)

    def test_trigger_unsupported_backend_refuses_with_manual_path(self):
        backend = MenuOnlyBackend()
        result = cm.open_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "context-menu-unsupported")
        self.assertIn("Right-click the target yourself",
                      result["refusal"]["reason"])

    def test_trigger_no_menu_appears_refuses(self):
        backend = FakeContextBackend()
        backend.open_returns_none = True
        result = cm.open_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "trigger-failed")
        self.assertIn("Right-click the target yourself",
                      result["refusal"]["reason"])

    def test_trigger_empty_tree_refuses_as_unexposed(self):
        backend = FakeContextBackend()
        backend.tree_empty = True
        result = cm.open_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "menu-unexposed")
        self.assertIn("Right-click the target yourself",
                      result["refusal"]["reason"])

    def test_trigger_unreadable_tree_reports_without_verification(self):
        backend = FakeContextBackend()
        backend.tree_fail = True
        result = cm.open_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["verification"],
                         "opened-without-verification")
        self.assertEqual(result["items"], [])


# ----------------------------------------------------------------------
# Engine: target resolution + setup re-proof
# ----------------------------------------------------------------------


class TestTargetResolution(unittest.TestCase):
    def test_missing_target_refuses(self):
        result = cm.open_context_menu(
            None, snapshot=_snapshot(), backend=FakeContextBackend())
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "target-required")

    def test_target_not_found_lists_siblings(self):
        result = cm.open_context_menu(
            "missing.txt", snapshot=_snapshot(),
            backend=FakeContextBackend())
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "target-not-found")
        self.assertIn("report.txt", result["refusal"]["reason"])

    def test_target_ambiguous_lists_matches(self):
        controls = list(CONTROLS) + [
            {"id": 4, "name": "report.txt", "control_type": "TreeItem",
             "rect": (10, 100, 200, 120), "source": "walk"}]
        result = cm.open_context_menu(
            "report.txt", snapshot=_snapshot_with_controls(controls),
            backend=FakeContextBackend())
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "target-ambiguous")

    def test_mark_handle_resolves(self):
        snap = _snapshot()
        mark = dict(CONTROLS[0])
        minted = th.mint_mark(mark, {"hwnd": FG["hwnd"],
                                     "title": FG["title"]})
        backend = FakeContextBackend()
        result = cm.open_context_menu(
            None, target_handle_id=minted["id"], snapshot=snap,
            backend=backend)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["target_name"], "report.txt")
        self.assertEqual(backend.triggered, ["report.txt"])

    def test_mark_handle_stale_foreground_refuses(self):
        snap = _snapshot()
        mark = dict(CONTROLS[0])
        minted = th.mint_mark(mark, {"hwnd": FG["hwnd"],
                                     "title": FG["title"]})
        other_fg = dict(FG, hwnd=999)
        backend = FakeContextBackend(fg=other_fg)
        result = cm.open_context_menu(
            None, target_handle_id=minted["id"],
            snapshot=_snapshot(fg=other_fg), backend=backend)
        # The snapshot itself is fresh under the new fg, but the
        # mark was minted under the old window: F-07 refuses.
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "target-handle-refused")

    def test_mark_handle_target_gone_refuses(self):
        snap = _snapshot()
        mark = dict(CONTROLS[0])
        minted = th.mint_mark(mark, {"hwnd": FG["hwnd"],
                                     "title": FG["title"]})
        gone_controls = [c for c in CONTROLS
                         if c["name"] != "report.txt"]
        result = cm.open_context_menu(
            None, target_handle_id=minted["id"],
            snapshot=_snapshot_with_controls(gone_controls),
            backend=FakeContextBackend())
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "target-gone")

    def test_control_handle_refuses(self):
        # A control handle cannot be re-proven from a snapshot
        # observation (no candidate list is offered, because
        # snapshot controls carry no automation ids): it
        # refuses at resolution, honestly, instead of acting
        # on an unproven target.
        minted = th.mint_control({"automation_id": "file1"},
                                 "report.txt", "TreeItem")
        result = cm.open_context_menu(
            None, target_handle_id=minted["id"], snapshot=_snapshot(),
            backend=FakeContextBackend())
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "target-handle-refused")

    def test_unknown_handle_refuses(self):
        result = cm.open_context_menu(
            None, target_handle_id="no-such-handle",
            snapshot=_snapshot(), backend=FakeContextBackend())
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "target-handle-refused")

    def test_stale_snapshot_refuses(self):
        snap = _snapshot()
        backend = FakeContextBackend(fg=dict(FG, hwnd=999))
        result = cm.open_context_menu(
            "report.txt", snapshot=snap, backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "snapshot-stale")

    def test_stale_window_handle_refuses(self):
        minted = th.mint_window({"hwnd": 111, "title": "Notes"})
        backend = FakeContextBackend(fg=dict(FG, hwnd=999))
        result = cm.open_context_menu(
            "report.txt", snapshot=_snapshot(fg=dict(FG, hwnd=999)),
            backend=backend, window_handle_id=minted["id"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "window-handle-refused")


# ----------------------------------------------------------------------
# Engine: list
# ----------------------------------------------------------------------


class TestList(unittest.TestCase):
    def test_list_without_open_menu_refuses(self):
        backend = FakeContextBackend()
        result = cm.list_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "no-context-menu-open")
        self.assertIn("trigger it first", result["refusal"]["reason"])

    def test_list_after_trigger_reads_open_menu(self):
        backend = FakeContextBackend()
        opened = cm.open_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertTrue(opened["ok"], opened)
        result = cm.list_context_menu(
            "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "listed")
        self.assertEqual([i["name"] for i in result["items"]],
                         ["Open", "Rename", "Delete", "Open With"])
        # Listing never re-triggers.
        self.assertEqual(backend.triggered, ["report.txt"])


# ----------------------------------------------------------------------
# Engine: invoke (item-3 reuse)
# ----------------------------------------------------------------------


class TestInvoke(unittest.TestCase):
    def test_invoke_plain_leaf(self):
        backend = FakeContextBackend()
        result = cm.invoke_context_path(
            "Open", "report.txt", snapshot=_snapshot(), backend=backend)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "invoked")
        self.assertEqual(result["verification"],
                         "invoked-without-verification")
        self.assertEqual(backend.invoked, ["Open"])

    def test_invoke_submenu_leaf_opens(self):
        backend = FakeContextBackend()
        result = cm.invoke_context_path(
            "Open With", "report.txt", snapshot=_snapshot(),
            backend=backend)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "opened")
        self.assertEqual([i["name"] for i in result["items"]],
                         ["Notepad", "WordPad"])

    def test_invoke_nested_path(self):
        backend = FakeContextBackend()
        result = cm.invoke_context_path(
            "Open With → Notepad", "report.txt",
            snapshot=_snapshot(), backend=backend)
        self.assertTrue(result["ok"], result)
        self.assertEqual(backend.invoked, ["Notepad"])

    def test_invoke_not_found_lists_siblings(self):
        backend = FakeContextBackend()
        result = cm.invoke_context_path(
            "Explode", "report.txt", snapshot=_snapshot(),
            backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "item-not-found")
        self.assertIn("Delete", result["refusal"]["reason"])

    def test_invoke_ambiguous_refuses(self):
        tree = [_cnode("Delete", "d1"), _cnode("Delete", "d2")]
        backend = FakeContextBackend(tree=tree)
        result = cm.invoke_context_path(
            "Delete", "report.txt", snapshot=_snapshot(),
            backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "item-ambiguous")
        self.assertEqual(backend.invoked, [])

    def test_invoke_disabled_refuses(self):
        tree = [_cnode("Delete", "d1", enabled=False)]
        backend = FakeContextBackend(tree=tree)
        result = cm.invoke_context_path(
            "Delete", "report.txt", snapshot=_snapshot(),
            backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "item-disabled")
        self.assertEqual(backend.invoked, [])

    def test_invoke_rebuilt_refuses(self):
        backend = FakeContextBackend()
        backend.rebuild_on_expand = [_cnode("Changed", "c1")]
        result = cm.invoke_context_path(
            "Open With → Notepad", "report.txt",
            snapshot=_snapshot(), backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "menu-rebuilt")
        self.assertEqual(backend.invoked, [])

    def test_invoke_not_a_submenu_refuses(self):
        backend = FakeContextBackend()
        result = cm.invoke_context_path(
            "Open → Notepad", "report.txt", snapshot=_snapshot(),
            backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "not-a-submenu")

    def test_invoke_trigger_failure_is_the_answer(self):
        backend = FakeContextBackend()
        backend.open_returns_none = True
        result = cm.invoke_context_path(
            "Delete", "report.txt", snapshot=_snapshot(),
            backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "trigger-failed")
        self.assertEqual(result["path"], ["Delete"])
        self.assertEqual(backend.invoked, [])

    def test_invoke_empty_path_refuses(self):
        result = cm.invoke_context_path(
            "", "report.txt", snapshot=_snapshot(),
            backend=FakeContextBackend())
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "empty-path")

    def test_c14_stop_then_resolve_zero_invokes(self):
        backend = FakeContextBackend()
        stopped = cm.invoke_context_path(
            "Delete", "report.txt", snapshot=_snapshot(),
            backend=backend)
        self.assertFalse(stopped["ok"])
        self.assertEqual(stopped["refusal"]["code"], "confirm-required")
        self.assertEqual(backend.invoked, [])
        resolved = cm.invoke_context_path(
            "Delete", "report.txt", snapshot=_snapshot(),
            backend=backend, confirmed=True)
        self.assertTrue(resolved["ok"], resolved)
        self.assertEqual(backend.invoked, ["Delete"])

    def test_save_leaf_not_c14(self):
        tree = [_cnode("Save", "save")]
        backend = FakeContextBackend(tree=tree)
        result = cm.invoke_context_path(
            "Save", "report.txt", snapshot=_snapshot(),
            backend=backend)
        self.assertTrue(result["ok"], result)
        self.assertEqual(backend.invoked, ["Save"])

    def test_effectful_declared_stops(self):
        backend = FakeContextBackend()
        result = cm.invoke_context_path(
            "Rename", "report.txt", snapshot=_snapshot(),
            backend=backend, effectful=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "confirm-required")
        self.assertEqual(backend.invoked, [])

    def test_invoke_failed_honest(self):
        backend = FakeContextBackend()
        backend.invoke_result = (False, False, "pattern missing")
        result = cm.invoke_context_path(
            "Open", "report.txt", snapshot=_snapshot(),
            backend=backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "invoke-failed")

    def test_invoke_verified_flag_honoured(self):
        backend = FakeContextBackend()
        backend.invoke_result = (True, True, "")
        result = cm.invoke_context_path(
            "Open", "report.txt", snapshot=_snapshot(),
            backend=backend)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["verification"], "verified")


# ----------------------------------------------------------------------
# VS Code "delete file" path proof
# ----------------------------------------------------------------------


class TestVSCodeDeleteFile(unittest.TestCase):
    def setUp(self):
        aa.reset_registry()
        va.register_vscode_adapter()

    def tearDown(self):
        aa.reset_registry()

    def test_adapter_delete_file_resolves_confirm_gated(self):
        record = va.vscode_adapter_record()
        entry = record["vocabulary"]["delete file"]
        self.assertEqual(entry["kind"], "action")
        self.assertEqual(entry["quirk"], "explorer_context_menu")
        self.assertIn("action:delete file", record["confirm_actions"])
        fg = {"proc": "Code.exe", "title": "file.txt - VS Code",
              "hwnd": 5, "pid": 9}
        stopped = aa.resolve("delete file", foreground=fg,
                             confirmed=False)
        self.assertFalse(stopped["ok"])
        self.assertTrue(stopped["needs_confirmation"])
        resolved = aa.resolve("delete file", foreground=fg,
                              confirmed=True)
        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["target"]["name"], "delete file")

    def test_context_engine_drives_delete_path(self):
        # The adapter names the job; the context engine drives
        # it: unconfirmed stops (C-14, zero invokes), confirmed
        # invokes the Delete leaf of the Explorer menu.
        backend = FakeContextBackend()
        stopped = cm.invoke_context_path(
            "Delete", "report.txt", snapshot=_snapshot(),
            backend=backend)
        self.assertFalse(stopped["ok"])
        self.assertEqual(stopped["refusal"]["code"], "confirm-required")
        self.assertEqual(backend.invoked, [])
        resolved = cm.invoke_context_path(
            "Delete", "report.txt", snapshot=_snapshot(),
            backend=backend, confirmed=True)
        self.assertTrue(resolved["ok"], resolved)
        self.assertEqual(backend.invoked, ["Delete"])


# ----------------------------------------------------------------------
# Funnel
# ----------------------------------------------------------------------


class _DataGuard:
    def __init__(self):
        self._saved = {}

    def _scan(self):
        found = {}
        for path in sorted((ROOT / "data").rglob("*")):
            if path.is_file():
                try:
                    found[path.relative_to(ROOT / "data")] = \
                        path.read_bytes()
                except OSError:
                    pass
        return found

    def __enter__(self):
        self._saved = self._scan()
        return self

    def __exit__(self, *exc):
        current = self._scan()
        for rel in current:
            if rel not in self._saved:
                try:
                    ((ROOT / "data") / rel).unlink()
                except OSError:
                    pass
        for rel, data in self._saved.items():
            path = (ROOT / "data") / rel
            try:
                if not path.exists() or path.read_bytes() != data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            except OSError:
                pass
        return False


class FunnelTests(unittest.TestCase):
    def setUp(self):
        self._guard = _DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False
        self._orig_backend = cm.default_backend
        self._backend = FakeContextBackend()
        cm.default_backend = lambda: self._backend

    def tearDown(self):
        cm.default_backend = self._orig_backend
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()

    def call(self, name, args, approve=False):
        confirm_gate.clear_handler()
        if approve:
            confirm_gate.register_handler(lambda n, s: True)
        try:
            return tools.execute(name, dict(args))
        finally:
            confirm_gate.clear_handler()

    def _snap_arg(self):
        # Snapshots cannot cross the funnel boundary as objects
        # in these tests; the engine takes a fresh observation
        # when none is passed, so funnel tests patch the
        # perception default too. Instead, pass the snapshot
        # dict built here directly (the funnel passes args
        # through to the handler verbatim).
        return _snapshot()

    def test_trigger_is_gated_list_is_free(self):
        snap = self._snap_arg()
        listed = self.call("context_menu_action",
                           {"action": "list", "target": "report.txt",
                            "snapshot": snap})
        # Nothing open yet: list refuses at the engine, but the
        # gate must NOT have refused it (no popup involved).
        self.assertEqual(listed["status"], "success", listed)
        self.assertIn("no context menu is open", listed["result"])
        refused = self.call("context_menu_action",
                            {"action": "trigger", "target": "report.txt",
                             "snapshot": snap})
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["kind"], "not_confirmed")
        refused_invoke = self.call(
            "context_menu_action",
            {"action": "invoke", "target": "report.txt",
             "path": "Delete", "snapshot": snap})
        self.assertEqual(refused_invoke["status"], "refused")
        self.assertEqual(refused_invoke["kind"], "not_confirmed")

    def test_approved_trigger_lists_items(self):
        snap = self._snap_arg()
        out = self.call("context_menu_action",
                        {"action": "trigger", "target": "report.txt",
                         "snapshot": snap}, approve=True)
        self.assertEqual(out["status"], "success", out)
        self.assertIn("Context menu items:", out["result"])
        self.assertIn("- Delete (enabled)", out["result"])

    def test_approved_invoke_c14_stop_then_resolve(self):
        snap = self._snap_arg()
        stopped = self.call("context_menu_action",
                            {"action": "invoke", "target": "report.txt",
                             "path": "Delete", "snapshot": snap},
                            approve=True)
        self.assertEqual(stopped["status"], "success", stopped)
        self.assertIn("confirm it first (C-14 approve-first)",
                      stopped["result"])
        self.assertEqual(self._backend.invoked, [])
        resolved = self.call("context_menu_action",
                             {"action": "invoke", "target": "report.txt",
                              "path": "Delete", "snapshot": snap,
                              "confirmed": True}, approve=True)
        self.assertEqual(resolved["status"], "success", resolved)
        self.assertIn("invoked", resolved["result"])
        self.assertEqual(self._backend.invoked, ["Delete"])

    def test_unknown_action_gates_fail_closed(self):
        out = self.call("context_menu_action",
                        {"action": "explode", "target": "report.txt"})
        self.assertEqual(out["status"], "refused", out)

    def test_vscode_delete_file_funnel_path(self):
        # Adapter resolution (confirm-gated) + context engine
        # drive of the Delete leaf, through the funnel.
        aa.reset_registry()
        va.register_vscode_adapter()
        try:
            fg = {"proc": "Code.exe",
                  "title": "file.txt - VS Code", "hwnd": 5, "pid": 9}
            resolution = aa.resolve("delete file", foreground=fg,
                                    confirmed=True)
            self.assertTrue(resolution["ok"])
            self.assertEqual(resolution["target"]["name"],
                             "delete file")
            snap = self._snap_arg()
            out = self.call("context_menu_action",
                            {"action": "invoke", "target": "report.txt",
                             "path": "Delete", "snapshot": snap,
                             "confirmed": True}, approve=True)
            self.assertEqual(out["status"], "success", out)
            self.assertIn("invoked", out["result"])
            self.assertEqual(self._backend.invoked, ["Delete"])
        finally:
            aa.reset_registry()


# ----------------------------------------------------------------------
# Coordinator: refusal rewrite + stop detection + runs
# ----------------------------------------------------------------------


def _success_out(result):
    return {"status": "success", "result": result}


class TestCoordinatorShapes(unittest.TestCase):
    def test_refusal_shapes_rewrite(self):
        cases = [
            ("Context menu refused (trigger-failed): the context "
             "menu for 'report.txt' did not open — Right-click "
             "the target yourself, then name the menu item."),
            ("Context menu path Delete refused: 'Delete' is "
             "disabled — the action refuses."),
            ("Context menu refused (menu-unexposed): that "
             "target's context menu isn't exposed."),
        ]
        for sentence in cases:
            out = tc._rewrite_s7_refusal("context_menu_action",
                                         _success_out(sentence))
            self.assertEqual(out["status"], "refused", sentence)
            self.assertEqual(out["kind"], "context_menu_refused")
            self.assertEqual(out["message"], sentence)

    def test_success_lines_pass_through(self):
        lines = [
            "Context menu items:\n- Delete (enabled)",
            "Context menu path Open invoked (window handle h1) "
            "— invoked-without-verification",
            "Context menu path Open With opened (window handle h1)",
            "Context menu for 'report.txt' opened (4 items) "
            "— verified",
        ]
        for line in lines:
            out = tc._rewrite_s7_refusal("context_menu_action",
                                         _success_out(line))
            self.assertEqual(out, _success_out(line), line)

    def test_stop_shape_detected(self):
        sentence = ("Context menu path Delete refused: 'Delete' "
                    "is an effectful menu action — confirm it "
                    "first (C-14 approve-first) before it is "
                    "invoked. Confirm the action, then invoke "
                    "the path again.")
        stop = tc._detect_s7_confirmation_stop(
            "context_menu_action", _success_out(sentence))
        self.assertIsNotNone(stop)
        self.assertEqual(stop[1], "confirmed")

    def test_genuine_refusal_is_not_a_stop(self):
        sentence = ("Context menu path Delete refused: 'Delete' "
                    "is disabled — the action refuses.")
        stop = tc._detect_s7_confirmation_stop(
            "context_menu_action", _success_out(sentence))
        self.assertIsNone(stop)

    def test_non_s7_tool_untouched(self):
        sentence = "Context menu refused (trigger-failed): boom"
        out = tc._rewrite_s7_refusal("some_other_tool",
                                     _success_out(sentence))
        self.assertEqual(out, _success_out(sentence))


def _step(tool, args, **kw):
    mapping = {"tool": tool, "args": args}
    mapping.update(kw)
    return tc.StepDef.from_mapping(mapping)


def _fake_executor(handler):
    def execute(tool, args):
        execute.calls.append((tool, dict(args)))
        return handler(tool, args)
    execute.calls = []
    return execute


class TestCoordinatorRuns(unittest.TestCase):
    def test_refused_context_step_fails_not_succeeds(self):
        sentence = ("Context menu refused (trigger-failed): the "
                    "context menu did not open.")
        executor = _fake_executor(
            lambda tool, args: _success_out(sentence))
        coord = tc.TurnCoordinator(executor=executor)
        task = coord.new_task(
            "ctx goal", "routine",
            [_step("context_menu_action",
                   {"action": "trigger", "target": "report.txt"})])
        receipt = coord.run_task(task)
        self.assertEqual(task.outcomes[0].status, "refused")
        self.assertEqual(task.outcomes[0].kind, "context_menu_refused")

    def test_stop_pauses_then_approval_resolves(self):
        stop_sentence = ("Context menu path Delete refused: "
                         "'Delete' is an effectful menu action — "
                         "confirm it first (C-14 approve-first) "
                         "before it is invoked.")

        def handler(tool, args):
            if args.get("confirmed"):
                return _success_out(
                    "Context menu path Delete invoked — verified")
            return _success_out(stop_sentence)

        executor = _fake_executor(handler)
        coord = tc.TurnCoordinator(executor=executor)
        task = coord.new_task(
            "ctx goal", "routine",
            [_step("context_menu_action",
                   {"action": "invoke", "target": "report.txt",
                    "path": "Delete"})])
        receipt = coord.run_task(task)
        pending = coord.pending_approval(task.id)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["confirm_arg"], "confirmed")
        receipt2 = coord.resolve_pending(task.id, approved=True)
        self.assertIsNone(coord.pending_approval(task.id))
        self.assertEqual(task.outcomes[0].status, "success")


if __name__ == "__main__":
    unittest.main()
