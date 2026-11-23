"""§7 consumers batch 1 — funnel tools for the §7 engines.

Menus (item 3), file dialogs (item 4), system surfaces (item 7),
and app adapters (item 9) landed as engines with no caller. This
suite proves the four funnel tools that call them:

- menu_action, file_dialog_action, system_surface_action,
  adapter_action: registration, registry count, per-tool resolve
  paths, every refusal class the engines can produce, and the
  two-layer confirm protocol (funnel gate refusal, then engine
  stop, then resolve with the confirmed flag).

Technique (no product seam): the engines' default-backend
factories are patched to return fakes copied from the engine
suites' own patterns, and item-2 snapshots are built through
the real perception adapter over a fake perception backend.
Adapter tests use the real registry with the real landed
adapters (reset per test, exactly like the adapter suites).

Confirm protocol, stated once so no test below looks like a
mistake: funnel approval alone does NOT satisfy an engine's
confirmed flag (the run_command precedent — funnel popup plus
trust-layer confirm are independent layers). So a gated path
proves THREE steps through the funnel: unapproved call refuses
at the gate; approved call runs the handler but the engine
stops with needs_confirmation and ZERO invokes; the same call
carrying confirmed=True (the GUI confirm path's re-issue)
resolves. The gate branches deliberately do not treat a
caller-supplied confirmed=True as approval — a model-set flag
must never wave itself through the popup.
"""

import os
import pathlib
import re
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.desktop import app_adapters as aa  # INTENTIONAL (Phase 3 S7a-2): moved to core/desktop/
from core.desktop import chrome_adapter as ca  # INTENTIONAL (Phase 3 S7b): moved to core/desktop/
from core import confirm_gate
from core.desktop import explorer_adapter as ea  # INTENTIONAL (Phase 3 S7b): moved to core/desktop/
from core.desktop import file_dialogs as fd  # INTENTIONAL (Phase 3 S7a): file_dialogs moved to core/desktop/
from core import injection
from core.desktop import menu_paths as mp  # INTENTIONAL (Phase 3 S7a): menu_paths moved to core/desktop/
from core.desktop import perception_adapter as pa  # INTENTIONAL (Phase 3 S7a): perception_adapter moved to core/desktop/
from core.desktop import system_surfaces as ss  # INTENTIONAL (Phase 3 S7a): system_surfaces moved to core/desktop/
from core.desktop import system_surfaces_shell as sh  # INTENTIONAL (Phase 3 S7a): system_surfaces_shell moved to core/desktop/
from core.desktop import target_handles as th  # INTENTIONAL (Phase 3 S7a): target_handles moved to core/desktop/
from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.desktop import vscode_adapter as va  # INTENTIONAL (Phase 3 S7b): moved to core/desktop/
from core.config import config as app_config

TOOLS = tools

FG = {"hwnd": 111, "pid": 42, "title": "Notes", "proc": "notes.exe"}
BOUNDS = (0, 0, 800, 600)

FG_CHROME = {"hwnd": 101, "pid": 4242, "title": "Example — Google Chrome",
              "proc": "chrome.exe", "window_class": "Chrome_WidgetWin_1"}
FG_EXPLORER = {"hwnd": 101, "pid": 4242, "title": "Documents",
               "proc": "explorer.exe", "window_class": "CabinetWClass"}
FG_VSCODE = {"hwnd": 301, "pid": 5151,
             "title": "notes.txt - workspace - Visual Studio Code",
             "proc": "Code.exe", "window_class": "Chrome_WidgetWin_1"}
FG_UNKNOWN = {"hwnd": 999, "pid": 1, "title": "Mystery App",
              "proc": "mystery.exe", "window_class": "MysteryClass"}


# ----------------------------------------------------------------------
# Fakes (copied from the engine suites' own patterns)
# ----------------------------------------------------------------------

def _mnode(name, aid, children=None, enabled=True, checked=None,
           rect=(0, 0, 100, 20)):
    return {"name": name, "control_type": "MenuItem",
            "automation_id": aid, "enabled": enabled,
            "checked": checked, "children": list(children or []),
            "rect": rect}


def _mtree():
    return [
        _mnode("File", "file", [
            _mnode("New", "new"),
            _mnode("Open", "open"),
            _mnode("Save", "save"),
            _mnode("Save As...", "saveas"),
            _mnode("Delete", "delete"),
            _mnode("Exit", "exit"),
        ]),
        _mnode("Edit", "edit", [
            _mnode("Undo", "undo"),
            _mnode("Redo", "redo", enabled=False),
        ]),
        _mnode("Tools", "tools", [_mnode("Options", "options")],
               enabled=False),
        _mnode("View", "view", [
            _mnode("Zoom", "zoom1"),
            _mnode("Zoom", "zoom2"),
        ]),
        _mnode("Help", "help", [_mnode("About", "about")]),
    ]


class FakePerceptionBackend:
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
    def __init__(self, tree=None, fg=None):
        self.tree = tree if tree is not None else _mtree()
        self.fg = dict(fg) if fg is not None else dict(FG)
        self.stamp = 1
        self.tree_calls = 0
        self.expanded = []
        self.invoked = []
        self.rebuild_on_expand = None
        self.invoke_result = (True, False, "")

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
        return (list(node.get("children") or []), self.stamp, "")

    def invoke(self, node):
        self.invoked.append(node.get("name"))
        return self.invoke_result


class FakeDialogBackend:
    def __init__(self, kind="open", folder="/docs", items=None,
                 exposed=True, password=False, fg=None,
                 close_after_reads=None, on_read=None, folders=None):
        self.kind = kind
        self.current_folder = folder
        self.items = list(items) if items is not None else []
        self.exposed = exposed
        self.password = password
        self.fg = dict(fg) if fg is not None else dict(FG)
        self.close_after_reads = close_after_reads
        self.on_read = on_read
        self.folders = folders
        self.reads = 0
        self.closed = False
        self.filename_set = None
        self.filter_set = None
        self.invoke_calls = []
        self.invoke_result = (True, True, None, "")
        self.app_reported = None

    def foreground_identity(self):
        return dict(self.fg)

    def bounds(self):
        return BOUNDS

    def dialog_state(self):
        self.reads += 1
        if self.on_read is not None:
            self.on_read(self.reads)
        if self.closed or (self.close_after_reads is not None
                           and self.reads > self.close_after_reads):
            return None
        return {"kind": self.kind, "title": "Open",
                "current_folder": self.current_folder,
                "items": list(self.items),
                "filename_field": True, "exposed": self.exposed,
                "has_password_field": self.password,
                "file_type_filter": self.filter_set, "note": ""}

    def set_folder(self, folder):
        if self.folders is not None and folder not in self.folders:
            return False, "folder does not exist"
        self.current_folder = folder
        return True, ""

    def set_filename(self, name):
        self.filename_set = name
        return True, ""

    def set_filter(self, file_type):
        self.filter_set = file_type
        return True, ""

    def invoke(self, button):
        self.invoke_calls.append(button)
        invoked, closed_flag, reported, note = self.invoke_result
        if closed_flag:
            self.closed = True
        if reported is None:
            reported = self.app_reported
        return invoked, closed_flag, reported, note


def _snode(key, name, **over):
    node = {"key": key, "name": name, "control_type": "MenuItem",
            "automation_id": None, "enabled": True, "checked": None,
            "has_children": False}
    node.update(over)
    return node


class FakeSurfaceBackend:
    def __init__(self):
        self.calls = []
        self.tray = [
            {"icon_id": "t1", "app": "ChatApp", "title": "Chat",
             "has_menu": True},
            {"icon_id": "t2", "app": "ChatApp", "title": "Chat Alerts",
             "has_menu": True},
            {"icon_id": "t3", "app": "Mail", "title": "Mail",
             "has_menu": False},
        ]
        self.menus = {
            "t1": ([_snode("m1", "Open"),
                    _snode("m2", "Settings", has_children=True),
                    _snode("m3", "Delete")], "stamp-t1"),
            "t2": ([_snode("m9", "Open")], "stamp-t2"),
        }
        self.expansions = {
            "m2": ([_snode("m4", "General"),
                    _snode("m5", "Advanced", enabled=False)],
                   "stamp-m2"),
        }
        self.taskbar = [
            {"button_id": "b1", "app": "Browser", "title": "Browser",
             "pinned": True, "running": True},
            {"button_id": "b2", "app": "Editor", "title": "Editor",
             "pinned": True, "running": False},
            {"button_id": "b3", "app": "Notes", "title": "Notes",
             "pinned": False, "running": True},
        ]
        self.start = [
            {"result_id": "r1", "name": "Calculator", "kind": "app"},
            {"result_id": "r2", "name": "Calendar", "kind": "app"},
            {"result_id": "r3", "name": "Settings", "kind": "page"},
            {"result_id": "r4", "name": "Settings", "kind": "app"},
        ]
        self.notification_rows = [
            {"notification_id": "n1", "app": "Mail", "title": "New mail",
             "body": "secret body text", "expired": False,
             "dismissed": False, "actions": ["open", "dismiss"]},
            {"notification_id": "n2", "app": "Clock", "title": "Old alert",
             "body": "", "expired": True, "dismissed": False,
             "actions": ["open"]},
            {"notification_id": "n3", "app": "Chat", "title": "Gone",
             "body": "", "expired": False, "dismissed": True,
             "actions": ["open"]},
        ]
        self.settings = [
            {"page_id": "p1", "name": "Display"},
            {"page_id": "p2", "name": "Sound"},
            {"page_id": "p3", "name": "Network"},
            {"page_id": "p4", "name": "Network"},
        ]

    def tray_icons(self):
        return list(self.tray), ""

    def tray_menu(self, icon_id):
        roots, stamp = self.menus.get(icon_id, ([], None))
        return list(roots), stamp, ""

    def expand_tray(self, node):
        children, stamp = self.expansions.get(node.get("key"),
                                              ([], None))
        return list(children), stamp, ""

    def invoke_tray(self, node):
        self.calls.append(("invoke_tray", node.get("key")))
        return True, True, ""

    def taskbar_buttons(self):
        return list(self.taskbar), ""

    def activate_taskbar(self, button_id):
        self.calls.append(("activate", button_id))
        return True, False, ""

    def launch_taskbar(self, button_id):
        self.calls.append(("launch_tb", button_id))
        return True, True, ""

    def pin_taskbar(self, button_id):
        self.calls.append(("pin", button_id))
        return True, False, ""

    def start_search(self, query):
        query = query.lower()
        found = [entry for entry in self.start
                 if query in entry["name"].lower()
                 or entry["result_id"] == query]
        return found, ""

    def launch_start(self, target):
        self.calls.append(("launch_start", target))
        return True, False, ""

    def notifications(self):
        return list(self.notification_rows), ""

    def act_notification(self, notification_id, action):
        self.calls.append(("act", notification_id, action))
        return True, True, ""

    def settings_pages(self):
        return list(self.settings), ""

    def open_settings(self, page_id):
        self.calls.append(("open_settings", page_id))
        return True, False, ""


def _snapshot(fg=None):
    return pa.observe(backend=FakePerceptionBackend(fg))


def _register_all_adapters():
    aa.register(ca.chrome_adapter_record())
    aa.register(ea.explorer_adapter_record())
    aa.register(va.vscode_adapter_record())


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------

class TestRegistration(unittest.TestCase):
    def test_batch1_tools_registered_and_count_is_772(self):  # INTENTIONAL — Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        names = [d["name"] for d in TOOLS.get_declarations()]
        # §7 consumers batch 2: INTENTIONAL — 3 tools added
        # (649 -> 652): drag_drop_action, monitor_action,
        # journal_action. §7 consumers batch 8: INTENTIONAL —
        # 1 tool added (652 -> 653): context_menu_action. Stale-pin sweep: INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (count proof: registry 655, no duplicates); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(names), 772, "registry must be 758 + 11")  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        for name in ("menu_action", "file_dialog_action",
                     "system_surface_action", "adapter_action"):
            self.assertIn(name, names)
        self.assertEqual(len(names), len(set(names)), "no duplicates")

    def test_tool_schemas_carry_their_required_args(self):
        by_name = {d["name"]: d for d in TOOLS.get_declarations()}
        self.assertEqual(by_name["menu_action"]["parameters"]["required"],
                         ["action"])
        self.assertEqual(
            by_name["file_dialog_action"]["parameters"]["required"],
            ["action"])
        self.assertEqual(
            by_name["system_surface_action"]["parameters"]["required"],
            ["surface", "action"])
        self.assertEqual(by_name["adapter_action"]["parameters"]["required"],
                         ["phrase"])


class TestAdapterStartupRegistration(unittest.TestCase):
    """The funnel's adapter tool is only end-to-end callable if
    the three landed adapters are registered in production — the
    registry is empty until something registers, and nothing did
    until this batch. This proves the wiring two ways: the calls
    are present in tools_registry.py source, and a FRESH
    interpreter that imports the registry already resolves the
    three adapters (no test setup, no reset)."""

    def test_registry_source_registers_all_three_adapters(self):
        source = (ROOT / "core" / "funnel" / "tools_registry.py").read_text(
            encoding="utf-8")  # Phase 3 F3 INTENTIONAL: live path check moved where bound
        for call in ("register_chrome_adapter()",
                     "register_explorer_adapter()",
                     "register_vscode_adapter()"):
            self.assertIn(call, source)

    def test_fresh_interpreter_registry_has_all_three_adapters(self):
        import subprocess
        probe = (
            "from core.funnel import tools_registry as tools_mod\n"
            "from core.desktop import app_adapters as aa\n"
            "tools_mod.tools  # import registers tools + adapters\n"
            "print(','.join(aa.list_adapters()))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(ROOT), env={"PYTHONPATH": str(ROOT),
                                "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        ids = proc.stdout.strip().split("\n")[-1].split(",")
        self.assertEqual(ids, ["chrome", "explorer", "vscode"])


# ----------------------------------------------------------------------
# menu_action (direct handler calls, fake menu backend)
# ----------------------------------------------------------------------

class MenuToolTests(unittest.TestCase):
    def setUp(self):
        th.reset_store()
        self._orig = mp.default_backend
        self.backend = FakeMenuBackend()
        mp.default_backend = lambda: self.backend

    def tearDown(self):
        mp.default_backend = self._orig
        th.reset_store()

    def menu(self, **kwargs):
        kwargs.setdefault("snapshot", _snapshot())
        # GAPS-23 (F-V7): through the funnel — the gate asks first
        # for invokes (approved here); list is gate-exempt, and the
        # handler's own stops still pin for unconfirmed invokes.
        confirm_gate.register_handler(lambda n, s: True)
        try:
            return TOOLS.execute("menu_action", kwargs)["result"]
        finally:
            confirm_gate.clear_handler()

    def test_list_renders_roots_states_and_handle(self):
        result = self.menu(action="list")
        self.assertIn("File (enabled, submenu)", result)
        self.assertIn("Tools (disabled, submenu)", result)
        self.assertIn("Edit (enabled, submenu)", result)
        self.assertIn("window handle th-window-", result)

    def test_list_unexposed_refuses_exactly(self):
        self.backend.menu_tree = lambda: ([], 1, "no menu bar exposed")
        result = self.menu(action="list")
        self.assertIn("refused", result)
        self.assertIn("isn't exposed", result)

    def test_invoke_leaf_verified(self):
        self.backend.invoke_result = (True, True, "")
        result = self.menu(action="invoke", path="File → Save")
        self.assertIn("Menu path File → Save invoked", result)
        self.assertIn("verified", result)
        self.assertEqual(self.backend.invoked, ["Save"])

    def test_invoke_disabled_refuses_zero_invokes(self):
        result = self.menu(action="invoke", path="Edit → Redo")
        self.assertIn("disabled", result)
        self.assertEqual(self.backend.invoked, [])

    def test_invoke_ambiguous_refuses_zero_invokes(self):
        result = self.menu(action="invoke", path="View → Zoom")
        self.assertIn("more than one item is named", result)
        self.assertIn("never picked blindly", result)
        self.assertEqual(self.backend.invoked, [])

    def test_invoke_not_found_names_siblings(self):
        result = self.menu(action="invoke", path="File → Nope")
        self.assertIn("is not in this menu level", result)
        self.assertIn("'Save'", result)
        self.assertEqual(self.backend.invoked, [])

    def test_invoke_rebuilt_refuses(self):
        self.backend.rebuild_on_expand = _mtree()
        result = self.menu(action="invoke", path="File → Save")
        self.assertIn("rebuilt", result)
        self.assertEqual(self.backend.invoked, [])

    def test_invoke_not_a_submenu_refuses(self):
        result = self.menu(action="invoke", path="File → Save → Deep")
        self.assertIn("submenu", result)
        self.assertEqual(self.backend.invoked, [])

    def test_stale_snapshot_refuses_before_walking(self):
        stale_backend = FakeMenuBackend(
            fg={"hwnd": 999, "pid": 42, "title": "Notes"})
        mp.default_backend = lambda: stale_backend
        result = self.menu(action="invoke", path="File → Save")
        self.assertIn("stale", result)
        self.assertEqual(stale_backend.tree_calls, 0)

    def test_submenu_leaf_opens_not_invokes(self):
        result = self.menu(action="invoke", path="File")
        self.assertIn("opened", result)
        self.assertEqual(self.backend.invoked, [])

    def test_invoked_without_verification_says_so(self):
        self.backend.invoke_result = (True, False, "")
        result = self.menu(action="invoke", path="File → Save")
        self.assertIn("invoked-without-verification", result)

    def test_c14_leaf_stops_then_resolves(self):
        stopped = self.menu(action="invoke", path="File → Delete")
        self.assertIn("confirm it first", stopped)
        self.assertEqual(self.backend.invoked, [])
        resolved = self.menu(action="invoke", path="File → Delete",
                             confirmed=True)
        self.assertIn("invoked", resolved)
        self.assertEqual(self.backend.invoked, ["Delete"])

    def test_caller_declared_effectful_stops_then_resolves(self):
        stopped = self.menu(action="invoke", path="File → Save",
                            effectful=True)
        self.assertIn("confirm it first", stopped)
        self.assertEqual(self.backend.invoked, [])
        resolved = self.menu(action="invoke", path="File → Save",
                             confirmed=True, effectful=True)
        self.assertIn("invoked", resolved)

    def test_save_is_not_in_the_destructive_set(self):
        result = self.menu(action="invoke", path="File → Save")
        self.assertIn("invoked", result)
        self.assertEqual(self.backend.invoked, ["Save"])

    def test_non_dict_snapshot_refuses_plainly(self):
        # GAPS-23 (F-V7): through the funnel — a non-object snapshot
        # is refused by the schema gate first; its message names
        # the snapshot, which is what this pins.
        out = TOOLS.execute("menu_action",
                            {"action": "list", "snapshot": "stamp?"})
        result = out["result"] if out.get("status") == "success" \
            else out.get("message", "")
        self.assertIn("snapshot", result)

    def test_unknown_action_refuses(self):
        result = self.menu(action="teleport")
        self.assertIn("Unknown menu action", result)


# ----------------------------------------------------------------------
# file_dialog_action (direct handler calls, fake dialog backend)
# ----------------------------------------------------------------------

class DialogToolTests(unittest.TestCase):
    def setUp(self):
        th.reset_store()
        self._orig = fd.default_backend
        self.backend = FakeDialogBackend(
            kind="open", folder="/docs",
            items=["report.txt", "notes.txt"])
        fd.default_backend = lambda: self.backend

    def tearDown(self):
        fd.default_backend = self._orig
        th.reset_store()

    def dialog(self, **kwargs):
        kwargs.setdefault("snapshot", _snapshot())
        # GAPS-23 (F-V7): through the funnel — the gate asks first
        # (approved here); the handler's own stops still pin.
        confirm_gate.register_handler(lambda n, s: True)
        try:
            return TOOLS.execute("file_dialog_action", kwargs)["result"]
        finally:
            confirm_gate.clear_handler()

    def test_open_exact_match_verified(self):
        self.backend.app_reported = "/docs/report.txt"
        result = self.dialog(action="open", filename="report.txt")
        self.assertIn("File dialog open /docs/report.txt", result)
        self.assertEqual(self.backend.invoke_calls, ["open"])

    def test_kind_mismatch_both_directions_zero_invokes(self):
        result = self.dialog(action="save", filename="report.txt")
        self.assertIn("kind", result.lower())
        self.assertEqual(self.backend.invoke_calls, [])
        self.backend.kind = "save"
        result = self.dialog(action="open", filename="report.txt")
        self.assertIn("kind", result.lower())
        self.assertEqual(self.backend.invoke_calls, [])

    def test_zero_matches_names_path(self):
        result = self.dialog(action="open", filename="missing.txt")
        self.assertIn("missing.txt", result)
        self.assertEqual(self.backend.invoke_calls, [])

    def test_multiple_matches_list_candidates_never_first_pick(self):
        self.backend.items = ["a.txt", "A.txt"]
        result = self.dialog(action="open", filename="a.txt")
        self.assertIn("several", result.lower())
        self.assertEqual(self.backend.invoke_calls, [])

    def test_save_overwrite_stops_with_zero_invokes_then_resolves(self):
        self.backend.kind = "save"
        stopped = self.dialog(action="save", filename="report.txt")
        self.assertIn("confirmation", stopped)
        self.assertEqual(self.backend.invoke_calls, [])
        resolved = self.dialog(action="save", filename="report.txt",
                               confirm_overwrite=True)
        self.assertIn("File dialog save", resolved)
        self.assertEqual(self.backend.invoke_calls, ["save"])

    def test_save_new_file_needs_no_confirmation(self):
        self.backend.kind = "save"
        result = self.dialog(action="save", filename="draft.txt")
        self.assertIn("File dialog save", result)
        self.assertEqual(self.backend.invoke_calls, ["save"])

    def test_filter_unsupported_refuses_not_ignores(self):
        self.backend.set_filter = None
        result = self.dialog(action="open", filename="report.txt",
                             file_type="*.txt")
        self.assertIn("filter", result.lower())
        self.assertEqual(self.backend.invoke_calls, [])

    def test_password_prompt_is_a_wall_zero_invokes(self):
        self.backend.password = True
        result = self.dialog(action="open", filename="report.txt")
        self.assertIn("password", result.lower())
        self.assertEqual(self.backend.invoke_calls, [])

    def test_undriven_dialog_refuses_zero_invokes(self):
        self.backend.exposed = False
        result = self.dialog(action="open", filename="report.txt")
        self.assertIn("does not expose", result)
        self.assertEqual(self.backend.invoke_calls, [])

    def test_dialog_absent_refuses(self):
        self.backend.close_after_reads = 0
        result = self.dialog(action="open", filename="report.txt")
        self.assertIn("no dialog", result.lower())
        self.assertEqual(self.backend.invoke_calls, [])

    def test_dialog_still_open_refuses(self):
        self.backend.invoke_result = (True, False, None, "")
        result = self.dialog(action="open", filename="report.txt")
        self.assertIn("still open", result)

    def test_folder_retargeted_toctou_refuses_zero_invokes(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "real")
            other = os.path.join(tmp, "other")
            os.mkdir(real)
            os.mkdir(other)
            link = os.path.join(tmp, "link")
            os.symlink(real, link)

            def swap_on_third_read(reads):
                if reads == 3:
                    os.unlink(link)
                    os.symlink(other, link)

            backend = FakeDialogBackend(
                kind="open", folder=link, items=["note.txt"],
                folders={link}, on_read=swap_on_third_read)
            fd.default_backend = lambda: backend
            result = self.dialog(action="open", folder=link,
                                 filename="note.txt")
            self.assertIn("changed between consent and execution",
                            result)
            self.assertEqual(backend.invoke_calls, [])

    def test_folder_pick(self):
        self.backend.kind = "folder"
        result = self.dialog(action="folder")
        self.assertIn("File dialog folder /docs", result)
        self.assertEqual(self.backend.invoke_calls, ["select_folder"])


# ----------------------------------------------------------------------
# system_surface_action (direct handler calls, fake surface backend)
# ----------------------------------------------------------------------

class SurfaceToolTests(unittest.TestCase):
    def setUp(self):
        th.reset_store()
        self._orig = ss.default_backend
        self.backend = FakeSurfaceBackend()
        ss.default_backend = lambda: self.backend

    def tearDown(self):
        ss.default_backend = self._orig
        th.reset_store()

    def surface(self, **kwargs):
        # GAPS-23 (F-V7): through the funnel — the gate asks first
        # for effectful surface actions (approved here); list is
        # gate-exempt, and the handler's own stops still pin.
        confirm_gate.register_handler(lambda n, s: True)
        try:
            return TOOLS.execute("system_surface_action", kwargs)["result"]
        finally:
            confirm_gate.clear_handler()

    def test_tray_list_renders_icons(self):
        result = self.surface(surface="tray", action="list")
        self.assertIn("Chat", result)
        self.assertIn("Mail", result)

    def test_tray_ambiguous_lists_and_asks(self):
        result = self.surface(surface="tray", action="open_menu",
                              target="ChatApp")
        self.assertIn("several", result.lower())
        self.assertIn("Chat", result)

    def test_menu_less_icon_refuses_with_manual_path(self):
        result = self.surface(surface="tray", action="open_menu",
                              target="Mail")
        self.assertIn("menu", result.lower())
        self.assertIn("manually", result)

    def test_tray_invoke_leaf_verified(self):
        result = self.surface(surface="tray", action="invoke_path",
                              target="t1", path="Open")
        self.assertIn("tray", result)
        self.assertIn(("invoke_tray", "m1"), self.backend.calls)

    def test_tray_disabled_leaf_refuses_zero_calls(self):
        result = self.surface(surface="tray", action="invoke_path",
                              target="t1", path="Settings → Advanced")
        self.assertIn("disabled", result)
        self.assertNotIn(("invoke_tray", "m5"), self.backend.calls)

    def test_tray_destructive_leaf_stops_then_resolves(self):
        stopped = self.surface(surface="tray", action="invoke_path",
                               target="t1", path="Delete")
        self.assertIn("confirmation", stopped)
        self.assertEqual(self.backend.calls, [])
        resolved = self.surface(surface="tray", action="invoke_path",
                                target="t1", path="Delete",
                                confirmed=True)
        self.assertIn("tray", resolved)
        self.assertIn(("invoke_tray", "m3"), self.backend.calls)

    def test_two_call_proof_same_target_succeeds_cross_target_refuses(self):
        opened = self.surface(surface="tray", action="open_menu",
                              target="t1")
        match = re.search(r"th-[a-z]+-\d+", opened)
        self.assertIsNotNone(match, opened)
        handle = match.group(0)
        same = self.surface(surface="tray", action="invoke_path",
                            target="t1", path="Open",
                            target_handle_id=handle)
        self.assertIn(("invoke_tray", "m1"), self.backend.calls)
        cross = self.surface(surface="tray", action="invoke_path",
                             target="t2", path="Open",
                             target_handle_id=handle)
        self.assertIn("refused", cross)

    def test_taskbar_list_and_switch_running_only(self):
        listed = self.surface(surface="taskbar", action="list")
        self.assertIn("Browser", listed)
        switched = self.surface(surface="taskbar", action="switch",
                                target="Browser")
        self.assertIn("taskbar", switched)
        refused = self.surface(surface="taskbar", action="switch",
                               target="Editor")
        self.assertIn("refused", refused)
        self.assertNotIn(("activate", "b2"), self.backend.calls)

    def test_taskbar_launch_pinned_only(self):
        refused = self.surface(surface="taskbar", action="launch",
                               target="Notes")
        self.assertIn("refused", refused)
        self.assertNotIn(("launch_tb", "b3"), self.backend.calls)
        launched = self.surface(surface="taskbar", action="launch",
                                target="Browser")
        self.assertIn("taskbar", launched)
        self.assertIn(("launch_tb", "b1"), self.backend.calls)

    def test_taskbar_pin_stops_then_resolves(self):
        stopped = self.surface(surface="taskbar", action="pin",
                               target="Notes")
        self.assertIn("confirmation", stopped)
        self.assertEqual(self.backend.calls, [])
        resolved = self.surface(surface="taskbar", action="pin",
                                target="Notes", confirmed=True)
        self.assertIn("taskbar", resolved)
        self.assertIn(("pin", "b3"), self.backend.calls)

    def test_start_search_renders_and_launch_ambiguous_asks(self):
        found = self.surface(surface="start", action="search",
                             query="calc")
        self.assertIn("Calculator", found)
        ambiguous = self.surface(surface="start", action="launch",
                                 target="Settings")
        self.assertIn("several", ambiguous.lower())
        launched = self.surface(surface="start", action="launch",
                                target="Calculator")
        self.assertIn("start", launched)
        self.assertIn(("launch_start", "r1"), self.backend.calls)

    def test_notification_list_titles_only_by_default(self):
        listed = self.surface(surface="notification", action="list")
        self.assertIn("New mail", listed)
        self.assertIn("body withheld", listed)
        self.assertNotIn("secret body text", listed)

    def test_notification_list_body_only_when_approved(self):
        approved = self.surface(
            surface="notification", action="list", include_body=True,
            body_approved_ids=["n1"])
        self.assertIn("secret body text", approved)
        unapproved = self.surface(
            surface="notification", action="list", include_body=True,
            body_approved_ids=[])
        self.assertNotIn("secret body text", unapproved)

    def test_notification_act_needs_id_and_title(self):
        acted = self.surface(surface="notification", action="act",
                             notification_id="n1",
                             expected_title="New mail",
                             detail_action="open")
        self.assertIn("notification", acted)
        self.assertIn(("act", "n1", "open"), self.backend.calls)
        mismatch = self.surface(surface="notification", action="act",
                                notification_id="n1",
                                expected_title="Wrong title",
                                detail_action="open")
        self.assertIn("refused", mismatch)
        expired = self.surface(surface="notification", action="act",
                               notification_id="n2",
                               expected_title="Old alert",
                               detail_action="open")
        self.assertIn("refused", expired)
        dismissed = self.surface(surface="notification", action="act",
                                 notification_id="n3",
                                 expected_title="Gone",
                                 detail_action="open")
        self.assertIn("refused", dismissed)

    def test_notification_dismiss_stops_then_resolves(self):
        stopped = self.surface(surface="notification", action="dismiss",
                               notification_id="n1",
                               expected_title="New mail")
        self.assertIn("confirmation", stopped)
        resolved = self.surface(surface="notification", action="dismiss",
                                notification_id="n1",
                                expected_title="New mail",
                                confirmed=True)
        self.assertIn("notification", resolved)

    def test_notification_unexposed_action_refuses(self):
        result = self.surface(surface="notification", action="act",
                              notification_id="n1",
                              expected_title="New mail",
                              detail_action="snooze")
        self.assertIn("refused", result)

    def test_settings_list_and_open_and_ambiguous(self):
        listed = self.surface(surface="settings", action="list")
        self.assertIn("Display", listed)
        opened = self.surface(surface="settings", action="open",
                              target="Display")
        self.assertIn("settings", opened)
        ambiguous = self.surface(surface="settings", action="open",
                                 target="Network")
        self.assertIn("several", ambiguous.lower())

    def test_walls_refuse_and_unknown_surface_refuses(self):
        for name in ("lock screen", "UAC", "password field", "CAPTCHA",
                     "secure desktop", "privilege escalation"):
            result = self.surface(surface=name, action="list")
            self.assertIn("wall-refused", result, name)
        result = self.surface(surface="teleporter", action="list")
        self.assertIn("unknown-surface", result)


# ----------------------------------------------------------------------
# adapter_action (direct handler calls, real registry + real adapters)
# ----------------------------------------------------------------------

class AdapterToolTests(unittest.TestCase):
    def setUp(self):
        aa.reset_registry()
        _register_all_adapters()

    def tearDown(self):
        aa.reset_registry()

    def adapter(self, **kwargs):
        # GAPS-23 (F-V7): through the funnel — the gate asks first
        # (approved here); the handler's own "confirmation-required"
        # stop still pins for unconfirmed destructive phrases.
        confirm_gate.register_handler(lambda n, s: True)
        try:
            return TOOLS.execute("adapter_action", kwargs)["result"]
        finally:
            confirm_gate.clear_handler()

    def test_chrome_safe_phrase_resolves_with_receipt(self):
        result = self.adapter(phrase="new tab",
                              foreground=dict(FG_CHROME))
        self.assertIn("chrome", result)
        self.assertIn("version", result.lower())

    def test_unknown_phrase_refuses(self):
        result = self.adapter(phrase="search google for cats",
                              foreground=dict(FG_CHROME))
        self.assertIn("unknown-phrase", result)
        self.assertIn("search google for cats", result)

    def test_quirk_failure_names_the_quirk(self):
        aa.reset_registry()
        record = ca.chrome_adapter_record()
        record["quirks"]["flyout_windows"]["working"] = False
        self.assertTrue(aa.register(record)["ok"])
        result = self.adapter(phrase="new tab",
                              foreground=dict(FG_CHROME))
        self.assertIn("quirk-failed", result)
        self.assertIn("flyout_windows", result)

    def test_confirm_gated_phrase_stops_then_resolves(self):
        stopped = self.adapter(phrase="close tab",
                               foreground=dict(FG_CHROME))
        self.assertIn("confirmation-required", stopped)
        resolved = self.adapter(phrase="close tab",
                                foreground=dict(FG_CHROME),
                                confirmed=True)
        self.assertIn("chrome", resolved)

    def test_app_mismatch_refuses(self):
        result = self.adapter(phrase="new tab", app_id="chrome",
                              foreground=dict(FG_EXPLORER))
        self.assertIn("mismatch", result)
        self.assertIn("explorer", result)

    def test_app_pinned_to_unmatched_foreground_refuses(self):
        result = self.adapter(phrase="new tab", app_id="chrome",
                              foreground=dict(FG_UNKNOWN))
        self.assertIn("mismatch", result)

    def test_no_profile_generic_fallback_is_honest(self):
        result = self.adapter(phrase="new tab",
                              foreground=dict(FG_UNKNOWN))
        self.assertIn("generic", result.lower())
        self.assertIn("No profile", result)

    def test_two_match_generic_fallback_names_both(self):
        record = ca.chrome_adapter_record()
        record["app_id"] = "chrome_clone"
        self.assertTrue(aa.register(record)["ok"])
        result = self.adapter(phrase="new tab",
                              foreground=dict(FG_CHROME))
        self.assertIn("generic", result.lower())
        self.assertIn("chrome", result)
        self.assertIn("chrome_clone", result)

    def test_version_unpinned_honesty_in_receipt(self):
        unpinned = self.adapter(phrase="new tab",
                                foreground=dict(FG_CHROME))
        self.assertIn("version pin unverified", unpinned)
        wrong_version = self.adapter(
            phrase="new tab", foreground=dict(FG_CHROME),
            app_version="999.0-test-only")
        self.assertIn("generic", wrong_version.lower())

    def test_vscode_identifies_from_proc_only_foreground(self):
        observed_style = {"hwnd": 301, "pid": 5151,
                          "title": "notes.txt - Visual Studio Code",
                          "proc": "Code.exe"}
        result = self.adapter(phrase="open file",
                              foreground=observed_style)
        self.assertIn("vscode", result)

    def test_chrome_without_window_class_falls_back_generically(self):
        observed_style = {"hwnd": 101, "pid": 4242,
                          "title": "Example — Google Chrome",
                          "proc": "chrome.exe"}
        result = self.adapter(phrase="new tab",
                              foreground=observed_style)
        self.assertIn("generic", result.lower())

    def test_observed_foreground_without_a_class_is_honest(self):
        # No foreground arg: the tool observes fresh. This sandbox
        # has no foreground app, so the honest answer is generic
        # controls — never an invented window class.
        result = self.adapter(phrase="new tab")
        self.assertIn("generic", result.lower())

    def test_empty_phrase_refuses(self):
        result = self.adapter(phrase="   ",
                              foreground=dict(FG_CHROME))
        self.assertIn("phrase", result)


# ----------------------------------------------------------------------
# Funnel: gate + handler through tools.execute
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
        aa.reset_registry()
        _register_all_adapters()

    def tearDown(self):
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        aa.reset_registry()
        self._guard.__exit__()

    def call(self, name, args, approve=False):
        confirm_gate.clear_handler()
        if approve:
            confirm_gate.register_handler(lambda n, s: True)
        try:
            return tools.execute(name, dict(args))
        finally:
            confirm_gate.clear_handler()

    def test_menu_list_is_free_and_menu_invoke_is_gated(self):
        listed = self.call("menu_action", {"action": "list"})
        self.assertEqual(listed["status"], "success", listed)
        refused = self.call("menu_action",
                            {"action": "invoke",
                             "path": "File → Save"})
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["kind"], "not_confirmed")

    def test_menu_invoke_approved_reaches_engine_stop(self):
        # Approved at the funnel, stopped at the engine: the
        # sandbox has no menu backend, so the engine refuses
        # honestly instead of invoking anything.
        approved = self.call("menu_action",
                             {"action": "invoke", "path": "File → Save"},
                             approve=True)
        self.assertEqual(approved["status"], "success", approved)
        self.assertIn("refused", approved["result"])

    def test_file_dialog_is_gated_both_legs(self):
        refused = self.call("file_dialog_action",
                            {"action": "open", "filename": "x.txt"})
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["kind"], "not_confirmed")
        approved = self.call("file_dialog_action",
                             {"action": "open", "filename": "x.txt"},
                             approve=True)
        self.assertEqual(approved["status"], "success", approved)
        self.assertIn("refused", approved["result"])

    def test_surface_lists_and_start_search_are_free(self):
        for args in ({"surface": "tray", "action": "list"},
                     {"surface": "taskbar", "action": "list"},
                     {"surface": "notification", "action": "list"},
                     {"surface": "settings", "action": "list"},
                     {"surface": "start", "action": "search",
                      "query": "calc"}):
            result = self.call("system_surface_action", args)
            self.assertEqual(result["status"], "success", (args, result))

    def test_surface_pin_is_gated_and_wall_refuses_after_approval(self):
        refused = self.call("system_surface_action",
                            {"surface": "taskbar", "action": "pin",
                             "target": "Notes"})
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["kind"], "not_confirmed")
        wall = self.call("system_surface_action",
                         {"surface": "lock screen", "action": "list"},
                         approve=True)
        self.assertEqual(wall["status"], "success", wall)
        self.assertIn("wall-refused", wall["result"])

    def test_adapter_safe_phrase_is_free_gated_phrase_stops_then_resolves(self):
        free = self.call("adapter_action",
                         {"phrase": "new tab",
                          "foreground": dict(FG_CHROME)})
        self.assertEqual(free["status"], "success", free)
        self.assertIn("chrome", free["result"])
        refused = self.call("adapter_action",
                            {"phrase": "close tab",
                             "foreground": dict(FG_CHROME)})
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["kind"], "not_confirmed")
        stopped = self.call("adapter_action",
                            {"phrase": "close tab",
                             "foreground": dict(FG_CHROME)},
                            approve=True)
        self.assertEqual(stopped["status"], "success", stopped)
        self.assertIn("confirmation-required", stopped["result"])
        resolved = self.call("adapter_action",
                             {"phrase": "close tab",
                              "foreground": dict(FG_CHROME),
                              "confirmed": True},
                             approve=True)
        self.assertEqual(resolved["status"], "success", resolved)
        self.assertIn("chrome", resolved["result"])

    def test_adapter_gate_without_foreground_gates_fail_closed(self):
        # No explicit foreground: the gate cannot resolve without
        # observing, so it gates — the handler then observes and
        # resolves generically once approved.
        refused = self.call("adapter_action",
                            {"phrase": "new tab"})
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["kind"], "not_confirmed")
        approved = self.call("adapter_action",
                             {"phrase": "new tab"},
                             approve=True)
        self.assertEqual(approved["status"], "success", approved)
        self.assertIn("generic", approved["result"].lower())

    def test_schema_validation_rejects_bad_args_before_handler(self):
        bad = self.call("menu_action", {"action": 42}, approve=True)
        self.assertEqual(bad["status"], "refused", bad)
        self.assertEqual(bad["kind"], "validation")
