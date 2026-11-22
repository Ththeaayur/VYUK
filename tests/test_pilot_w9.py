"""Pilot slices tests — descriptors and records only,
P15 workshop / P16 student / P17 dev PILOT slices,
nothing executed, read live, deployed, or signed.

Proves: the bench descriptor echoes exactly
caller-supplied project name and notes, with read_live
and measured False; the build-step descriptor echoes
caller-supplied step labels in order, duplicates kept
once and reported, executed False; the study-session
descriptor echoes caller-supplied subject, materials,
and questions, with every material carrying its source
as supplied, generated False, read_live False; the
revision-plan descriptor echoes caller-supplied topics
in order, scheduled False, reminder_set False; the
dev-workflow descriptor echoes caller-supplied repo
label and steps, executed, deployed, and signed False;
the review-note descriptor echoes caller-supplied
file:line cites and notes, approved and merged False;
a pilot record re-validates and refuses a tampered
descriptor, an action-shaped step, a protected material
without provenance, or a success, deployment, or signing
claim; any execute, run, deploy, install, promote, push,
merge, sign, or apply action refuses whole; no
subprocess, eval, exec, network, or file-reading code
exists in the module (AST on imports and banned call
names, not on prose); tool classifications, grouping in
GROUP_MEMBERS with friendly plain-language names, and
funnel confirm refusal without a handler. Registry moves
613 -> 620. Nothing here writes a real file except the
confirm-gated record tool under a registered handler,
into temp dirs only.

Stdlib unittest, no GUI, no network, no code execution.
Run from the tree root with PYTHONPATH=. (as the other
suites are run by tests/run_tests.py).
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from core import pilot as pilot_mod
from core import pilot_tools as pilot_tools
from core import confirm_gate
from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/


def _catalogue_groups():
    """GROUP_MEMBERS from the single catalogue source
    (core/catalogue_groups_data.py — pure data, no Qt)."""
    # GAPS-23 (F-V7): the AST re-parse is gone with the Qt
    # module that forced it — the tables live in core now.
    from core.catalogue_groups_data import GROUP_MEMBERS
    return {group: set(names)
            for group, names in GROUP_MEMBERS.items()}


def _friendly_names():
    source = Path(__file__).resolve().parent.parent.joinpath(
        "core", "tool_labels.py").read_text()
        # INTENTIONAL (refactor P1 step 5): friendly-names table moved verbatim to window_widgets_toolnames.py.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) \
                        and target.id == "TOOL_FRIENDLY_NAMES":
                    return {k.value: v.value
                            for k, v in zip(node.value.keys,
                                            node.value.values)
                            if isinstance(k, ast.Constant)}
    raise AssertionError("TOOL_FRIENDLY_NAMES not found")


def _result(executed):
    if isinstance(executed, dict) and "result" in executed:
        value = executed["result"]
        return json.loads(value) if isinstance(value, str) else value
    return executed


PILOT_TOOLS = ("pilot_bench", "pilot_build_steps",
               "pilot_study_session", "pilot_revision_plan",
               "pilot_dev_workflow", "pilot_review_note",
               "pilot_record_pilot")


class DescriptorTests(unittest.TestCase):
    def test_bench_echoes_caller_fields_only(self):
        desc = pilot_mod.build_project_bench(
            "spider gripper", "layer height 0.2, infill 40")
        self.assertEqual(desc["kind"], "PROJECT_BENCH")
        self.assertEqual(desc["project_name"], "spider gripper")
        self.assertEqual(desc["bench_notes"],
                         "layer height 0.2, infill 40")
        self.assertFalse(desc["read_live"])
        self.assertFalse(desc["measured"])
        self.assertIn("Nothing was probed", desc["note"])

    def test_bench_refuses_empty_and_claims(self):
        with self.assertRaises(ValueError):
            pilot_mod.build_project_bench("")
        with self.assertRaises(ValueError):
            pilot_mod.build_project_bench(
                "bench", "build succeeded on first try")
        with self.assertRaises(ValueError):
            pilot_mod.build_project_bench(
                "bench", action="measure")

    def test_build_steps_echo_order_duplicates_and_never_run(self):
        desc = pilot_mod.build_build_steps(
            ["draft outline", "sketch diagram", "draft outline",
             "list parts"],
            assumptions="parts on hand only")
        self.assertEqual(desc["kind"], "BUILD_STEPS")
        self.assertEqual(desc["steps"],
                         ["draft outline", "sketch diagram",
                          "list parts"])
        self.assertEqual(desc["duplicates_removed"],
                         ["draft outline"])
        self.assertEqual(desc["assumptions"], "parts on hand only")
        self.assertFalse(desc["executed"])
        self.assertFalse(desc["read_live"])

    def test_build_steps_refuse_action_shaped_steps(self):
        for step in ("deploy to staging", "run the checks",
                     "push the branch", "sign the build",
                     "install the package"):
            with self.assertRaises(ValueError, msg=step):
                pilot_mod.build_build_steps([step])
        with self.assertRaises(ValueError):
            pilot_mod.build_build_steps([])
        with self.assertRaises(ValueError):
            pilot_mod.build_build_steps(["draft"], action="execute")

    def test_study_session_echoes_materials_with_sources(self):
        desc = pilot_mod.build_study_session(
            "BB101", [{"title": "L14 notes", "source": "my own notes"},
                      {"title": "Photo 51 paper",
                       "source": "course reading list"}],
            questions=["why does the helix repeat every 10 bases?"])
        self.assertEqual(desc["kind"], "STUDY_SESSION")
        self.assertEqual(desc["subject"], "BB101")
        self.assertEqual(desc["materials"][0],
                         {"title": "L14 notes",
                          "source": "my own notes"})
        self.assertEqual(desc["questions"],
                         ["why does the helix repeat every 10 bases?"])
        self.assertFalse(desc["generated"])
        self.assertFalse(desc["read_live"])

    def test_study_session_refuses_sourceless_and_protected(self):
        with self.assertRaises(ValueError):
            pilot_mod.build_study_session(
                "BB101", [{"title": "L14 notes"}])
        with self.assertRaises(ValueError):
            pilot_mod.build_study_session(
                "BB101", [{"title": "L14 notes", "source": "",
                           }])
        with self.assertRaises(ValueError):
            pilot_mod.build_study_session(
                "BB101", [{"title": "exam paper",
                           "source": "a friend",
                           "protected": True}])
        with self.assertRaises(ValueError):
            pilot_mod.build_study_session(
                "BB101", [{"title": "L14 notes",
                           "source": "my own notes",
                           "grade": "A"}])
        with self.assertRaises(ValueError):
            pilot_mod.build_study_session("BB101", [])

    def test_revision_plan_echoes_topics_and_schedules_nothing(self):
        desc = pilot_mod.build_revision_plan(
            ["L10", "L11", "L10", "L12"])
        self.assertEqual(desc["kind"], "REVISION_PLAN")
        self.assertEqual(desc["topics"], ["L10", "L11", "L12"])
        self.assertEqual(desc["duplicates_removed"], ["L10"])
        self.assertFalse(desc["scheduled"])
        self.assertFalse(desc["reminder_set"])
        with self.assertRaises(ValueError):
            pilot_mod.build_revision_plan(["L10"], action="schedule")
        with self.assertRaises(ValueError):
            pilot_mod.build_revision_plan([])

    def test_dev_workflow_echoes_and_never_deploys_or_signs(self):
        desc = pilot_mod.build_dev_workflow(
            "voice-ai-assistant",
            ["draft change list", "note open questions"])
        self.assertEqual(desc["kind"], "DEV_WORKFLOW")
        self.assertEqual(desc["repo_label"], "voice-ai-assistant")
        self.assertEqual(desc["steps"],
                         ["draft change list", "note open questions"])
        self.assertFalse(desc["executed"])
        self.assertFalse(desc["deployed"])
        self.assertFalse(desc["signed"])
        self.assertFalse(desc["read_live"])
        with self.assertRaises(ValueError):
            pilot_mod.build_dev_workflow("repo", ["merge the branch"])
        with self.assertRaises(ValueError):
            pilot_mod.build_dev_workflow("repo", ["draft"],
                                         action="deploy")

    def test_review_note_echoes_cites_and_notes(self):
        desc = pilot_mod.build_review_note(
            # INTENTIONAL (Phase 2 B4-1 rename): cite follows the renamed module file (was "core/pilot_w9.py:42"; content byte-identical, line unchanged).
            ["core/pilot.py:42", "tests/test_pilot_w9.py:7"],
            "check the refusal scan covers new steps")
        self.assertEqual(desc["kind"], "REVIEW_NOTE")
        self.assertEqual(desc["cites"],
                         # INTENTIONAL (Phase 2 B4-1 rename): expected cite follows the renamed module file.
                         ["core/pilot.py:42",
                          "tests/test_pilot_w9.py:7"])
        self.assertFalse(desc["approved"])
        self.assertFalse(desc["merged"])
        self.assertFalse(desc["read_live"])
        for bad in ("no colon here", "file.py:notaline",
                    "file.py:", ":42"):
            with self.assertRaises(ValueError, msg=bad):
                pilot_mod.build_review_note([bad], "notes")
        with self.assertRaises(ValueError):
            pilot_mod.build_review_note(
                ["a.py:1"], "already merged, looks good")

    def test_record_rebuilds_and_refuses_tampered(self):
        rebuilt = pilot_mod.validate_recordable_pilot(
            "dev-workflow",
            {"repo_label": "voice-ai-assistant",
             "steps": ["draft change list"]})
        self.assertEqual(rebuilt["kind"], "DEV_WORKFLOW")
        with self.assertRaises(ValueError):
            pilot_mod.validate_recordable_pilot(
                "dev-workflow",
                {"repo_label": "voice-ai-assistant",
                 "steps": ["draft change list"],
                 "deployed": True})
        with self.assertRaises(ValueError):
            pilot_mod.validate_recordable_pilot(
                "dev-workflow",
                {"repo_label": "voice-ai-assistant",
                 "steps": ["deploy to staging"]})
        with self.assertRaises(ValueError):
            pilot_mod.validate_recordable_pilot(
                "study-session",
                {"subject": "BB101",
                 "materials": [{"title": "L14 notes"}]})
        with self.assertRaises(ValueError):
            pilot_mod.validate_recordable_pilot(
                "unknown-kind", {})
        bench = pilot_mod.validate_recordable_pilot(
            "bench", {"project_name": "gripper",
                      "bench_notes": "notes"})
        self.assertEqual(bench["project_name"], "gripper")

    def test_no_execution_or_live_read_code(self):
        # The module itself must contain no execution, file,
        # or network call at all. The coordinator legitimately
        # writes the confirm-gated record file (destination-
        # gated, caller-chosen .json only), so its file writes
        # are the feature, not a violation — but it must
        # still contain no execution or network call. (A bare
        # "get" is NOT banned: dict.get is not a network
        # get, and the network-get words are covered by the
        # banned imports.)
        # (The coordinator legitimately imports pathlib for
        # its destination gate — the same as the gaming
        # coordinator — so pathlib is banned only for the
        # module file, which imports nothing at all.)
        module_banned_imports = {"subprocess", "socket", "urllib",
                                 "requests", "os", "sys", "pathlib"}
        coordinator_banned_imports = {"subprocess", "socket",
                                      "urllib", "requests", "os", "sys"}
        module_banned_calls = {"system", "popen", "exec", "eval",
                               "compile", "open", "socket", "connect",
                               "urlopen", "read_text", "read_bytes",
                               "write_text"}
        coordinator_banned_calls = {"system", "popen", "exec", "eval",
                                    "compile", "socket", "connect",
                                    "urlopen"}
        for filename, banned_imports, banned_calls in (
                ("core/pilot.py", module_banned_imports,
                 module_banned_calls),
                ("core/pilot_tools.py", coordinator_banned_imports,
                 coordinator_banned_calls)):
            path = Path(__file__).resolve().parent.parent / filename
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(
                            alias.name.split(".")[0], banned_imports,
                            f"banned import in {filename}")
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotIn(
                        (node.module or "").split(".")[0],
                        banned_imports, f"banned import in {filename}")
                elif isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name):
                        called = func.id
                    elif isinstance(func, ast.Attribute):
                        called = func.attr
                    else:
                        continue
                    self.assertNotIn(called, banned_calls,
                                     f"banned call {called} in {filename}")


class WiringTests(unittest.TestCase):
    def setUp(self):
        self._confirm_gate = confirm_gate
        self._confirm_gate.register_handler(None)

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in PILOT_TOOLS:
            self.assertIn(name, names)
        for name in PILOT_TOOLS[:6]:
            self.assertIn(name, gate._READ_ONLY_TOOLS)
        self.assertIn("pilot_record_pilot", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["pilot_record_pilot"],
            ("output_path",))
        # Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 769)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_bench_tool_name_trips_no_money_guard(self):
        # Regression: the bench tool was first named
        # pilot_project_bench, and the money guard's
        # separator-stripped blob ("pilotprojectbench")
        # contains "otp" across the pilot/project join,
        # so every call tripped MN-5.14 rule 1 and was
        # routed to a confirm ask that refuses it outright
        # — the tool was unusable through the funnel.
        # The tool is therefore pilot_bench, and this test
        # pins that no pilot tool name trips the guard.
        from core.funnel import money_guard as money_guard  # Phase 3 F2b INTENTIONAL: money_guard moved to core/funnel/
        for name in PILOT_TOOLS:
            self.assertIsNone(
                money_guard.check(name, {}),
                f"money guard trips on {name}")

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in PILOT_TOOLS:
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        self.assertEqual(names["pilot_bench"], "Project bench")
        self.assertEqual(names["pilot_build_steps"], "Build steps")
        self.assertEqual(names["pilot_study_session"], "Study session")
        self.assertEqual(names["pilot_revision_plan"], "Revision plan")
        self.assertEqual(names["pilot_dev_workflow"], "Dev workflow")
        self.assertEqual(names["pilot_review_note"], "Review note")
        self.assertEqual(names["pilot_record_pilot"], "Record pilot")

    def test_readonly_tools_through_funnel(self):
        bench = _result(tools.execute("pilot_bench", {
            "project_name": "gripper",
            "bench_notes": "notes"}))
        self.assertEqual(bench["kind"], "PROJECT_BENCH")
        self.assertFalse(bench["read_live"])
        steps = _result(tools.execute("pilot_build_steps", {
            "steps": ["draft outline"]}))
        self.assertEqual(steps["kind"], "BUILD_STEPS")
        self.assertFalse(steps["executed"])
        session = _result(tools.execute("pilot_study_session", {
            "subject": "BB101",
            "materials": [{"title": "L14 notes",
                           "source": "my own notes"}]}))
        self.assertEqual(session["kind"], "STUDY_SESSION")
        self.assertFalse(session["generated"])
        plan = _result(tools.execute("pilot_revision_plan", {
            "topics": ["L10", "L11"]}))
        self.assertEqual(plan["kind"], "REVISION_PLAN")
        self.assertFalse(plan["scheduled"])
        workflow = _result(tools.execute("pilot_dev_workflow", {
            "repo_label": "voice-ai-assistant",
            "steps": ["draft change list"]}))
        self.assertEqual(workflow["kind"], "DEV_WORKFLOW")
        self.assertFalse(workflow["deployed"])
        self.assertFalse(workflow["signed"])
        note = _result(tools.execute("pilot_review_note", {
            "cites": ["a.py:1"], "notes": "check this"}))
        self.assertEqual(note["kind"], "REVIEW_NOTE")
        self.assertFalse(note["approved"])
        # A handler-level refusal flows through the funnel
        # as a success envelope carrying ok False — the
        # refusal itself is in the result, not the envelope.
        refused = _result(tools.execute("pilot_build_steps", {
            "steps": ["deploy to staging"]}))
        self.assertFalse(refused["ok"], refused)
        self.assertIn("deploy", refused["reason"], refused)

    def test_record_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "pilot.json")
            result = tools.execute("pilot_record_pilot", {
                "record_kind": "bench",
                "record_json": json.dumps(
                    {"project_name": "gripper",
                     "bench_notes": "notes"}),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "pilot.json")
            data = _result(tools.execute("pilot_record_pilot", {
                "record_kind": "dev-workflow",
                "record_json": json.dumps(
                    {"repo_label": "voice-ai-assistant",
                     "steps": ["draft change list"]}),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["executed"])
            self.assertFalse(data["deployed"])
            self.assertFalse(data["signed"])
            self.assertFalse(data["read_live"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["pilot"]["repo_label"],
                             "voice-ai-assistant")
            self.assertFalse(written["deployed"])
            self.assertFalse(written["signed"])
            self.assertEqual(sorted(Path(tmp).iterdir()), [Path(target)])

    def test_record_refuses_tampered_or_action_pilot(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "pilot.json")
            for kind, fields in (
                    ("dev-workflow", {"repo_label": "repo",
                                      "steps": ["deploy to staging"]}),
                    ("dev-workflow", {"repo_label": "repo",
                                      "steps": ["draft"],
                                      "deployed": True}),
                    ("study-session", {"subject": "BB101",
                                       "materials": [
                                           {"title": "notes"}]}),
                    ("review-note", {"cites": ["a.py:1"],
                                     "notes": "tests passed, ship it"})):
                data = _result(tools.execute("pilot_record_pilot", {
                    "record_kind": kind,
                    "record_json": json.dumps(fields),
                    "output_path": target}))
                self.assertFalse(data["ok"], data)
            self.assertFalse(Path(target).exists())


if __name__ == "__main__":
    unittest.main()
