"""Tests for Wave 10 Build 3: P23 first-run cluster
descriptors — clean exit, three-minute first command,
no-setup mode, fake-data playground, fix-my-setup
checklist.

What is proven here:
* every descriptor echoes exactly caller-supplied
  inputs, with exited/executed/applied/installed/
  settings_changed/sent False and no claim beyond
  the supplied facts;
* the clean-exit record describes an exit and never
  performs one, kills a process, or deletes user
  data; the first-command guide never executes the
  command itself; the no-setup record never claims a
  capability that needs setup, a key, or an install;
  the playground is fake throughout, never real user
  data, never sent, never presented as real; the
  fix-my-setup checklist performs none of its checks
  or fixes, changes no setting, installs nothing;
* send/publish/install/apply/execute/run/deploy,
  completed-action claims (fixed, installed, exited,
  shipped, sent, executed, deleted), ruling-34
  (payment/tier/trial/refund/tax), legal/compliance/
  conformance, and real-user-data inputs refuse WHOLE,
  never partially;
* no subprocess, network, install, or settings-write
  import — AST over imports (not prose);
* the 2 tools are registered with the right
  classifications, GROUPED in GROUP_MEMBERS with
  friendly plain-language names, and behave through
  the funnel with and without a confirm handler;
* no first-run tool name trips the money guard;
* the fitted-list breach is RETIRED, not hidden:
  the legacy all-direct builder was removed by owner
  ruling (DECL-01, 2026-11-01); this build's two tools'
  descriptions still lead with their safety clause
  (nothing trimmed, gutted, or unwired to chase bytes,
  and the cap was not raised).
"""

import ast
import json
import re
import tempfile
import unittest
from pathlib import Path

from core import firstrun as firstrun_mod
from core import firstrun_tools as firstrun_tools
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


FIRSTRUN_TOOLS = ("firstrun_describe", "firstrun_record")


class DescriptorTests(unittest.TestCase):
    def test_exit_echo_and_flags(self):
        out = firstrun_mod.describe(
            "exit", items=["voice session closed at exit",
                           "no temp files remain"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "CLEAN_EXIT")
        self.assertEqual(out["items"],
                         ["voice session closed at exit",
                          "no temp files remain"])
        self.assertFalse(out["exited"])
        self.assertFalse(out["performed"])
        self.assertFalse(out["processes_killed"])
        self.assertFalse(out["data_deleted"])
        self.assertIn("not legal or tax advice", out["caveat"])
        self.assertIn("except the transaction surface",
                      out["exit_wording"])

    def test_first_command_echo_not_executed(self):
        out = firstrun_mod.describe(
            "first_command", command="say hello",
            steps=["type the command", "press Enter",
                   "read the reply"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "FIRST_COMMAND")
        self.assertEqual(out["command"], "say hello")
        self.assertEqual(out["steps"][2], "read the reply")
        self.assertFalse(out["executed"])
        self.assertTrue(out["fits_about_three_minutes"])

    def test_nosetup_echo_no_setup_needed(self):
        out = firstrun_mod.describe(
            "nosetup",
            capabilities=["voice commands out of the box",
                          "local notes"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "NO_SETUP_MODE")
        self.assertFalse(out["setup_required"])
        self.assertFalse(out["needs_key"])
        self.assertFalse(out["needs_install"])

    def test_playground_fixed_examples_when_no_samples(self):
        out = firstrun_mod.describe("playground")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "FAKE_DATA_PLAYGROUND")
        self.assertTrue(out["fake"])
        self.assertFalse(out["real"])
        self.assertFalse(out["sent"])
        self.assertTrue(all("Example" in sample["label"]
                            for sample in out["samples"]))

    def test_playground_caller_samples_marked_fake(self):
        out = firstrun_mod.describe(
            "playground",
            samples=[{"label": "Example pet", "value": "Example cat"}])
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["fake"])
        self.assertFalse(out["real"])
        self.assertFalse(out["sent"])
        self.assertEqual(out["samples"][0]["value"], "Example cat")

    def test_fix_checklist_order_and_flags(self):
        out = firstrun_mod.describe(
            "fix", symptoms=["app window stays blank"],
            checks=[{"check": "look at the voice card",
                     "instruction": "open Settings and look at the voice card"},
                    {"check": "look at the microphone level",
                     "instruction": "open Settings and look at the level"}])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "FIX_MY_SETUP_CHECKLIST")
        self.assertEqual(out["checks"][1]["check"],
                         "look at the microphone level")
        self.assertFalse(out["applied"])
        self.assertFalse(out["settings_changed"])
        self.assertFalse(out["installed"])

    def test_unknown_kind_and_wrong_fields_refuse(self):
        self.assertFalse(firstrun_mod.describe("store")["ok"])
        self.assertFalse(firstrun_mod.describe(
            "exit", items=["a"], command="say hello")["ok"])
        self.assertFalse(firstrun_mod.describe(
            "first_command", command="say hello")["ok"])
        self.assertFalse(firstrun_mod.describe(
            "fix", symptoms=["s"], checks=[])["ok"])

    def test_non_describe_action_refuses(self):
        self.assertFalse(firstrun_mod.describe(
            "exit", items=["a"], action="run")["ok"])
        self.assertFalse(firstrun_mod.describe(
            "fix", symptoms=["s"],
            checks=[{"check": "c", "instruction": "i"}],
            action="apply")["ok"])

    def test_action_words_refuse_whole(self):
        self.assertFalse(firstrun_mod.describe(
            "exit", items=["we will send you a report"])["ok"])
        self.assertFalse(firstrun_mod.describe(
            "first_command", command="install helper",
            steps=["type the command"])["ok"])
        self.assertFalse(firstrun_mod.describe(
            "nosetup", capabilities=["notes that upload daily"])["ok"])

    def test_completed_claims_refuse_whole(self):
        self.assertFalse(firstrun_mod.describe(
            "exit", items=["exit performed cleanly"])["ok"])
        self.assertFalse(firstrun_mod.describe(
            "fix", symptoms=["already fixed"],
            checks=[{"check": "c", "instruction": "i"}])["ok"])
        self.assertFalse(firstrun_mod.describe(
            "fix", symptoms=["s"],
            checks=[{"check": "c",
                     "instruction": "fix applied already"}])["ok"])
        self.assertFalse(firstrun_mod.describe(
            "first_command", command="say hello",
            steps=["command executed"])["ok"])

    def test_setup_needing_capability_refuses_as_nosetup(self):
        self.assertFalse(firstrun_mod.describe(
            "nosetup",
            capabilities=["voice notes with an api key"])["ok"])
        self.assertFalse(firstrun_mod.describe(
            "nosetup",
            capabilities=["notes that need setup first"])["ok"])

    def test_real_user_data_refuses_in_playground(self):
        self.assertFalse(firstrun_mod.describe(
            "playground",
            samples=[{"label": "Example pet",
                      "value": "my password is hunter2"}])["ok"])
        self.assertFalse(firstrun_mod.describe(
            "playground",
            samples=[{"label": "Example pet", "value": "real user data"}])["ok"])
        self.assertFalse(firstrun_mod.describe(
            "playground",
            samples=[{"label": "Example pet"}])["ok"])
        self.assertFalse(firstrun_mod.describe(
            "playground",
            samples=[{"label": "Example pet", "value": "Example cat",
                      "owner": "someone"}])["ok"])

    def test_ruling34_and_conformance_refuse(self):
        self.assertFalse(firstrun_mod.describe(
            "exit", items=["clear pricing tiers"])["ok"])
        self.assertFalse(firstrun_mod.describe(
            "nosetup",
            capabilities=["wcag conformance included"])["ok"])
        self.assertFalse(firstrun_mod.describe(
            "exit", items=["refund help included"])["ok"])

    def test_empty_and_nonlist_fields_refuse(self):
        self.assertFalse(firstrun_mod.describe("exit", items=[])["ok"])
        self.assertFalse(firstrun_mod.describe(
            "exit", items="not a list")["ok"])
        self.assertFalse(firstrun_mod.describe(
            "first_command", command="  ",
            steps=["type the command"])["ok"])
        self.assertFalse(firstrun_mod.describe(
            "fix", symptoms=["s"],
            checks=[{"check": "c"}])["ok"])

    def test_no_forbidden_imports_ast(self):
        banned = {"socket", "requests", "urllib", "smtplib",
                  "email", "http", "ftplib", "stripe", "paypal",
                  "subprocess", "os", "shutil", "winreg"}
        for filename in ("core/firstrun.py",
                         "core/firstrun_tools.py"):
            path = Path(__file__).resolve().parent.parent / filename
            tree = ast.parse(path.read_text())
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0]
                                    for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imported.add(node.module.split(".")[0])
            self.assertFalse(imported & banned, (filename, imported))
        module = ast.parse(
            (Path(__file__).resolve().parent.parent
             / "core/firstrun.py").read_text())
        imports = set()
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                imports.update(a.name.split(".")[0]
                               for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertEqual(imports, {"__future__", "re"})


class WiringTests(unittest.TestCase):
    def setUp(self):
        self._confirm_gate = confirm_gate
        self._confirm_gate.register_handler(None)

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in FIRSTRUN_TOOLS:
            self.assertIn(name, names)
        self.assertIn("firstrun_describe", gate._READ_ONLY_TOOLS)
        self.assertIn("firstrun_record", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["firstrun_record"],
            ("output_path",))
        # Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 756)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in FIRSTRUN_TOOLS:
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        self.assertEqual(names["firstrun_describe"], "First run")
        self.assertEqual(names["firstrun_record"], "Record first run")

    def test_readonly_tools_through_funnel(self):
        desc = _result(tools.execute("firstrun_describe", {
            "kind": "exit",
            "record_json": json.dumps(
                {"items": ["voice session closed at exit",
                           "no temp files remain"]})}))
        self.assertTrue(desc["ok"], desc)
        self.assertEqual(desc["kind"], "CLEAN_EXIT")
        self.assertFalse(desc["exited"])
        self.assertFalse(desc["performed"])
        guide = _result(tools.execute("firstrun_describe", {
            "kind": "first_command",
            "record_json": json.dumps(
                {"command": "say hello",
                 "steps": ["type the command", "press Enter",
                           "read the reply"]})}))
        self.assertTrue(guide["ok"], guide)
        self.assertFalse(guide["executed"])
        playground = _result(tools.execute("firstrun_describe", {
            "kind": "playground"}))
        self.assertTrue(playground["ok"], playground)
        self.assertTrue(playground["fake"])
        self.assertFalse(playground["real"])
        # A handler-level refusal flows through the funnel
        # as a success envelope carrying ok False — the
        # refusal itself is in the result, not the envelope.
        refused = _result(tools.execute("firstrun_describe", {
            "kind": "nosetup",
            "record_json": json.dumps(
                {"capabilities": ["notes with an api key"]})}))
        self.assertFalse(refused["ok"], refused)

    def test_tool_names_do_not_trip_money_guard(self):
        result = tools.execute("firstrun_describe", {
            "kind": "exit",
            "record_json": json.dumps(
                {"items": ["voice session closed at exit"]})})
        data = _result(result)
        self.assertTrue(data["ok"], data)

    def test_record_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "firstrun.json")
            result = tools.execute("firstrun_record", {
                "record_kind": "exit",
                "record_json": json.dumps(
                    {"items": ["voice session closed at exit"]}),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "firstrun.json")
            data = _result(tools.execute("firstrun_record", {
                "record_kind": "fix",
                "record_json": json.dumps(
                    {"symptoms": ["app window stays blank"],
                     "checks": [{"check": "look at the voice card",
                                 "instruction": "open Settings and look "
                                                "at the voice card"}]}),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["exited"])
            self.assertFalse(data["executed"])
            self.assertFalse(data["applied"])
            self.assertFalse(data["installed"])
            self.assertFalse(data["sent"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["firstrun"]["kind"],
                             "FIX_MY_SETUP_CHECKLIST")
            self.assertFalse(written["applied"])
            self.assertFalse(written["conformance_claim"])
            self.assertEqual(sorted(Path(tmp).iterdir()), [Path(target)])

    def test_record_refuses_tampered_or_claiming(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "firstrun.json")
            for kind, record in (
                    ("exit", {"items": ["a"], "exited": True}),
                    ("first_command", {"command": "say hello",
                                       "steps": ["command executed"]}),
                    ("nosetup", {"capabilities": ["notes with an api key"]}),
                    ("playground", {"samples": [
                        {"label": "Example pet",
                         "value": "my password is hunter2"}]}),
                    ("fix", {"symptoms": ["s"],
                            "checks": [{"check": "c",
                                        "instruction": "fix applied already"}]}),
                    ("exit", {"items": ["clear pricing tiers"]})):
                data = _result(tools.execute("firstrun_record", {
                    "record_kind": kind,
                    "record_json": json.dumps(record),
                    "output_path": target}))
                self.assertFalse(data["ok"], (kind, data))
            self.assertFalse(Path(target).exists())

    def test_record_refuses_bad_output_paths(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        tree = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            for target in (str(tree / "data" / "firstrun.json"),
                           str(tree / "core" / "firstrun.json"),
                           str(Path(tmp) / "firstrun.txt"),
                           str(Path(tmp) / "missing" / "firstrun.json"),
                           str(Path(tmp) / "key.pem")):
                data = _result(tools.execute("firstrun_record", {
                    "record_kind": "exit",
                    "record_json": json.dumps(
                        {"items": ["voice session closed at exit"]}),
                    "output_path": target}))
                # Refusal may arrive as the handler's ok False
                # or as the funnel's refusal envelope (status
                # refused since Audit A5's P1-10 vocabulary;
                # legacy status error accepted too) when the
                # funnel blocks the path before the handler
                # runs — either way, nothing was recorded.
                self.assertTrue(data.get("ok") is False
                                or data.get("status") in ("error",
                                                          "refused"),
                                (target, data))

    def test_new_tool_descriptions_lead_with_safety_clause(self):
        """DECL-01 (owner ruling 2026-11-01): the legacy
        fitted builder is retired, so this test pins only
        that the new tools' registered descriptions still
        lead with their safety clause (nothing was trimmed,
        gutted, or unwired to chase bytes)."""
        decls = {d["name"]: d for d in tools.get_declarations()}
        self.assertTrue(
            decls["firstrun_describe"]["description"].startswith(
                "DESCRIBE ONLY, NOTHING EXITED, RUN, FIXED, "
                "INSTALLED, OR SENT"))
        self.assertTrue(
            decls["firstrun_record"]["description"].startswith(
                "RECORD ONLY, NOTHING EXITED, RUN, FIXED, "
                "INSTALLED, OR SENT"))


if __name__ == "__main__":
    unittest.main()
