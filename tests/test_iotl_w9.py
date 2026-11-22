"""Local IoT deltas tests — descriptors and records only,
nothing ever reached, polled, or controlled.

Proves: the device descriptor echoes exactly
caller-supplied device name, local address label, and
administered-network label, with reached False and
controlled False, and a note saying it was not reached
and not controlled; the state summary echoes
caller-supplied state words and timestamp, with polled
False and measured False; the network check returns
"local-administered as supplied" or "missing", never
scanning anything; a device record re-validates and
refuses a tampered device, or one claiming it was
reached or controlled; any actuation action, scan
action, external network label, URL-scheme address,
missing field, or reach claim refuses whole; no socket
or network code exists in the module (AST on imports
and reach-shaped call names, not on prose); tool
classifications, grouping in GROUP_MEMBERS with
friendly plain-language names, and funnel confirm
refusal without a handler. Registry moves 593 -> 597.
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

from core import iotl as iotl_mod
from core import iotl_tools as iotl_tools
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


IOTL_TOOLS = ("iot_local_device", "iot_device_state",
              "iot_network_check", "iot_record_device")


class DescriptorTests(unittest.TestCase):
    def test_descriptor_echoes_caller_fields_only(self):
        device = iotl_mod.build_device_descriptor(
            "Living room lamp", "lamp.local", "home wifi")
        self.assertEqual(device["kind"], "LOCAL_DEVICE_DESCRIPTOR")
        self.assertEqual(device["device_name"], "Living room lamp")
        self.assertEqual(device["address_label"], "lamp.local")
        self.assertEqual(device["network_label"], "home wifi")
        self.assertFalse(device["reached"])
        self.assertFalse(device["controlled"])
        self.assertIn("NOT reached", device["note"])
        self.assertIn("NOT controlled", device["note"])

    def test_descriptor_action_describe_accepted_others_refused(self):
        device = iotl_mod.build_device_descriptor(
            "Living room lamp", "lamp.local", "home wifi",
            action="describe")
        self.assertEqual(device["kind"], "LOCAL_DEVICE_DESCRIPTOR")
        for action in ("turn-on", "turn-off", "toggle", "set",
                       "control", "dim", "scan", "discover", "probe",
                       "ping", "teleport"):
            with self.assertRaises(ValueError, msg=action):
                iotl_mod.build_device_descriptor(
                    "Living room lamp", "lamp.local", "home wifi",
                    action=action)

    def test_missing_or_empty_field_refused(self):
        for name, address, network in ((None, "lamp.local", "home wifi"),
                                       ("", "lamp.local", "home wifi"),
                                       ("Lamp", None, "home wifi"),
                                       ("Lamp", "   ", "home wifi"),
                                       ("Lamp", "lamp.local", None),
                                       ("Lamp", "lamp.local", "  "),
                                       ("Lamp", "lamp.local", 42)):
            with self.assertRaises(ValueError, msg=str((name, address, network))):
                iotl_mod.build_device_descriptor(name, address, network)

    def test_external_network_label_refused_whole(self):
        for network in ("home cloud", "public wifi", "remote office",
                        "internet", "external network", "wan link"):
            with self.assertRaises(ValueError, msg=network):
                iotl_mod.build_device_descriptor(
                    "Lamp", "lamp.local", network)

    def test_url_address_refused_whole(self):
        for address in ("https://lamp.example.com",
                        "http://192.168.1.10"):
            with self.assertRaises(ValueError, msg=address):
                iotl_mod.build_device_descriptor(
                    "Lamp", address, "home wifi")

    def test_local_labels_accepted(self):
        for address, network in (("192.168.1.10", "home wifi"),
                                 ("lamp.local", "administered office"),
                                 ("shelf-sensor-3", "home wifi")):
            device = iotl_mod.build_device_descriptor(
                "Device", address, network)
            self.assertFalse(device["reached"])


class StateTests(unittest.TestCase):
    def test_state_echoes_caller_words_only(self):
        state = iotl_mod.build_device_state(
            "Living room lamp", "on", "2026-10-26T22:00:00+05:30")
        self.assertEqual(state["kind"], "LOCAL_DEVICE_STATE")
        self.assertEqual(state["device_name"], "Living room lamp")
        self.assertEqual(state["state"], "on")
        self.assertFalse(state["polled"])
        self.assertFalse(state["measured"])
        self.assertIn("nothing was polled", state["note"])
        self.assertIn("nothing was measured", state["note"])

    def test_state_missing_field_or_reach_claim_refused(self):
        for args in (("Lamp", "", "2026-10-26"),
                     ("Lamp", "on", ""),
                     ("Lamp", "on", None)):
            with self.assertRaises(ValueError, msg=str(args)):
                iotl_mod.build_device_state(*args)
        for kwargs in ({"reached": True}, {"polled": True},
                       {"controlled": True}):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                iotl_mod.build_device_state(
                    "Lamp", "on", "2026-10-26", **kwargs)
        with self.assertRaises(ValueError):
            iotl_mod.build_device_state(
                "Lamp", "on", "2026-10-26", action="turn-off")


class NetworkCheckTests(unittest.TestCase):
    def test_supplied_and_missing(self):
        supplied = iotl_mod.check_local_network("home wifi")
        self.assertEqual(supplied["status"],
                         "local-administered as supplied")
        self.assertEqual(supplied["network_label"], "home wifi")
        self.assertIn("nothing was scanned", supplied["note"])
        for label in (None, "", "   "):
            missing = iotl_mod.check_local_network(label)
            self.assertEqual(missing["status"], "missing")
            self.assertEqual(missing["missing"], ["network_label"])

    def test_external_label_refused_whole(self):
        for network in ("cloud", "public wifi", "remote office"):
            with self.assertRaises(ValueError, msg=network):
                iotl_mod.check_local_network(network)


class RecordValidationTests(unittest.TestCase):
    def test_rebuild_ok_and_tampered_refused(self):
        rebuilt = iotl_mod.validate_recordable_device(
            {"device_name": "Lamp", "address_label": "lamp.local",
             "network_label": "home wifi"})
        self.assertEqual(rebuilt["device_name"], "Lamp")
        self.assertFalse(rebuilt["reached"])
        self.assertFalse(rebuilt["controlled"])
        for fields in ({"device_name": "Lamp",
                        "address_label": "lamp.local",
                        "network_label": "home wifi",
                        "reached": True},
                       {"device_name": "Lamp",
                        "address_label": "lamp.local",
                        "network_label": "home wifi",
                        "controlled": True},
                       {"device_name": "Lamp",
                        "address_label": "lamp.local"},
                       {"device_name": "Lamp",
                        "address_label": "lamp.local",
                        "network_label": "home wifi",
                        "action": "turn-on"},
                       {"device_name": "Lamp",
                        "address_label": "lamp.local",
                        "network_label": "cloud"}):
            with self.assertRaises(ValueError, msg=str(fields)):
                iotl_mod.validate_recordable_device(fields)


class NoNetworkTests(unittest.TestCase):
    def test_no_socket_or_network_code(self):
        for filename in ("core/iotl.py", "core/iotl_tools.py"):
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
        for name in IOTL_TOOLS:
            self.assertIn(name, names)
        for name in IOTL_TOOLS[:3]:
            self.assertIn(name, gate._READ_ONLY_TOOLS)
        self.assertIn("iot_record_device", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["iot_record_device"],
            ("output_path",))
        # Wave 9 Build 3 adds 4 local IoT tools (593 -> 597). Wave 9 Build 4 added 4 hardware tools (597 -> 601). Wave 9 Build 5 added 4 accessibility tools (601 -> 605). Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 769)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in IOTL_TOOLS:
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        self.assertEqual(names["iot_local_device"], "Local device")
        self.assertEqual(names["iot_device_state"], "Device state")
        self.assertEqual(names["iot_network_check"], "Network check")
        self.assertEqual(names["iot_record_device"], "Record device")

    def test_readonly_tools_through_funnel(self):
        device = _result(tools.execute("iot_local_device", {
            "device_name": "Living room lamp",
            "address_label": "lamp.local",
            "network_label": "home wifi"}))
        self.assertEqual(device["kind"], "LOCAL_DEVICE_DESCRIPTOR")
        self.assertFalse(device["reached"])
        self.assertFalse(device["controlled"])
        state = _result(tools.execute("iot_device_state", {
            "device_name": "Living room lamp", "state": "on",
            "timestamp": "2026-10-26T22:00:00+05:30"}))
        self.assertEqual(state["kind"], "LOCAL_DEVICE_STATE")
        self.assertFalse(state["polled"])
        check = _result(tools.execute("iot_network_check", {
            "network_label": "home wifi"}))
        self.assertEqual(check["status"],
                         "local-administered as supplied")
        # A handler-level refusal flows through the funnel
        # as a success envelope carrying ok False — the
        # refusal itself is in the result, not the envelope.
        refused = _result(tools.execute("iot_local_device", {
            "device_name": "Lamp", "address_label": "lamp.local",
            "network_label": "home wifi", "action": "turn-on"}))
        self.assertFalse(refused["ok"], refused)
        self.assertIn("device control", refused["reason"], refused)

    def test_record_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "device.json")
            result = tools.execute("iot_record_device", {
                "device_json": json.dumps(
                    {"device_name": "Lamp",
                     "address_label": "lamp.local",
                     "network_label": "home wifi"}),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "device.json")
            data = _result(tools.execute("iot_record_device", {
                "device_json": json.dumps(
                    {"device_name": "Lamp",
                     "address_label": "lamp.local",
                     "network_label": "home wifi"}),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["executed"])
            self.assertFalse(data["reached"])
            self.assertFalse(data["controlled"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["device"]["device_name"], "Lamp")
            self.assertFalse(written["reached"])
            self.assertFalse(written["controlled"])
            self.assertEqual(sorted(Path(tmp).iterdir()), [Path(target)])

    def test_record_refuses_tampered_or_reached_device(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "device.json")
            for fields in ({"device_name": "Lamp",
                            "address_label": "lamp.local",
                            "network_label": "home wifi",
                            "reached": True},
                           {"device_name": "Lamp",
                            "address_label": "lamp.local",
                            "network_label": "home wifi",
                            "extra": "x"}):
                data = _result(tools.execute("iot_record_device", {
                    "device_json": json.dumps(fields),
                    "output_path": target}))
                self.assertFalse(data["ok"], data)
            self.assertFalse(Path(target).exists())


if __name__ == "__main__":
    unittest.main()
