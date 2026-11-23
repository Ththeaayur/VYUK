"""Wave 7 Build 5 tests — P18 multiverse planner + compiler.

Proves the proposal and dry-run surface, not a deployment
surface: a multiverse of Build 4 proposals, each with a
readable routine, plans whole or refuses whole; a
caller-chosen branch compiles to a routine + dry-run
report; an uncompilable branch falls back, never executes;
a core/credential branch is refused unless it carries the
1692 boundary check, and even then it stays a proposal;
and a perfect plan + compile changes no real file.

Stdlib unittest, no GUI, no network, no code execution.
The confirm handler is cleared in setUp/tearDown, so no
test here ever writes the real data/ tree through a tool.
Run from the tree root with PYTHONPATH=. (as the other
suites are run by tests/run_tests.py).
"""

import ast
import hashlib
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from core import multiverse_planner as mv_mod
from core import multiverse_tools as mv_tools
from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/


def _proposal(path, old, new, **overrides):
    proposal = {
        "title": f"Change {path}",
        "summary": f"Replace old text in {path}.",
        "diffs": [{"path": path, "old_text": old, "new_text": new}],
        "regression_guards": ["test_notes_change"],
        "evidence": [{"kind": "proven-job", "ref": "job-7",
                      "detail": "Field-proven job changed this file before."}],
    }
    proposal.update(overrides)
    return proposal


def _branch(name, path="notes/a.txt", old="old a\n", new="new a\n",
            **overrides):
    branch = {"name": name, "proposal": _proposal(path, old, new)}
    branch.update(overrides)
    return branch


def _good_multiverse():
    result = mv_mod.plan_multiverse(
        "Tidy the notes",
        [_branch("short", new="new a short\n"),
         _branch("long", path="notes/b.txt", old="old b\n",
                 new="new b long\n")])
    assert result["ok"], result
    return result["multiverse"]


def _result(payload):
    assert payload["status"] == "success", payload
    return json.loads(payload["result"])


class ModuleTests(unittest.TestCase):
    def test_two_branches_plan_with_routines_and_no_deployment(self):
        result = mv_mod.plan_multiverse(
            "Tidy the notes",
            [_branch("short", new="new a short\n"),
             _branch("long", path="notes/b.txt", old="old b\n",
                     new="new b long\n")],
            constraints=["copy-only"])
        self.assertTrue(result["ok"], result)
        multi = result["multiverse"]
        self.assertEqual(multi["kind"], "MULTIVERSE")
        self.assertEqual(len(multi["branches"]), 2)
        self.assertFalse(multi["deployed"])
        self.assertTrue(multi["executes_nothing"])
        for branch in multi["branches"]:
            self.assertFalse(branch["deployed"])
            self.assertTrue(branch["executes_nothing"])
            routine = branch["routine"]
            self.assertEqual(routine["scope"], "copy-only")
            self.assertTrue(routine["steps"])
            self.assertIn("notes", routine["inputs"]["diff_paths"][0])
            self.assertFalse(routine["c14_required"])

    def test_branch_without_evidence_refuses_whole_multiverse(self):
        bad = _branch("bad", proposal=_proposal(
            "notes/a.txt", "old a\n", "new a\n", evidence=[]))
        result = mv_mod.plan_multiverse(
            "Tidy", [bad, _branch("good")])
        self.assertFalse(result["ok"])
        self.assertNotIn("multiverse", result)

    def test_branch_without_guard_refuses_whole_multiverse(self):
        bad = _branch("bad", proposal=_proposal(
            "notes/a.txt", "old a\n", "new a\n", regression_guards=[]))
        result = mv_mod.plan_multiverse("Tidy", [bad, _branch("good")])
        self.assertFalse(result["ok"])

    def test_single_branch_and_duplicate_names_refused(self):
        self.assertFalse(mv_mod.plan_multiverse(
            "Tidy", [_branch("only")])["ok"])
        self.assertFalse(mv_mod.plan_multiverse(
            "Tidy", [_branch("same"), _branch("same")])["ok"])

    def test_core_branch_without_boundary_refused_with_boundary_proposal_only(self):
        core_no = _branch("core-no", proposal=_proposal(
            "core/helper.py", "a", "b"))
        self.assertFalse(mv_mod.plan_multiverse(
            "Tidy", [core_no, _branch("good")])["ok"])
        core_yes = _branch("core-yes", proposal=_proposal(
            "core/helper.py", "a", "b",
            boundary_check={
                "check": "core-modification-boundary-1692",
                "reason": "Human reviewed: helper only.",
                "checked_by": "human"}))
        result = mv_mod.plan_multiverse("Tidy", [core_yes, _branch("good")])
        self.assertTrue(result["ok"], result)
        core_branch = result["multiverse"]["branches"][0]
        self.assertTrue(core_branch["core_touching"])
        self.assertFalse(core_branch["deployed"])

    def test_credential_branch_and_deploy_operation_refused(self):
        cred = _branch("cred", proposal=_proposal(".env", "x", "y"))
        self.assertFalse(mv_mod.plan_multiverse(
            "Tidy", [cred, _branch("good")])["ok"])
        deploy = _branch("deploy", operations=["deploy"])
        self.assertFalse(mv_mod.plan_multiverse(
            "Tidy", [deploy, _branch("good")])["ok"])
        move = _branch("move", actions=["move"])
        self.assertFalse(mv_mod.plan_multiverse(
            "Tidy", [move, _branch("good")])["ok"])

    def test_consequential_action_needs_c14_never_a_waiver(self):
        waived = _branch("waived", actions=["send"], c14_required=False)
        self.assertFalse(mv_mod.plan_multiverse(
            "Tidy", [waived, _branch("good")])["ok"])
        gated = _branch("gated", actions=["send"], c14_required=True)
        result = mv_mod.plan_multiverse("Tidy", [gated, _branch("good")])
        self.assertTrue(result["ok"], result)
        self.assertTrue(
            result["multiverse"]["branches"][0]["c14_required"])
        self.assertTrue(
            result["multiverse"]["branches"][0]["routine"]["c14_required"])

    def test_compile_chosen_branch_with_sim_prediction(self):
        multi = _good_multiverse()
        seed = {"notes/a.txt": "old a\n", "notes/b.txt": "old b\n"}
        result = mv_mod.compile_branch(multi, "short", seed=seed)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["kind"], "COMPILED-ROUTINE")
        self.assertEqual(result["chosen_by"], "caller")
        self.assertFalse(result["deployed"])
        self.assertTrue(result["executes_nothing"])
        self.assertTrue(result["routine"]["steps"][0].startswith("1. "))
        dry = result["dry_run"]
        self.assertFalse(dry["executed"])
        self.assertFalse(dry["fallback"])
        self.assertIsNotNone(dry["sim_prediction"])
        self.assertEqual(dry["sim_prediction"]["summary"]["modified"], 1)

    def test_compile_never_auto_chooses_a_branch(self):
        multi = _good_multiverse()
        self.assertFalse(mv_mod.compile_branch(multi, "")["ok"])
        self.assertFalse(mv_mod.compile_branch(multi, "nope")["ok"])
        self.assertFalse(mv_mod.compile_branch(multi, None)["ok"])

    def test_compile_core_branch_falls_back_never_executes(self):
        core = _branch("core-yes", proposal=_proposal(
            "core/helper.py", "a", "b",
            boundary_check={
                "check": "core-modification-boundary-1692",
                "reason": "Human reviewed: helper only.",
                "checked_by": "human"}))
        multi = mv_mod.plan_multiverse(
            "Tidy", [core, _branch("good")])["multiverse"]
        # SIM refuses core paths by design, so the compiler
        # must fall back to routine + proposal dry-run only.
        result = mv_mod.compile_branch(
            multi, "core-yes", seed={"core/helper.py": "a"})
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["dry_run"]["fallback"])
        self.assertIsNotNone(result["dry_run"]["fallback_reason"])
        self.assertFalse(result["dry_run"]["executed"])
        self.assertTrue(result["executes_nothing"])
        self.assertTrue(result["routine"]["steps"])

    def test_plan_and_compile_change_no_real_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "sentinel.txt"
            sentinel.write_text("do not touch")
            before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
            multi = _good_multiverse()
            result = mv_mod.compile_branch(
                multi, "short",
                seed={"notes/a.txt": "old a\n"},
                sentinels=[str(sentinel)])
            self.assertTrue(result["ok"], result)
            self.assertEqual(
                hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)
            self.assertEqual(sentinel.read_text(), "do not touch")
            self.assertTrue(result["dry_run"]["sim_prediction"]
                            ["sentinel_unchanged"])

    def test_no_executor_or_deploy_path_in_module(self):
        source = Path(mv_mod.__file__).read_text()
        tree = ast.parse(source)
        def_names = {n.name for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        forbidden = {"deploy", "apply_proposal", "promote", "install",
                     "self_deploy", "apply_diff", "execute_branch",
                     "auto_choose", "choose_branch"}
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
        for fn in (mv_mod.plan_multiverse, mv_mod.compile_branch):
            self.assertNotIn("executor", inspect.signature(fn).parameters)


class ToolWiringTests(unittest.TestCase):
    def setUp(self):
        from core import confirm_gate
        confirm_gate.register_handler(None)
        self._confirm_gate = confirm_gate

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        self.assertIn("multiverse_plan", names)
        self.assertIn("multiverse_compile", names)
        self.assertIn("multiverse_compile_report", names)
        self.assertIn("multiverse_plan", gate._READ_ONLY_TOOLS)
        self.assertIn("multiverse_compile", gate._READ_ONLY_TOOLS)
        self.assertIn("multiverse_compile_report",
                     gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["multiverse_compile_report"],
            ("output_path",))
        # Wave 7 Build 5 added 3 multiverse tools (560 -> 563); Wave 8 Build 1 added 3 anticipation tools (563 -> 566); Wave 8 Build 2 added 3 Track L tools (566 -> 575); Wave 8 Build 3 added 6 Phase 5 tools (569 -> 575); Wave 8 Build 4 added 3 feel tools (575 -> 578); Wave 8 Build 5 added 2 learning-last tools (578 -> 580; first-open opt-in added 4 continuous tools (580 -> 584)). Wave 9 Build 1 added 5 wellbeing tools (584 -> 589). Wave 9 Build 2 added 4 communications tools (589 -> 593). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 4 added 4 hardware tools (597 -> 601). Wave 9 Build 5 added 4 accessibility tools (601 -> 605). Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 772)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_plan_and_compile_through_funnel(self):
        planned = _result(tools.execute("multiverse_plan", {
            "goal": "Tidy the notes",
            "branches_json": json.dumps([
                _branch("short", new="new a short\n"),
                _branch("long", path="notes/b.txt", old="old b\n",
                        new="new b long\n")])}))
        self.assertTrue(planned["ok"], planned)
        multi = planned["multiverse"]
        self.assertEqual(multi["kind"], "MULTIVERSE")
        compiled = _result(tools.execute("multiverse_compile", {
            "multiverse_json": json.dumps(multi),
            "branch_name": "short",
            "seed_json": json.dumps({"notes/a.txt": "old a\n"})}))
        self.assertEqual(compiled["kind"], "COMPILED-ROUTINE")
        self.assertFalse(compiled["deployed"])

    def test_report_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "routine.json")
            result = tools.execute("multiverse_compile_report", {
                "multiverse_json": json.dumps(_good_multiverse()),
                "branch_name": "short",
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_report_with_confirm_writes_routine_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "routine.json")
            data = _result(tools.execute("multiverse_compile_report", {
                "multiverse_json": json.dumps(_good_multiverse()),
                "branch_name": "short",
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["deployed"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["kind"], "COMPILED-ROUTINE")
            self.assertFalse(written["deployed"])


if __name__ == "__main__":
    unittest.main()
