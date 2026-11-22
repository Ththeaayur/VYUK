"""Wave 6 wiring tests — production callers, not just modules.

The five staging unit suites (harness 29, AGENT 20, WORK 23,
MISS 24, mission 31) prove the modules. This suite proves the
WIRING: registry presence + classification, funnel refusal
without a confirm handler, caller binding through the §2.1
shape, staged-plan consumption, and the AGENT packet fold.
Per WAVE6_WIRING_SPEC §6.

Store isolation: the harness store and the mission journal
are pointed at a temp directory for the whole suite (the
coordinator's test seams, as in study_flow / triage4), and
the confirm handler is cleared in setUp/tearDown, so no test
here ever writes the real data/ tree. Tool results arrive as
JSON strings inside execute()'s {"status": "success",
"result": ...} envelope and are parsed before asserting.
"""

import ast
import asyncio
import functools
import json
import os
import tempfile
import unittest
from pathlib import Path

from core import agent_coordination as agent_mod
from core import mission_jobs as jobs_mod
from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core import integration_harness_tools as w6
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

try:
    from _budget_isolation import restore_budget, snapshot_budget
except ImportError:  # run as a package module instead of top-level
    from tests._budget_isolation import (  # type: ignore
        restore_budget, snapshot_budget)



def _run_coro(coro):
    loop = asyncio.get_event_loop_policy().new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _result(payload):
    """Parse an execute() envelope's JSON-string result."""
    assert payload["status"] == "success", payload
    return json.loads(payload["result"])


def _manifest(**overrides):
    manifest = {
        "name": "demo-notes",
        "version": "1.0.0",
        "licence": "MIT",
        "declared_permissions": ["files.own.read", "files.own.write"],
        "declared_files": ["notes.txt"],
    }
    manifest.update(overrides)
    return manifest


def _proven_job(job_id="job-1", field_proven=True, proof="field-run-001",
                steps=("open settings", "toggle sounds")):
    return {
        "job_id": job_id,
        "goal_text": "set up sounds",
        "binder": {
            "name": job_id, "goal_text": "set up sounds",
            "status": "completed", "reason": "",
            "steps": list(steps),
            "evidence": [{"source": "field-run",
                          "detail": "ran on his machine"}],
        },
        "field_proven": field_proven,
        "proof_reference": proof,
    }


class _Wave6Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        w6.set_test_harness_store(
            os.path.join(self._tmp.name, "extensions.json"))
        w6.set_test_journal_path(
            os.path.join(self._tmp.name, "mission_journal.json"))
        from core import confirm_gate

        confirm_gate.register_handler(None)
        self._confirm_gate = confirm_gate

    def tearDown(self):
        w6.clear_test_harness_store()
        w6.clear_test_journal_path()
        self._confirm_gate.register_handler(None)
        self._tmp.cleanup()

    def _approve(self):
        self._confirm_gate.register_handler(lambda name, summary: True)


class TestRegistryTotal(_Wave6Base):
    # Research wiring batch: INTENTIONAL — renamed with the registry total (640 -> 641).
    def test_registry_total_is_769(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        # Wave 6 integration added 19 tools (532 -> 551: 7
        # harness + 3 WORK + 3 MISS + 6 mission; AGENT has no
        # tool row, deliberately — see test_agent_caller_wired).
        # Wave 7 Build 1 added 2 sandbox tools (551 -> 553); Wave 7 Build 2 added 3 undo tools (553 -> 556); Wave 7 Build 3 added 2 SIM tools (556 -> 558); Wave 7 Build 4 added 2 proposal tools (558 -> 560); Wave 7 Build 5 added 3 multiverse tools (560 -> 563); Wave 8 Build 1 added 3 anticipation tools (563 -> 566); Wave 8 Build 2 added 3 Track L tools (566 -> 575); Wave 8 Build 3 added 6 Phase 5 tools (569 -> 575); Wave 8 Build 4 added 3 feel tools (575 -> 578); Wave 8 Build 5 added 2 learning-last tools (578 -> 580; first-open opt-in added 4 continuous tools (580 -> 584)). Wave 9 Build 1 added 5 wellbeing tools (584 -> 589). Wave 9 Build 2 added 4 communications tools (589 -> 593). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 4 added 4 hardware tools (597 -> 601). Wave 9 Build 5 added 4 accessibility tools (601 -> 605). Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        decls = tools.get_declarations()
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Library sweep: INTENTIONAL — 4 tools added (641 -> 645); pin was stale since the sweep, proven identical (645 != 641) on the pristine backup tree; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; stale-pin sweep: INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply; wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(decls), 769)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169


class TestHarnessToolsWired(_Wave6Base):
    TOOLS = ["extension_audit", "extension_status", "extension_register",
             "extension_approve", "extension_enable", "extension_disable",
             "extension_revoke"]

    def test_all_seven_registered_with_spec_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in self.TOOLS:
            self.assertIn(name, names)
        for name in ("extension_audit", "extension_status"):
            self.assertIn(name, gate._READ_ONLY_TOOLS)
        for name in ("extension_register", "extension_approve",
                     "extension_enable", "extension_revoke"):
            self.assertIn(name, gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(gate._FREE_BY_DESIGN["extension_disable"],
                         "app-state")
        self.assertEqual(gate._WRITE_DESTINATION_ARGS["extension_register"],
                         ("directory",))

    def test_audit_and_status_succeed_without_a_handler(self):
        audit = _result(tools.execute(
            "extension_audit",
            {"manifest_json": json.dumps(_manifest())}))
        self.assertTrue(audit["ok"])
        self.assertTrue(audit["licence_ok"])
        status = _result(tools.execute(
            "extension_status", {"name": "demo-notes"}))
        self.assertEqual(status["state"], "unknown extension")

    def test_confirm_gated_four_refuse_without_a_handler_and_write_nothing(self):
        store = os.path.join(self._tmp.name, "extensions.json")
        for name, args in [
            ("extension_register", {"manifest_json": json.dumps(_manifest()),
                                    "directory": self._tmp.name}),
            ("extension_approve", {"name": "demo-notes"}),
            ("extension_enable", {"name": "demo-notes"}),
            ("extension_revoke", {"name": "demo-notes", "reason": "x"}),
        ]:
            result = tools.execute(name, args)
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", (name, result))
            self.assertEqual(result["kind"], "not_confirmed", (name, result))
        self.assertFalse(os.path.exists(store))

    def test_hostile_manifest_refused_through_the_tool(self):
        self._approve()
        hostile = _result(tools.execute(
            "extension_register",
            {"manifest_json": json.dumps(_manifest(
                licence="GPL-3.0", declared_files=["../evil.txt"])),
             "directory": self._tmp.name}))
        self.assertFalse(hostile["ok"])
        self.assertTrue(any("GPL" in e or "escapes" in e
                            for e in hostile["errors"]))

    def test_register_approve_enable_through_the_tools(self):
        self._approve()
        registered = _result(tools.execute(
            "extension_register",
            {"manifest_json": json.dumps(_manifest()),
             "directory": self._tmp.name}))
        self.assertTrue(registered["ok"])
        approved = _result(tools.execute(
            "extension_approve", {"name": "demo-notes"}))
        self.assertTrue(approved["ok"])
        self.assertEqual(approved["state"], "approved")
        enabled = _result(tools.execute(
            "extension_enable", {"name": "demo-notes"}))
        self.assertTrue(enabled["ok"])
        self.assertEqual(enabled["state"], "enabled")
        status = _result(tools.execute(
            "extension_status", {"name": "demo-notes"}))
        self.assertEqual(status["state"], "enabled")

    def test_symlink_inside_declared_dir_pointing_outside_is_refused(self):
        # Integration precondition (spec §1.3): the core
        # harness resolves real paths. A symlink inside the
        # declared directory pointing outside it passes lexical
        # containment but must fail the resolved check.
        from core import extension_safety_harness as harness_mod

        real_dir = os.path.join(self._tmp.name, "real")
        outside = os.path.join(self._tmp.name, "outside")
        os.makedirs(real_dir)
        os.makedirs(outside)
        with open(os.path.join(outside, "secret.txt"), "w") as handle:
            handle.write("secret")
        os.symlink(os.path.join(outside, "secret.txt"),
                   os.path.join(real_dir, "link.txt"))
        view = {"state": "enabled",
                "declared_permissions": ["files.own.read"],
                "directory": real_dir}
        refused = harness_mod.check_path_access(view, "link.txt", "read")
        self.assertFalse(refused["ok"])
        with open(os.path.join(real_dir, "plain.txt"), "w") as handle:
            handle.write("plain")
        allowed = harness_mod.check_path_access(view, "plain.txt", "read")
        self.assertTrue(allowed["ok"])


class TestAgentCallerWired(_Wave6Base):
    def _run(self, coro):
        loop = asyncio.get_event_loop_policy().new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def _binding(self, results, calls, stop_check=None):
        """The §2.1 binding shape: a partial over a funnel +
        the resilience stop-check, driving the coordinator."""
        async def fake(goal_text, *, goal_context=None, **kwargs):
            calls.append(goal_text)
            return results[len(calls) - 1]

        return agent_mod.AgentCoordinator(
            run_goal=fake, stop_check=stop_check)

    def test_two_subgoal_job_completes_with_both_binders(self):
        calls = []
        coordinator = self._binding([
            {"status": "completed", "steps": ["did a"],
             "evidence": [{"source": "funnel", "detail": "a ran"}]},
            {"status": "completed", "steps": ["did b"],
             "evidence": [{"source": "funnel", "detail": "b ran"}]},
        ], calls)
        result = self._run(coordinator.run_job(
            "do a then b",
            [{"name": "a", "goal_text": "do a"},
             {"name": "b", "goal_text": "do b", "depends_on": ["a"]}]))
        self.assertTrue(result.completed)
        self.assertEqual(len(calls), 2)
        self.assertEqual([b.name for b in result.binders], ["a", "b"])
        self.assertIn("Job complete.", result.report)

    def test_refused_subgoal_stops_the_job_with_later_not_run(self):
        calls = []
        coordinator = self._binding([
            {"status": "refused", "reason": "C-14 refused"},
            {"status": "completed", "steps": [], "evidence": []},
        ], calls)
        result = self._run(coordinator.run_job(
            "do a then b",
            [{"name": "a", "goal_text": "do a"},
             {"name": "b", "goal_text": "do b", "depends_on": ["a"]}]))
        self.assertFalse(result.completed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.binders[1].status, "not-run")

    def test_tripped_stop_check_halts_before_the_next_subgoal(self):
        calls = []
        coordinator = self._binding([
            {"status": "completed", "steps": [], "evidence": []},
        ], calls, stop_check=lambda: "resilience tripped")
        result = self._run(coordinator.run_job(
            "do a then b",
            [{"name": "a", "goal_text": "do a"},
             {"name": "b", "goal_text": "do b"}]))
        self.assertFalse(result.completed)
        self.assertEqual(len(calls), 0)
        self.assertEqual(result.binders[0].status, "not-run")

    def test_real_loop_binding_through_planner(self):
        """One job through the actual planner.run_goal (not a
        fake funnel): the picker returns done immediately, so
        the sub-goal completes and the coordinator classifies
        it completed from the loop's own marker."""
        from core.brain import planner as planner_mod  # Phase 3 B2-remainder INTENTIONAL: planner moved to core/brain/

        async def pick_action(goal, observation, history):
            return {"done": "did it"}

        async def observe():
            return {"fg": "test", "focus": "test"}

        bound = functools.partial(
            planner_mod.run_goal, pick_action=pick_action,
            observe=observe)
        coordinator = agent_mod.AgentCoordinator(run_goal=bound)
        result = self._run(coordinator.run_job(
            "do one thing", [{"name": "only", "goal_text": "do one thing"}]))
        self.assertTrue(result.completed)
        self.assertEqual(result.binders[0].status, "completed")

    def test_clients_carry_the_plan_binding(self):
        """AST-level: both text clients gained the plan
        parameter and construct the coordinator from the
        staged-plan holder (the GUI session run itself is
        field proof, not a lab claim)."""
        root = Path(__file__).resolve().parent.parent
        for fname in ("core/voice/cloud_text_client.py", "core/voice/local_client.py"):
            # B39 INTENTIONAL: run_goal moved to the goal mixin; for
            # the cloud client the pin reads facade + goal-mixin
            # text (a superset), same assertions, never weakened.
            if fname == "core/voice/cloud_text_client.py":
                src = ((root / fname).read_text(encoding="utf-8")
                       + (root / "core/voice/cloud_text_goal.py")
                       .read_text(encoding="utf-8"))
            elif fname == "core/voice/local_client.py":
                # BATCH 4 INTENTIONAL: run_goal moved to the plan-goal
                # mixin; the pin reads facade + mixin text (a
                # superset), same assertions, never weakened.
                src = ((root / fname).read_text(encoding="utf-8")
                       + (root / "core/voice/local_client_plangoal.py")
                       .read_text(encoding="utf-8"))
            else:
                src = (root / fname).read_text(encoding="utf-8")
            # INTENTIONAL (voice-brain adoption): run_goal gained
            # an optional interruption_check kwarg (voice wrapper's
            # cancel/session-stop check, threaded to the planner's
            # step boundaries). The plan binding is unchanged — the
            # pin follows the signature, it does not weaken it.
            # P-3 (debug pass): run_goal gained an optional
            # search kwarg (search=None) in both the cloud goal
            # mixin and the local plan-goal mixin.
            self.assertIn(
                "async def run_goal(self, goal_text: str, plan=None,\n"
                "                       interruption_check=None, search=None)",
                src, fname)
            self.assertIn("AgentCoordinator", src, fname)
            self.assertIn("consume_plan_for_goal", src, fname)

    def test_mission_packet_is_the_agent_packet_plus_two_keys(self):
        """The Build 5 fold: no second packet builder in
        production — AGENT's builder carries mission_brief +
        delegation when supplied, and neither key otherwise."""
        subgoal = agent_mod.SubGoal("s1", "do s1")
        plain = agent_mod.build_handoff_packet([], subgoal)
        self.assertNotIn("mission_brief", plain)
        self.assertNotIn("delegation", plain)
        folded = agent_mod.build_handoff_packet(
            [], subgoal, mission_brief={"goal": "mission"},
            delegation={"level": "consequential-only"})
        self.assertEqual(folded["mission_brief"], {"goal": "mission"})
        self.assertEqual(folded["delegation"],
                         {"level": "consequential-only"})


class TestWorkToolsWired(_Wave6Base):
    def _distilled(self):
        return _result(tools.execute(
            "workflow_distil",
            {"jobs_json": json.dumps([_proven_job()])}))

    def test_distil_and_plan_through_execute(self):
        workflow = self._distilled()
        self.assertEqual(workflow["name"], "learned-workflow")
        self.assertEqual(len(workflow["steps"]), 2)
        self.assertEqual(workflow["steps"][0]["source_job_id"], "job-1")
        self.assertEqual(
            workflow["steps"][0]["evidence_ref"]["proof_reference"],
            "field-run-001")
        plan = _result(tools.execute(
            "workflow_build_plan",
            {"workflow_json": json.dumps(workflow)}))
        self.assertTrue(plan["proposed_only"])
        self.assertTrue(plan["executes_nothing"])
        # The plan passes AGENT's own split_plan validation.
        agent_mod.split_plan(plan["goal_text"], plan["plan"])
        steps = _result(tools.execute(
            "workflow_to_playbook_steps",
            {"workflow_json": json.dumps(workflow)}))
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["proof_reference"], "field-run-001")

    def test_unproven_input_refuses_whole_through_the_tool(self):
        result = _result(tools.execute(
            "workflow_distil",
            {"jobs_json": json.dumps([_proven_job(field_proven=False)])}))
        self.assertEqual(result["status"], "error")
        self.assertIn("field_proven", result["message"])

    def test_approval_claim_stripped_through_the_tool(self):
        job = _proven_job(steps=["open settings",
                                 {"text": "send a message",
                                  "approved": True}])
        workflow = _result(tools.execute(
            "workflow_distil", {"jobs_json": json.dumps([job])}))
        send_step = [s for s in workflow["steps"]
                     if s["text"] == "send a message"][0]
        self.assertNotIn("approved", send_step)
        self.assertIn("C-14", send_step.get("gate_requirement") or "")


class TestMissToolsWired(_Wave6Base):
    def _workflow(self):
        return _result(tools.execute(
            "workflow_distil",
            {"jobs_json": json.dumps([_proven_job()])}))

    def test_compound_capability_and_playbook_through_execute(self):
        workflow = self._workflow()
        compound = _result(tools.execute(
            "miss_compound",
            {"workflows_json": json.dumps([workflow])}))
        self.assertEqual([s["text"] for s in compound["steps"]],
                         ["open settings", "toggle sounds"])
        cmap = _result(tools.execute(
            "miss_capability_map",
            {"workflows_json": json.dumps([workflow])}))
        self.assertEqual(len(cmap["capabilities"]), 1)
        self.assertEqual(cmap["unproven"], [])
        steps = _result(tools.execute(
            "miss_to_playbook_steps",
            {"compound_json": json.dumps(compound)}))
        self.assertEqual(len(steps), 2)

    def test_mixed_input_refuses_whole_through_the_tool(self):
        workflow = self._workflow()
        bad = {"name": "unproven-workflow", "steps": [
            {"text": "do something", "source_job_id": "",
             "evidence_ref": {}}]}
        result = _result(tools.execute(
            "miss_compound",
            {"workflows_json": json.dumps([workflow, bad])}))
        self.assertEqual(result["status"], "error")

    def test_claim_without_workflow_lands_in_unproven(self):
        cmap = _result(tools.execute(
            "miss_capability_map",
            {"workflows_json": json.dumps(
                [{"name": "ghost", "steps": []}])}))
        self.assertEqual(cmap["capabilities"], [])
        self.assertEqual(len(cmap["unproven"]), 1)

    def test_deletion_step_refuses_whole_through_the_tool(self):
        compound = {"name": "c", "steps": [
            {"id": "1", "text": "delete the file",
             "source_job_id": "job-1",
             "evidence_ref": {"proof_reference": "field-run-001"}}]}
        result = _result(tools.execute(
            "miss_to_playbook_steps",
            {"compound_json": json.dumps(compound)}))
        self.assertEqual(result["status"], "error")


class TestMissionToolsWired(_Wave6Base):
    def _workflow(self):
        return _result(tools.execute(
            "workflow_distil",
            {"jobs_json": json.dumps([_proven_job()])}))

    def _brief(self, workflow):
        return _result(tools.execute(
            "mission_build_brief",
            {"goal": "Run the sounds workflow",
             "workflows_json": json.dumps([workflow])}))

    def test_brief_plan_board_journal_through_execute(self):
        workflow = self._workflow()
        brief = self._brief(workflow)
        self.assertEqual(brief["goal"], "Run the sounds workflow")
        self.assertEqual(len(brief["known_facts"]), 2)
        plan = _result(tools.execute(
            "mission_build_plan",
            {"brief_json": json.dumps(brief),
             "workflows_json": json.dumps([workflow])}))
        agent_mod.split_plan(plan["goal_text"], plan["plan"])
        self.assertEqual(plan["mission_brief"]["goal"],
                         "Run the sounds workflow")
        board = _result(tools.execute(
            "mission_evidence_board",
            {"mission": "sounds",
             "receipts_or_binders_json": json.dumps([{
                 "sub_goal": "step-1", "goal_text": "open settings",
                 "status": "completed",
                 "evidence_refs": [{"source": "field-run",
                                    "detail": "ran on his machine"}]}])}))
        self.assertFalse(board["stored"])
        self.assertEqual(len(board["entries"]), 1)
        entry = _result(tools.execute(
            "mission_journal_entry",
            {"decision": "use the distilled workflow",
             "options_json": json.dumps(["use it", "skip it"]),
             "evidence_cited_json": json.dumps(
                 [{"source": "field-run", "detail": "ran on his machine"}]),
             "approved_by": "Aayur",
             "timestamp": "2026-10-26T12:00:00Z"}))
        self.assertEqual(entry["approved_by"], "Aayur")
        appended = _result(tools.execute(
            "mission_journal_append",
            {"entry_json": json.dumps(entry)}))
        self.assertEqual(appended["entries"], 1)
        journal = json.loads(Path(
            os.path.join(self._tmp.name, "mission_journal.json")
        ).read_text(encoding="utf-8"))
        self.assertEqual(len(journal), 1)

    def test_brief_refuses_on_unproven_workflow(self):
        bad = {"name": "bad", "steps": [
            {"text": "do something", "source_job_id": "",
             "evidence_ref": {}}]}
        result = _result(tools.execute(
            "mission_build_brief",
            {"goal": "Run something",
             "workflows_json": json.dumps([bad])}))
        self.assertEqual(result["status"], "error")

    def test_run_job_refuses_without_a_handler_and_stages_nothing(self):
        workflow = self._workflow()
        brief = self._brief(workflow)
        plan = _result(tools.execute(
            "mission_build_plan",
            {"brief_json": json.dumps(brief),
             "workflows_json": json.dumps([workflow])}))
        result = tools.execute(
            "mission_run_job",
            {"goal": "Run the sounds workflow",
             "plan_json": json.dumps(plan["plan"])})
        # L3: INTENTIONAL assertion update, not a weakening — the
        # funnel never emits legacy status "error" since audit A5
        # (core/funnel/tool_result.py): _audited() normalizes by kind, and
        # "not_confirmed" is a refusal kind, so the refusal of an
        # unconfirmed mission job carries status "refused" (the
        # test's own name already says "refuses"). Kind and the
        # stages-nothing assertion are unchanged.
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["kind"], "not_confirmed")
        self.assertIsNone(jobs_mod.peek_staged_plan())

    def test_run_job_stages_once_and_consumes_once(self):
        self._approve()
        workflow = self._workflow()
        brief = self._brief(workflow)
        plan = _result(tools.execute(
            "mission_build_plan",
            {"brief_json": json.dumps(brief),
             "workflows_json": json.dumps([workflow])}))
        staged = _result(tools.execute(
            "mission_run_job",
            {"goal": "Run the sounds workflow",
             "plan_json": json.dumps(plan["plan"])}))
        self.assertTrue(staged["staged"])
        self.assertEqual(staged["sub_goals"], ["step-1", "step-2"])
        # The client binding consumes the staged plan once
        # through the coordinator (counting fake funnel).
        calls = []

        async def fake(goal_text, *, goal_context=None, **kwargs):
            calls.append(goal_text)
            return {"status": "completed", "steps": ["did it"],
                    "evidence": [{"source": "funnel", "detail": "ran"}]}

        coordinator = agent_mod.AgentCoordinator(run_goal=fake)
        consumed = jobs_mod.consume_plan_for_goal("Run the sounds workflow")
        self.assertIsNotNone(consumed)
        result = _run_coro(coordinator.run_job(
            "Run the sounds workflow", consumed["plan"]))
        self.assertTrue(result.completed)
        self.assertEqual(len(calls), 2)
        # A second goal run without a new stage behaves as a
        # single goal: the holder is empty.
        self.assertIsNone(jobs_mod.consume_plan_for_goal(
            "Run the sounds workflow"))

    def test_waiving_delegation_refuses_never_downgrades(self):
        self._approve()
        workflow = self._workflow()
        brief = self._brief(workflow)
        plan = _result(tools.execute(
            "mission_build_plan",
            {"brief_json": json.dumps(brief),
             "workflows_json": json.dumps([workflow])}))
        result = _result(tools.execute(
            "mission_run_job",
            {"goal": "Run the sounds workflow",
             "plan_json": json.dumps(plan["plan"]),
             "delegation": "auto-approve"}))
        self.assertEqual(result["status"], "error")
        self.assertIsNone(jobs_mod.peek_staged_plan())


class TestNoSecondGateOwner(unittest.TestCase):
    def test_integration_modules_import_no_gate(self):
        """The wiring adds no new gate module: the two
        integration modules import no gate at all — every
        refusal traces to tools_gate classification,
        confirm_gate approval, the resilience_gate trip, or
        a staging module's own fail-closed validation."""
        root = Path(__file__).resolve().parent.parent
        # INTENTIONAL (Phase 2 B2-1 rename): path moved with its module (was "core/mission_jobs_w6.py").
        # INTENTIONAL (skills split B11): facade-only scan would pass
        # vacuously for moved code, so the impl modules are scanned too
        # (B1 pattern) — same assertion, strictly stronger coverage.
        for fname in ("core/integration_harness_tools.py",
                      "core/integration_harness_tools_helpers.py",
                      "core/integration_harness_tools_extensions.py",
                      "core/integration_harness_tools_workflow.py",
                      "core/integration_harness_tools_handlers.py",
                      "core/integration_harness_tools_register.py",
                      "core/mission_jobs.py"):
            tree = ast.parse((root / fname).read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imported.add(node.module)
            gate_imports = [name for name in imported if "gate" in name]
            self.assertEqual(gate_imports, [], fname)

    def test_doctor_card_exists_without_renumbering(self):
        """The one GUI row: an Extensions card on the existing
        Doctor page, with Approve/Enable/Revoke handlers."""
        root = Path(__file__).resolve().parent.parent
        src = (root / "gui/doctor_page.py").read_text(encoding="utf-8")
        self.assertIn("Extensions (harness)", src)
        self.assertIn("_on_ext_approve", src)
        self.assertIn("_on_ext_enable", src)
        self.assertIn("_on_ext_revoke", src)


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
