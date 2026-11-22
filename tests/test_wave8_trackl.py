"""Wave 8 Build 2 tests — Track L remainder: release checklist +
installer validation support, with signing and feed hosting
staying DEFERRED.

Proves the checklist, not a ship: a well-formed fixture passes;
a tampered manifest, a wrong version, a missing CC-1 report, or
an ungranted CC-1 failure blocks the verdict whole, with every
failure named; signing and feed hosting appear as DEFERRED in
every report, never as passed or built; validation refuses a
malformed artifact, a signed claim, and a feed claim. Nothing
here compiles an installer, signs anything, or hosts a feed —
proven by AST test — and no tool here writes a real file except
the confirm-gated report tool, which writes only its report.

Stdlib unittest, no GUI, no network, no code execution.
The confirm handler is cleared in setUp/tearDown, so no
test here ever writes the real data/ tree through a tool.
Run from the tree root with PYTHONPATH=. (as the other
suites are run by tests/run_tests.py).
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from core import release_checklist as checklist_mod
from core import release_checklist_tools as checklist_tools
from core.version import VERSION as LIVE_VERSION
from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

try:
    from _budget_isolation import restore_budget, snapshot_budget
except ImportError:  # run as a package module instead of top-level
    from tests._budget_isolation import (  # type: ignore
        restore_budget, snapshot_budget)


INSTALLER_FILES = list(checklist_mod.REQUIRED_INSTALLER_FILES)


def _good_evidence(**overrides):
    evidence = {
        "version": "5.90.0",
        "claimed_version": "5.90.0",
        "manifest_ok": True,
        "manifest_checked": 500,
        "data_bytes": 369701,
        "registry_total": 566,
        "catalogue_total": 566,
        "catalogue_more_skills": 0,
        "battery_ok": True,
        "battery_failures": 0,
        "cc1_report_present": True,
        "cc1_true_exit": 0,
        "cc1_failures": [],
        "cc1_grants": [],
        "installer_files_present": list(INSTALLER_FILES),
    }
    evidence.update(overrides)
    return evidence


def _good_artifact(**overrides):
    artifact = {
        "files": list(INSTALLER_FILES),
        "version": "5.90.0",
        "manifest_version": "5.90.0",
        "claims_signed": False,
        "claims_feed": False,
    }
    artifact.update(overrides)
    return artifact


def _result(payload):
    assert payload["status"] == "success", payload
    return json.loads(payload["result"])


class ChecklistTests(unittest.TestCase):
    def test_good_fixture_passes(self):
        result = checklist_mod.evaluate_checklist(_good_evidence())
        self.assertEqual(result["verdict"], "PASS", result)
        self.assertEqual(result["failures"], [])
        self.assertEqual(len(result["checks"]), 7)
        self.assertTrue(all(c["passed"] for c in result["checks"]))

    def test_tampered_manifest_blocks_whole(self):
        result = checklist_mod.evaluate_checklist(
            _good_evidence(manifest_ok=False, manifest_checked=0))
        self.assertEqual(result["verdict"], "BLOCKED", result)
        self.assertIn("manifest_signed_integrity", result["failures"])
        self.assertIn("manifest", result["summary"])

    def test_wrong_version_blocks_whole(self):
        result = checklist_mod.evaluate_checklist(
            _good_evidence(claimed_version="5.89.0"))
        self.assertEqual(result["verdict"], "BLOCKED", result)
        self.assertIn("version_single_source", result["failures"])

    def test_drifted_data_blocks_whole(self):
        result = checklist_mod.evaluate_checklist(
            _good_evidence(data_bytes=369702))
        self.assertEqual(result["verdict"], "BLOCKED", result)
        self.assertIn("data_baseline", result["failures"])

    def test_ungrouped_tools_block_whole(self):
        result = checklist_mod.evaluate_checklist(
            _good_evidence(catalogue_more_skills=3,
                           catalogue_total=563))
        self.assertEqual(result["verdict"], "BLOCKED", result)
        self.assertIn("registry_catalogue", result["failures"])

    def test_failed_battery_blocks_whole(self):
        result = checklist_mod.evaluate_checklist(
            _good_evidence(battery_ok=False, battery_failures=2))
        self.assertEqual(result["verdict"], "BLOCKED", result)
        self.assertIn("battery", result["failures"])

    def test_missing_cc1_report_blocks_whole(self):
        result = checklist_mod.evaluate_checklist(
            _good_evidence(cc1_report_present=False,
                           cc1_true_exit=None))
        self.assertEqual(result["verdict"], "BLOCKED", result)
        self.assertIn("cc1_report", result["failures"])

    def test_ungranted_cc1_failure_blocks_whole(self):
        result = checklist_mod.evaluate_checklist(
            _good_evidence(cc1_true_exit=1,
                           cc1_failures=["some_file.py"],
                           cc1_grants=[]))
        self.assertEqual(result["verdict"], "BLOCKED", result)
        self.assertIn("cc1_report", result["failures"])

    def test_granted_cc1_failure_passes_with_disclosure(self):
        result = checklist_mod.evaluate_checklist(
            _good_evidence(cc1_true_exit=1,
                           cc1_failures=["some_file.py"],
                           cc1_grants=["some_file.py"]))
        self.assertEqual(result["verdict"], "PASS", result)
        check = [c for c in result["checks"]
                 if c["check"] == "cc1_report"][0]
        self.assertIn("grants", check["detail"])

    def test_missing_evidence_field_fails_not_defaults(self):
        evidence = _good_evidence()
        del evidence["manifest_ok"]
        del evidence["data_bytes"]
        result = checklist_mod.evaluate_checklist(evidence)
        self.assertEqual(result["verdict"], "BLOCKED", result)
        self.assertIn("manifest_signed_integrity", result["failures"])
        self.assertIn("data_baseline", result["failures"])

    def test_missing_installer_file_blocks_whole(self):
        present = list(INSTALLER_FILES)
        present.remove("installer/vyuk.iss")
        result = checklist_mod.evaluate_checklist(
            _good_evidence(installer_files_present=present))
        self.assertEqual(result["verdict"], "BLOCKED", result)
        self.assertIn("installer_files", result["failures"])
        detail = [c for c in result["checks"]
                  if c["check"] == "installer_files"][0]["detail"]
        self.assertIn("installer/vyuk.iss", detail)

    def test_deferred_items_in_every_report(self):
        for evidence in (_good_evidence(),
                         _good_evidence(manifest_ok=False)):
            result = checklist_mod.evaluate_checklist(evidence)
            items = {d["item"]: d["status"]
                     for d in result["deferred"]}
            self.assertEqual(items["signing"], "DEFERRED")
            self.assertEqual(items["feed_hosting"], "DEFERRED")
            self.assertEqual(items["installer_compilation"], "DEFERRED")
            # A deferred item is never counted as a passed check.
            self.assertNotIn("signing",
                             [c["check"] for c in result["checks"]])
            self.assertNotIn("feed_hosting",
                             [c["check"] for c in result["checks"]])

    def test_collect_tree_evidence_on_fixture_tree(self):
        # A fixture tree, not the live tree: running any suite
        # drifts the live data/ size, so the live tree cannot
        # itself be the baseline fixture.
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "core").mkdir()
            (tree / "core" / "version.py").write_text(
                'VERSION = "5.90.0"\n')
            (tree / "data").mkdir()
            (tree / "data" / "a.json").write_text("x" * 100)
            (tree / "data" / "b.json").write_text("y" * 50)
            for rel in INSTALLER_FILES:
                path = tree / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"")
            (tree / "install_manifest.json").write_text("{}")
            evidence = checklist_mod.collect_tree_evidence(tree)
            self.assertEqual(evidence["version"], "5.90.0")
            self.assertEqual(evidence["data_bytes"], 150)
            self.assertEqual(evidence["installer_files_missing"], [])
            self.assertTrue(evidence["manifest_present"])
            # And the live tree's version reads the same way.
            # Gate note (v5.91): this assertion tracks
            # core/version.py dynamically — the hardcoded "5.90.0"
            # literal here broke at the v5.91 bump (live 5.91.0)
            # and would break at every future bump. The fixture
            # literals above stay frozen: they describe the
            # fixture tree, not the live tree.
            live = Path(__file__).resolve().parent.parent
            self.assertEqual(
                checklist_mod.collect_tree_evidence(live)["version"],
                LIVE_VERSION)

    def test_no_compile_sign_host_or_deploy_in_module(self):
        source = Path(checklist_mod.__file__).read_text()
        tree = ast.parse(source)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
            if isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        for forbidden in ("subprocess", "eval", "exec",
                          "sign", "deploy", "install", "publish",
                          "upload"):
            self.assertNotIn(forbidden, names,
                             f"module must not {forbidden} anything")
        # "compile" is allowed only as re.compile (the semver
        # regex) — never as an installer compile step.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "compile":
                value = node.func.value
                self.assertIsInstance(value, ast.Name)
                self.assertEqual(value.id, "re")
        # The deferred words appear in prose (docstrings/comments)
        # about what is deferred; the AST proves no such name is
        # used as an identifier, import, or attribute.


class InstallerValidationTests(unittest.TestCase):
    def test_good_artifact_validates(self):
        result = checklist_mod.validate_installer_artifact(
            _good_artifact())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["defects"], [])

    def test_missing_file_refused(self):
        files = list(INSTALLER_FILES)
        files.remove("installer/dpi_gate.py")
        result = checklist_mod.validate_installer_artifact(
            _good_artifact(files=files))
        self.assertFalse(result["ok"])
        self.assertTrue(any("dpi_gate" in d for d in result["defects"]))

    def test_version_mismatch_refused(self):
        result = checklist_mod.validate_installer_artifact(
            _good_artifact(manifest_version="5.89.0"))
        self.assertFalse(result["ok"])
        self.assertTrue(any("manifest version" in d
                            for d in result["defects"]))

    def test_signed_claim_refused(self):
        result = checklist_mod.validate_installer_artifact(
            _good_artifact(claims_signed=True))
        self.assertFalse(result["ok"])
        self.assertTrue(any("DEFERRED" in d for d in result["defects"]))

    def test_feed_claim_refused(self):
        result = checklist_mod.validate_installer_artifact(
            _good_artifact(claims_feed=True))
        self.assertFalse(result["ok"])
        self.assertTrue(any("DEFERRED" in d for d in result["defects"]))

    def test_validation_deferred_list_present(self):
        result = checklist_mod.validate_installer_artifact(
            _good_artifact())
        items = {d["item"]: d["status"] for d in result["deferred"]}
        self.assertEqual(items["signing"], "DEFERRED")
        self.assertEqual(items["feed_hosting"], "DEFERRED")


class ToolWiringTests(unittest.TestCase):
    def setUp(self):
        from core import confirm_gate
        confirm_gate.register_handler(None)
        self._confirm_gate = confirm_gate

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        self.assertIn("release_checklist", names)
        self.assertIn("installer_validate", names)
        self.assertIn("release_checklist_report", names)
        self.assertIn("release_checklist", gate._READ_ONLY_TOOLS)
        self.assertIn("installer_validate", gate._READ_ONLY_TOOLS)
        self.assertIn("release_checklist_report",
                     gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["release_checklist_report"],
            ("output_path",))
        # Wave 8 Build 2 added 3 Track L tools (566 -> 575); Wave 8 Build 3 added 6 Phase 5 tools (569 -> 575); Wave 8 Build 4 added 3 feel tools (575 -> 578); Wave 8 Build 5 added 2 learning-last tools (578 -> 580; first-open opt-in added 4 continuous tools (580 -> 584)). Wave 9 Build 1 added 5 wellbeing tools (584 -> 589). Wave 9 Build 2 added 4 communications tools (589 -> 593). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 4 added 4 hardware tools (597 -> 601). Wave 9 Build 5 added 4 accessibility tools (601 -> 605). Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 764)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169

    def test_checklist_and_validation_through_funnel(self):
        checked = _result(tools.execute("release_checklist", {
            "evidence_json": json.dumps(_good_evidence())}))
        self.assertEqual(checked["verdict"], "PASS", checked)
        blocked = _result(tools.execute("release_checklist", {
            "evidence_json": json.dumps(
                _good_evidence(manifest_ok=False))}))
        self.assertEqual(blocked["verdict"], "BLOCKED", blocked)
        validated = _result(tools.execute("installer_validate", {
            "artifact_json": json.dumps(_good_artifact())}))
        self.assertTrue(validated["ok"], validated)
        refused = _result(tools.execute("installer_validate", {
            "artifact_json": json.dumps(
                _good_artifact(claims_signed=True))}))
        self.assertFalse(refused["ok"], refused)

    def test_report_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "checklist.json")
            result = tools.execute("release_checklist_report", {
                "evidence_json": json.dumps(_good_evidence()),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_report_with_confirm_writes_report_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "checklist.json")
            data = _result(tools.execute("release_checklist_report", {
                "evidence_json": json.dumps(_good_evidence()),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertEqual(data["verdict"], "PASS")
            self.assertTrue(data["executes_nothing"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["verdict"], "PASS")
            self.assertEqual(
                {d["item"] for d in written["deferred"]},
                {"signing", "feed_hosting", "installer_compilation"})
            # Only the report file exists in the temp dir.
            self.assertEqual([p.name for p in Path(tmp).iterdir()],
                             ["checklist.json"])


if __name__ == "__main__":
    unittest.main()


# L3: budget-isolation guest rule (test-isolation precedent,
# shared helper tests/_budget_isolation.py) — this suite
# executes tools through the shared funnel, so it snapshots
# the budget window + clock + confirm handler, opens a clean
# window, and restores byte-for-byte, instead of inheriting
# the earlier suites' dispatches and tripping on ordering.
_BUDGET_SNAPSHOT = None


def setUpModule():
    global _BUDGET_SNAPSHOT
    _BUDGET_SNAPSHOT = snapshot_budget()


def tearDownModule():
    global _BUDGET_SNAPSHOT
    if _BUDGET_SNAPSHOT is not None:
        restore_budget(_BUDGET_SNAPSHOT)
        _BUDGET_SNAPSHOT = None
