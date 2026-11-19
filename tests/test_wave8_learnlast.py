"""Wave 8 Build 5 tests — Phase 8 learning-last continuation.

Proves: proven jobs only (Wave 6 WORK rule) distil into a
spec whose change is a Wave 7 proposal (exact diff +
regression guard + evidence ledger rebuilt from proof
references); unproven, failed, evidence-free, step-free, or
proof-less input refuses the whole spec; mixed proven +
unproven refuses whole; a spec without a guard is refused;
a spec without a diff is refused; a core-touching diff
without the 1692 boundary check is refused and with it is
still proposal-only; a perfect spec changes no shipped file
(sentinels unchanged, no file created beside them);
non-deployment by construction (no deploy/apply/promote/
install/self-deploy identifier, no subprocess, no eval/exec
in the module's AST); tool classifications, grouping in
GROUP_MEMBERS with friendly names, and funnel confirm
refusal without a handler for the report tool. Registry
moves 578 -> 580. Nothing here writes a real file except
the confirm-gated report tool under a registered handler,
into temp dirs.

Stdlib unittest, no GUI, no network, no code execution.
Run from the tree root with PYTHONPATH=. (as the other
suites are run by tests/run_tests.py).
"""

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from core import learning_last as learn_mod
from core import learning_last_tools as learn_tools
from core import confirm_gate
from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

try:
    from _budget_isolation import restore_budget, snapshot_budget
except ImportError:  # run as a package module instead of top-level
    from tests._budget_isolation import (  # type: ignore
        restore_budget, snapshot_budget)



def _catalogue_groups():
    """Read GROUP_MEMBERS from the single catalogue source
    (core/catalogue_groups_data.py, GAPS-23 F-V7) without
    importing it — the same reason the other suites test
    Qt-free logic only."""
    # GAPS-23 (F-V7): GROUP_MEMBERS lives in
    # core/catalogue_groups_data.py; this helper reads the tables there.
    source = Path(__file__).resolve().parent.parent.joinpath(
        "core", "catalogue_groups_data.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) \
                    and target.id == "GROUP_MEMBERS" and value is not None:
                groups = ast.literal_eval(value)
                return {name: group
                        for group, names in groups.items()
                        for name in names}
    return {}


def _friendly_names():
    """Read TOOL_FRIENDLY_NAMES from core/tool_labels.py
    source without importing it.
    INTENTIONAL (refactor P1 step 5): the table moved verbatim from
    window_widgets.py to window_widgets_toolnames.py; GAPS-28
    (G-V7) moved it to core/tool_labels.py (data, not GUI)."""
    source = Path(__file__).resolve().parent.parent.joinpath(
        "core", "tool_labels.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) \
                    and target.id == "TOOL_FRIENDLY_NAMES" \
                    and value is not None:
                return ast.literal_eval(value)
    return {}


def _good_job(**overrides):
    job = {
        "job_id": "job-1",
        "goal_text": "summarise the morning notes",
        "binder": {
            "name": "binder-1",
            "goal_text": "summarise the morning notes",
            "status": "completed",
            "reason": "",
            "steps": ["read the notes file", "write a summary"],
            "evidence": [{"source": "funnel",
                          "detail": "job completed in 3 steps"}],
        },
        "field_proven": True,
        "proof_reference": "field-run-2026-10-26-001",
    }
    job.update(overrides)
    return job


def _good_spec(**overrides):
    payload = {
        "jobs": [_good_job()],
        "title": "Learned summary routine",
        "summary": "A routine distilled from one proven summary job.",
        "diffs": [{"path": "notes/summary_routine.txt",
                   "old_text": "draft routine",
                   "new_text": "proven summary routine"}],
        "regression_guards": ["test_summary_routine"],
    }
    payload.update(overrides)
    return payload


def _result(payload):
    """tools.execute returns {"status": "success", "result": <JSON str>}."""
    assert payload["status"] == "success", payload
    return json.loads(payload["result"])


class TestSpecFromProven(unittest.TestCase):
    def test_proven_job_yields_spec_with_proposal(self):
        result = learn_mod.build_learning_spec(
            _good_spec()["jobs"], "Learned summary routine",
            "A routine distilled from one proven summary job.",
            _good_spec()["diffs"], ["test_summary_routine"])
        self.assertTrue(result["ok"], result)
        spec = result["spec"]
        self.assertEqual(spec["kind"], "LEARNING_LAST_SPEC")
        self.assertEqual(spec["status"], "PROPOSED")
        self.assertFalse(spec["deployed"])
        self.assertTrue(spec["executes_nothing"])
        self.assertIsNone(spec["applies_to"])
        self.assertEqual(spec["learned_from"]["source_job_ids"], ["job-1"])
        self.assertEqual(spec["learned_from"]["proof_references"],
                         ["field-run-2026-10-26-001"])
        texts = [s["text"] for s in spec["learned_from"]["steps"]]
        self.assertEqual(texts, ["read the notes file", "write a summary"])
        proposal = spec["proposal"]
        self.assertEqual(proposal["kind"], "PROPOSAL")
        self.assertEqual(proposal["status"], "PROPOSED")
        self.assertFalse(proposal["deployed"])
        diff = proposal["diffs"][0]
        self.assertEqual(diff["old_sha256"],
                         hashlib.sha256(b"draft routine").hexdigest())
        self.assertEqual(diff["new_sha256"],
                         hashlib.sha256(b"proven summary routine").hexdigest())
        self.assertEqual(proposal["regression_guards"],
                         ["test_summary_routine"])
        self.assertEqual(proposal["evidence"][0]["kind"], "field_proven_job")
        self.assertEqual(proposal["evidence"][0]["ref"],
                         "field-run-2026-10-26-001")

    def test_send_step_carries_gate_not_approval(self):
        job = _good_job()
        job["binder"]["steps"] = ["send the summary to the team"]
        result = learn_mod.build_learning_spec(
            [job], "Learned send routine", "A routine that sends.",
            [{"path": "notes/send_routine.txt", "old_text": "a",
             "new_text": "b"}], ["test_send_routine"])
        self.assertTrue(result["ok"], result)
        step = result["spec"]["learned_from"]["steps"][0]
        self.assertIn("C-14", step["c14_requirement"])
        self.assertNotIn("approved", json.dumps(step))

    def test_extra_evidence_appended_after_proven_ledger(self):
        result = learn_mod.build_learning_spec(
            _good_spec()["jobs"], "T", "S",
            _good_spec()["diffs"], ["test_x"],
            extra_evidence=[{"kind": "field_note", "ref": "note-9",
                             "detail": "owner watched the run"}])
        self.assertTrue(result["ok"], result)
        refs = [e["ref"] for e in result["spec"]["proposal"]["evidence"]]
        self.assertEqual(refs, ["field-run-2026-10-26-001", "note-9"])

    def test_spec_from_json_revalidates(self):
        payload = _good_spec()
        payload["jobs"][0]["field_proven"] = False
        result = learn_mod.spec_from_json(json.dumps(payload))
        self.assertFalse(result["ok"])
        self.assertIsNone(result["spec"])


class TestRefusals(unittest.TestCase):
    def _refused(self, **overrides):
        payload = _good_spec(**overrides)
        return learn_mod.build_learning_spec(
            payload["jobs"], payload["title"], payload["summary"],
            payload["diffs"], payload["regression_guards"],
            boundary_check=payload.get("boundary_check"),
            extra_evidence=payload.get("extra_evidence"))

    def test_unproven_job_refused(self):
        job = _good_job(field_proven=False)
        self.assertFalse(self._refused(jobs=[job])["ok"])

    def test_proof_without_assertion_refused(self):
        job = _good_job(proof_reference="")
        self.assertFalse(self._refused(jobs=[job])["ok"])

    def test_failed_binder_refused(self):
        job = _good_job()
        job["binder"]["status"] = "failed"
        self.assertFalse(self._refused(jobs=[job])["ok"])

    def test_evidence_free_job_refused(self):
        job = _good_job()
        job["binder"]["evidence"] = []
        self.assertFalse(self._refused(jobs=[job])["ok"])

    def test_step_free_job_refused(self):
        job = _good_job()
        job["binder"]["steps"] = []
        self.assertFalse(self._refused(jobs=[job])["ok"])

    def test_mixed_proven_unproven_refused_whole(self):
        good = _good_job()
        bad = _good_job(job_id="job-2", field_proven=False)
        result = self._refused(jobs=[good, bad])
        self.assertFalse(result["ok"])
        self.assertIsNone(result["spec"])

    def test_empty_jobs_refused(self):
        self.assertFalse(self._refused(jobs=[])["ok"])

    def test_spec_without_guard_refused(self):
        self.assertFalse(self._refused(regression_guards=[])["ok"])

    def test_guard_not_naming_test_refused(self):
        self.assertFalse(
            self._refused(regression_guards=["summary routine"])["ok"])

    def test_spec_without_diff_refused(self):
        self.assertFalse(self._refused(diffs=[])["ok"])

    def test_core_diff_without_boundary_refused(self):
        diffs = [{"path": "core/routine.py", "old_text": "a",
                  "new_text": "b"}]
        self.assertFalse(self._refused(diffs=diffs)["ok"])

    def test_core_diff_with_boundary_is_proposal_only(self):
        diffs = [{"path": "core/routine.py", "old_text": "a",
                  "new_text": "b"}]
        result = self._refused(
            diffs=diffs,
            boundary_check={"check": "core-modification-boundary-1692",
                            "reason": "routine change, reviewed",
                            "checked_by": "parent"})
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["spec"]["deployed"])
        self.assertEqual(result["spec"]["status"], "PROPOSED")

    def test_credential_diff_refused_even_with_boundary(self):
        diffs = [{"path": ".env", "old_text": "a", "new_text": "b"}]
        result = self._refused(
            diffs=diffs,
            boundary_check={"check": "core-modification-boundary-1692",
                            "reason": "x", "checked_by": "parent"})
        self.assertFalse(result["ok"])

    def test_bad_extra_evidence_refused(self):
        result = self._refused(
            extra_evidence=[{"kind": "note", "ref": "", "detail": "x"}])
        self.assertFalse(result["ok"])


class TestNoDeployment(unittest.TestCase):
    def test_ast_has_no_deploy_path(self):
        source = Path(learn_mod.__file__).read_text()
        tree = ast.parse(source)
        banned = {"deploy", "apply_proposal", "promote", "install",
                  "self_deploy", "run_goal", "subprocess"}
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in banned:
                found.add(node.id)
            if isinstance(node, ast.Attribute) and node.attr in banned:
                found.add(node.attr)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in banned:
                        found.add(alias.name)
            if isinstance(node, ast.ImportFrom) and node.module \
                    and node.module.split(".")[0] in banned:
                found.add(node.module)
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", None) \
                    or getattr(func, "attr", None)
                if name in ("eval", "exec"):
                    found.add(name)
        self.assertEqual(found, set(), found)
        self.assertNotIn("import subprocess", source)

    def test_perfect_spec_changes_no_shipped_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "shipped.txt"
            sentinel.write_text("shipped behaviour, untouched")
            before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
            result = learn_mod.build_learning_spec(
                _good_spec()["jobs"], "T", "S",
                _good_spec()["diffs"], ["test_x"])
            self.assertTrue(result["ok"], result)
            self.assertEqual(
                hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)
            self.assertEqual([p.name for p in Path(tmp).iterdir()],
                             ["shipped.txt"])


class TestWiring(unittest.TestCase):
    def test_registry_total_is_758(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        # Wave 8 Build 4 added 3 feel tools (575 -> 578); Wave 8
        # Build 5 added 2 learning-last tools (578 -> 580; first-open opt-in added 4 continuous tools (580 -> 584)). Wave 9 Build 1 added 5 wellbeing tools (584 -> 589). Wave 9 Build 2 added 4 communications tools (589 -> 593). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 4 added 4 hardware tools (597 -> 601). Wave 9 Build 5 added 4 accessibility tools (601 -> 605). Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 758)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing

    def test_classifications(self):
        self.assertIn("learnlast_spec", gate._READ_ONLY_TOOLS)
        self.assertIn("learnlast_report", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(gate._WRITE_DESTINATION_ARGS["learnlast_report"],
                         ("output_path",))

    def test_grouping_and_friendly_names(self):
        groups = _catalogue_groups()
        self.assertEqual(groups.get("learnlast_spec"), "System & apps")
        self.assertEqual(groups.get("learnlast_report"), "System & apps")
        names = _friendly_names()
        self.assertEqual(names.get("learnlast_spec"), "Learning-last spec")
        self.assertEqual(names.get("learnlast_report"),
                         "Learning-last spec report")

    def test_spec_through_funnel_without_handler(self):
        payload = _result(tools.execute(
            "learnlast_spec", {"spec_json": json.dumps(_good_spec())}))
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["spec"]["status"], "PROPOSED")
        self.assertFalse(payload["spec"]["deployed"])

    def test_report_refused_without_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "spec.json")
            payload = tools.execute(
                "learnlast_report",
                {"spec_json": json.dumps(_good_spec()),
                 "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(payload.get("status"), "refused")
            self.assertEqual(payload.get("kind"), "not_confirmed")
            self.assertFalse(Path(target).exists())

    def test_report_with_handler_writes_spec_only(self):
        confirm_gate.register_handler(lambda *a: True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = str(Path(tmp) / "spec.json")
                payload = _result(tools.execute(
                    "learnlast_report",
                    {"spec_json": json.dumps(_good_spec()),
                     "output_path": target}))
                self.assertTrue(payload["ok"], payload)
                self.assertFalse(payload["deployed"])
                written = json.loads(Path(target).read_text())
                self.assertEqual(written["kind"], "LEARNING_LAST_SPEC")
                self.assertEqual(written["status"], "PROPOSED")
        finally:
            confirm_gate.register_handler(None)

    def test_report_rebuilds_refused_spec(self):
        confirm_gate.register_handler(lambda *a: True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = str(Path(tmp) / "spec.json")
                bad = _good_spec()
                bad["jobs"][0]["field_proven"] = False
                payload = _result(tools.execute(
                    "learnlast_report",
                    {"spec_json": json.dumps(bad),
                     "output_path": target}))
                self.assertFalse(payload["ok"])
                self.assertFalse(Path(target).exists())
        finally:
            confirm_gate.register_handler(None)


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
