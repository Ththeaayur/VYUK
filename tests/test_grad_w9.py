"""Tests for Wave 9 Build 10: queued graduations 1 —
TENSION graduation step 1 (continuous-display gate
enforcement, NO capture) plus graduation ledger.

What is proven here:
* the gate reuses the first-open gate: enabled only on
  a caller-supplied record saying allowed=True for
  exactly continuous-display, with a non-empty chooser,
  timestamp, retention rule, and deletion rule — and
  the answer states the recorded retention rule and
  the indicator requirement with it;
* missing, False, wrong-scope, or incomplete records
  return disabled with a stated reason, never
  defaulted on;
* the retention descriptor states the 7-day figure as
  builder-chosen, NOT ruled by the owner, unless the
  caller supplies days;
* the indicator descriptor states the on-screen sign,
  that switching off stops new watching but does not
  delete what was kept, and that PF-3 is still open;
* the ledger entry states evidence plus form plus
  still-owed and is never a claim the item is built,
  shipped, or field-proven;
* capture-shaped, global-delete, built/shipped/
  field-proven, and PF-3-decided inputs refuse WHOLE,
  never partially;
* no watcher, screen, clipboard, keystroke, or sensor
  import — AST over imports (not prose);
* the 2 tools are registered with the right
  classifications, GROUPED in GROUP_MEMBERS with
  friendly plain-language names, and behave through
  the funnel with and without a confirm handler;
* no graduation tool name trips the money guard (the
  Build 8 pilot_bench lesson: a separator-stripped
  blob can contain a banned token across a name join);
* the legacy fitted builder is RETIRED (DECL-01, owner
  ruling 2026-11-01): the new tools' canonical
  descriptions still lead with their safety clause.
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from core import grad as grad_mod
from core import grad_tools as grad_tools
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


GRAD_TOOLS = ("grad_describe", "grad_record")

GOOD_RECORD = {
    "allowed": True, "chooser": "the user",
    "scope": "continuous-display",
    "timestamp": "2026-10-26T22:00:00+05:30",
    "retention_rule": "kept notes are thrown away after 7 days",
    "deletion_rule": "you can delete everything it kept at any time",
}


class GateTests(unittest.TestCase):
    def test_gate_enabled_record_states_rule_and_indicator(self):
        desc = grad_mod.describe_gate(**GOOD_RECORD)
        self.assertTrue(desc["ok"], desc)
        self.assertTrue(desc["enabled"])
        self.assertEqual(desc["retention_rule"],
                         GOOD_RECORD["retention_rule"])
        self.assertEqual(desc["deletion_rule"],
                         GOOD_RECORD["deletion_rule"])
        self.assertTrue(desc["indicator_required"])
        self.assertIn("small sign", desc["indicator"])
        self.assertTrue(desc["local_only"])
        self.assertTrue(desc["default_off"])
        self.assertFalse(desc["captured"])
        self.assertFalse(desc["granted"])

    def test_gate_missing_record_is_disabled_not_defaulted(self):
        desc = grad_mod.describe_gate()
        self.assertTrue(desc["ok"], desc)
        self.assertFalse(desc["enabled"])
        self.assertIn("not allowed", desc["reason"])

    def test_gate_false_record_is_disabled(self):
        record = dict(GOOD_RECORD, allowed=False)
        desc = grad_mod.describe_gate(**record)
        self.assertFalse(desc["enabled"])
        self.assertIn("not allowed", desc["reason"])

    def test_gate_wrong_scope_is_disabled(self):
        record = dict(GOOD_RECORD, scope="suggestion-cards")
        desc = grad_mod.describe_gate(**record)
        self.assertFalse(desc["enabled"])
        self.assertIn("suggestion-cards", desc["reason"])

    def test_gate_missing_rule_is_disabled(self):
        record = dict(GOOD_RECORD, retention_rule="")
        desc = grad_mod.describe_gate(**record)
        self.assertFalse(desc["enabled"])
        record = dict(GOOD_RECORD, deletion_rule="")
        desc = grad_mod.describe_gate(**record)
        self.assertFalse(desc["enabled"])
        record = dict(GOOD_RECORD, chooser="")
        desc = grad_mod.describe_gate(**record)
        self.assertFalse(desc["enabled"])

    def test_gate_everywhere_deletion_rule_refuses_whole(self):
        record = dict(GOOD_RECORD,
                      deletion_rule="delete everything everywhere")
        desc = grad_mod.describe_gate(**record)
        self.assertFalse(desc["ok"])
        self.assertIn("PF-3", desc["reason"])

    def test_gate_capture_shaped_input_refuses_whole(self):
        record = dict(GOOD_RECORD, form="screenshot history")
        desc = grad_mod.describe_gate(**record)
        self.assertFalse(desc["ok"])
        record = dict(GOOD_RECORD, scope="capture")
        desc = grad_mod.describe_gate(**record)
        self.assertFalse(desc["ok"])

    def test_gate_action_other_than_describe_refuses(self):
        desc = grad_mod.describe_gate(action="capture")
        self.assertFalse(desc["ok"])
        desc = grad_mod.describe("gate", action="watch")
        self.assertFalse(desc["ok"])


class RetentionIndicatorTests(unittest.TestCase):
    def test_retention_default_is_seven_and_not_owner_ruled(self):
        desc = grad_mod.describe_retention()
        self.assertTrue(desc["ok"], desc)
        self.assertEqual(desc["retention_days"], 7)
        self.assertEqual(desc["rule"],
                         "kept notes are thrown away after 7 days")
        self.assertFalse(desc["supplied_by_caller"])
        self.assertFalse(desc["owner_ruled"])
        self.assertIn("NOT ruled by the owner", desc["note"])
        self.assertIn("builder", desc["note"])

    def test_retention_supplied_days_recorded_as_supplied(self):
        desc = grad_mod.describe_retention(30)
        self.assertEqual(desc["retention_days"], 30)
        self.assertEqual(desc["rule"],
                         "kept notes are thrown away after 30 days")
        self.assertTrue(desc["supplied_by_caller"])
        self.assertFalse(desc["owner_ruled"])
        desc = grad_mod.describe_retention(1)
        self.assertEqual(desc["rule"],
                         "kept notes are thrown away after 1 day")

    def test_retention_bad_figures_refuse_whole(self):
        for bad in (0, -5, 1.5, "30", True, 99999):
            desc = grad_mod.describe_retention(bad)
            self.assertFalse(desc["ok"], bad)

    def test_indicator_states_sign_stop_and_pf3_open(self):
        desc = grad_mod.describe_indicator()
        self.assertTrue(desc["ok"], desc)
        self.assertTrue(desc["indicator_required"])
        self.assertIn("small sign", desc["indicator"])
        self.assertTrue(desc["turn_off_stops_new_watching"])
        self.assertFalse(desc["turn_off_deletes_kept_data"])
        self.assertIn("PF-3", desc["global_delete_rule"])
        self.assertIn("still open", desc["global_delete_rule"])


class LedgerTests(unittest.TestCase):
    def test_ledger_entry_echoes_and_never_claims_built(self):
        entry = grad_mod.build_ledger_entry(
            "TENSION-1",
            "21:59 ruling + first-open opt-in build",
            "gate enforcement descriptor, nothing watched",
            ["PF-3 still open", "actual capture feature",
             "owner eye verdict on prompt wording",
             "field proof"])
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(entry["item_id"], "TENSION-1")
        self.assertEqual(entry["evidence"],
                         "21:59 ruling + first-open opt-in build")
        self.assertEqual(len(entry["still_owed"]), 4)
        self.assertFalse(entry["built"])
        self.assertFalse(entry["shipped"])
        self.assertFalse(entry["field_proven"])
        self.assertIn("not a claim", entry["note"])

    def test_ledger_missing_or_malformed_fields_refuse(self):
        base = ("TENSION-1", "21:59 ruling",
                "gate enforcement descriptor")
        for kwargs in (
                {"item_id": "", "evidence": "e", "form": "f",
                 "still_owed": []},
                {"item_id": "TENSION-1", "evidence": "",
                 "form": "f", "still_owed": []},
                {"item_id": "TENSION-1", "evidence": "e",
                 "form": "", "still_owed": []},
                {"item_id": "TENSION-1", "evidence": "e",
                 "form": "f", "still_owed": "PF-3"},
                {"item_id": "TENSION-1", "evidence": "e",
                 "form": "f", "still_owed": [""]},
        ):
            entry = grad_mod.build_ledger_entry(
                kwargs["item_id"], kwargs["evidence"],
                kwargs["form"], kwargs["still_owed"])
            self.assertFalse(entry["ok"], kwargs)

    def test_ledger_completion_claims_refuse_whole(self):
        for form in ("the item is built", "already shipped",
                     "field-proven in the lab",
                     "PF-3 decided, delete everywhere"):
            entry = grad_mod.build_ledger_entry(
                "TENSION-1", "21:59 ruling", form, ["PF-3"])
            self.assertFalse(entry["ok"], form)

    def test_ledger_record_rebuilds_and_tamper_refuses(self):
        fields = {"item_id": "TENSION-1",
                  "evidence": "21:59 ruling",
                  "form": "gate enforcement descriptor",
                  "still_owed": ["PF-3 still open"]}
        rebuilt = grad_mod.validate_recordable_graduation(fields)
        self.assertEqual(rebuilt["item_id"], "TENSION-1")
        self.assertFalse(rebuilt["built"])
        with self.assertRaises(ValueError):
            grad_mod.validate_recordable_graduation(
                dict(fields, built=True))
        with self.assertRaises(ValueError):
            grad_mod.validate_recordable_graduation(
                dict(fields, form="the item is built"))


class DispatchTests(unittest.TestCase):
    def test_dispatch_unknown_kind_and_wrong_fields_refuse(self):
        self.assertFalse(grad_mod.describe("capture")["ok"])
        self.assertFalse(grad_mod.describe(
            "gate", retention_days=7)["ok"])
        self.assertFalse(grad_mod.describe(
            "retention", item_id="TENSION-1")["ok"])
        self.assertFalse(grad_mod.describe(
            "indicator", form="x")["ok"])
        self.assertFalse(grad_mod.describe(
            "ledger", retention_days=7)["ok"])

    def test_dispatch_all_kinds_through_one_entry(self):
        gate = grad_mod.describe("gate", **GOOD_RECORD)
        self.assertTrue(gate["enabled"])
        retention = grad_mod.describe("retention",
                                      retention_days=14)
        self.assertEqual(retention["retention_days"], 14)
        indicator = grad_mod.describe("indicator")
        self.assertTrue(indicator["indicator_required"])
        ledger = grad_mod.describe(
            "ledger", item_id="TENSION-1",
            evidence="21:59 ruling",
            form="gate enforcement descriptor",
            still_owed=["PF-3 still open"])
        self.assertFalse(ledger["built"])


class NoCaptureTests(unittest.TestCase):
    def test_module_imports_nothing_capture_shaped(self):
        banned = {"watchers", "mss", "pyautogui",
                  "cv2", "PIL", "pyperclip", "keyboard",
                  "mouse", "win32gui", "screeninfo",
                  "pywin32", "subprocess", "threading",
                  "sched"}
        for filename, allowed in (
                ("core/grad.py",
                 {"__future__", "annotations", "re",
                  "firstopen_optin"}),  # INTENTIONAL (Phase 2 B6 rename): allowed set follows the renamed module (was "firstopen_optin_w9").
                ("core/grad_tools.py",
                 {"__future__", "annotations", "json",
                  "pathlib", "Path", "grad"})):  # INTENTIONAL (Phase 2 B4-1 rename): allowed set follows the renamed module (was "grad_w9").
            source = Path(__file__).resolve().parent.parent.joinpath(
                filename).read_text()
            tree = ast.parse(source)
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0]
                                    for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.update(a.name for a in node.names)
                    if node.module:
                        imported.add(node.module.split(".")[0])
            self.assertFalse(imported & banned, (filename, imported))
            self.assertEqual(imported, allowed,
                             (filename, imported))
            # No capture-shaped function is defined here.
            defined = {n.name for n in ast.walk(tree)
                       if isinstance(n, ast.FunctionDef)}
            for name in defined:
                lowered = name.lower()
                self.assertNotIn("capture", lowered)
                self.assertNotIn("watch", lowered)
                self.assertNotIn("listen", lowered)
                self.assertNotIn("screenshot", lowered)


class WiringTests(unittest.TestCase):
    def setUp(self):
        self._confirm_gate = confirm_gate
        self._confirm_gate.register_handler(None)

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in GRAD_TOOLS:
            self.assertIn(name, names)
        self.assertIn("grad_describe", gate._READ_ONLY_TOOLS)
        self.assertIn("grad_record", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["grad_record"],
            ("output_path",))
        # Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 756)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in GRAD_TOOLS:
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        self.assertEqual(names["grad_describe"], "Gate and ledger")
        self.assertEqual(names["grad_record"], "Record graduation")

    def test_describe_gate_through_funnel_without_handler(self):
        enabled = _result(tools.execute("grad_describe", {
            "kind": "gate",
            "record_json": json.dumps(GOOD_RECORD)}))
        self.assertTrue(enabled["ok"], enabled)
        self.assertTrue(enabled["enabled"])
        self.assertFalse(enabled["captured"])
        self.assertFalse(enabled["granted"])
        disabled = _result(tools.execute("grad_describe", {
            "kind": "gate"}))
        self.assertTrue(disabled["ok"], disabled)
        self.assertFalse(disabled["enabled"])

    def test_describe_other_kinds_through_funnel(self):
        retention = _result(tools.execute("grad_describe", {
            "kind": "retention"}))
        self.assertEqual(retention["retention_days"], 7)
        self.assertFalse(retention["owner_ruled"])
        indicator = _result(tools.execute("grad_describe", {
            "kind": "indicator"}))
        self.assertTrue(indicator["indicator_required"])
        ledger = _result(tools.execute("grad_describe", {
            "kind": "ledger", "item_id": "TENSION-1",
            "evidence": "21:59 ruling",
            "form": "gate enforcement descriptor",
            "still_owed": ["PF-3 still open"]}))
        self.assertFalse(ledger["built"])
        # Fields belonging to another kind refuse whole.
        wrong = _result(tools.execute("grad_describe", {
            "kind": "gate", "retention_days": 7}))
        self.assertFalse(wrong["ok"], wrong)

    def test_describe_does_not_trip_money_guard(self):
        # The Build 8 lesson: a separator-stripped tool
        # name can contain a banned token across a join.
        # With no confirm handler, a read-only tool must
        # still return ok, not a money-guard refusal.
        result = _result(tools.execute("grad_describe", {
            "kind": "indicator"}))
        self.assertTrue(result["ok"], result)

    def test_record_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "graduation.json")
            result = tools.execute("grad_record", {
                "record_json": json.dumps({
                    "item_id": "TENSION-1",
                    "evidence": "21:59 ruling",
                    "form": "gate enforcement descriptor",
                    "still_owed": ["PF-3 still open"]}),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "graduation.json")
            data = _result(tools.execute("grad_record", {
                "record_json": json.dumps({
                    "item_id": "TENSION-1",
                    "evidence": "21:59 ruling",
                    "form": "gate enforcement descriptor",
                    "still_owed": ["PF-3 still open"]}),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["built"])
            self.assertFalse(data["captured"])
            self.assertFalse(data["granted"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["graduation"]["item_id"],
                             "TENSION-1")
            self.assertFalse(written["graduation"]["built"])
            self.assertEqual(sorted(Path(tmp).iterdir()), [Path(target)])

    def test_record_refuses_tampered_or_claiming_entry(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "graduation.json")
            for fields in ({"item_id": "TENSION-1",
                            "evidence": "21:59 ruling",
                            "form": "gate enforcement descriptor",
                            "still_owed": ["PF-3 still open"],
                            "built": True},
                           {"item_id": "TENSION-1",
                            "evidence": "21:59 ruling",
                            "form": "the item is built",
                            "still_owed": []}):
                data = _result(tools.execute("grad_record", {
                    "record_json": json.dumps(fields),
                    "output_path": target}))
                self.assertFalse(data["ok"], data)
            self.assertFalse(Path(target).exists())

    def test_new_tool_descriptions_lead_with_safety_clause(self):
        """DECL-01 (owner ruling 2026-11-01): the legacy
        fitted builder is retired, so this test pins only
        that the new tools' registered descriptions still
        lead with their safety clause (nothing was trimmed,
        gutted, or unwired to chase bytes)."""
        by_name = {d["name"]: d for d in tools.get_declarations()}
        self.assertTrue(
            by_name["grad_describe"]["description"].startswith(
                "GATE ONLY, NOTHING CAPTURED"),
            by_name["grad_describe"]["description"])
        self.assertTrue(
            by_name["grad_record"]["description"].startswith(
                "RECORD ONLY, NOTHING CAPTURED"),
            by_name["grad_record"]["description"])


if __name__ == "__main__":
    unittest.main()
