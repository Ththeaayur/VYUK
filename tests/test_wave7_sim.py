"""Wave 7 Build 3 tests — SIM plan-effect prediction.

Run from the tree root with PYTHONPATH=. (as the other suites
are run by tests/run_tests.py), or via that runner.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import sim_predict as sim_mod
from core import sim_tools as sim_tools


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class TestSimPrediction(unittest.TestCase):
    def test_hand_computed_case(self):
        seed = {"a.txt": "hello", "b.txt": "x", "keep.txt": "same"}
        ops = [
            {"kind": "write", "path": "a.txt", "content": "hello world"},
            {"kind": "delete", "path": "b.txt"},
            {"kind": "write", "path": "c.txt", "content": "new"},
        ]
        result = sim_mod.predict_plan(seed, ops)
        self.assertTrue(result["ok"])
        pred = result["prediction"]
        self.assertEqual(pred["kind"], "PREDICTION")
        self.assertFalse(pred["executed"])
        self.assertTrue(pred["executes_nothing"])
        self.assertIn("PREDICTION", pred["label"])
        self.assertIn("assumptions", pred)
        self.assertTrue(pred["assumptions"])
        self.assertIn("uncertainty", pred)
        self.assertTrue(pred["uncertainty"])
        eff = pred["effects"]
        self.assertEqual(
            eff["modified"],
            [{"path": "a.txt", "old_bytes": 5, "new_bytes": 11,
              "old_sha256": _sha("hello"),
              "new_sha256": _sha("hello world")}])
        self.assertEqual(
            eff["deleted"],
            [{"path": "b.txt", "old_bytes": 1, "old_sha256": _sha("x")}])
        self.assertEqual(
            eff["created"],
            [{"path": "c.txt", "new_bytes": 3, "new_sha256": _sha("new")}])
        self.assertEqual(eff["unchanged"], [{"path": "keep.txt", "bytes": 4}])
        self.assertEqual(pred["summary"],
                         {"created": 1, "deleted": 1, "modified": 1,
                          "unchanged": 1})

    def test_sentinels_unchanged_and_nothing_outside_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "outside.txt"
            sentinel.write_text("do not touch")
            before = sentinel.read_text()
            seed = {"a.txt": "one"}
            ops = [{"kind": "write", "path": "a.txt", "content": "two"},
                   {"kind": "write", "path": "b.txt", "content": "three"}]
            result = sim_mod.predict_plan(seed, ops,
                                          sentinels=[str(sentinel)])
            self.assertTrue(result["ok"])
            self.assertTrue(result["prediction"]["sentinel_unchanged"])
            self.assertEqual(sentinel.read_text(), before)
            # Nothing was created beside the sentinel.
            self.assertEqual(sorted(os.listdir(tmp)), ["outside.txt"])

    def test_refused_kinds_fail_whole_no_partial_prediction(self):
        seed = {"a.txt": "one"}
        for bad in (
            [{"kind": "execute", "path": "a.txt"}],
            [{"kind": "send", "path": "a.txt"}],
            [{"kind": "purchase", "path": "a.txt"}],
            [{"kind": "move", "path": "a.txt"}],
            [{"kind": "write", "path": "../escape.txt", "content": "x"}],
            [{"kind": "write", "path": "/abs.txt", "content": "x"}],
            [{"kind": "write", "path": "core/evil.py", "content": "x"}],
            [{"kind": "write", "path": ".env", "content": "x"}],
            [{"kind": "write", "path": "key.pem", "content": "x"}],
            [{"kind": "delete", "path": "missing.txt"}],
            [],
            [{"kind": "write", "path": "a.txt"}],  # no content
        ):
            result = sim_mod.predict_plan(seed, bad)
            self.assertFalse(result["ok"], bad)
            self.assertIsNone(result["prediction"])
            self.assertFalse(result["executed"])

    def test_no_executor_anywhere(self):
        # Check code, not prose: the docstring legitimately says
        # "no executor argument", so assert on AST identifiers
        # and the signature instead of a raw string search.
        import ast
        import inspect
        tree = ast.parse(Path(sim_mod.__file__).read_text())
        identifiers = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                identifiers.add(node.id)
            if isinstance(node, ast.Attribute):
                identifiers.add(node.attr)
            if isinstance(node, ast.arg):
                identifiers.add(node.arg)
        self.assertNotIn("executor", identifiers)
        self.assertNotIn("subprocess", identifiers)
        self.assertNotIn("request_promotion", identifiers)
        sig = inspect.signature(sim_mod.predict_plan)
        self.assertNotIn("executor", sig.parameters)

    def test_prediction_is_not_execution(self):
        # A prediction over a seed never changes the caller's data.
        seed = {"a.txt": "one"}
        ops = [{"kind": "delete", "path": "a.txt"}]
        result = sim_mod.predict_plan(seed, ops)
        self.assertTrue(result["ok"])
        self.assertEqual(seed, {"a.txt": "one"})
        self.assertEqual(result["prediction"]["effects"]["deleted"][0]["path"],
                         "a.txt")


class TestSimTools(unittest.TestCase):
    def test_predict_plan_tool_is_pure_json(self):
        out = sim_tools.sim_predict_plan_handler(
            json.dumps({"a.txt": "hi"}),
            json.dumps([{"kind": "write", "path": "a.txt",
                         "content": "hi there"}]))
        data = json.loads(out)
        self.assertTrue(data["ok"])
        self.assertEqual(data["prediction"]["summary"]["modified"], 1)

    def test_report_tool_writes_prediction_not_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = str(Path(tmp) / "prediction.json")
            out = sim_tools.sim_predict_report_handler(
                json.dumps({"a.txt": "hi"}),
                json.dumps([{"kind": "delete", "path": "a.txt"}]),
                out_path)
            data = json.loads(out)
            self.assertTrue(data["ok"])
            self.assertEqual(data["report_path"], out_path)
            written = json.loads(Path(out_path).read_text())
            self.assertEqual(written["kind"], "PREDICTION")
            self.assertFalse(written["executed"])
            # The deleted file was never created in the output dir.
            self.assertEqual(sorted(os.listdir(tmp)), ["prediction.json"])

    def test_report_tool_refuses_missing_folder(self):
        out = sim_tools.sim_predict_report_handler(
            json.dumps({"a.txt": "hi"}),
            json.dumps([{"kind": "write", "path": "a.txt",
                         "content": "x"}]),
            "/nonexistent_folder_xyz/prediction.json")
        data = json.loads(out)
        self.assertFalse(data["ok"])

    def test_report_tool_refuses_non_json_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = sim_tools.sim_predict_report_handler(
                json.dumps({"a.txt": "hi"}),
                json.dumps([{"kind": "write", "path": "a.txt",
                             "content": "x"}]),
                str(Path(tmp) / "prediction.txt"))
            self.assertFalse(json.loads(out)["ok"])
            self.assertEqual(os.listdir(tmp), [])


class TestSimWiring(unittest.TestCase):
    def test_tools_registered_and_classified(self):
        from core.funnel import tools_gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
        from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        decls = tools.get_declarations()
        names = [d["name"] for d in decls]
        self.assertIn("sim_predict_plan", names)
        self.assertIn("sim_predict_report", names)
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Library sweep: INTENTIONAL — 4 tools added (641 -> 645); pin was stale since the sweep, proven identical (645 != 641) on the pristine backup tree; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; stale-pin sweep: INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply; wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(decls), 757)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan
        self.assertIn("sim_predict_plan", tools_gate._READ_ONLY_TOOLS)
        self.assertIn("sim_predict_report",
                      tools_gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            tools_gate._WRITE_DESTINATION_ARGS["sim_predict_report"],
            ("output_path",))

    def test_report_refused_without_confirm_handler(self):
        from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "prediction.json")
            result = tools.execute(
                "sim_predict_report",
                {"seed_json": json.dumps({"a.txt": "hi"}),
                 "operations_json": json.dumps(
                     [{"kind": "write", "path": "a.txt", "content": "x"}]),
                 "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_predict_plan_succeeds_through_funnel_without_confirm(self):
        from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        result = tools.execute(
            "sim_predict_plan",
            {"seed_json": json.dumps({"a.txt": "hi"}),
             "operations_json": json.dumps(
                 [{"kind": "write", "path": "a.txt", "content": "hi!"}])})
        self.assertEqual(result["status"], "success", result)
        data = json.loads(result["result"])
        self.assertTrue(data["ok"])
        self.assertEqual(data["prediction"]["summary"]["modified"], 1)


if __name__ == "__main__":
    unittest.main()
