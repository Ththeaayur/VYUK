"""Wave 8 Build 3 tests — Phase 5 picks: INTERFACES, ENT shelf,
PORT, PLAT-TOOLS, FIN-PLAN.

Proves each cluster's delta: a valid descriptor / shelf / bundle /
record / plan passes; a face, a fake-data row, a renumbering claim,
a blocked or unknown licence, a vetoed feel token, a tampered
bundle, a credential key, a wrong programme state, and any
transaction-shaped input refuse the whole call; the PORT round-trip
proves byte-for-byte on the file itself; tool classifications and
funnel confirm refusal without a handler hold; the registry total
moves 569 -> 575 and every new tool is grouped out of
"More skills". Nothing here writes a real file except the
confirm-gated tools under a registered handler, into temp dirs.

Stdlib unittest, no GUI, no network, no code execution.
Run from the tree root with PYTHONPATH=. (as the other suites
are run by tests/run_tests.py).
"""

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from core import phase5_picks as phase5_mod
from core import phase5_tools as phase5_tools
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
    return {name: group
            for group, names in GROUP_MEMBERS.items()
            for name in names}


def _good_descriptor(**overrides):
    descriptor = {
        "surface": "Globe page layers panel",
        "rows": [
            {"label": "Earthquakes", "action": "show",
             "value_kind": "measured", "value": "12 events today"},
            {"label": "Air quality", "action": "show",
             "value_kind": "unknown"},
            {"label": "Open briefing", "action": "confirm",
             "value_kind": "unknown", "c14_required": True},
        ],
    }
    descriptor.update(overrides)
    return descriptor


def _good_shelf():
    return [
        {"title": "Rain sounds", "kind": "audio",
         "source": "caller library", "licence": "CC0"},
        {"title": "Public tale", "kind": "story",
         "source": "caller library", "licence": "public-domain"},
    ]


def _good_records():
    return [{"name": "alpha", "note": "first"},
            {"name": "beta", "note": "second"}]


def _result(payload):
    """tools.execute returns {"status": "success", "result": <JSON str>}."""
    assert payload["status"] == "success", payload
    return json.loads(payload["result"])


def _good_plan(**overrides):
    plan = {
        "name": "Monthly record",
        "entries": [
            {"label": "Rent budget", "kind": "budget",
             "amount": 500, "currency": "INR"},
            {"label": "Savings goal", "kind": "saving-goal",
             "amount": 200, "currency": "INR"},
        ],
    }
    plan.update(overrides)
    return plan


class TestInterfaces(unittest.TestCase):
    def test_valid_descriptor_passes(self):
        result = phase5_mod.describe_interface(_good_descriptor())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["row_count"], 3)
        self.assertFalse(result["redesign"])
        self.assertFalse(result["renumbered"])
        self.assertEqual(result["rows"][1]["value"], "unknown")

    def test_face_refused(self):
        for text in ("User face panel", "Portrait view"):
            bad = _good_descriptor(surface=text)
            result = phase5_mod.describe_interface(bad)
            self.assertFalse(result["ok"], (text, result))
            # "interface" contains "face" as a substring but must
            # NOT trip the whole-word face ban:
        ok = phase5_mod.describe_interface(
            _good_descriptor(surface="Generated interface panel"))
        self.assertTrue(ok["ok"], ok)

    def test_fake_data_refused(self):
        for kind, value in (("estimated", "about 12"),
                            ("sample", "12"), ("demo", "12")):
            bad = _good_descriptor(rows=[
                {"label": "Earthquakes", "action": "show",
                 "value_kind": kind, "value": value}])
            self.assertFalse(
                phase5_mod.describe_interface(bad)["ok"], kind)
        measured_no_value = _good_descriptor(rows=[
            {"label": "Earthquakes", "action": "show",
             "value_kind": "measured"}])
        self.assertFalse(
            phase5_mod.describe_interface(measured_no_value)["ok"])
        unknown_with_value = _good_descriptor(rows=[
            {"label": "Earthquakes", "action": "show",
             "value_kind": "unknown", "value": "12 events"}])
        self.assertFalse(
            phase5_mod.describe_interface(unknown_with_value)["ok"])

    def test_renumbering_and_new_surface_refused(self):
        self.assertFalse(phase5_mod.describe_interface(
            _good_descriptor(stack_index=9))["ok"])
        self.assertFalse(phase5_mod.describe_interface(
            _good_descriptor(surface="New sidebar entry"))["ok"])
        self.assertFalse(phase5_mod.describe_interface(
            _good_descriptor(surface="A new page for jobs"))["ok"])

    def test_refused_actions_and_confirm_without_c14(self):
        for action in ("execute", "send", "purchase", "deploy", "run"):
            bad = _good_descriptor(rows=[
                {"label": "Do it", "action": action,
                 "value_kind": "unknown"}])
            self.assertFalse(
                phase5_mod.describe_interface(bad)["ok"], action)
        bad = _good_descriptor(rows=[
            {"label": "Open briefing", "action": "confirm",
             "value_kind": "unknown"}])
        self.assertFalse(phase5_mod.describe_interface(bad)["ok"])


class TestEntShelf(unittest.TestCase):
    def test_valid_shelf_passes(self):
        result = phase5_mod.list_ent_shelf(_good_shelf())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["item_count"], 2)
        self.assertFalse(result["assets_bundled"])

    def test_blocked_and_unknown_licences_refused(self):
        for licence in ("CC-BY-NC", "GPL-3.0", "CC-BY-ND",
                        "some-made-up-licence", ""):
            bad = [{"title": "Rain sounds", "kind": "audio",
                    "source": "caller library", "licence": licence}]
            self.assertFalse(
                phase5_mod.list_ent_shelf(bad)["ok"], licence)

    def test_missing_source_refused_and_vetoed_feel_refused(self):
        bad = [{"title": "Rain sounds", "kind": "audio",
                "licence": "CC0"}]
        self.assertFalse(phase5_mod.list_ent_shelf(bad)["ok"])
        for title in ("Charon voice pack", "F6 signature tones",
                      "Space Grotesk pairing demo"):
            bad = [{"title": title, "kind": "audio",
                    "source": "caller library", "licence": "CC0"}]
            self.assertFalse(
                phase5_mod.list_ent_shelf(bad)["ok"], title)


class TestPort(unittest.TestCase):
    def test_roundtrip_byte_for_byte(self):
        records = _good_records()
        result = phase5_mod.port_roundtrip(records)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["byte_for_byte"])
        canonical = json.dumps(records, sort_keys=True,
                               separators=(",", ":"))
        self.assertEqual(
            result["sha256"],
            hashlib.sha256(canonical.encode()).hexdigest())

    def test_tampered_bundle_imports_nothing(self):
        built = phase5_mod.export_port_bundle(_good_records())
        self.assertTrue(built["ok"], built)
        bundle = built["bundle"]
        bundle["records"][0]["note"] = "tampered"
        result = phase5_mod.import_port_bundle(bundle)
        self.assertFalse(result["ok"])
        self.assertNotIn("records", result)

    def test_credential_key_refused(self):
        bad = [{"name": "alpha", "api_key": "should-not-travel"}]
        self.assertFalse(phase5_mod.export_port_bundle(bad)["ok"])
        self.assertFalse(phase5_mod.port_roundtrip(bad)["ok"])

    def test_tool_writes_file_and_proves_on_the_file(self):
        confirm_gate.register_handler(lambda *a: True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "bundle.json"
                result = _result(tools.execute(
                    "port_roundtrip",
                    {"records_json": json.dumps(_good_records()),
                     "output_path": str(target)}))
                self.assertTrue(result["ok"], result)
                self.assertTrue(result["byte_for_byte"])
                on_disk = json.loads(target.read_text())
                imported = phase5_mod.import_port_bundle(on_disk)
                self.assertTrue(imported["ok"], imported)
                self.assertEqual(imported["records"], _good_records())
        finally:
            confirm_gate.register_handler(None)


class TestPlatTools(unittest.TestCase):
    def test_valid_records_pass(self):
        for kind, state in (("lifecycle", "shipped"),
                            ("rollout", "paused"),
                            ("governance", "approved")):
            result = phase5_mod.record_plat(
                {"kind": kind, "name": "Wave 8", "state": state,
                 "note": "programme note"})
            self.assertTrue(result["ok"], (kind, result))
            self.assertFalse(result["enforced"])

    def test_wrong_state_and_governance_without_note_refused(self):
        self.assertFalse(phase5_mod.record_plat(
            {"kind": "lifecycle", "name": "X",
             "state": "rolling-out"})["ok"])
        self.assertFalse(phase5_mod.record_plat(
            {"kind": "governance", "name": "X",
             "state": "approved"})["ok"])
        self.assertFalse(phase5_mod.record_plat(
            {"kind": "mystery", "name": "X",
             "state": "noted"})["ok"])


class TestFinPlan(unittest.TestCase):
    def test_valid_plan_totals_caller_amounts(self):
        result = phase5_mod.record_fin_plan(_good_plan())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["totals"], {"INR": 700.0})
        self.assertFalse(result["transaction"])
        self.assertFalse(result["payment"])
        self.assertFalse(result["advice"])

    def test_transaction_shaped_input_refused_whole(self):
        cases = [
            _good_plan(payment={"to": "someone", "amount": 5}),
            _good_plan(entries=[
                {"label": "Pay rent", "kind": "expense-record",
                 "amount": 100, "currency": "INR"}]),
            _good_plan(entries=[
                {"label": "Groceries", "kind": "expense-record",
                 "amount": 100, "currency": "INR",
                 "payee": "someone"}]),
            _good_plan(entries=[
                {"label": "Groceries", "kind": "expense-record",
                 "amount": 100, "currency": "INR",
                 "destination_account": "123"}]),
            _good_plan(entries=[
                {"label": "Groceries", "kind": "expense-record",
                 "amount": -5, "currency": "INR"}]),
            _good_plan(entries=[
                {"label": "Groceries", "kind": "expense-record",
                 "amount": 100, "currency": "IN"}]),
            _good_plan(advice="buy this"),
        ]
        for plan in cases:
            result = phase5_mod.record_fin_plan(plan)
            self.assertFalse(result["ok"], (plan, result))


class TestWiring(unittest.TestCase):
    def setUp(self):
        confirm_gate.register_handler(None)

    def tearDown(self):
        confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in ("phase5_interface_describe", "ent_shelf_list",
                     "port_roundtrip", "plat_tools_record",
                     "fin_plan_record", "phase5_report"):
            self.assertIn(name, names)
        for name in ("phase5_interface_describe", "ent_shelf_list",
                     "plat_tools_record", "fin_plan_record"):
            self.assertIn(name, gate._READ_ONLY_TOOLS)
        for name in ("port_roundtrip", "phase5_report"):
            self.assertIn(name, gate._CONFIRM_GATE_TOOLS)
            self.assertEqual(
                gate._WRITE_DESTINATION_ARGS[name], ("output_path",))
        # Wave 8 Build 3 added 6 Phase 5 tools (569 -> 575).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 764)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169

    def test_catalogue_groups_new_tools(self):
        groups = _catalogue_groups()
        self.assertTrue(groups, "GROUP_MEMBERS not found in source")
        for name in ("phase5_interface_describe", "ent_shelf_list",
                     "port_roundtrip", "plat_tools_record",
                     "fin_plan_record", "phase5_report"):
            self.assertIn(name, groups, name)
            self.assertNotEqual(groups[name], "More skills", name)

    def test_read_only_tools_run_without_handler(self):
        result = _result(tools.execute(
            "phase5_interface_describe",
            {"descriptor_json": json.dumps(_good_descriptor())}))
        self.assertTrue(result["ok"], result)
        result = _result(tools.execute(
            "fin_plan_record",
            {"plan_json": json.dumps(_good_plan())}))
        self.assertTrue(result["ok"], result)

    def test_confirm_tools_refused_without_handler(self):
        result = tools.execute(
            "port_roundtrip",
            {"records_json": json.dumps(_good_records()),
             "output_path": "/tmp/phase5_no_handler.json"})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "not_confirmed")
        result = tools.execute(
            "phase5_report",
            {"cluster": "fin",
             "payload_json": json.dumps(_good_plan()),
             "output_path": "/tmp/phase5_no_handler.json"})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "not_confirmed")

    def test_report_tool_re_runs_and_writes_only_report(self):
        confirm_gate.register_handler(lambda *a: True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "report.json"
                result = _result(tools.execute(
                    "phase5_report",
                    {"cluster": "fin",
                     "payload_json": json.dumps(_good_plan()),
                     "output_path": str(target)}))
                self.assertTrue(result["ok"], result)
                on_disk = json.loads(target.read_text())
                self.assertEqual(on_disk["kind"], "FIN_PLAN")
                self.assertEqual(on_disk["totals"], {"INR": 700.0})
                # A missing parent folder is refused, never created:
                missing = Path(tmp) / "nope" / "report.json"
                result = _result(tools.execute(
                    "phase5_report",
                    {"cluster": "fin",
                     "payload_json": json.dumps(_good_plan()),
                     "output_path": str(missing)}))
                self.assertFalse(result["ok"])
                self.assertFalse(missing.parent.exists())
        finally:
            confirm_gate.register_handler(None)

    def test_module_has_no_execution_surface(self):
        source = Path(phase5_mod.__file__).read_text()
        tree = ast.parse(source)
        banned = {"deploy", "apply_proposal", "promote", "install",
                  "self_deploy", "execute", "send_payment",
                  "make_payment", "transfer"}
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in banned:
                found.add(node.name)
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in ("subprocess", "socket", "requests"):
                        found.add(alias.name)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("subprocess", "socket", "requests"):
                        found.add(alias.name)
        self.assertEqual(found, set(), found)


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
