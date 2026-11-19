"""Network deltas tests — descriptors and records only,
read-only, own-network forms, nothing ever scanned,
probed, connected, resolved, or reached.

Proves: the network descriptor echoes exactly
caller-supplied network label and scope, with probed,
scanned, reached, measured, and connected all False,
and a note saying nothing was probed, scanned, reached,
measured, or connected; the status summary echoes
caller-supplied status words and timestamp, with polled
and measured False; the read-only check returns
"own-network as supplied", "missing", or "outside own
network — refused", never resolving or connecting
anything; a network record re-validates and refuses a
tampered network, or one claiming a reach; any scan,
connect, discovery, or external-scope/label input
refuses whole; no socket or network-connect code exists
in the module (AST on imports and reach-shaped call
names, not on prose); tool classifications, grouping in
GROUP_MEMBERS with friendly plain-language names, and
funnel confirm refusal without a handler. Registry moves
605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630).
Nothing here writes a real file except the confirm-gated
record tool under a registered handler, into temp dirs
only.

Stdlib unittest, no GUI, no network, no code execution.
Run from the tree root with PYTHONPATH=. (as the other
suites are run by tests/run_tests.py).
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from core import net as net_mod
from core import net_tools as net_tools
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


NET_TOOLS = ("net_network_descriptor", "net_network_status",
             "net_readonly_check", "net_record_network")


class DescriptorTests(unittest.TestCase):
    def test_descriptor_echoes_caller_fields_only(self):
        desc = net_mod.build_network_descriptor("home wifi", "own")
        self.assertEqual(desc["kind"], "NETWORK_DESCRIPTOR")
        self.assertEqual(desc["network_label"], "home wifi")
        self.assertEqual(desc["scope"], "own")
        for flag in ("probed", "scanned", "reached",
                     "measured", "connected"):
            self.assertFalse(desc[flag], flag)
        self.assertIn("Nothing was probed", desc["note"])

    def test_descriptor_action_describe_accepted_others_refused(self):
        net_mod.build_network_descriptor("home wifi", "administered",
                                         action="describe")
        for action in ("scan", "connect", "probe", "ping",
                       "discover", "resolve", "fetch"):
            with self.assertRaises(ValueError, msg=action):
                net_mod.build_network_descriptor(
                    "home wifi", "own", action=action)

    def test_missing_or_empty_field_refused(self):
        for label, scope in (("", "own"), ("home wifi", ""),
                             (None, "own"), ("home wifi", None)):
            with self.assertRaises(ValueError, msg=str((label, scope))):
                net_mod.build_network_descriptor(label, scope)

    def test_external_scope_or_label_refused_whole(self):
        for scope in ("public", "cloud", "internet", "remote"):
            with self.assertRaises(ValueError, msg=scope):
                net_mod.build_network_descriptor("home wifi", scope)
        for label in ("public internet", "cloud network",
                      "remote office wan"):
            with self.assertRaises(ValueError, msg=label):
                net_mod.build_network_descriptor(label, "own")

    def test_url_label_refused_whole(self):
        with self.assertRaises(ValueError):
            net_mod.build_network_descriptor("https://example.com",
                                             "own")

    def test_local_labels_accepted(self):
        for label, scope in (("home wifi", "own"),
                             ("office lan", "administered")):
            desc = net_mod.build_network_descriptor(label, scope)
            self.assertEqual(desc["network_label"], label)
            self.assertEqual(desc["scope"], scope)

    def test_status_echoes_caller_words_only(self):
        status = net_mod.build_network_status(
            "up and quiet", "2026-10-26T22:00:00+05:30")
        self.assertEqual(status["kind"], "NETWORK_STATUS")
        self.assertEqual(status["status"], "up and quiet")
        self.assertFalse(status["polled"])
        self.assertFalse(status["measured"])

    def test_status_reach_claim_refused(self):
        for words in ("scanned the network", "probed and reached",
                      "measured yesterday", "connected to the router"):
            with self.assertRaises(ValueError, msg=words):
                net_mod.build_network_status(
                    words, "2026-10-26T22:00:00+05:30")


class CheckTests(unittest.TestCase):
    def test_supplied_missing_and_outside(self):
        ok = net_mod.check_readonly_target("printer.local", "own")
        self.assertEqual(ok["result"], "own-network as supplied")
        self.assertFalse(ok["resolved"])
        self.assertFalse(ok["connected"])
        missing = net_mod.check_readonly_target("", "own")
        self.assertEqual(missing["result"], "missing")
        outside = net_mod.check_readonly_target("printer.local",
                                                "public")
        self.assertEqual(outside["result"],
                         "outside own network — refused")
        url = net_mod.check_readonly_target("https://example.com",
                                            "own")
        self.assertEqual(url["result"],
                         "outside own network — refused")
        ext = net_mod.check_readonly_target(
            "printer.local", "own", network_label="public internet")
        self.assertEqual(ext["result"],
                         "outside own network — refused")

    def test_check_action_scan_refused(self):
        with self.assertRaises(ValueError):
            net_mod.check_readonly_target("printer.local", "own",
                                          action="scan")


class RebuildTests(unittest.TestCase):
    def test_rebuild_ok_and_tampered_refused(self):
        rebuilt = net_mod.validate_recordable_network(
            "descriptor", {"network_label": "home wifi",
                           "scope": "own"})
        self.assertEqual(rebuilt["network_label"], "home wifi")
        for fields in ({"network_label": "home wifi",
                        "scope": "own", "scanned": True},
                       {"network_label": "home wifi",
                        "scope": "public"},
                       {"network_label": "home wifi"}):
            with self.assertRaises(ValueError, msg=str(fields)):
                net_mod.validate_recordable_network(
                    "descriptor", fields)
        with self.assertRaises(ValueError):
            net_mod.validate_recordable_network("unknown", {})


class NoNetworkTests(unittest.TestCase):
    def test_no_socket_or_network_code(self):
        for filename in ("core/net.py", "core/net_tools.py"):
            source = Path(__file__).resolve().parent.parent.joinpath(
                filename).read_text()
            tree = ast.parse(source)
            banned_imports = {
                "socket", "requests", "urllib", "http", "ssl",
                "ftplib", "telnetlib", "aiohttp", "websockets",
                "asyncio", "subprocess", "serial",
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0],
                                         banned_imports, alias.name)
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0],
                                     banned_imports, node.module)
                if isinstance(node, ast.Call):
                    func = node.func
                    called = None
                    if isinstance(func, ast.Name):
                        called = func.id
                    elif isinstance(func, ast.Attribute):
                        called = func.attr
                    self.assertNotIn(called, {"connect", "send",
                                              "sendall", "recv",
                                              "gethostbyname",
                                              "getaddrinfo"},
                                     f"reach-shaped call {called} in {filename}")


class WiringTests(unittest.TestCase):
    def setUp(self):
        self._confirm_gate = confirm_gate
        self._confirm_gate.register_handler(None)

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in NET_TOOLS:
            self.assertIn(name, names)
        for name in NET_TOOLS[:3]:
            self.assertIn(name, gate._READ_ONLY_TOOLS)
        self.assertIn("net_record_network", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["net_record_network"],
            ("output_path",))
        # Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 758)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in NET_TOOLS:
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        self.assertEqual(names["net_network_descriptor"],
                         "Network descriptor")
        self.assertEqual(names["net_network_status"], "Network status")
        self.assertEqual(names["net_readonly_check"],
                         "Read-only network check")
        self.assertEqual(names["net_record_network"], "Record network")

    def test_readonly_tools_through_funnel(self):
        desc = _result(tools.execute("net_network_descriptor", {
            "network_label": "home wifi", "scope": "own"}))
        self.assertEqual(desc["kind"], "NETWORK_DESCRIPTOR")
        self.assertFalse(desc["scanned"])
        self.assertFalse(desc["connected"])
        status = _result(tools.execute("net_network_status", {
            "status_words": "up and quiet",
            "timestamp": "2026-10-26T22:00:00+05:30"}))
        self.assertEqual(status["kind"], "NETWORK_STATUS")
        self.assertFalse(status["polled"])
        check = _result(tools.execute("net_readonly_check", {
            "target_label": "printer.local", "scope": "own"}))
        self.assertEqual(check["result"], "own-network as supplied")
        # A handler-level refusal flows through the funnel
        # as a success envelope carrying ok False — the
        # refusal itself is in the result, not the envelope.
        refused = _result(tools.execute("net_network_descriptor", {
            "network_label": "home wifi", "scope": "own",
            "action": "scan"}))
        self.assertFalse(refused["ok"], refused)
        self.assertIn("scan", refused["reason"], refused)

    def test_record_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "network.json")
            result = tools.execute("net_record_network", {
                "record_kind": "descriptor",
                "record_json": json.dumps(
                    {"network_label": "home wifi", "scope": "own"}),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "network.json")
            data = _result(tools.execute("net_record_network", {
                "record_kind": "descriptor",
                "record_json": json.dumps(
                    {"network_label": "home wifi", "scope": "own"}),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["executed"])
            self.assertFalse(data["scanned"])
            self.assertFalse(data["connected"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["network"]["network_label"],
                             "home wifi")
            self.assertFalse(written["scanned"])
            self.assertFalse(written["connected"])
            self.assertEqual(sorted(Path(tmp).iterdir()), [Path(target)])

    def test_record_refuses_tampered_or_reached_network(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "network.json")
            for fields in ({"network_label": "home wifi",
                            "scope": "own", "scanned": True},
                           {"network_label": "home wifi",
                            "scope": "own", "extra": "x"}):
                data = _result(tools.execute("net_record_network", {
                    "record_kind": "descriptor",
                    "record_json": json.dumps(fields),
                    "output_path": target}))
                self.assertFalse(data["ok"], data)
            self.assertFalse(Path(target).exists())


if __name__ == "__main__":
    unittest.main()
