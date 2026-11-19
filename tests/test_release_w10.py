"""Tests for Wave 10 Build 1: P23 release spine —
multiple install routes, verifiable releases
(structure only), and a release train with gates,
as descriptors and records only.

What is proven here:
* the route descriptor echoes caller-supplied route
  labels, each stating what it does and does not do
  — nothing compiled, nothing installed, no store
  listing claimed to exist;
* the verify descriptor checks the SHAPE only —
  version supplied, manifest present, hash present
  and well-formed, data baseline stated — and never
  verifies a signature, never claims a release is
  signed or authentic, and never re-verifies
  single-sourcing from files;
* the train descriptor returns caller-supplied stage
  gates in order, verdicts as supplied, running no
  gate and passing no gate;
* signing, compiling, publishing, shipping, feed,
  and payment/tier/trial/refund inputs refuse WHOLE,
  never partially — including the whole-word edge
  ("frontier" must NOT trip on "tier");
* no signing, installer, network, or feed import —
  AST over imports (not prose);
* the 2 tools are registered with the right
  classifications, GROUPED in GROUP_MEMBERS with
  friendly plain-language names, and behave through
  the funnel with and without a confirm handler;
* no release tool name trips the money guard (the
  Build 8 pilot_bench lesson: a separator-stripped
  blob can contain a banned token across a name
  join);
* the legacy fitted builder is RETIRED (DECL-01, owner
  ruling 2026-11-01): the new tools' canonical
  descriptions still lead with their safety clause.
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from core import release_spine as release_mod
from core import release_spine_tools as release_tools
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


RELEASE_TOOLS = ("release_describe", "release_record")

GOOD_HASH = "a" * 64


class DescribeTests(unittest.TestCase):
    def test_route_echoes_labels_and_claims_nothing(self):
        data = release_mod.describe(
            "route", routes=["zip over folder",
                             "installer file",
                             "store listing"])
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["kind"], "INSTALL_ROUTES")
        self.assertEqual([r["label"] for r in data["routes"]],
                         ["zip over folder", "installer file",
                          "store listing"])
        for route in data["routes"]:
            self.assertTrue(route["described_only"])
            self.assertFalse(route["installer_compiled"])
            self.assertFalse(route["store_listing_exists"])
            self.assertFalse(route["installed_by_this_build"])
        self.assertFalse(data["released"])
        self.assertFalse(data["signed"])
        self.assertFalse(data["compiled"])
        self.assertFalse(data["published"])
        self.assertEqual(data["exit"], release_mod.WAVE10_EXIT)
        self.assertIn("not legal or tax advice", data["caveat"])

    def test_route_refuses_empty_and_claim_labels(self):
        for routes in ([], [""],
                       ["signed installer"],
                       ["already published build"],
                       ["store listing exists"],
                       ["free trial download"]):
            data = release_mod.describe("route", routes=routes)
            self.assertFalse(data["ok"], routes)

    def test_route_whole_word_edge_frontier_does_not_trip(self):
        data = release_mod.describe(
            "route", routes=["frontier preview"])
        self.assertTrue(data["ok"], data)

    def test_verify_shape_ok_and_shape_failed(self):
        good = release_mod.describe(
            "verify", version="5.92.0", manifest_present=True,
            sha256=GOOD_HASH, data_baseline_bytes=369701)
        self.assertTrue(good["ok"], good)
        self.assertTrue(good["shape_ok"], good)
        self.assertEqual(good["failed_checks"], [])
        self.assertFalse(good["signature_verified"])
        self.assertFalse(good["signed"])
        self.assertFalse(good["authentic"])
        self.assertFalse(good["released"])
        bad = release_mod.describe(
            "verify", version="5.92.0", manifest_present=False,
            sha256="xyz", data_baseline_bytes=None)
        self.assertTrue(bad["ok"], bad)
        self.assertFalse(bad["shape_ok"], bad)
        self.assertIn("manifest_present", bad["failed_checks"])
        self.assertIn("hash_well_formed", bad["failed_checks"])
        self.assertIn("data_baseline_stated", bad["failed_checks"])
        self.assertFalse(bad["sha256_well_formed"])

    def test_verify_refuses_claims_and_bad_types(self):
        for kwargs in ({"version": "signed 5.92.0",
                        "manifest_present": True,
                        "sha256": GOOD_HASH},
                       {"version": "5.92.0",
                        "manifest_present": "yes",
                        "sha256": GOOD_HASH},
                       {"version": "5.92.0",
                        "manifest_present": True,
                        "sha256": GOOD_HASH,
                        "data_baseline_bytes": -1},
                       {"version": "",
                        "manifest_present": True,
                        "sha256": GOOD_HASH}):
            data = release_mod.describe("verify", **kwargs)
            self.assertFalse(data["ok"], kwargs)

    def test_train_echoes_gates_and_passes_none(self):
        data = release_mod.describe(
            "train", gates=[{"name": "battery", "verdict": "green"},
                            {"name": "licence audit",
                             "verdict": "blocked"}])
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["kind"], "RELEASE_TRAIN")
        self.assertEqual(data["train_verdict"], "AS_SUPPLIED")
        self.assertFalse(data["gates_run_by_this_descriptor"])
        self.assertFalse(data["gates_passed_by_this_descriptor"])
        self.assertFalse(data["released"])
        self.assertEqual(
            [g["verdict_as_supplied"] for g in data["gates"]],
            ["green", "blocked"])

    def test_train_refuses_empty_claims_and_bad_shapes(self):
        for gates in ([], [{"name": "signing", "verdict": "done"}],
                      [{"name": "battery",
                        "verdict": "published"}],
                      [{"name": "battery"}],
                      [{"name": "", "verdict": "green"}],
                      ["battery"]):
            data = release_mod.describe("train", gates=gates)
            self.assertFalse(data["ok"], gates)

    def test_dispatch_refuses_action_kind_fields(self):
        self.assertFalse(release_mod.describe(
            "route", action="publish",
            routes=["zip over folder"])["ok"])
        self.assertFalse(release_mod.describe(
            "unknown", routes=["zip over folder"])["ok"])
        self.assertFalse(release_mod.describe(
            "route", routes=["zip over folder"],
            version="5.92.0")["ok"])


class RecordTests(unittest.TestCase):
    def test_record_rebuilds_and_refuses_tampered(self):
        rebuilt = release_mod.validate_recordable_release(
            "verify", {"version": "5.92.0",
                       "manifest_present": True,
                       "sha256": GOOD_HASH,
                       "data_baseline_bytes": 369701})
        self.assertTrue(rebuilt["shape_ok"])
        with self.assertRaises(ValueError):
            release_mod.validate_recordable_release(
                "verify", {"version": "5.92.0",
                           "manifest_present": True,
                           "sha256": GOOD_HASH,
                           "signed": True})
        with self.assertRaises(ValueError):
            release_mod.validate_recordable_release(
                "route", {"routes": ["signed installer"]})
        with self.assertRaises(ValueError):
            release_mod.validate_recordable_release(
                "unknown", {"routes": ["zip over folder"]})


class AstTests(unittest.TestCase):
    def test_no_signing_network_feed_import(self):
        banned = {"subprocess", "socket", "urllib", "http",
                  "requests", "ssl", "cryptography", "nacl",
                  "feedparser", "msilib", "signing"}
        for filename in ("core/release_spine.py",
                         "core/release_spine_tools.py"):
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
        mod_tree = ast.parse(
            (Path(__file__).resolve().parent.parent
             / "core/release_spine.py").read_text())
        mod_imports = set()
        for node in ast.walk(mod_tree):
            if isinstance(node, ast.Import):
                mod_imports.update(a.name.split(".")[0]
                                   for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    mod_imports.add(node.module.split(".")[0])
        self.assertEqual(mod_imports, {"__future__", "re"})


class WiringTests(unittest.TestCase):
    def setUp(self):
        self._confirm_gate = confirm_gate
        self._confirm_gate.register_handler(None)

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in RELEASE_TOOLS:
            self.assertIn(name, names)
        self.assertIn("release_describe", gate._READ_ONLY_TOOLS)
        self.assertIn("release_record", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["release_record"],
            ("output_path",))
        # Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 758)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in RELEASE_TOOLS:
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        self.assertEqual(names["release_describe"],
                         "Release spine")
        self.assertEqual(names["release_record"],
                         "Record release")

    def test_describe_through_funnel(self):
        route = _result(tools.execute("release_describe", {
            "kind": "route",
            "record_json": json.dumps(
                {"routes": ["zip over folder"]})}))
        self.assertTrue(route["ok"], route)
        self.assertEqual(route["kind"], "INSTALL_ROUTES")
        self.assertFalse(route["signed"])
        verify = _result(tools.execute("release_describe", {
            "kind": "verify",
            "record_json": json.dumps(
                {"version": "5.92.0", "manifest_present": True,
                 "sha256": GOOD_HASH,
                 "data_baseline_bytes": 369701})}))
        self.assertTrue(verify["shape_ok"], verify)
        self.assertFalse(verify["signature_verified"])
        refused = _result(tools.execute("release_describe", {
            "kind": "route",
            "record_json": json.dumps(
                {"routes": ["signed installer"]})}))
        self.assertFalse(refused["ok"], refused)
        self.assertIn("signed", refused["reason"], refused)

    def test_describe_returns_ok_with_no_handler(self):
        # Read-only tool: no money-guard refusal with no
        # confirm handler (the Build 8 pilot_bench lesson).
        route = _result(tools.execute("release_describe", {
            "kind": "train",
            "record_json": json.dumps(
                {"gates": [{"name": "battery",
                            "verdict": "green"}]})}))
        self.assertTrue(route["ok"], route)

    def test_record_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "release.json")
            result = tools.execute("release_record", {
                "record_kind": "route",
                "record_json": json.dumps(
                    {"routes": ["zip over folder"]}),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "release.json")
            data = _result(tools.execute("release_record", {
                "record_kind": "route",
                "record_json": json.dumps(
                    {"routes": ["zip over folder"]}),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["released"])
            self.assertFalse(data["signed"])
            self.assertFalse(data["compiled"])
            self.assertFalse(data["published"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["release"]["kind"],
                             "INSTALL_ROUTES")
            self.assertEqual(written["exit"],
                             release_mod.WAVE10_EXIT)
            self.assertEqual(sorted(Path(tmp).iterdir()), [Path(target)])

    def test_record_refuses_tampered_or_claiming(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "release.json")
            for kind, fields in (
                    ("route", {"routes": ["signed installer"]}),
                    ("verify", {"version": "5.92.0",
                                "manifest_present": True,
                                "sha256": GOOD_HASH,
                                "extra": "x"})):
                data = _result(tools.execute("release_record", {
                    "record_kind": kind,
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
        descriptions = {d["name"]: d.get("description", "")
                        for d in tools.get_declarations()}
        self.assertTrue(
            descriptions["release_describe"].startswith(
                "DESCRIBE ONLY, NOTHING RELEASED OR SIGNED"),
            descriptions["release_describe"][:80])
        self.assertTrue(
            descriptions["release_record"].startswith(
                "RECORD ONLY, NOTHING RELEASED OR SIGNED"),
            descriptions["release_record"][:80])


# Audit fix Batch 5 (TEST-02): L3 budget-isolation guest rule
# (shared helper tests/_budget_isolation.py) — this suite
# executes tools through the shared funnel, so it snapshots
# the budget window + clock + confirm handler, opens a clean
# window, and restores byte-for-byte, instead of inheriting
# the earlier suites' dispatches and tripping on ordering.
# The production 120-calls-per-60-seconds limit is untouched.
_BUDGET_SNAPSHOT = None


def setUpModule():
    global _BUDGET_SNAPSHOT
    _BUDGET_SNAPSHOT = snapshot_budget()


def tearDownModule():
    global _BUDGET_SNAPSHOT
    if _BUDGET_SNAPSHOT is not None:
        restore_budget(_BUDGET_SNAPSHOT)
        _BUDGET_SNAPSHOT = None


if __name__ == "__main__":
    unittest.main()
