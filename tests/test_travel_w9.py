"""Tests for Wave 9 Build 9: P19 travel + P20 PILOT +
P23 recipes/scoreboard, descriptors and records only.

What is proven here:
* descriptors echo exactly caller-supplied inputs, with
  the right False flags (booked, routed, paid, driven,
  rendered, moved, read_live, played, shared, scored);
* no legs means NO trip, NOT_COMPUTED — for trips and
  for door-to-door plans alike;
* booking, purchase, payment, send, vehicle-control,
  commercial-routing, safety-sizing, and fake-data
  inputs refuse WHOLE, never partially;
* scoreboard entries without evidence, and recipe steps
  without a source, refuse whole; numeric scores refuse
  whole;
* no vehicle, payment, routing-service, or hardware
  import — AST over imports and banned call names (not
  prose);
* the 10 tools are registered with the right
  classifications, GROUPED in GROUP_MEMBERS with
  friendly plain-language names, and behave through the
  funnel with and without a confirm handler;
* no travel tool name trips the money guard (the Build 8
  pilot_bench lesson: a separator-stripped blob can
  contain a banned token across a name join).
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from core import travel as travel_mod
from core import travel_tools as travel_tools
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


TRAVEL_TOOLS = ("travel_trip", "travel_door_to_door",
                "travel_transport", "travel_loadout",
                "travel_scene", "travel_spatial",
                "travel_sonify", "travel_recipe",
                "travel_scoreboard", "travel_record")

LEGS = [{"origin": "home", "destination": "airport",
         "mode": "cab"},
        {"origin": "airport", "destination": "campus",
         "mode": "train"}]


class DescriptorTests(unittest.TestCase):
    def test_trip_echoes_legs_and_never_books_or_routes(self):
        desc = travel_mod.build_trip(
            LEGS, accessibility_note="step-free option",
            pack_items=["charger", "notes"],
            parking_note="level 2, bay 14")
        self.assertEqual(desc["kind"], "TRIP_DESCRIPTOR")
        self.assertEqual(desc["legs"], LEGS)
        self.assertEqual(desc["accessibility_note"],
                         "step-free option")
        self.assertFalse(desc["accessibility_inferred"])
        self.assertEqual(desc["pack_items"], ["charger", "notes"])
        self.assertEqual(desc["parking_note"], "level 2, bay 14")
        self.assertFalse(desc["computed"])
        self.assertFalse(desc["booked"])
        self.assertFalse(desc["routed"])
        self.assertFalse(desc["paid"])

    def test_trip_no_legs_is_not_computed(self):
        desc = travel_mod.build_trip([])
        self.assertEqual(desc["kind"], "TRIP_NOT_COMPUTED")
        self.assertFalse(desc["computed"])
        self.assertEqual(desc["legs"], [])
        desc2 = travel_mod.build_door_to_door([])
        self.assertEqual(desc2["kind"], "TRIP_NOT_COMPUTED")
        self.assertFalse(desc2["computed"])

    def test_trip_refuses_bad_legs_and_action(self):
        self.assertFalse(travel_mod.build_trip(
            [{"origin": "home"}])["ok"])
        self.assertFalse(travel_mod.build_trip(
            [{"origin": "home", "destination": "airport",
              "booked": True}])["ok"])
        self.assertFalse(travel_mod.build_trip(
            LEGS, action="book")["ok"])
        self.assertFalse(travel_mod.build_trip(
            "not a list")["ok"])
        self.assertFalse(travel_mod.build_trip(
            LEGS, pack_items=["book the cab"])["ok"])

    def test_door_to_door_echoes_and_never_routes(self):
        plan = travel_mod.build_door_to_door(
            LEGS, deadline="at the airport by 9",
            assumptions="cab takes 40 minutes")
        self.assertEqual(plan["kind"], "DOOR_TO_DOOR_PLAN")
        self.assertEqual(plan["legs"], LEGS)
        self.assertFalse(plan["routed"])
        self.assertFalse(plan["commercial_routing_used"])
        self.assertIn("No commercial routing", plan["note"])

    def test_door_to_door_refuses_routing_claims(self):
        self.assertFalse(travel_mod.build_door_to_door(
            LEGS, assumptions="route computed by graphhopper")["ok"])
        self.assertFalse(travel_mod.build_door_to_door(
            [{"origin": "home", "destination": "airport",
              "routed": True}])["ok"])

    def test_transport_handler_modes(self):
        generic = travel_mod.build_transport("train")
        self.assertEqual(generic["kind"], "TRANSPORT_HANDLER")
        self.assertFalse(generic["controlled"])
        self.assertFalse(generic["drives"])
        driving = travel_mod.build_transport("driving")
        self.assertIn("OFF by default", driving["note"])
        self.assertIn("maps app", driving["note"])
        safe = travel_mod.build_transport("safe-arrival")
        self.assertIn("NOT a replacement for emergency services",
                      safe["note"])
        self.assertIn("call them when it is serious", safe["note"])

    def test_transport_refuses_vehicle_control(self):
        for mode in ("autopilot", "self-driving", "steer left"):
            self.assertFalse(travel_mod.build_transport(mode)["ok"],
                             mode)
        self.assertFalse(travel_mod.build_transport(
            "train", action="drive")["ok"])
        self.assertFalse(travel_mod.build_transport("")["ok"])

    def test_loadout_lists_and_applies_nothing(self):
        loadout = travel_mod.build_loadout(
            "workshop", ["bench notes", "timer"],
            ["confirm before any change", "bench notes"])
        self.assertEqual(loadout["kind"], "LOADOUT_DESCRIPTOR")
        self.assertEqual(loadout["capabilities"],
                         ["bench notes", "timer"])
        self.assertFalse(loadout["applied"])
        self.assertFalse(loadout["restored"])
        self.assertFalse(travel_mod.build_loadout(
            "workshop", ["deploy the app"], [])["ok"])
        self.assertFalse(travel_mod.build_loadout(
            "workshop", ["timer"], [], action="apply")["ok"])

    def test_scene_is_a_description_not_a_render(self):
        scene = travel_mod.build_scene(["orb", "model"], "hologram")
        self.assertEqual(scene["kind"], "SCENE_DESCRIPTOR")
        self.assertFalse(scene["hardware"])
        self.assertFalse(scene["rendered"])
        self.assertIn("classic illusion, not a true hologram",
                      scene["note"])
        self.assertFalse(travel_mod.build_scene(
            ["orb"], "video")["ok"])
        self.assertFalse(travel_mod.build_scene(
            ["render the orb"])["ok"])

    def test_spatial_layout_and_label_rules(self):
        layout = travel_mod.build_spatial(
            ["left", "right"],
            [{"zone": "left", "label": "schematic"}])
        self.assertEqual(layout["kind"], "SPATIAL_DESCRIPTOR")
        self.assertFalse(layout["read_live"])
        self.assertEqual(layout["augmented_labels"],
                         [{"zone": "left", "label": "schematic"}])
        self.assertFalse(travel_mod.build_spatial(
            ["left"],
            [{"zone": "left", "label": "port",
              "confidence": 0.9}])["ok"])
        self.assertFalse(travel_mod.build_spatial(
            ["left"], [{"zone": "left"}])["ok"])

    def test_sonify_is_a_map_not_a_sound(self):
        cue_map = travel_mod.build_sonify(
            ["build healthy", "download near done"],
            [{"state": "build healthy", "cue": "calm tone"},
             {"state": "download near done",
              "cue": "rising texture"}])
        self.assertEqual(cue_map["kind"], "SONIFY_DESCRIPTOR")
        self.assertFalse(cue_map["played"])
        self.assertFalse(cue_map["audio"])
        self.assertFalse(travel_mod.build_sonify(
            ["ok"], [{"state": "ok"}])["ok"])

    def test_recipe_requires_a_source_on_every_step(self):
        recipe = travel_mod.build_recipe(
            "morning setup",
            [{"step": "open bench notes", "source": "my own notes"},
             {"step": "list parts", "source": "my own notes"}])
        self.assertEqual(recipe["kind"], "RECIPE_DESCRIPTOR")
        self.assertFalse(recipe["shared"])
        self.assertFalse(travel_mod.build_recipe(
            "morning setup",
            [{"step": "open bench notes"}])["ok"])
        self.assertFalse(travel_mod.build_recipe(
            "morning setup",
            [{"step": "open bench notes", "source": "mine",
              "personal": True}])["ok"])
        self.assertFalse(travel_mod.build_recipe("x", [])["ok"])

    def test_scoreboard_requires_evidence_and_refuses_scores(self):
        board = travel_mod.build_scoreboard(
            [{"item": "travel_trip", "evidence": "test_travel_w9 18/18"},
             {"item": "travel_recipe", "evidence": "test_travel_w9 18/18"}])
        self.assertEqual(board["kind"], "SCOREBOARD_DESCRIPTOR")
        self.assertIsNone(board["scores"])
        self.assertFalse(travel_mod.build_scoreboard(
            [{"item": "travel_trip"}])["ok"])
        self.assertFalse(travel_mod.build_scoreboard(
            [{"item": "travel_trip", "evidence": "tests",
              "score": 95}])["ok"])
        self.assertFalse(travel_mod.build_scoreboard(
            [{"item": "travel_trip", "evidence": "tests",
              "pass_rate": 0.9}])["ok"])
        self.assertFalse(travel_mod.build_scoreboard([])["ok"])

    def test_safety_and_fake_phrases_refuse_whole(self):
        for phrase in ("will hold 50 kg", "load-bearing wall",
                       "certified safe design", "safety-critical sizing"):
            self.assertFalse(travel_mod.build_trip(
                LEGS, accessibility_note=phrase)["ok"], phrase)
        self.assertFalse(travel_mod.build_scoreboard(
            [{"item": "shield", "evidence": "fake attack log"}])["ok"])
        self.assertFalse(travel_mod.build_scene(
            ["fake data demo"])["ok"])

    def test_record_rebuilds_and_refuses_tampering(self):
        rebuilt = travel_mod.validate_recordable_travel(
            "trip", {"legs": LEGS,
                     "accessibility_note": "step-free option"})
        self.assertEqual(rebuilt["kind"], "TRIP_DESCRIPTOR")
        with self.assertRaises(ValueError):
            travel_mod.validate_recordable_travel(
                "trip", {"legs": LEGS, "booked": True})
        with self.assertRaises(ValueError):
            travel_mod.validate_recordable_travel(
                "trip", {"legs": [{"origin": "home"}]})
        with self.assertRaises(ValueError):
            travel_mod.validate_recordable_travel(
                "nope", {"legs": LEGS})
        with self.assertRaises(ValueError):
            travel_mod.validate_recordable_travel(
                "scoreboard",
                {"entries": [{"item": "x"}]})


class NoForbiddenImportTests(unittest.TestCase):
    def test_no_vehicle_payment_routing_or_hardware_import(self):
        # The module imports nothing at all; the coordinator
        # legitimately imports json and pathlib for the
        # destination-gated record file, as the pilot
        # coordinator does.
        module_banned = {"subprocess", "socket", "urllib",
                         "requests", "serial", "usb", "stripe",
                         "vehicle", "can", "obd", "graphhopper"}
        coordinator_banned = {"subprocess", "socket", "urllib",
                              "requests", "serial", "usb"}
        banned_calls = {"connect", "send", "sendall", "recv",
                        "gethostbyname", "getaddrinfo", "pay",
                        "charge", "drive", "steer", "render"}
        for filename, banned_imports, banned_calls_set in (
                # INTENTIONAL (travel split B1): travel.py is now a
                # facade; the impl modules carry the moved code, so
                # they are scanned with the same banned sets — without
                # them the proof would be vacuous for the moved code.
                ("core/travel.py", module_banned, banned_calls),
                ("core/travel_checks.py", module_banned, banned_calls),
                ("core/travel_builders.py", module_banned, banned_calls),
                ("core/travel_validate.py", module_banned, banned_calls),
                ("core/travel_tools.py", coordinator_banned,
                 banned_calls)):
            tree = ast.parse(
                Path(__file__).resolve().parent.parent.joinpath(
                    filename).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(
                            alias.name.split(".")[0],
                            banned_imports,
                            f"banned import in {filename}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        self.assertNotIn(
                            node.module.split(".")[0],
                            banned_imports,
                            f"banned import in {filename}")
                elif isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name):
                        called = func.id
                    elif isinstance(func, ast.Attribute):
                        called = func.attr
                    else:
                        continue
                    self.assertNotIn(
                        called, banned_calls_set,
                        f"banned call {called} in {filename}")


class WiringTests(unittest.TestCase):
    def setUp(self):
        self._confirm_gate = confirm_gate
        self._confirm_gate.register_handler(None)

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in TRAVEL_TOOLS:
            self.assertIn(name, names)
        for name in TRAVEL_TOOLS[:9]:
            self.assertIn(name, gate._READ_ONLY_TOOLS)
        self.assertIn("travel_record", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["travel_record"],
            ("output_path",))
        # Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 769)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in TRAVEL_TOOLS:
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        expected = {
            "travel_trip": "Trip",
            "travel_door_to_door": "Door-to-door plan",
            "travel_transport": "Transport handler",
            "travel_loadout": "Loadout",
            "travel_scene": "Scene",
            "travel_spatial": "Spatial desktop",
            "travel_sonify": "Sonification map",
            "travel_recipe": "Shareable recipe",
            "travel_scoreboard": "Open scoreboard",
            "travel_record": "Record travel",
        }
        for tool_name, friendly in expected.items():
            self.assertEqual(names[tool_name], friendly)

    def test_readonly_tools_through_funnel(self):
        trip = _result(tools.execute("travel_trip", {
            "legs": LEGS}))
        self.assertEqual(trip["kind"], "TRIP_DESCRIPTOR")
        self.assertFalse(trip["booked"])
        self.assertFalse(trip["routed"])
        empty = _result(tools.execute("travel_trip", {
            "legs": []}))
        self.assertEqual(empty["kind"], "TRIP_NOT_COMPUTED")
        plan = _result(tools.execute("travel_door_to_door", {
            "legs": LEGS}))
        self.assertFalse(plan["commercial_routing_used"])
        refused = _result(tools.execute("travel_trip", {
            "legs": LEGS, "action": "book"}))
        self.assertFalse(refused["ok"], refused)

    def test_no_travel_tool_trips_the_money_guard(self):
        # Build 8 lesson: a separator-stripped tool-name blob
        # can contain a banned token across a name join
        # (pilot_project_bench contained "otp"). Every
        # read-only travel tool must run clean through the
        # funnel with no confirm handler registered.
        cases = {
            "travel_trip": {"legs": LEGS},
            "travel_door_to_door": {"legs": LEGS},
            "travel_transport": {"mode_label": "train"},
            "travel_loadout": {"name": "workshop",
                               "capabilities": ["timer"],
                               "rules": ["confirm first"]},
            "travel_scene": {"labels": ["orb"]},
            "travel_spatial": {"zones": ["left"]},
            "travel_sonify": {"states": ["ok"],
                              "cues": [{"state": "ok",
                                        "cue": "calm tone"}]},
            "travel_recipe": {"title": "setup",
                              "steps": [{"step": "list parts",
                                         "source": "my notes"}]},
            "travel_scoreboard": {
                "entries": [{"item": "travel_trip",
                             "evidence": "tests"}]},
        }
        for name, args in cases.items():
            result = _result(tools.execute(name, args))
            self.assertTrue(result.get("ok"), (name, result))
            self.assertNotEqual(result.get("kind"), "not_confirmed",
                                (name, result))

    def test_record_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "travel.json")
            result = tools.execute("travel_record", {
                "record_kind": "trip",
                "record_json": json.dumps({"legs": LEGS}),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "travel.json")
            data = _result(tools.execute("travel_record", {
                "record_kind": "trip",
                "record_json": json.dumps({"legs": LEGS}),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["booked"])
            self.assertFalse(data["routed"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["travel"]["legs"], LEGS)
            self.assertFalse(written["booked"])
            self.assertEqual(sorted(Path(tmp).iterdir()), [Path(target)])

    def test_record_refuses_tampered_or_routed_travel(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "travel.json")
            for fields in ({"legs": LEGS, "booked": True},
                           {"legs": [{"origin": "home"}]}):
                data = _result(tools.execute("travel_record", {
                    "record_kind": "trip",
                    "record_json": json.dumps(fields),
                    "output_path": target}))
                self.assertFalse(data["ok"], data)
            self.assertFalse(Path(target).exists())


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
