"""Wellbeing, human-first tests — suggestions only, sensing-free.

Proves: the break plan echoes caller-supplied minutes only
and is a suggestion, never a prescription, claiming no
health benefit; the reminder descriptor schedules nothing
and says so; the wind-down routine accepts only the fixed
non-medical list, in order, refusing an unknown step whole
and reporting duplicates removed; the day summary totals
caller-supplied numbers only, refuses a missing number
whole, and produces no average, score, or rating; any
diagnosis, symptom, mood, biometric, or prevention /
treatment-claim input refuses whole with a stated reason;
no camera, microphone, keyboard, or sensor code exists in
the module (AST on imports and identifiers, not on prose);
tool classifications, grouping in GROUP_MEMBERS with
friendly plain-language names, and funnel confirm refusal
without a handler, plus re-validation refusal of a
tampered plan. Registry moves 584 -> 589. Nothing here
writes a real file except the confirm-gated record tool
under a registered handler, into temp dirs only.

Stdlib unittest, no GUI, no network, no code execution.
Run from the tree root with PYTHONPATH=. (as the other
suites are run by tests/run_tests.py).
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from core import well as well_mod
from core import well_tools as well_tools
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


WELL_TOOLS = ("well_break_plan", "well_reminder", "well_winddown",
              "well_day_summary", "well_record_plan")


class BreakPlanTests(unittest.TestCase):
    def test_plan_shape_and_echo(self):
        plan = well_mod.build_break_plan(90, 15, 30)
        self.assertEqual(plan["kind"], "BREAK_PLAN")
        self.assertEqual(plan["work_minutes"], 90)
        self.assertEqual(plan["break_minutes"], 15)
        self.assertEqual(plan["focus_minutes"], 30)
        self.assertEqual(plan["stretches"], [
            {"focus_minutes": 30},
            {"rest_moment": "stand up"},
            {"focus_minutes": 30},
            {"rest_moment": "look away from the screen"},
            {"focus_minutes": 30},
        ])
        self.assertIn("not a prescription", plan["note"])
        self.assertIn("no health benefit", plan["note"])

    def test_plan_default_focus_stretch(self):
        plan = well_mod.build_break_plan(60, 10)
        self.assertEqual(plan["focus_minutes"], 25)
        self.assertEqual(plan["stretches"], [
            {"focus_minutes": 25},
            {"rest_moment": "stand up"},
            {"focus_minutes": 25},
            {"rest_moment": "look away from the screen"},
            {"focus_minutes": 10},
        ])

    def test_plan_refuses_bad_minutes(self):
        for args in ((0, 10), (-5, 10), (90, -1), (90, 10, 0),
                     (481, 10), (90, 121), (90.5, 10), (True, 10),
                     ("90", 10), (None, 10)):
            with self.assertRaises(ValueError, msg=str(args)):
                well_mod.build_break_plan(*args)


class ReminderTests(unittest.TestCase):
    def test_reminder_wording_and_no_scheduling(self):
        reminder = well_mod.build_reminder("drink some water", "after lunch")
        self.assertEqual(reminder["kind"], "REMINDER")
        self.assertEqual(reminder["wording"],
                         "Reminder: drink some water — after lunch")
        self.assertFalse(reminder["scheduled"])
        self.assertIn("Nothing is scheduled", reminder["note"])

    def test_reminder_refuses_empty_and_oversize(self):
        for trigger, when in (("", "after lunch"),
                              ("drink some water", ""),
                              ("   ", "after lunch"),
                              ("x" * 501, "after lunch")):
            with self.assertRaises(ValueError, msg=trigger[:20]):
                well_mod.build_reminder(trigger, when)

    def test_reminder_refuses_medical_mood_biometric_claim(self):
        cases = [
            ("check my headache symptoms", "now", "medical"),
            ("how is my mood today", "now", "mood"),
            ("track my sleep tonight", "now", "biometric"),
            ("prevent burnout", "now", "prevention"),
            ("treat my back pain", "now", "medical"),
        ]
        for trigger, when, kind in cases:
            with self.assertRaises(ValueError, msg=trigger):
                well_mod.build_reminder(trigger, when)


class WinddownTests(unittest.TestCase):
    def test_routine_keeps_order_and_fixed_list_only(self):
        routine = well_mod.build_winddown(
            ["dim the lights", "drink some water"])
        self.assertEqual(routine["kind"], "WIND_DOWN")
        self.assertEqual(routine["steps"],
                         ["dim the lights", "drink some water"])
        self.assertIn("not treatments", routine["note"])

    def test_routine_refuses_unknown_step_whole(self):
        with self.assertRaises(ValueError):
            well_mod.build_winddown(["dim the lights", "take a pill"])
        with self.assertRaises(ValueError):
            well_mod.build_winddown(["meditate for an hour"])

    def test_routine_refuses_empty_nonlist_nonstring(self):
        for steps in ([], "dim the lights", [123], None):
            with self.assertRaises(ValueError, msg=str(steps)):
                well_mod.build_winddown(steps)

    def test_routine_reports_duplicates_removed(self):
        routine = well_mod.build_winddown(
            ["dim the lights", "drink some water", "dim the lights"])
        self.assertEqual(routine["steps"],
                         ["dim the lights", "drink some water"])
        self.assertEqual(routine["duplicates_removed"], ["dim the lights"])


class SummaryTests(unittest.TestCase):
    def test_summary_totals_caller_numbers_only(self):
        summary = well_mod.summarize_day(300, 4, 45)
        self.assertEqual(summary["kind"], "DAY_SUMMARY")
        self.assertEqual(summary["work_minutes"], 300)
        self.assertEqual(summary["breaks_taken"], 4)
        self.assertEqual(summary["break_minutes_total"], 45)
        self.assertEqual(summary["total_minutes_accounted"], 345)
        self.assertFalse(summary["measured"])
        self.assertIn("numbers you told it", summary["note"])
        self.assertIn("no score", summary["note"])
        for field in ("score", "rating", "average", "mood", "health"):
            self.assertNotIn(field, summary)

    def test_summary_refuses_missing_or_bad_number(self):
        for args in ((None, 4, 45), (300, None, 45), (300, 4, None),
                     (-1, 4, 45), (300, -1, 45), (300.5, 4, 45)):
            with self.assertRaises(ValueError, msg=str(args)):
                well_mod.summarize_day(*args)


class RefusalTests(unittest.TestCase):
    def test_plain_text_carries_no_refusal(self):
        self.assertIsNone(well_mod.refusal_reason("drink some water"))
        self.assertIsNone(well_mod.refusal_reason("step away now"))
        self.assertIsNone(well_mod.refusal_reason(None))

    def test_refusal_reasons_name_the_kind(self):
        self.assertIn("medical", well_mod.refusal_reason("my symptoms"))
        self.assertIn("mood", well_mod.refusal_reason("my mood"))
        self.assertIn("biometric", well_mod.refusal_reason("my sleep"))
        self.assertIn("prevention", well_mod.refusal_reason("prevent colds"))

    def test_record_validation_rejects_tampered_fields(self):
        with self.assertRaises(ValueError):
            well_mod.validate_recordable_plan(
                "break", {"work_minutes": 90, "break_minutes": 15,
                          "health_score": 9})
        with self.assertRaises(ValueError):
            well_mod.validate_recordable_plan("winddown", {
                "steps": ["dim the lights", "take a pill"]})
        with self.assertRaises(ValueError):
            well_mod.validate_recordable_plan("unknown", {})


class NoSensorTests(unittest.TestCase):
    def test_no_sensor_or_capture_imports_or_identifiers(self):
        source = Path(well_mod.__file__).read_text()
        tree = ast.parse(source)
        banned_roots = {
            "cv2", "mss", "pyautogui", "keyboard", "mouse",
            "sounddevice", "pyaudio", "PIL", "win32gui", "threading",
            "sched", "subprocess", "socket", "requests", "watchers",
            # INTENTIONAL (Phase 2 B1b-1 rename): was "anticipation_v562" —
            # the guard must name the CURRENT module or it silently dies.
            "anticipation", "camera", "microphone",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], banned_roots)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self.assertNotIn(node.module.split(".")[0], banned_roots)
            elif isinstance(node, ast.FunctionDef):
                self.assertNotIn("watch", node.name)
                self.assertNotIn("capture", node.name)
                self.assertNotIn("schedule", node.name)
                self.assertNotIn("listen", node.name)
            elif isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                self.assertNotIn(name, ("eval", "exec", "compile"))


class ToolWiringTests(unittest.TestCase):
    def setUp(self):
        confirm_gate.register_handler(None)
        self._confirm_gate = confirm_gate

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in WELL_TOOLS:
            self.assertIn(name, names)
        for name in WELL_TOOLS[:4]:
            self.assertIn(name, gate._READ_ONLY_TOOLS)
        self.assertIn("well_record_plan", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["well_record_plan"],
            ("output_path",))
        # Wave 9 Build 1 adds 5 wellbeing tools (584 -> 589). Wave 9 Build 2 added 4 communications tools (589 -> 593). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 4 added 4 hardware tools (597 -> 601). Wave 9 Build 5 added 4 accessibility tools (601 -> 605). Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 757)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in WELL_TOOLS:
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        self.assertEqual(names["well_break_plan"], "Break plan")
        self.assertEqual(names["well_reminder"], "Wellbeing reminder")
        self.assertEqual(names["well_winddown"], "Wind-down routine")
        self.assertEqual(names["well_day_summary"], "Day summary")
        self.assertEqual(names["well_record_plan"], "Record wellbeing plan")

    def test_descriptors_and_summary_through_funnel(self):
        plan = _result(tools.execute("well_break_plan", {
            "work_minutes": 90, "break_minutes": 15, "focus_minutes": 30}))
        self.assertEqual(plan["kind"], "BREAK_PLAN")
        reminder = _result(tools.execute("well_reminder", {
            "trigger": "drink some water", "time_text": "after lunch"}))
        self.assertEqual(reminder["kind"], "REMINDER")
        self.assertFalse(reminder["scheduled"])
        routine = _result(tools.execute("well_winddown", {
            "steps_json": json.dumps(["dim the lights",
                                      "drink some water"])}))
        self.assertEqual(routine["kind"], "WIND_DOWN")
        summary = _result(tools.execute("well_day_summary", {
            "work_minutes": 300, "breaks_taken": 4,
            "break_minutes_total": 45}))
        self.assertEqual(summary["total_minutes_accounted"], 345)

    def test_record_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "plan.json")
            result = tools.execute("well_record_plan", {
                "plan_kind": "break",
                "plan_json": json.dumps({"work_minutes": 90,
                                         "break_minutes": 15,
                                         "focus_minutes": 30}),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "plan.json")
            data = _result(tools.execute("well_record_plan", {
                "plan_kind": "winddown",
                "plan_json": json.dumps(
                    {"steps": ["dim the lights", "drink some water"]}),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["executed"])
            self.assertFalse(data["scheduled"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["plan"]["steps"],
                             ["dim the lights", "drink some water"])
            self.assertEqual(sorted(Path(tmp).iterdir()), [Path(target)])

    def test_record_refuses_tampered_plan(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "plan.json")
            data = _result(tools.execute("well_record_plan", {
                "plan_kind": "break",
                "plan_json": json.dumps({"work_minutes": 90,
                                         "break_minutes": 15,
                                         "health_score": 9}),
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
