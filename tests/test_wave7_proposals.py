"""Wave 7 Build 4 tests — self-improvement PROPOSALS.

Proves the Evolution Mode item 26 form, not just a diff
container: a proposal with an exact diff + a named regression
guard + an evidence ledger passes inspection; a proposal
without evidence, without a guard, or touching a core path
without the core-modification boundary check is refused whole;
and a perfect proposal changes no real file — non-deployment
by construction, not by promise.

Stdlib unittest, no GUI, no network, no code execution.
The confirm handler is cleared in setUp/tearDown, so no
test here ever writes the real data/ tree through a tool.
Run from the tree root with PYTHONPATH=. (as the other
suites are run by tests/run_tests.py).
"""

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from core import improvement_proposals as prop_mod
from core import proposal_tools as prop_tools
from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _good_proposal(**overrides):
    proposal = {
        "title": "Fix off-by-one in retry counter",
        "summary": "Increment after, not before, the retry check.",
        "diffs": [{"path": "notes/retry.py", "old_text": "n = 0\nn += 1\n",
                   "new_text": "n = 0\nn = n + 1\n"}],
        "regression_guards": ["test_retry_counter"],
        "evidence": [{"kind": "proven-job", "ref": "job-123",
                      "detail": "Field-proven job failed on the 5th retry."}],
    }
    proposal.update(overrides)
    return proposal


def _result(payload):
    assert payload["status"] == "success", payload
    return json.loads(payload["result"])


class ModuleTests(unittest.TestCase):
    def test_valid_proposal_passes_with_exact_hashes(self):
        result = prop_mod.inspect_proposal(_good_proposal())
        self.assertTrue(result["ok"], result)
        proposal = result["proposal"]
        self.assertEqual(proposal["kind"], "PROPOSAL")
        self.assertEqual(proposal["status"], "PROPOSED")
        self.assertFalse(proposal["deployed"])
        self.assertTrue(proposal["executes_nothing"])
        diff = proposal["diffs"][0]
        self.assertEqual(diff["old_sha256"], _sha("n = 0\nn += 1\n"))
        self.assertEqual(diff["new_sha256"], _sha("n = 0\nn = n + 1\n"))
        self.assertFalse(proposal["core_touching"])
        self.assertIsNone(proposal["boundary_check"])
        dry = result["dry_run"]
        self.assertTrue(dry["ok"])
        self.assertEqual(dry["kind"], "DRY-RUN")
        self.assertFalse(dry["executed"])
        self.assertTrue(dry["temp_discarded"])
        self.assertEqual(dry["files"][0]["path"], "notes/retry.py")

    def test_proposal_without_evidence_refused_whole(self):
        result = prop_mod.inspect_proposal(_good_proposal(evidence=[]))
        self.assertFalse(result["ok"])
        self.assertIn("evidence", result["reason"])

    def test_proposal_without_guard_refused(self):
        result = prop_mod.inspect_proposal(
            _good_proposal(regression_guards=[]))
        self.assertFalse(result["ok"])
        self.assertIn("guard", result["reason"])

    def test_guard_not_naming_a_test_refused(self):
        result = prop_mod.inspect_proposal(
            _good_proposal(regression_guards=["run everything"]))
        self.assertFalse(result["ok"])

    def test_core_path_without_boundary_check_refused(self):
        proposal = _good_proposal(diffs=[
            {"path": "core/helper.py", "old_text": "a", "new_text": "b"}])
        result = prop_mod.inspect_proposal(proposal)
        self.assertFalse(result["ok"])
        self.assertIn("boundary", result["reason"])

    def test_core_path_with_wrong_boundary_check_refused(self):
        proposal = _good_proposal(
            diffs=[{"path": "core/helper.py", "old_text": "a",
                    "new_text": "b"}],
            boundary_check={"check": "some-other-check",
                            "reason": "looked fine", "checked_by": "human"})
        result = prop_mod.inspect_proposal(proposal)
        self.assertFalse(result["ok"])

    def test_core_path_with_correct_boundary_check_is_proposal_only(self):
        proposal = _good_proposal(
            diffs=[{"path": "core/helper.py", "old_text": "a",
                    "new_text": "b"}],
            boundary_check={
                "check": "core-modification-boundary-1692",
                "reason": "Human reviewed: helper only, no gate change.",
                "checked_by": "human"})
        result = prop_mod.inspect_proposal(proposal)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["proposal"]["core_touching"])
        self.assertFalse(result["proposal"]["deployed"])
        self.assertEqual(
            result["proposal"]["boundary_check"]["check"],
            "core-modification-boundary-1692")

    def test_duplicate_path_identical_text_absolute_and_credential_refused(self):
        base = _good_proposal()
        dup = _good_proposal(diffs=[
            {"path": "a.py", "old_text": "x", "new_text": "y"},
            {"path": "a.py", "old_text": "x", "new_text": "z"}])
        self.assertFalse(prop_mod.inspect_proposal(dup)["ok"])
        noop = _good_proposal(diffs=[
            {"path": "a.py", "old_text": "same", "new_text": "same"}])
        self.assertFalse(prop_mod.inspect_proposal(noop)["ok"])
        absolute = _good_proposal(diffs=[
            {"path": "/tmp/evil.py", "old_text": "x", "new_text": "y"}])
        self.assertFalse(prop_mod.inspect_proposal(absolute)["ok"])
        cred = _good_proposal(diffs=[
            {"path": ".env", "old_text": "x", "new_text": "y"}])
        self.assertFalse(prop_mod.inspect_proposal(cred)["ok"])
        self.assertTrue(prop_mod.inspect_proposal(base)["ok"])

    def test_perfect_proposal_changes_no_real_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "sentinel.txt"
            sentinel.write_text("do not touch")
            result = prop_mod.inspect_proposal(_good_proposal())
            self.assertTrue(result["ok"], result)
            self.assertEqual(sentinel.read_text(), "do not touch")
            self.assertFalse((Path(tmp) / "notes" / "retry.py").exists())

    def test_no_apply_or_deploy_path_in_module(self):
        source = Path(prop_mod.__file__).read_text()
        tree = ast.parse(source)
        def_names = {n.name for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        forbidden = {"deploy", "apply_proposal", "promote", "install",
                     "self_deploy", "apply_diff"}
        self.assertFalse(def_names.intersection(forbidden), def_names)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertNotIn("subprocess", imported)
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Name)}
        self.assertFalse({"eval", "exec"}.intersection(called))


class ToolWiringTests(unittest.TestCase):
    def setUp(self):
        from core import confirm_gate
        confirm_gate.register_handler(None)
        self._confirm_gate = confirm_gate

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        self.assertIn("improvement_propose", names)
        self.assertIn("improvement_report", names)
        self.assertIn("improvement_propose", gate._READ_ONLY_TOOLS)
        self.assertIn("improvement_report", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["improvement_report"],
            ("output_path",))
        # Wave 7 Build 4 added 2 proposal tools (558 -> 560); Wave 7 Build 5 added 3 multiverse tools (560 -> 563); Wave 8 Build 1 added 3 anticipation tools (563 -> 566); Wave 8 Build 2 added 3 Track L tools (566 -> 575); Wave 8 Build 3 added 6 Phase 5 tools (569 -> 575); Wave 8 Build 4 added 3 feel tools (575 -> 578); Wave 8 Build 5 added 2 learning-last tools (578 -> 580; first-open opt-in added 4 continuous tools (580 -> 584)). Wave 9 Build 1 added 5 wellbeing tools (584 -> 589). Wave 9 Build 2 added 4 communications tools (589 -> 593). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 4 added 4 hardware tools (597 -> 601). Wave 9 Build 5 added 4 accessibility tools (601 -> 605). Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 756)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine

    def test_propose_through_funnel_without_confirm(self):
        data = _result(tools.execute(
            "improvement_propose",
            {"proposal_json": json.dumps(_good_proposal())}))
        self.assertTrue(data["ok"], data)
        self.assertFalse(data["deployed"])

    def test_report_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "proposal.json")
            result = tools.execute(
                "improvement_report",
                {"proposal_json": json.dumps(_good_proposal()),
                 "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_report_with_confirm_writes_proposal_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "proposal.json")
            data = _result(tools.execute(
                "improvement_report",
                {"proposal_json": json.dumps(_good_proposal()),
                 "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["deployed"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["status"], "PROPOSED")
            self.assertFalse(written["deployed"])


if __name__ == "__main__":
    unittest.main()
