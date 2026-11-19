"""Tests for Wave 10 Build 2: P23 trust + help +
changelog descriptors — trust centre, AI transparency,
in-app help plus diagnostic bundle, known-issues feed,
support runbook, plain-language changelog, one audience
first.

What is proven here:
* every descriptor echoes exactly caller-supplied
  inputs, with sent/published/shipped False and no
  claim beyond the supplied facts;
* the trust record carries the batch caveat verbatim
  and never a legal, privacy-policy, compliance, or
  conformance claim; accessibility conformance is NOT
  claimed here;
* the help bundle is explicit-send-only and is never
  sent by this build; the known-issues record is
  never a live feed and never an invented issue; the
  changelog record is never a claim that version
  shipped; the audience record is never a market
  claim;
* send/publish, legal/compliance/conformance, shipped,
  live-feed, ticket, helped-user, ruling-34
  (payment/tier/trial/refund/tax), and fake inputs
  refuse WHOLE, never partially;
* no network, send, feed, or payment import — AST
  over imports (not prose);
* the 2 tools are registered with the right
  classifications, GROUPED in GROUP_MEMBERS with
  friendly plain-language names, and behave through
  the funnel with and without a confirm handler;
* no trust tool name trips the money guard;
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

from core import trust_spine as trust_mod
from core import trust_spine_tools as trust_tools
from core import confirm_gate
from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

try:
    from _budget_isolation import restore_budget, snapshot_budget
except ImportError:  # run as a package module instead of top-level
    from tests._budget_isolation import (  # type: ignore
        restore_budget, snapshot_budget)



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


TRUST_TOOLS = ("trust_describe", "trust_record")


class DescriptorTests(unittest.TestCase):
    def test_trust_echo_and_flags(self):
        out = trust_mod.describe(
            "trust", facts=["everything stays on this computer",
                            "no data leaves this computer"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "TRUST_CENTRE")
        self.assertEqual(out["facts"],
                         ["everything stays on this computer",
                          "no data leaves this computer"])
        self.assertFalse(out["sent"])
        self.assertFalse(out["published"])
        self.assertFalse(out["legal_claim"])
        self.assertFalse(out["privacy_policy_claim"])
        self.assertFalse(out["conformance_claim"])
        self.assertIn("not legal or tax advice", out["caveat"])
        self.assertIn("except the transaction surface",
                      out["exit_wording"])

    def test_transparency_echo(self):
        out = trust_mod.describe(
            "transparency",
            notes=["answers from your own notes",
                   "does not book anything"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "AI_TRANSPARENCY")
        self.assertFalse(out["capability_claim_beyond_notes"])

    def test_help_bundle_explicit_send_only(self):
        out = trust_mod.describe(
            "help", topics=["getting started", "voice commands"],
            diagnostics=["startup check", "microphone check"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "HELP_BUNDLE")
        self.assertFalse(out["sent"])
        self.assertTrue(out["explicit_send_only"])

    def test_issues_echo_not_live_not_invented(self):
        out = trust_mod.describe(
            "issues", entries=[{"title": "globe tiles slow",
                                "status": "investigating"},
                               {"title": "flat map fallback works",
                                "status": "noted"}])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "KNOWN_ISSUES_FEED")
        self.assertFalse(out["live"])
        self.assertFalse(out["invented"])
        self.assertEqual(out["entries"][1]["status"], "noted")

    def test_runbook_order(self):
        out = trust_mod.describe(
            "runbook", steps=["open Settings", "check the voice card",
                              "restart the app"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["steps"][2], "restart the app")

    def test_changelog_not_shipped(self):
        out = trust_mod.describe(
            "changelog", version="5.92.0",
            entries=["trust centre descriptors added",
                     "help bundle descriptors added"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "CHANGELOG")
        self.assertEqual(out["version"], "5.92.0")
        self.assertFalse(out["shipped"])

    def test_audience_no_market_claim(self):
        out = trust_mod.describe(
            "audience", audience="everyday computer users")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["kind"], "FIRST_AUDIENCE")
        self.assertFalse(out["market_claim"])

    def test_unknown_kind_and_wrong_fields_refuse(self):
        self.assertFalse(trust_mod.describe("store")["ok"])
        self.assertFalse(trust_mod.describe(
            "trust", facts=["a fact"], notes=["wrong kind"])["ok"])
        self.assertFalse(trust_mod.describe(
            "help", topics=["t"])["ok"])

    def test_non_describe_action_refuses(self):
        self.assertFalse(trust_mod.describe(
            "trust", facts=["a fact"], action="send")["ok"])
        self.assertFalse(trust_mod.describe(
            "runbook", steps=["s"], action="publish")["ok"])

    def test_send_publish_inputs_refuse(self):
        for kind, kwargs in (
                ("trust", {"facts": ["we will send you updates"]}),
                ("transparency", {"notes": ["publishes a report"]}),
                ("help", {"topics": ["t"], "diagnostics": ["upload logs"]}),
                ("runbook", {"steps": ["email the bundle"]}),
                ("changelog", {"version": "5.92.0",
                              "entries": ["posted the notes"]})):
            out = trust_mod.describe(kind, **kwargs)
            self.assertFalse(out["ok"], (kind, out))

    def test_legal_compliance_conformance_refuse(self):
        for kind, kwargs in (
                ("trust", {"facts": ["we are GDPR compliant"]}),
                ("trust", {"facts": ["see our privacy policy"]}),
                ("transparency", {"notes": ["certified secure"]}),
                ("help", {"topics": ["conformance report"],
                          "diagnostics": ["d"]}),
                ("audience", {"audience": "compliant teams"})):
            out = trust_mod.describe(kind, **kwargs)
            self.assertFalse(out["ok"], (kind, out))

    def test_shipped_live_ticket_helped_refuse(self):
        for kind, kwargs in (
                ("changelog", {"version": "5.92.0",
                              "entries": ["already shipped"]}),
                ("issues", {"entries": [{"title": "feed is live",
                                         "status": "open"}]}),
                ("issues", {"entries": [{"title": "ticket exists",
                                         "status": "open"}]}),
                ("runbook", {"steps": ["user was helped"]})):
            out = trust_mod.describe(kind, **kwargs)
            self.assertFalse(out["ok"], (kind, out))

    def test_ruling34_content_refuses_not_decided(self):
        for kind, kwargs in (
                ("trust", {"facts": ["clear pricing tiers"]}),
                ("changelog", {"version": "5.92.0",
                              "entries": ["trial refunds added"]}),
                ("audience", {"audience": "paying subscribers"}),
                ("runbook", {"steps": ["collect payment"]})):
            out = trust_mod.describe(kind, **kwargs)
            self.assertFalse(out["ok"], (kind, out))

    def test_fake_inputs_refuse(self):
        self.assertFalse(trust_mod.describe(
            "issues", entries=[{"title": "fake issue",
                                "status": "open"}])["ok"])
        self.assertFalse(trust_mod.describe(
            "trust", facts=["a fake user story"])["ok"])

    def test_empty_and_malformed_refuse(self):
        self.assertFalse(trust_mod.describe("trust", facts=[])["ok"])
        self.assertFalse(trust_mod.describe("trust", facts="nope")["ok"])
        self.assertFalse(trust_mod.describe(
            "issues", entries=[{"title": "no status"}])["ok"])
        self.assertFalse(trust_mod.describe(
            "issues", entries=[{"title": "t", "status": "s",
                                "extra": "x"}])["ok"])
        self.assertFalse(trust_mod.describe(
            "changelog", version="", entries=["e"])["ok"])
        self.assertFalse(trust_mod.describe(
            "audience", audience="   ")["ok"])

    def test_record_rebuild_and_tamper(self):
        fields = {"kind": "trust",
                  "facts": ["everything stays on this computer"]}
        rebuilt = trust_mod.validate_recordable_trust(fields)
        self.assertEqual(rebuilt["kind"], "TRUST_CENTRE")
        with self.assertRaises(ValueError):
            trust_mod.validate_recordable_trust(
                {"kind": "trust",
                 "facts": ["everything stays on this computer"],
                 "shipped": True})
        with self.assertRaises(ValueError):
            trust_mod.validate_recordable_trust(
                {"kind": "changelog", "version": "5.92.0",
                 "entries": ["already shipped"]})

    def test_no_forbidden_imports_ast(self):
        banned = {"socket", "requests", "urllib", "smtplib",
                  "email", "http", "ftplib", "stripe", "paypal"}
        for filename in ("core/trust_spine.py",
                         "core/trust_spine_tools.py"):
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
             / "core/trust_spine.py").read_text())
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
        for name in TRUST_TOOLS:
            self.assertIn(name, names)
        self.assertIn("trust_describe", gate._READ_ONLY_TOOLS)
        self.assertIn("trust_record", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["trust_record"],
            ("output_path",))
        # Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 758)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in TRUST_TOOLS:
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        self.assertEqual(names["trust_describe"], "Trust and help")
        self.assertEqual(names["trust_record"], "Record trust")

    def test_readonly_tools_through_funnel(self):
        desc = _result(tools.execute("trust_describe", {
            "kind": "trust",
            "record_json": json.dumps(
                {"facts": ["everything stays on this computer"]})}))
        self.assertTrue(desc["ok"], desc)
        self.assertEqual(desc["kind"], "TRUST_CENTRE")
        self.assertFalse(desc["sent"])
        changelog = _result(tools.execute("trust_describe", {
            "kind": "changelog",
            "record_json": json.dumps(
                {"version": "5.92.0",
                 "entries": ["trust centre descriptors added"]})}))
        self.assertTrue(changelog["ok"], changelog)
        self.assertFalse(changelog["shipped"])
        # A handler-level refusal flows through the funnel
        # as a success envelope carrying ok False — the
        # refusal itself is in the result, not the envelope.
        refused = _result(tools.execute("trust_describe", {
            "kind": "trust",
            "record_json": json.dumps(
                {"facts": ["we will send you updates"]})}))
        self.assertFalse(refused["ok"], refused)
        self.assertIn("send", refused["reason"], refused)

    def test_tool_names_do_not_trip_money_guard(self):
        result = tools.execute("trust_describe", {
            "kind": "trust",
            "record_json": json.dumps(
                {"facts": ["everything stays on this computer"]})})
        data = _result(result)
        self.assertTrue(data["ok"], data)

    def test_record_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "trust.json")
            result = tools.execute("trust_record", {
                "record_kind": "trust",
                "record_json": json.dumps(
                    {"facts": ["everything stays on this computer"]}),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "trust.json")
            data = _result(tools.execute("trust_record", {
                "record_kind": "help",
                "record_json": json.dumps(
                    {"topics": ["getting started"],
                     "diagnostics": ["startup check"]}),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["sent"])
            self.assertFalse(data["published"])
            self.assertFalse(data["shipped"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["trust"]["kind"], "HELP_BUNDLE")
            self.assertFalse(written["sent"])
            self.assertEqual(sorted(Path(tmp).iterdir()), [Path(target)])

    def test_record_refuses_tampered_or_claiming(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "trust.json")
            for kind, record in (
                    ("trust", {"facts": ["a fact"],
                              "shipped": True}),
                    ("changelog", {"version": "5.92.0",
                                  "entries": ["already shipped"]}),
                    ("trust", {"facts": ["clear pricing tiers"]})):
                data = _result(tools.execute("trust_record", {
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
            decls["trust_describe"]["description"].startswith(
                "DESCRIBE ONLY, NOTHING SENT OR PUBLISHED"))
        self.assertTrue(
            decls["trust_record"]["description"].startswith(
                "RECORD ONLY, NOTHING SENT OR PUBLISHED"))


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
