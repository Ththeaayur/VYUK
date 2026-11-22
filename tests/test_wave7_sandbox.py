"""Wave 7 Build 1 tests — SANDBOX-light completion (filesystem form).

Proves the completion, not just the Wave 4 in-memory form:
a test action inside the real-filesystem sandbox changes
nothing outside it; symlink escapes, ``..`` escapes, and
absolute paths are refused; core paths are refused in
code; send/network/purchase/execute kinds are refused,
never executed; promotion is request-only and copies
nothing outside; and the production tools are wired
(sandbox_run confirm-gated, sandbox_list read-only).

Stdlib unittest, no GUI, no network, no code execution.
The confirm handler is cleared in setUp/tearDown, so no
test here ever writes the real data/ tree through a tool.
"""

import ast
import json
import os
import tempfile
import unittest
from pathlib import Path

from core import sandbox_filesystem as sandbox_mod
from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/


def _result(payload):
    assert payload["status"] == "success", payload
    return json.loads(payload["result"])


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "sandbox")
        os.makedirs(self.root)
        self.outside = os.path.join(self._tmp.name, "outside")
        os.makedirs(self.outside)
        self.sentinel = os.path.join(self.outside, "sentinel.txt")
        with open(self.sentinel, "w") as handle:
            handle.write("do not touch")
        from core import confirm_gate

        confirm_gate.register_handler(None)
        self._confirm_gate = confirm_gate

    def tearDown(self):
        self._confirm_gate.register_handler(None)
        self._tmp.cleanup()

    def _approve(self):
        self._confirm_gate.register_handler(lambda name, summary: True)


class ModuleTests(_Base):
    def test_write_inside_changes_nothing_outside(self):
        sb = sandbox_mod.FileSystemSandbox(self.root)
        report = sb.run_actions(
            [{"kind": "write", "path": "a.txt", "content": "inside"},
             {"kind": "write", "path": "sub/b.txt", "content": "nested"}],
            sentinels=[self.sentinel])
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["files_changed"], ["a.txt", "sub/b.txt"])
        self.assertTrue(report["sentinel_unchanged"])
        with open(self.sentinel) as handle:
            self.assertEqual(handle.read(), "do not touch")
        self.assertFalse(os.path.exists(os.path.join(self.outside, "a.txt")))
        with open(os.path.join(self.root, "a.txt")) as handle:
            self.assertEqual(handle.read(), "inside")

    def test_dotdot_and_absolute_paths_refused(self):
        sb = sandbox_mod.FileSystemSandbox(self.root)
        report = sb.run_actions([
            {"kind": "write", "path": "../evil.txt", "content": "x"},
            {"kind": "write", "path": "/tmp/evil.txt", "content": "x"},
            {"kind": "write", "path": "ok.txt", "content": "ok"},
        ])
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["refused"]), 2)
        self.assertEqual(report["files_changed"], ["ok.txt"])
        self.assertFalse(os.path.exists(
            os.path.join(self._tmp.name, "evil.txt")))

    def test_symlink_escape_refused_and_outside_unchanged(self):
        link = os.path.join(self.root, "link.txt")
        os.symlink(self.sentinel, link)
        sb = sandbox_mod.FileSystemSandbox(self.root)
        report = sb.run_actions(
            [{"kind": "write", "path": "link.txt", "content": "pwned"}],
            sentinels=[self.sentinel])
        self.assertFalse(report["ok"])
        self.assertTrue(any("escapes" in r["reason"]
                            for r in report["refused"]))
        self.assertTrue(report["sentinel_unchanged"])
        with open(self.sentinel) as handle:
            self.assertEqual(handle.read(), "do not touch")

    def test_symlink_dir_escape_refused(self):
        linkdir = os.path.join(self.root, "linkdir")
        os.symlink(self.outside, linkdir)
        sb = sandbox_mod.FileSystemSandbox(self.root)
        report = sb.run_actions(
            [{"kind": "write", "path": "linkdir/evil.txt", "content": "x"}])
        self.assertFalse(report["ok"])
        self.assertFalse(os.path.exists(
            os.path.join(self.outside, "evil.txt")))

    def test_core_root_refused_at_construction(self):
        tree = Path(sandbox_mod.__file__).resolve().parent.parent
        with self.assertRaises(ValueError):
            sandbox_mod.FileSystemSandbox(tree / "core")
        with self.assertRaises(ValueError):
            sandbox_mod.FileSystemSandbox(tree / "data")
        with self.assertRaises(ValueError):
            sandbox_mod.FileSystemSandbox(tree)

    def test_core_path_via_symlink_refused(self):
        tree = Path(sandbox_mod.__file__).resolve().parent.parent
        core_file = tree / "core" / "__init__.py"
        link = os.path.join(self.root, "core_link.py")
        os.symlink(str(core_file), link)
        sb = sandbox_mod.FileSystemSandbox(self.root)
        before = core_file.read_bytes()
        report = sb.run_actions(
            [{"kind": "write", "path": "core_link.py", "content": "pwned"}])
        self.assertFalse(report["ok"])
        self.assertEqual(core_file.read_bytes(), before)

    def test_refused_kinds_never_execute(self):
        sb = sandbox_mod.FileSystemSandbox(self.root)
        report = sb.run_actions([
            {"kind": "send", "path": "a.txt", "content": "x"},
            {"kind": "network", "path": "a.txt"},
            {"kind": "purchase", "path": "a.txt"},
            {"kind": "execute", "path": "a.txt"},
            {"kind": "run", "path": "a.txt"},
            {"kind": "bogus", "path": "a.txt"},
            {"kind": "read", "path": "missing.txt"},
            {"kind": "delete", "path": "missing.txt"},
            {"kind": "write", "path": "ok.txt"},
        ])
        self.assertFalse(report["ok"])
        # 5 refused kinds + bogus kind + missing read +
        # missing delete + content-less write = 9 refusals.
        self.assertEqual(len(report["refused"]), 9)
        self.assertEqual(report["files_changed"], [])
        self.assertFalse(os.path.exists(os.path.join(self.root, "ok.txt")))

    def test_promotion_is_request_only(self):
        sb = sandbox_mod.FileSystemSandbox(self.root)
        sb.write("a.txt", "inside")
        self.assertFalse(sb.request_promotion("dir/")["ok"])
        self.assertFalse(sb.request_promotion("*.txt")["ok"])
        result = sb.request_promotion("a.txt")
        self.assertTrue(result["ok"])
        self.assertIn("REQUESTED", result["note"])
        self.assertEqual(sb.promotions(), ["a.txt"])
        # Nothing was copied outside by the request.
        self.assertFalse(os.path.exists(os.path.join(self.outside, "a.txt")))

    def test_seed_and_delete_and_list(self):
        sb = sandbox_mod.FileSystemSandbox(
            self.root, seed={"seed.txt": "seeded"})
        self.assertEqual(sb.read("seed.txt")["content"], "seeded")
        self.assertTrue(sb.delete("seed.txt")["ok"])
        self.assertEqual(sb.list_files(), [])
        with self.assertRaises(ValueError):
            sandbox_mod.FileSystemSandbox(
                self.root, seed={"../evil.txt": "x"})

    def test_module_executes_nothing(self):
        source = Path(sandbox_mod.__file__).read_text()
        tree = ast.parse(source)
        banned = set()
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                banned.update(a.name for a in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                banned.add(node.module)
            if isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Name):
                called.add(node.func.id)
        self.assertFalse({"subprocess", "os"}.intersection(banned))
        # Builtin code-execution calls, as AST calls — not
        # substring matches (re.compile is a regex, not
        # code execution, and is allowed).
        self.assertFalse({"eval", "exec", "compile"}.intersection(called))


class ToolWiringTests(_Base):
    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        self.assertIn("sandbox_run", names)
        self.assertIn("sandbox_list", names)
        self.assertIn("sandbox_run", gate._CONFIRM_GATE_TOOLS)
        self.assertIn("sandbox_list", gate._READ_ONLY_TOOLS)
        self.assertNotIn("sandbox_list", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(gate._WRITE_DESTINATION_ARGS["sandbox_run"],
                         ("sandbox_dir",))
        # Wave 7 Build 2 added 3 undo tools (553 -> 556); Wave 7 Build 3 added 2 SIM tools (556 -> 558); Wave 7 Build 4 added 2 proposal tools (558 -> 560); Wave 7 Build 5 added 3 multiverse tools (560 -> 563); Wave 8 Build 1 added 3 anticipation tools (563 -> 566); Wave 8 Build 2 added 3 Track L tools (566 -> 575); Wave 8 Build 3 added 6 Phase 5 tools (569 -> 575); Wave 8 Build 4 added 3 feel tools (575 -> 578); Wave 8 Build 5 added 2 learning-last tools (578 -> 580; first-open opt-in added 4 continuous tools (580 -> 584)). Wave 9 Build 1 added 5 wellbeing tools (584 -> 589). Wave 9 Build 2 added 4 communications tools (589 -> 593). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 4 added 4 hardware tools (597 -> 601). Wave 9 Build 5 added 4 accessibility tools (601 -> 605). Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 769)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_run_refuses_without_confirm_handler_and_writes_nothing(self):
        result = tools.execute("sandbox_run", {
            "sandbox_dir": os.path.join(self._tmp.name, "new_sandbox"),
            "actions_json": json.dumps(
                [{"kind": "write", "path": "a.txt", "content": "x"}]),
        })
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
        self.assertEqual(result["status"], "refused", result)
        self.assertEqual(result["kind"], "not_confirmed", result)
        self.assertFalse(os.path.exists(
            os.path.join(self._tmp.name, "new_sandbox")))

    def test_run_and_list_through_the_tools(self):
        self._approve()
        report = _result(tools.execute("sandbox_run", {
            "sandbox_dir": self.root,
            "actions_json": json.dumps([
                {"kind": "write", "path": "a.txt", "content": "inside"},
                {"kind": "write", "path": "../evil.txt", "content": "x"},
                {"kind": "send", "path": "a.txt", "content": "x"},
            ]),
            "sentinels_json": json.dumps([self.sentinel]),
        }))
        self.assertFalse(report["ok"])
        self.assertEqual(report["files_changed"], ["a.txt"])
        self.assertTrue(report["sentinel_unchanged"])
        listing = _result(tools.execute(
            "sandbox_list", {"sandbox_dir": self.root}))
        self.assertEqual(listing["files"], ["a.txt"])

    def test_list_succeeds_without_handler_and_creates_nothing(self):
        missing = os.path.join(self._tmp.name, "not_created")
        listing = _result(tools.execute(
            "sandbox_list", {"sandbox_dir": missing}))
        self.assertFalse(listing["ok"])
        self.assertFalse(os.path.exists(missing))
        listing = _result(tools.execute(
            "sandbox_list", {"sandbox_dir": self.root}))
        self.assertTrue(listing["ok"])
        self.assertEqual(listing["files"], [])


if __name__ == "__main__":
    unittest.main()
