"""§7 consumers batch 2 — funnel tools for drag-drop (item 5),
monitors (item 6), and the action journal (item 8).

Batch 1 gave funnel tools to menus, file dialogs, system
surfaces, and app adapters. This suite proves the remaining
three: drag_drop_action executes through the item-5 engine with
its gate order intact (handle re-proof, walls, destructive
confirm stop with zero drags, auto-run refusal, per-monitor
drop-point conversion at execution time, OLE availability,
target-kind refusal, format negotiation, declared-fields-only
payloads, mechanism named in the receipt); monitor_action
resolves and converts against a fresh table on every call and
surfaces the engine's refusals exactly (never clamping onto
another monitor); journal_action reads, plans, reconciles, and
compensates through the item-8 engine with append-only history
and loud corruption.

Fakes follow the engine suites' patterns: a fake OLE backend
patched in as core.desktop.drag_drop.default_backend, fake monitor
tables patched in as core.desktop.monitors.read_table, and temp receipt
stores for the journal legs. Every test resets the handle
store, so it cannot poison later suites in the same process.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.desktop import drag_drop as dd  # INTENTIONAL (Phase 3 S7a): drag_drop moved to core/desktop/
from core.desktop import monitors as mon  # INTENTIONAL (Phase 3 S7a): monitors moved to core/desktop/
from core.desktop import target_handles as th  # INTENTIONAL (Phase 3 S7a): target_handles moved to core/desktop/
from core import tool_receipts as tr
from core.funnel.tools_registry import tools as TOOLS  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.funnel.tools_gate import _needs_confirmation  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel.tools_gate_readonly import _READ_ONLY_TOOLS  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/

FG = {"hwnd": 111, "pid": 42, "title": "Notes", "proc": "notes.exe"}


def _catalogue_groups():
    """GROUP_MEMBERS from the single catalogue source
    (core/catalogue_groups_data.py — pure data, no Qt)."""
    # GAPS-23 (F-V7): the AST re-parse is gone with the Qt
    # module that forced it — the tables live in core now.
    from core.catalogue_groups_data import GROUP_MEMBERS
    return {name: group
            for group, names in GROUP_MEMBERS.items()
            for name in names}

class FakeDragBackend:
    """Minimal drag protocol: knobs for every negotiation outcome."""

    ole_available = True

    def __init__(self, advertised=("CF_HDROP", "CF_UNICODETEXT",
                                   "UniformResourceLocator"),
                 profile=None, effect="copy", verification="file present",
                 gesture_proven=False, gesture_ok=True):
        self.advertised = list(advertised)
        self.profile = profile
        self.effect = effect
        self.verification = verification
        self._gesture_proven = gesture_proven
        self._gesture_ok = gesture_ok
        self.build_calls = 0
        self.do_calls = 0
        self.gesture_calls = 0
        self.last_point = None

    def advertised_formats(self, target):
        return list(self.advertised)

    def current_target_profile(self, target):
        return dict(self.profile) if self.profile else None

    def build_data_object(self, payload, format_name):
        self.build_calls += 1
        return {"payload": payload, "format": format_name}

    def do_drag_drop(self, data_object, source, target, point):
        self.do_calls += 1
        self.last_point = point
        return {"effect": self.effect}

    def verify_drop(self, target, payload):
        return self.verification

    def gesture_proven(self, target):
        return self._gesture_proven

    def gesture_drag(self, source, target, point):
        self.gesture_calls += 1
        return self._gesture_ok


def _d1(**over):
    entry = {
        "monitor_id": "\\\\.\\DISPLAY1",
        "device": "\\\\.\\DISPLAY1",
        "left": 0,
        "top": 0,
        "width": 1920,
        "height": 1080,
        "work_left": 0,
        "work_top": 0,
        "work_right": 1919,
        "work_bottom": 1079,
        "primary": True,
        "scale_x": 1.0,
        "scale_y": 1.0,
    }
    entry.update(over)
    return entry


def _d2(**over):
    entry = {
        "monitor_id": "\\\\.\\DISPLAY2",
        "device": "\\\\.\\DISPLAY2",
        "left": 1920,
        "top": 0,
        "width": 2560,
        "height": 1440,
        "work_left": 1920,
        "work_top": 0,
        "work_right": 4479,
        "work_bottom": 1439,
        "primary": False,
        "scale_x": 1.5,
        "scale_y": 1.5,
    }
    entry.update(over)
    return entry


def _table_a():
    return mon.canonical_sort([_d2(), _d1()])


class TestConsumers2Registration(unittest.TestCase):
    def test_three_tools_registered_and_count_is_756(self):  # INTENTIONAL — Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        names = [d["name"] for d in TOOLS.get_declarations()]
        # §7 consumers batch 2: INTENTIONAL — 3 tools added
        # (649 -> 652): drag_drop_action, monitor_action,
        # journal_action. §7 consumers batch 8: INTENTIONAL —
        # 1 tool added (652 -> 653): context_menu_action. Stale-pin sweep: INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (count proof: registry 655, no duplicates); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(names), 756, "registry must be 754 + 1")  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        for name in ("drag_drop_action", "monitor_action",
                     "journal_action"):
            self.assertIn(name, names)
        self.assertEqual(len(names), len(set(names)), "no duplicates")

    def test_new_tools_grouped_exactly_once(self):
        groups = _catalogue_groups()
        self.assertTrue(groups, "GROUP_MEMBERS not found in source")
        for name in ("drag_drop_action", "monitor_action",
                     "journal_action"):
            self.assertIn(name, groups, name)
            self.assertEqual(groups[name], "Hands & eyes", name)

    def test_confirm_classification(self):
        self.assertIn("drag_drop_action", _CONFIRM_GATE_TOOLS)
        self.assertIn("journal_action", _CONFIRM_GATE_TOOLS)
        self.assertNotIn("monitor_action", _CONFIRM_GATE_TOOLS)

    def test_read_only_classification(self):
        self.assertIn("monitor_action", _READ_ONLY_TOOLS)
        self.assertNotIn("drag_drop_action", _READ_ONLY_TOOLS)
        self.assertNotIn("journal_action", _READ_ONLY_TOOLS)

    def test_drag_gate_always_pops(self):
        self.assertTrue(_needs_confirmation("drag_drop_action", {}))
        self.assertTrue(_needs_confirmation(
            "drag_drop_action",
            {"source_handle_id": "th-1", "target_handle_id": "th-2",
             "payload": {"kind": "text", "text": "hi"}}))

    def test_journal_gate_frees_reads_only(self):
        self.assertFalse(_needs_confirmation(
            "journal_action", {"action": "list"}))
        self.assertFalse(_needs_confirmation(
            "journal_action", {"action": "plan"}))
        self.assertTrue(_needs_confirmation(
            "journal_action", {"action": "reconcile"}))
        self.assertTrue(_needs_confirmation(
            "journal_action", {"action": "compensate"}))
        # Unknown actions gate fail-closed.
        self.assertTrue(_needs_confirmation(
            "journal_action", {"action": "bogus"}))
        self.assertTrue(_needs_confirmation("journal_action", {}))

    def test_monitor_gate_never_pops(self):
        for action in ("list", "resolve", "convert", "place", "bogus"):
            self.assertFalse(_needs_confirmation(
                "monitor_action", {"action": action}))


class DragBase(unittest.TestCase):
    def setUp(self):
        th.reset_store()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.source_path = os.path.join(self._tmp.name, "note.txt")
        with open(self.source_path, "wb") as fh:
            fh.write(b"drag me")
        self.source = th.mint_file(self.source_path)
        self.target = th.mint_control(FG, name="Editor",
                                      control_type="edit")
        self.target_obs = {"identity": dict(FG)}
        self.snapshot = {"stamp": "snap-1"}

    def _drag(self, backend, payload=None, **kw):
        kw.setdefault("source_observation", {})
        kw.setdefault("target_observation", self.target_obs)
        kw.setdefault("snapshot", self.snapshot)
        kw.setdefault("target_kind", "editor")
        args = {"source_handle_id": self.source["id"],
                "target_handle_id": self.target["id"],
                "payload": payload if payload is not None else
                {"kind": "files", "files": [self.source_path]}}
        args.update(kw)
        # GAPS-23 (F-V7): the funnel speaks JSON — a tuple arg
        # (target_norm1000) is refused by the schema gate, so
        # normalise tuples to lists before the call.
        args = {k: (list(v) if isinstance(v, tuple) else v)
                for k, v in args.items()}
        # GAPS-23 (F-V7): through the funnel (gate approved). A
        # validation refusal has no "result" — return its message,
        # which carries the same wording the handler's check would
        # ("must be an object" for a non-object payload).
        from core import confirm_gate
        confirm_gate.register_handler(lambda n, s: True)
        try:
            with mock.patch.object(dd, "default_backend",
                                   return_value=backend):
                out = TOOLS.execute("drag_drop_action", args)
        finally:
            confirm_gate.clear_handler()
        if out.get("status") == "success":
            return out["result"]
        return out.get("message", "")


class TestDragDropAction(DragBase):
    def test_file_drop_ole_success_names_mechanism(self):
        backend = FakeDragBackend()
        text = self._drag(backend)
        self.assertIn("via ole", text)
        self.assertIn("effect: copy", text)
        self.assertIn("verification: file present", text)
        self.assertIn("1 file(s)", text)
        self.assertEqual(backend.do_calls, 1)

    def test_text_and_url_payloads(self):
        backend = FakeDragBackend()
        text = self._drag(backend, {"kind": "text", "text": "hello"})
        self.assertIn("text (5 chars)", text)
        url = self._drag(backend, {"kind": "url",
                                   "url": "https://example.test/"})
        self.assertIn("URL https://example.test/", url)

    def test_unknown_handle_refuses(self):
        backend = FakeDragBackend()
        # GAPS-23 (F-V7): through the funnel (gate approved).
        from core import confirm_gate
        confirm_gate.register_handler(lambda n, s: True)
        try:
            with mock.patch.object(dd, "default_backend",
                                   return_value=backend):
                out = TOOLS.execute(
                    "drag_drop_action",
                    {"source_handle_id": "th-nope",
                     "target_handle_id": self.target["id"],
                     "payload": {"kind": "files",
                                 "files": [self.source_path]},
                     "target_observation": self.target_obs,
                     "snapshot": self.snapshot,
                     "target_kind": "editor"})
        finally:
            confirm_gate.clear_handler()
        text = out["result"] if out.get("status") == "success" else out.get("message", "")
        self.assertIn("Drag refused", text)
        self.assertEqual(backend.do_calls, 0)

    def test_non_dict_payload_refuses(self):
        backend = FakeDragBackend()
        text = self._drag(backend, "not-a-dict")
        self.assertIn("must be an object", text)
        self.assertEqual(backend.do_calls, 0)

    def test_credential_field_refuses(self):
        backend = FakeDragBackend()
        text = self._drag(backend, {"kind": "text", "text": "hi",
                                    "api_key": "secret"})
        self.assertIn("Drag refused", text)
        self.assertNotIn("secret", text)
        self.assertEqual(backend.build_calls, 0)

    def test_unsupported_payload_kind_refuses(self):
        backend = FakeDragBackend()
        text = self._drag(backend, {"kind": "image", "path": "x.png"})
        self.assertIn("Drag refused", text)
        self.assertEqual(backend.do_calls, 0)

    def test_destructive_stop_then_confirmed_run(self):
        backend = FakeDragBackend()
        stopped = self._drag(backend, target_kind="trash")
        self.assertIn("destructive-needs-confirmation", stopped)
        self.assertEqual(backend.build_calls, 0)
        self.assertEqual(backend.do_calls, 0)
        confirmed = self._drag(backend, target_kind="trash",
                               confirm_destructive=True)
        self.assertIn("via ole", confirmed)
        self.assertEqual(backend.do_calls, 1)

    def test_wall_refused(self):
        backend = FakeDragBackend()
        text = self._drag(backend, target_kind="password-field")
        self.assertIn("Drag refused (by-design-wall)", text)
        self.assertEqual(backend.do_calls, 0)

    def test_auto_run_location_refused(self):
        startup = ("C:\\Users\\phoen\\AppData\\Roaming\\Microsoft\\"
                   "Windows\\Start Menu\\Programs\\Startup\\app.exe")
        folder = th.mint_file(startup)
        backend = FakeDragBackend()
        # GAPS-23 (F-V7): through the funnel (gate approved).
        from core import confirm_gate
        confirm_gate.register_handler(lambda n, s: True)
        try:
            with mock.patch.object(dd, "default_backend",
                                   return_value=backend):
                out = TOOLS.execute(
                    "drag_drop_action",
                    {"source_handle_id": self.source["id"],
                     "target_handle_id": folder["id"],
                     "payload": {"kind": "files",
                                 "files": [self.source_path]},
                     "target_observation": {},
                     "snapshot": self.snapshot,
                     "target_kind": "folder"})
        finally:
            confirm_gate.clear_handler()
        text = out["result"] if out.get("status") == "success" \
            else out.get("message", "")
        self.assertIn("Drag refused (auto-run-location)", text)
        self.assertEqual(backend.do_calls, 0)

    def test_no_ole_refuses_and_unproven_gesture_is_no_fallback(self):
        backend = FakeDragBackend()
        backend.ole_available = False
        refused = self._drag(backend)
        self.assertIn("Drag refused (target-no-ole-support)", refused)
        self.assertEqual(backend.do_calls, 0)
        refused2 = self._drag(backend, allow_gesture=True)
        self.assertIn("Drag refused (target-no-ole-support)", refused2)
        self.assertEqual(backend.gesture_calls, 0)

    def test_proven_gesture_receipt_names_gesture(self):
        backend = FakeDragBackend(gesture_proven=True)
        backend.ole_available = False
        text = self._drag(backend, allow_gesture=True)
        self.assertIn("via gesture", text)
        self.assertEqual(backend.do_calls, 0)
        self.assertEqual(backend.gesture_calls, 1)

    def test_target_kind_profile_contradiction_refuses(self):
        backend = FakeDragBackend(profile={"kind": "other"})
        text = self._drag(backend)
        self.assertIn("Drag refused", text)
        self.assertEqual(backend.do_calls, 0)

    def test_format_negotiation_refuses(self):
        backend = FakeDragBackend(advertised=("CF_UNICODETEXT",))
        text = self._drag(backend)
        self.assertIn("Drag refused", text)
        self.assertEqual(backend.do_calls, 0)

    def test_failed_drop_source_untouched(self):
        backend = FakeDragBackend(effect="none")
        text = self._drag(backend)
        self.assertIn("Drag refused (drop-failed)", text)
        with open(self.source_path, "rb") as fh:
            self.assertEqual(fh.read(), b"drag me")

    def test_monitor_conversion_at_execution_time(self):
        table = [_d1()]
        backend = FakeDragBackend()
        with mock.patch.object(mon, "read_table",
                               return_value=(table, "")):
            text = self._drag(
                backend, monitor_reference="\\\\.\\DISPLAY1",
                target_norm1000=(500, 500))
        self.assertIn("via ole", text)
        self.assertEqual(backend.last_point, (960, 540))

    def test_monitor_unreadable_scale_refuses(self):
        table = [_d2(scale_x=None, scale_y=None)]
        backend = FakeDragBackend()
        with mock.patch.object(mon, "read_table",
                               return_value=(table, "")):
            text = self._drag(
                backend, monitor_reference="\\\\.\\DISPLAY2",
                target_norm1000=(500, 500))
        self.assertIn("Drag refused (monitor-unresolved)", text)
        self.assertIn("unreadable scale", text)
        self.assertEqual(backend.do_calls, 0)

    def test_bad_monitor_point_refuses(self):
        backend = FakeDragBackend()
        text = self._drag(backend, monitor_reference="\\\\.\\DISPLAY1",
                          target_norm1000="500,500")
        self.assertIn("norm1000", text)
        self.assertEqual(backend.do_calls, 0)


class MonitorBase(unittest.TestCase):
    def _monitor(self, action, table, **kw):
        # GAPS-23 (F-V7): through the funnel (monitor_action is a
        # read, so no handler is needed).
        with mock.patch.object(mon, "read_table",
                               return_value=(table, "")):
            return TOOLS.execute(
                "monitor_action", {"action": action, **kw})["result"]


class TestMonitorAction(MonitorBase):
    def test_list_names_both_monitors(self):
        text = self._monitor("list", _table_a())
        self.assertIn("DISPLAY1", text)
        self.assertIn("DISPLAY2", text)
        self.assertIn("150%", text)

    def test_list_empty_table_honest(self):
        with mock.patch.object(mon, "read_table",
                               return_value=([], "no backend here")):
            text = TOOLS.execute(
                "monitor_action", {"action": "list"})["result"]
        self.assertIn("no backend here", text)

    def test_resolve_second_monitor(self):
        text = self._monitor("resolve", _table_a(),
                             reference="second monitor",
                             nx=500, ny=500)
        self.assertIn("DISPLAY2", text)
        self.assertIn("bounds", text)

    def test_resolve_reads_fresh_every_call(self):
        # First call sees only DISPLAY1; a later call sees the
        # second monitor appear. A consent-time table could never
        # produce the second answer.
        calls = {"n": 0}

        def _fresh(*args):
            calls["n"] += 1
            if calls["n"] == 1:
                return ([_d1()], "")
            return (_table_a(), "")

        with mock.patch.object(mon, "read_table", side_effect=_fresh):
            first = TOOLS.execute(
                "monitor_action",
                {"action": "resolve", "reference": "second monitor",
                 "nx": 500, "ny": 500})["result"]
            second = TOOLS.execute(
                "monitor_action",
                {"action": "resolve", "reference": "second monitor",
                 "nx": 500, "ny": 500})["result"]
        self.assertIn("refused", first)
        self.assertIn("DISPLAY2", second)

    def test_resolve_unknown_lists_monitors(self):
        text = self._monitor("resolve", _table_a(),
                             reference="monitor 9", nx=500, ny=500)
        self.assertIn("refused", text)
        self.assertIn("DISPLAY1", text)
        self.assertIn("DISPLAY2", text)

    def test_resolve_ambiguous_tie_refuses(self):
        stacked = mon.canonical_sort([
            _d1(),
            _d2(left=0, top=1080, width=1920, height=1080,
                work_left=0, work_top=1080, work_right=1919,
                work_bottom=2159, primary=False),
        ])
        text = self._monitor("resolve", stacked, reference="left",
                             nx=500, ny=500)
        self.assertIn("refused", text)
        self.assertIn("more than one", text)

    def test_resolve_bounds_violation_never_clamps(self):
        # (750, 500) on the primary maps to (3359, 720): on D2,
        # not on D1. The refusal names the true point.
        text = self._monitor("resolve", _table_a(),
                             reference="primary", nx=750, ny=500)
        self.assertIn("refused", text)
        self.assertIn("3359", text)
        self.assertIn("never moved onto another monitor", text)

    def test_convert_logical_to_physical(self):
        text = self._monitor("convert", _table_a(),
                             reference="\\\\.\\DISPLAY2",
                             direction="logical_to_physical",
                             x=100, y=100)
        self.assertIn("150", text)
        self.assertIn("DISPLAY2", text)

    def test_convert_physical_to_logical(self):
        text = self._monitor("convert", _table_a(),
                             reference="\\\\.\\DISPLAY2",
                             direction="physical_to_logical",
                             x=150, y=150)
        self.assertIn("100", text)
        self.assertIn("DISPLAY2", text)

    def test_convert_unreadable_scale_refuses(self):
        table = [_d2(scale_x=None, scale_y=None)]
        text = self._monitor("convert", table,
                             reference="\\\\.\\DISPLAY2",
                             direction="logical_to_physical",
                             x=100, y=100)
        self.assertIn("refused", text)
        self.assertIn("unreadable scale", text)

    def test_convert_unknown_reference_refuses(self):
        text = self._monitor("convert", _table_a(),
                             reference="monitor 9",
                             direction="logical_to_physical",
                             x=100, y=100)
        self.assertIn("refused", text)
        self.assertIn("DISPLAY1", text)

    def test_convert_bad_direction_refuses(self):
        text = self._monitor("convert", _table_a(),
                             reference="\\\\.\\DISPLAY1",
                             direction="sideways", x=1, y=2)
        self.assertIn("direction", text)

    def test_place_fits_within_target(self):
        # GAPS-23 (F-V7): rect as a list — the funnel speaks
        # JSON and a tuple is refused by the schema gate.
        text = self._monitor("place", _table_a(),
                             rect=[100, 100, 500, 400],
                             from_reference="\\\\.\\DISPLAY1",
                             to_reference="\\\\.\\DISPLAY2")
        self.assertIn("(2025, 96, 2559, 496)", text)
        self.assertIn("DISPLAY2", text)

    def test_place_unreadable_scale_refuses(self):
        blind = _d2(scale_x=None, scale_y=None)
        # Resolve by device name against a table that holds only
        # the blind entry for the target.
        text2 = self._monitor("place", [_d1(), blind],
                              rect=[100, 100, 500, 400],
                              from_reference="\\\\.\\DISPLAY1",
                              to_reference="\\\\.\\DISPLAY2")
        self.assertIn("refused", text2)
        self.assertIn("unreadable scale", text2)

    def test_config_changes_refuse(self):
        for action in ("set_resolution", "arrange", "set_primary",
                       "set_scale", "rotate"):
            text = TOOLS.execute(
                "monitor_action", {"action": action})["result"]
            self.assertIn("out of scope", text)

    def test_unknown_action_refuses(self):
        text = TOOLS.execute(
            "monitor_action", {"action": "bogus"})["result"]
        self.assertIn("Unknown monitor action", text)

    def test_live_table_degrades_honestly(self):
        # No patch: this lab has no Windows display APIs.
        text = TOOLS.execute(
            "monitor_action", {"action": "list"})["result"]
        self.assertIn("Could not read the monitor list", text)


class JournalBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store_path = os.path.join(self._tmp.name, "receipts.jsonl")
        self.store = tr.ReceiptStore(path=self.store_path)

    def _seed(self, tool="open_url", compensations=None,
              manual_steps=None, dispatched=False, outcome=None):
        attempt = self.store.record_attempt(
            tool=tool, args={"url": "https://example.test/"},
            compensations=compensations, manual_steps=manual_steps)
        receipt_id = attempt["receipt_id"]
        if dispatched:
            self.store.mark_dispatched(receipt_id)
        if outcome is not None:
            self.store.record_outcome(receipt_id, status=outcome)
        return receipt_id

    def _lines(self):
        with open(self.store_path, "rb") as fh:
            return fh.read().splitlines()

    def _journal_raw(self, action, **kw):
        from core import confirm_gate
        kw.setdefault("store_path", self.store_path)
        args = {"action": action}
        args.update(kw)
        # GAPS-23 (F-V7): through the funnel — reconcile/compensate
        # ask first (approved here); list/plan are gate-exempt.
        # The outer call goes through the UNBOUND class method:
        # three compensate tests stub the singleton's `execute`
        # attribute to observe the compensation executor's INNER
        # calls, and a plain TOOLS.execute here would be
        # intercepted by that stub. The inner calls still resolve
        # through the (stubbed) attribute, as before.
        confirm_gate.register_handler(lambda n, s: True)
        try:
            return type(TOOLS).execute(TOOLS, "journal_action", args)
        finally:
            confirm_gate.clear_handler()

    def _journal(self, action, **kw):
        return self._journal_raw(action, **kw)["result"]


class TestJournalAction(JournalBase):
    def test_list_newest_first_with_counts(self):
        first = self._seed()
        second = self._seed(tool="file_control")
        text = self._journal("list")
        self.assertIn("file_control", text)
        self.assertIn("open_url", text)
        self.assertLess(text.index("file_control"),
                        text.index("open_url"))
        self.assertIn("retryable 2", text)

    def test_list_limit_validation(self):
        self.assertIn("at least 1", self._journal("list", limit=0))
        # GAPS-23 (F-V7): a non-integer limit never reaches the
        # handler's numeric check — the funnel's schema gate
        # refuses it first (the handler's check is defence-in-depth
        # behind the gate). Pin the refusal the user actually gets.
        refused = self._journal_raw("list", limit="many")
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["kind"], "validation", refused)
        self.assertIn("must be an integer", refused["message"])

    def test_plan_counts_and_writes_nothing(self):
        self._seed()
        unknown = self._seed(dispatched=True)
        done = self._seed(dispatched=True, outcome="succeeded")
        before = self._lines()
        text = self._journal("plan")
        self.assertIn("Retryable", text)
        self.assertIn("Done", text)
        self.assertIn("Unknown", text)
        self.assertEqual(self._lines(), before)

    def test_plan_with_receipt_shows_compensation_steps(self):
        receipt = self._seed(compensations=[
            {"tool": "first_step", "args": {}},
            {"tool": "second_step", "args": {}}])
        text = self._journal("plan", receipt_id=receipt)
        self.assertIn("second_step", text)
        self.assertIn("first_step", text)
        self.assertLess(text.index("second_step"),
                        text.index("first_step"))

    def test_plan_unknown_receipt_refuses(self):
        text = self._journal("plan", receipt_id="no-such-receipt")
        self.assertIn("No journal entry", text)

    def test_reconcile_unconfirmed_writes_nothing(self):
        receipt = self._seed(dispatched=True)
        before = self._lines()
        text = self._journal("reconcile", receipt_id=receipt,
                             happened=False,
                             verification="effect absent")
        self.assertIn("needs_confirmation", text)
        self.assertEqual(self._lines(), before)

    def test_reconcile_absent_records_failed_append_only(self):
        receipt = self._seed(dispatched=True)
        before = self._lines()
        # A dispatched seed holds two lines already (attempt +
        # dispatched record); append-only means every pre-existing
        # line survives byte-identically and exactly one line
        # is added.
        text = self._journal("reconcile", receipt_id=receipt,
                             happened=False,
                             verification="effect absent",
                             confirmed=True)
        self.assertIn("now failed", text)
        after = self._lines()
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(after[:len(before)], before)

    def test_reconcile_happened_records_succeeded(self):
        receipt = self._seed(dispatched=True)
        text = self._journal("reconcile", receipt_id=receipt,
                             happened=True,
                             verification="effect present",
                             confirmed=True)
        self.assertIn("now succeeded", text)
        # Outcome-known entries never re-run: a second
        # reconcile refuses.
        again = self._journal("reconcile", receipt_id=receipt,
                              happened=True,
                              verification="effect present",
                              confirmed=True)
        self.assertIn("refused", again)

    def test_reconcile_unknown_id_refuses(self):
        text = self._journal("reconcile", receipt_id="no-such",
                             happened=True, verification="v",
                             confirmed=True)
        self.assertIn("refused", text)

    def test_reconcile_needs_happened_and_verification(self):
        receipt = self._seed(dispatched=True)
        no_happened = self._journal("reconcile", receipt_id=receipt,
                                    verification="v", confirmed=True)
        self.assertIn("happened true or false", no_happened)
        no_verification = self._journal(
            "reconcile", receipt_id=receipt, happened=True,
            confirmed=True)
        self.assertIn("refused", no_verification)

    def test_reconcile_needs_confirmer_name(self):
        receipt = self._seed(dispatched=True)
        text = self._journal("reconcile", receipt_id=receipt,
                             happened=True, verification="v",
                             confirmed=True, confirmed_by="  ")
        self.assertIn("confirmer name", text)

    def test_reconcile_done_entry_refuses(self):
        receipt = self._seed(dispatched=True, outcome="succeeded")
        text = self._journal("reconcile", receipt_id=receipt,
                             happened=False,
                             verification="effect absent",
                             confirmed=True)
        self.assertIn("refused", text)

    def test_compensate_unconfirmed_runs_nothing(self):
        receipt = self._seed(compensations=[
            {"tool": "get_current_time_date", "args": {}}])
        calls = []
        with mock.patch.object(TOOLS, "execute",
                               side_effect=lambda *a: calls.append(a)
                               or {"status": "success"}):
            text = self._journal("compensate", receipt_id=receipt,
                                 confirmed=False)
        self.assertIn("needs_confirmation", text)
        self.assertEqual(calls, [])

    def test_compensate_no_undo_never_calls_executor(self):
        receipt = self._seed(manual_steps=["close it by hand"])
        calls = []
        with mock.patch.object(TOOLS, "execute",
                               side_effect=lambda *a: calls.append(a)
                               or {"status": "success"}):
            text = self._journal("compensate", receipt_id=receipt,
                                 confirmed=True)
        self.assertIn("No undo available", text)
        self.assertIn("close it by hand", text)
        self.assertEqual(calls, [])

    def test_compensate_declared_steps_complete_via_funnel(self):
        receipt = self._seed(compensations=[
            {"tool": "get_current_time_date", "args": {}}])
        text = self._journal("compensate", receipt_id=receipt,
                             confirmed=True)
        self.assertIn("Compensation complete", text)
        self.assertIn("1 step(s) verified", text)

    def test_compensate_reverse_order_first_failure_stops(self):
        receipt = self._seed(compensations=[
            {"tool": "first_declared", "args": {}},
            {"tool": "second_declared", "args": {}}])
        calls = []

        def _fake_execute(name, args):
            calls.append(name)
            if name == "second_declared":
                return {"status": "error", "kind": "failure"}
            return {"status": "success"}

        with mock.patch.object(TOOLS, "execute",
                               side_effect=_fake_execute):
            text = self._journal("compensate", receipt_id=receipt,
                                 confirmed=True)
        # Reverse order: second_declared runs first and fails,
        # so first_declared never runs.
        self.assertEqual(calls, ["second_declared"])
        self.assertIn("stopped", text)

    def test_compensate_unknown_receipt_refuses(self):
        text = self._journal("compensate", receipt_id="no-such",
                             confirmed=True)
        self.assertIn("No journal entry", text)

    def test_corrupt_line_fails_loudly_without_repair(self):
        self._seed()
        with open(self.store_path, "ab") as fh:
            fh.write(b"{this is not json}\n")
        before = self._lines()
        text = self._journal("list")
        self.assertIn("unreadable", text.lower())
        self.assertIn("repair", text.lower())
        self.assertEqual(self._lines(), before)

    def test_corrupt_line_reconcile_fails_loudly(self):
        self._seed(dispatched=True)
        with open(self.store_path, "ab") as fh:
            fh.write(b"garbage\n")
        text = self._journal("reconcile", receipt_id="x",
                             happened=True, verification="v",
                             confirmed=True)
        self.assertIn("unreadable", text.lower())


if __name__ == "__main__":
    unittest.main()
