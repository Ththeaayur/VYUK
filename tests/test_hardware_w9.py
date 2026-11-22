"""Hardware-adjacent deltas tests — descriptors and records only,
nothing ever moved, sized, rated, or signed off.

Proves: the inventory descriptor echoes exactly
caller-supplied part names and counts, with measured
False, and refuses any extra measurement field; the plan
descriptor echoes caller-supplied steps, requires a named
independent physical stop on every step that would move
hardware, and refuses a move step without one whole; the
safety descriptor states in plain words what a plan here
does NOT claim; a plan record re-validates and refuses a
tampered plan, a move step without a stop, or any safety
claim; any safety-sizing, load-bearing, will-it-hold, or
sign-off input refuses whole; no serial, device, or
hardware code exists in the module (AST on imports and
move-shaped call names, not on prose); tool
classifications, grouping in GROUP_MEMBERS with
friendly plain-language names, and funnel confirm
refusal without a handler. Registry moves 597 -> 601.
Nothing here writes a real file except the confirm-gated
record tool under a registered handler, into temp dirs
only. Nothing here makes any claim about any specific
project, arm, bench, or bench test.

Stdlib unittest, no GUI, no network, no code execution.
Run from the tree root with PYTHONPATH=. (as the other
suites are run by tests/run_tests.py).
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from core import hardware as hardware_mod
from core import hardware_tools as hardware_tools
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


HARDWARE_TOOLS = ("hardware_inventory", "hardware_plan",
                  "hardware_safety", "hardware_record_plan")

_MOVE_STEP = {"description": "tighten the frame bolt",
              "moves_hardware": True,
              "physical_stop": "hand on the power switch"}
_DESCRIBE_STEP = {"description": "list the parts on the bench",
                  "moves_hardware": False,
                  "physical_stop": None}


class InventoryTests(unittest.TestCase):
    def test_inventory_echoes_caller_parts_only(self):
        record = hardware_mod.build_hardware_inventory([
            {"name": "bolt", "count": 12},
            {"name": "spacer", "count": 5}])
        self.assertEqual(record["kind"], "HARDWARE_INVENTORY")
        self.assertEqual(record["parts"], [
            {"name": "bolt", "count": 12},
            {"name": "spacer", "count": 5}])
        self.assertEqual(record["total_parts"], 17)
        self.assertFalse(record["measured"])
        self.assertIn("Nothing here was measured", record["note"])

    def test_inventory_bad_parts_refused(self):
        for parts in (None, "bolt", [], [{"name": "bolt"}],
                      [{"name": "bolt", "count": -1}],
                      [{"name": "bolt", "count": True}],
                      [{"name": "bolt", "count": 2.5}],
                      [{"name": "", "count": 2}],
                      [{"name": "bolt", "count": 2, "weight": 10}],
                      [{"name": "bolt", "count": 2, "tolerance": "0.1"}],
                      [{"name": "bolt", "count": 2, "load": 5}]):
            with self.assertRaises(ValueError, msg=str(parts)):
                hardware_mod.build_hardware_inventory(parts)


class PlanTests(unittest.TestCase):
    def test_plan_echoes_steps_with_stop(self):
        plan = hardware_mod.build_hardware_plan(
            [_DESCRIBE_STEP, _MOVE_STEP])
        self.assertEqual(plan["kind"], "HARDWARE_PLAN")
        self.assertEqual(plan["steps"][1], _MOVE_STEP)
        self.assertTrue(plan["moves_anything"])
        self.assertFalse(plan["moved"])
        self.assertFalse(plan["executed"])
        self.assertIn("makes no safety claim", plan["safety"])

    def test_move_step_without_stop_refused_whole(self):
        for step in ({"description": "tighten the frame bolt",
                      "moves_hardware": True,
                      "physical_stop": None},
                     {"description": "tighten the frame bolt",
                      "moves_hardware": True,
                      "physical_stop": ""},
                     {"description": "tighten the frame bolt",
                      "moves_hardware": True},
                     {"description": "tighten the frame bolt",
                      "moves_hardware": "yes",
                      "physical_stop": "hand on switch"}):
            with self.assertRaises(ValueError, msg=str(step)):
                hardware_mod.build_hardware_plan([step])

    def test_plan_bad_shape_refused(self):
        for steps in (None, "tighten", [],
                      [{"description": "list parts",
                        "moves_hardware": False,
                        "physical_stop": None,
                        "action": "move"}],
                      [{"description": "list parts",
                        "moves_hardware": False}]):
            with self.assertRaises(ValueError, msg=str(steps)):
                hardware_mod.build_hardware_plan(steps)

    def test_non_move_step_needs_no_stop(self):
        plan = hardware_mod.build_hardware_plan([_DESCRIBE_STEP])
        self.assertFalse(plan["moves_anything"])
        self.assertIsNone(plan["steps"][0]["physical_stop"])


class SafetyTests(unittest.TestCase):
    def test_safety_descriptor_states_no_claim(self):
        safety = hardware_mod.build_hardware_safety()
        self.assertEqual(safety["kind"], "HARDWARE_SAFETY")
        for claim in ("no safety sizing",
                      "no engineering sign-off",
                      "no claim a design is safe",
                      "no claim anything will hold",
                      "no load rating"):
            self.assertIn(claim, safety["claims"])
        self.assertIn("makes no safety claim", safety["safety"])

    def test_safety_claim_inputs_refused_whole(self):
        for text in ("this will hold 50kg",
                     "is this load-bearing",
                     "give me the engineering sign-off",
                     "certified safe design",
                     "rated for heavy use",
                     "maximum load please"):
            with self.assertRaises(ValueError, msg=text):
                hardware_mod.build_hardware_plan([
                    {"description": text,
                     "moves_hardware": False,
                     "physical_stop": None}])
            with self.assertRaises(ValueError, msg=text):
                hardware_mod.build_hardware_inventory([
                    {"name": text, "count": 1}])

    def test_measured_value_inputs_refused_whole(self):
        for text in ("part weight 10 grams",
                     "tolerance 0.1 mm"):
            with self.assertRaises(ValueError, msg=text):
                hardware_mod.build_hardware_plan([
                    {"description": text,
                     "moves_hardware": False,
                     "physical_stop": None}])


class RecordValidationTests(unittest.TestCase):
    def test_rebuild_ok_and_tampered_refused(self):
        rebuilt = hardware_mod.validate_recordable_plan(
            {"steps": [_DESCRIBE_STEP, _MOVE_STEP]})
        self.assertEqual(rebuilt["steps"][1], _MOVE_STEP)
        self.assertFalse(rebuilt["moved"])
        for fields in ({"steps": [_DESCRIBE_STEP, _MOVE_STEP],
                        "safety": "signed off"},
                       {"steps": [_DESCRIBE_STEP, _MOVE_STEP],
                        "moved": True},
                       {"steps": [_DESCRIBE_STEP,
                                   {"description": "tighten bolt",
                                    "moves_hardware": True,
                                    "physical_stop": None}]},
                       {"steps": [_DESCRIBE_STEP,
                                   {"description": "this will hold",
                                    "moves_hardware": False,
                                    "physical_stop": None}]},
                       {"steps": [_DESCRIBE_STEP,
                                   {"description": "list parts",
                                    "moves_hardware": False,
                                    "physical_stop": None,
                                    "weight": 3}]}):
            with self.assertRaises(ValueError, msg=str(fields)):
                hardware_mod.validate_recordable_plan(fields)


class NoHardwareTests(unittest.TestCase):
    def test_no_serial_or_hardware_code(self):
        for filename in ("core/hardware.py",
                         "core/hardware_tools.py"):
            source = Path(__file__).resolve().parent.parent.joinpath(
                filename).read_text()
            tree = ast.parse(source)
            banned_imports = {
                "serial", "pyserial", "usb", "hid", "gpio",
                "RPi", "socket", "subprocess", "ctypes",
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0],
                                         banned_imports, alias.name)
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0],
                                     banned_imports, node.module)
                if isinstance(node, ast.FunctionDef):
                    self.assertNotIn(node.name,
                                     {"execute", "move", "actuate",
                                      "drive", "print_part", "cut"},
                                     f"move-shaped function {node.name} "
                                     f"in {filename}")

    def test_module_makes_no_specific_project_claim(self):
        source = Path(__file__).resolve().parent.parent.joinpath(
            "core", "hardware.py").read_text()
        # Docstrings wrap lines, so compare on normalised
        # whitespace — the wording, not the line break, is
        # what this test pins.
        normalised = " ".join(source.split())
        self.assertIn("no claim about any specific project",
                      normalised)


class WiringTests(unittest.TestCase):
    def setUp(self):
        self._confirm_gate = confirm_gate
        self._confirm_gate.register_handler(None)

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in HARDWARE_TOOLS:
            self.assertIn(name, names)
        for name in HARDWARE_TOOLS[:3]:
            self.assertIn(name, gate._READ_ONLY_TOOLS)
        self.assertIn("hardware_record_plan", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["hardware_record_plan"],
            ("output_path",))
        # Wave 9 Build 4 adds 4 hardware tools (597 -> 601).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 769)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in HARDWARE_TOOLS:
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        self.assertEqual(names["hardware_inventory"],
                         "Hardware inventory")
        self.assertEqual(names["hardware_plan"], "Hardware plan")
        self.assertEqual(names["hardware_safety"], "Hardware safety")
        self.assertEqual(names["hardware_record_plan"],
                         "Record hardware plan")

    def test_readonly_tools_through_funnel(self):
        record = _result(tools.execute("hardware_inventory", {
            "parts_json": json.dumps([{"name": "bolt", "count": 12}])}))
        self.assertEqual(record["kind"], "HARDWARE_INVENTORY")
        self.assertFalse(record["measured"])
        plan = _result(tools.execute("hardware_plan", {
            "steps_json": json.dumps([_MOVE_STEP])}))
        self.assertEqual(plan["kind"], "HARDWARE_PLAN")
        self.assertFalse(plan["moved"])
        safety = _result(tools.execute("hardware_safety", {}))
        self.assertEqual(safety["kind"], "HARDWARE_SAFETY")
        # A handler-level refusal flows through the funnel
        # as a success envelope carrying ok False — the
        # refusal itself is in the result, not the envelope.
        refused = _result(tools.execute("hardware_plan", {
            "steps_json": json.dumps([{
                "description": "tighten the frame bolt",
                "moves_hardware": True,
                "physical_stop": None}])}))
        self.assertFalse(refused["ok"], refused)
        self.assertIn("physical stop", refused["reason"], refused)

    def test_record_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "plan.json")
            result = tools.execute("hardware_record_plan", {
                "plan_json": json.dumps({"steps": [_MOVE_STEP]}),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "plan.json")
            data = _result(tools.execute("hardware_record_plan", {
                "plan_json": json.dumps({"steps": [_MOVE_STEP]}),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["executed"])
            self.assertFalse(data["moved"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["plan"]["steps"][0], _MOVE_STEP)
            self.assertFalse(written["moved"])
            self.assertIn("makes no safety claim",
                          written["safety"])
            self.assertEqual(sorted(Path(tmp).iterdir()), [Path(target)])

    def test_record_refuses_tampered_or_unsafe_plan(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "plan.json")
            for fields in ({"steps": [_MOVE_STEP], "moved": True},
                           {"steps": [{"description": "tighten bolt",
                                        "moves_hardware": True,
                                        "physical_stop": None}]},
                           {"steps": [{"description": "this will hold",
                                        "moves_hardware": False,
                                        "physical_stop": None}]},
                           {"steps": [_MOVE_STEP], "extra": "x"}):
                data = _result(tools.execute("hardware_record_plan", {
                    "plan_json": json.dumps(fields),
                    "output_path": target}))
                self.assertFalse(data["ok"], data)
            self.assertFalse(Path(target).exists())


if __name__ == "__main__":
    unittest.main()
