"""Tests for Wave 10 Build 4: P23 accessibility
conformance EVIDENCE PIPELINE — the discipline that
makes a future conformance claim checkable. This
build makes NO conformance claim.

What is proven here:
* the evidence ledger splits caller-supplied items
  into evidenced and not-evidenced lists, evidenced
  ONLY when evidence is non-empty AND outcome is
  supplied; anything else is not evidenced, never
  passed or failed by inference;
* the conformance statement lists evidenced and
  unevidenced checks separately for a caller-supplied
  scope, states that anything unevidenced is NOT
  claimed, and carries every verdict flag False — no
  blanket product claim, no audit performed, no live
  test run;
* a caller-supplied criterion label (for example a
  WCAG criterion name) is echoed as the label only;
  the verdict on it comes solely from its supplied
  evidence;
* verdict, invented-evidence, label, score, capture,
  legal, send/publish/install/apply/execute/run, and
  ruling-34 (payment/tier/trial/refund/tax) inputs
  refuse WHOLE, never partially — while an evidence
  item with an empty evidence or outcome VALUE is
  the defined not-evidenced case, not a refusal
  (missing KEYS refuse whole);
* no capture, OCR, sensor, or network import — AST
  over imports (not prose);
* the 2 tools are registered with the right
  classifications, GROUPED in GROUP_MEMBERS with
  friendly plain-language names, and behave through
  the funnel with and without a confirm handler;
* no conformance-evidence tool name trips the money
  guard;
* the fitted-list breach is RETIRED, not hidden:
  the legacy all-direct builder was removed by owner
  ruling (DECL-01, 2026-11-01); this build's two tools'
  descriptions still lead with their safety clause
  (nothing trimmed, gutted, or unwired to chase bytes,
  and the cap was not raised).
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from core import accessibility_conformance as accessconf_mod
from core import accessibility_conformance_tools as accessconf_tools
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


ACCESSCONF_TOOLS = ("accessconf_describe", "accessconf_record")

EVIDENCED = {"check_id": "CHK-1",
             "criterion": "WCAG 2.2 Success Criterion 1.4.3 "
                          "Contrast (Minimum)",
             "evidence": "review note dated 2026-10-27, page 3",
             "outcome": "noted"}

UNEVIDENCED = {"check_id": "CHK-2",
               "criterion": "WCAG 2.2 Success Criterion 2.4.7 "
                            "Focus Visible",
               "evidence": "",
               "outcome": ""}


class DescriptorTests(unittest.TestCase):
    def test_ledger_splits_evidenced_and_not(self):
        out = accessconf_mod.describe(
            "ledger", checks=[EVIDENCED, UNEVIDENCED])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "EVIDENCE_LEDGER")
        self.assertEqual(out["evidenced_count"], 1)
        self.assertEqual(out["not_evidenced_count"], 1)
        self.assertEqual(out["evidenced"][0]["check_id"], "CHK-1")
        self.assertEqual(out["evidenced"][0]["status"], "evidenced")
        self.assertEqual(out["not_evidenced"][0]["check_id"], "CHK-2")
        self.assertEqual(out["not_evidenced"][0]["status"],
                         "not_evidenced")
        self.assertFalse(out["conforms"])
        self.assertFalse(out["compliant"])
        self.assertFalse(out["certified"])
        self.assertFalse(out["blanket_claim"])
        self.assertFalse(out["audit_performed"])
        self.assertFalse(out["live_test_run"])
        self.assertIn("not legal or tax advice", out["caveat"])

    def test_pass_without_evidence_is_not_evidenced(self):
        out = accessconf_mod.describe("ledger", checks=[{
            "check_id": "CHK-9",
            "criterion": "keyboard operation note",
            "evidence": "",
            "outcome": "pass"}])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["evidenced_count"], 0)
        self.assertEqual(out["not_evidenced_count"], 1)
        self.assertEqual(out["not_evidenced"][0]["status"],
                         "not_evidenced")

    def test_outcome_without_evidence_is_not_evidenced(self):
        out = accessconf_mod.describe("ledger", checks=[{
            "check_id": "CHK-8",
            "criterion": "text clarity note",
            "evidence": "review note dated 2026-10-27",
            "outcome": ""}])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["evidenced_count"], 0)
        self.assertEqual(out["not_evidenced_count"], 1)

    def test_statement_lists_separately_and_claims_nothing(self):
        out = accessconf_mod.describe(
            "statement", scope="product accessibility notes",
            checks=[EVIDENCED, UNEVIDENCED])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "CONFORMANCE_STATEMENT")
        self.assertEqual(out["scope"], "product accessibility notes")
        self.assertEqual(out["evidenced_count"], 1)
        self.assertEqual(out["not_evidenced_count"], 1)
        self.assertFalse(out["conforms"])
        self.assertFalse(out["compliant"])
        self.assertFalse(out["certified"])
        self.assertFalse(out["accessible_claim"])
        self.assertTrue(out["unevidenced_not_claimed"])
        self.assertFalse(out["audit_performed"])
        self.assertFalse(out["live_test_run"])
        self.assertIn("NOT claimed", out["note"])
        for word in ("conforms", "compliant", "certified",
                     "accessible"):
            self.assertNotIn(word, out["note"])

    def test_criterion_label_echoed_only(self):
        out = accessconf_mod.describe("ledger", checks=[{
            "check_id": "CHK-3",
            "criterion": "WCAG 2.2 AA conformance criterion "
                         "1.4.3 Contrast (Minimum)",
            "evidence": "review note dated 2026-10-27",
            "outcome": "noted"}])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["evidenced"][0]["criterion"],
                         "WCAG 2.2 AA conformance criterion "
                         "1.4.3 Contrast (Minimum)")
        self.assertFalse(out["conforms"])

    def test_unknown_kind_and_wrong_fields_refuse(self):
        self.assertFalse(accessconf_mod.describe("audit")["ok"])
        self.assertFalse(accessconf_mod.describe(
            "ledger", checks=[EVIDENCED], scope="extra")["ok"])
        self.assertFalse(accessconf_mod.describe(
            "statement", scope="s")["ok"])

    def test_non_describe_action_refuses(self):
        self.assertFalse(accessconf_mod.describe(
            "ledger", checks=[EVIDENCED], action="run")["ok"])
        self.assertFalse(accessconf_mod.describe(
            "statement", scope="s", checks=[EVIDENCED],
            action="publish")["ok"])

    def test_verdict_words_in_evidence_outcome_scope_refuse(self):
        for kind, kwargs in (
                ("ledger", {"checks": [{**EVIDENCED,
                                        "evidence": "this conforms"}]}),
                ("ledger", {"checks": [{**EVIDENCED,
                                        "outcome": "compliant"}]}),
                ("ledger", {"checks": [{**EVIDENCED,
                                        "evidence":
                                            "fully accessible"}]}),
                ("statement", {"scope": "certified product notes",
                               "checks": [EVIDENCED]}),
                ("statement", {"scope": "s",
                               "checks": [{**EVIDENCED,
                                          "evidence":
                                              "certification file"}]})):
            out = accessconf_mod.describe(kind, **kwargs)
            self.assertFalse(out["ok"], (kind, out))

    def test_action_words_refuse(self):
        for kind, kwargs in (
                ("ledger", {"checks": [{**EVIDENCED,
                                        "evidence":
                                            "send the notes"}]}),
                ("ledger", {"checks": [{**EVIDENCED,
                                        "evidence":
                                            "install the checker"}]}),
                ("statement", {"scope": "apply the fixes",
                               "checks": [EVIDENCED]}),
                ("ledger", {"checks": [{**EVIDENCED,
                                        "evidence":
                                            "manual test run notes"}]})):
            out = accessconf_mod.describe(kind, **kwargs)
            self.assertFalse(out["ok"], (kind, out))

    def test_invented_evidence_refuses(self):
        self.assertFalse(accessconf_mod.describe(
            "ledger", checks=[{**EVIDENCED,
                              "evidence": "a fake note"}])["ok"])
        self.assertFalse(accessconf_mod.describe(
            "ledger", checks=[{**EVIDENCED,
                              "evidence": "invented entry"}])["ok"])

    def test_labels_scores_capture_legal_refuse(self):
        for kind, kwargs in (
                ("ledger", {"checks": [{**EVIDENCED,
                                        "evidence":
                                            "notes about disability"}]}),
                ("ledger", {"checks": [{**EVIDENCED,
                                        "evidence":
                                            "medical review note"}]}),
                ("ledger", {"checks": [{**EVIDENCED,
                                        "outcome": "95%"}]}),
                ("ledger", {"checks": [{**EVIDENCED,
                                        "evidence": "score 95 note"}]}),
                ("ledger", {"checks": [{**EVIDENCED,
                                        "evidence":
                                            "screenshot of the panel"}]}),
                ("ledger", {"checks": [{**EVIDENCED,
                                        "evidence":
                                            "live read of the screen"}]}),
                ("statement", {"scope": "legal review notes",
                               "checks": [EVIDENCED]})):
            out = accessconf_mod.describe(kind, **kwargs)
            self.assertFalse(out["ok"], (kind, out))

    def test_ruling34_content_refuses_not_decided(self):
        for kind, kwargs in (
                ("ledger", {"checks": [{**EVIDENCED,
                                        "evidence":
                                            "clear pricing tiers"}]}),
                ("statement", {"scope": "trial refunds",
                               "checks": [EVIDENCED]}),
                ("ledger", {"checks": [{**EVIDENCED,
                                        "outcome":
                                            "paying subscribers"}]})):
            out = accessconf_mod.describe(kind, **kwargs)
            self.assertFalse(out["ok"], (kind, out))

    def test_empty_and_malformed_refuse(self):
        self.assertFalse(accessconf_mod.describe(
            "ledger", checks=[])["ok"])
        self.assertFalse(accessconf_mod.describe(
            "ledger", checks="nope")["ok"])
        self.assertFalse(accessconf_mod.describe(
            "ledger", checks=[{"check_id": "CHK-1",
                               "criterion": "c",
                               "evidence": "e"}])["ok"])
        self.assertFalse(accessconf_mod.describe(
            "ledger", checks=[{**EVIDENCED, "extra": "x"}])["ok"])
        self.assertFalse(accessconf_mod.describe(
            "ledger", checks=[{**EVIDENCED, "check_id": ""}])["ok"])
        self.assertFalse(accessconf_mod.describe(
            "ledger", checks=[{**EVIDENCED, "criterion": "  "}])["ok"])
        self.assertFalse(accessconf_mod.describe(
            "ledger", checks=[{**EVIDENCED, "evidence": 42}])["ok"])
        self.assertFalse(accessconf_mod.describe(
            "statement", scope="", checks=[EVIDENCED])["ok"])
        self.assertFalse(accessconf_mod.describe(
            "statement", scope="   ", checks=[EVIDENCED])["ok"])

    def test_record_rebuild_and_tamper(self):
        fields = {"kind": "ledger", "checks": [EVIDENCED]}
        rebuilt = accessconf_mod.validate_recordable_accessconf(fields)
        self.assertEqual(rebuilt["kind"], "EVIDENCE_LEDGER")
        self.assertEqual(rebuilt["evidenced_count"], 1)
        with self.assertRaises(ValueError):
            accessconf_mod.validate_recordable_accessconf(
                {"kind": "ledger", "checks": [EVIDENCED],
                 "conforms": True})
        with self.assertRaises(ValueError):
            accessconf_mod.validate_recordable_accessconf(
                {"kind": "ledger",
                 "checks": [{**EVIDENCED,
                            "evidence": "this conforms"}]})
        with self.assertRaises(ValueError):
            accessconf_mod.validate_recordable_accessconf(
                {"kind": "statement", "scope": "s",
                 "checks": [EVIDENCED], "certified": True})

    def test_no_forbidden_imports_ast(self):
        banned = {"socket", "requests", "urllib", "smtplib",
                  "email", "http", "ftplib", "stripe", "paypal",
                  "cv2", "PIL", "mss", "pyautogui",
                  "win32gui", "pyperclip", "keyboard", "mouse"}
        for filename in ("core/accessibility_conformance.py",
                         "core/accessibility_conformance_tools.py"):
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
             / "core/accessibility_conformance.py").read_text())
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
        for name in ACCESSCONF_TOOLS:
            self.assertIn(name, names)
        self.assertIn("accessconf_describe", gate._READ_ONLY_TOOLS)
        self.assertIn("accessconf_record", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["accessconf_record"],
            ("output_path",))
        # Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 756)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in ACCESSCONF_TOOLS:
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        self.assertEqual(names["accessconf_describe"],
                         "Conformance evidence")
        self.assertEqual(names["accessconf_record"], "Record evidence")

    def test_readonly_tools_through_funnel(self):
        desc = _result(tools.execute("accessconf_describe", {
            "kind": "ledger",
            "record_json": json.dumps(
                {"checks": [EVIDENCED, UNEVIDENCED]})}))
        self.assertTrue(desc["ok"], desc)
        self.assertEqual(desc["kind"], "EVIDENCE_LEDGER")
        self.assertEqual(desc["evidenced_count"], 1)
        self.assertEqual(desc["not_evidenced_count"], 1)
        self.assertFalse(desc["conforms"])
        statement = _result(tools.execute("accessconf_describe", {
            "kind": "statement",
            "record_json": json.dumps(
                {"scope": "product accessibility notes",
                 "checks": [EVIDENCED]})}))
        self.assertTrue(statement["ok"], statement)
        self.assertTrue(statement["unevidenced_not_claimed"])
        # A handler-level refusal flows through the funnel
        # as a success envelope carrying ok False — the
        # refusal itself is in the result, not the envelope.
        refused = _result(tools.execute("accessconf_describe", {
            "kind": "ledger",
            "record_json": json.dumps(
                {"checks": [{**EVIDENCED,
                            "evidence": "this conforms"}]})}))
        self.assertFalse(refused["ok"], refused)
        self.assertIn("verdict", refused["reason"], refused)

    def test_tool_names_do_not_trip_money_guard(self):
        result = tools.execute("accessconf_describe", {
            "kind": "ledger",
            "record_json": json.dumps({"checks": [EVIDENCED]})})
        data = _result(result)
        self.assertTrue(data["ok"], data)

    def test_record_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "evidence.json")
            result = tools.execute("accessconf_record", {
                "record_kind": "ledger",
                "record_json": json.dumps({"checks": [EVIDENCED]}),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "evidence.json")
            data = _result(tools.execute("accessconf_record", {
                "record_kind": "statement",
                "record_json": json.dumps(
                    {"scope": "product accessibility notes",
                     "checks": [EVIDENCED, UNEVIDENCED]}),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["conforms"])
            self.assertFalse(data["certified"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["evidence"]["kind"],
                             "CONFORMANCE_STATEMENT")
            self.assertEqual(written["evidence"]["evidenced_count"], 1)
            self.assertEqual(written["evidence"]["not_evidenced_count"],
                             1)
            self.assertFalse(written["conforms"])
            self.assertEqual(sorted(Path(tmp).iterdir()), [Path(target)])

    def test_record_refuses_tampered_or_claiming(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "evidence.json")
            for kind, record in (
                    ("ledger", {"checks": [EVIDENCED],
                               "conforms": True}),
                    ("statement", {"scope": "s",
                                  "checks": [{**EVIDENCED,
                                             "evidence":
                                                 "this conforms"}]}),
                    ("ledger", {"checks": [{**EVIDENCED,
                                           "evidence":
                                               "clear pricing tiers"}]})):
                data = _result(tools.execute("accessconf_record", {
                    "record_kind": kind,
                    "record_json": json.dumps(record),
                    "output_path": target}))
                self.assertFalse(data["ok"], (kind, data))
            self.assertFalse(Path(target).exists())

    def test_new_tool_descriptions_lead_with_safety_clause(self):
        """DECL-01 (owner ruling 2026-11-01): the legacy
        fitted builder is retired, so this test pins only
        that the new tools' registered descriptions still
        lead with their safety clause (nothing was trimmed,
        gutted, or unwired to chase bytes)."""
        decls = {d["name"]: d for d in tools.get_declarations()}
        self.assertTrue(
            decls["accessconf_describe"]["description"].startswith(
                "DESCRIBE ONLY, NO CONFORMANCE CLAIM"))
        self.assertTrue(
            decls["accessconf_record"]["description"].startswith(
                "RECORD ONLY, NO CONFORMANCE CLAIM"))


if __name__ == "__main__":
    unittest.main()
