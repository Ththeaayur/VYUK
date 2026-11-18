"""R8-4 screen-break — 20-20-20 eye rest, guided deep breathing, and
the water-glass counter as VYUK's own code (PORT, not adopt: the
upstream is a single tkinter file and VYUK is PySide6; no new
dependency, requirements.txt untouched). Pins: the timer chain
(start / status / snooze / stop on the real scheduler, cleaned up
after every test), the breathing phase tables (box 4-4-4-4,
relaxing 4-7-8, clamps, plain-language cues), the glass counter
(re-read after write, real-store round trip restored), the funnel
gates, and the wellbeing boundary (no diagnosis / scoring /
enforcement, no trademarked names, no tkinter).
"""

from __future__ import annotations

import ast
import datetime
import json
import pathlib
import unittest

from core import eye_rest
from core.eye_rest import breathing_guide, eye_rest_timer, log_glass
from core.funnel.tools_gate import _FREE_BY_DESIGN, _READ_ONLY_TOOLS
from core.funnel.tools_registry import tools as _registry
from core.skills_productivity3_focus_day import _chain_pending

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _FakeStore:
    """Dict-backed stand-in for memory_store (get_fact / save_fact)."""

    def __init__(self):
        self.facts = {}

    def get_fact(self, category, key):
        return self.facts.get((category, key))

    def save_fact(self, category, key, value):
        self.facts[(category, key)] = {"value": value}
        return True


class TestTimer(unittest.TestCase):
    def tearDown(self):
        # Every timer test leaves the chain stopped, so no nudge
        # leaks into the next test or suite.
        eye_rest_timer("stop")

    def test_start_schedules_nudges_and_status_reports_them(self):
        out = eye_rest_timer("start", interval_minutes=120)
        self.assertIn("Eye-rest reminders on", out)
        self.assertIn("every 120 minutes", out)
        status = eye_rest_timer("status")
        self.assertIn("reminders are on", status)
        self.assertIn("4 nudges left", status)

    def test_start_message_names_the_20_20_20_rule(self):
        eye_rest_timer("start", interval_minutes=120)
        pending = _chain_pending("Eye rest")
        self.assertEqual(len(pending), 4)
        for record in pending:
            self.assertIn("20-20-20", record["message"])
            self.assertIn("20 feet", record["message"])

    def test_stop_cancels_and_reports_the_count(self):
        eye_rest_timer("start", interval_minutes=120)
        out = eye_rest_timer("stop")
        self.assertIn("cancelled 4 upcoming nudges", out)
        self.assertIn("aren't running", eye_rest_timer("status"))

    def test_stop_when_not_running_says_so(self):
        self.assertEqual(eye_rest_timer("stop"),
                         "Eye-rest reminders weren't running.")

    def test_snooze_moves_the_next_nudge(self):
        eye_rest_timer("start", interval_minutes=120)
        out = eye_rest_timer("snooze", snooze_minutes=10)
        self.assertTrue(out.startswith("Snoozed — your next eye-rest "
                                       "nudge is now at "))
        self.assertIn("reminders are on", eye_rest_timer("status"))

    def test_snooze_when_not_running_says_so(self):
        self.assertEqual(
            eye_rest_timer("snooze"),
            "No eye-rest nudge to snooze — the reminders aren't running.")

    def test_interval_is_clamped(self):
        out = eye_rest_timer("start", interval_minutes=1)
        self.assertIn("every 5 minutes", out)
        out = eye_rest_timer("start", interval_minutes=999)
        self.assertIn("every 120 minutes", out)

    def test_unknown_action_is_refused_plainly(self):
        self.assertEqual(
            eye_rest_timer("explode"),
            "Eye-rest timer actions are 'start', 'stop', "
            "'status' and 'snooze'.")


class TestBreathing(unittest.TestCase):
    def test_box_phases_are_4_4_4_4(self):
        result = breathing_guide("box", cycles=1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["label"], "Box breathing")
        self.assertEqual([(p["phase"], p["seconds"])
                          for p in result["phases"]],
                         [("inhale", 4), ("hold", 4),
                          ("exhale", 4), ("hold", 4)])
        self.assertEqual(result["total_seconds"], 16)

    def test_relaxing_phases_are_4_7_8(self):
        result = breathing_guide("relaxing", cycles=1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["label"], "Relaxing breathing")
        self.assertEqual([(p["phase"], p["seconds"])
                          for p in result["phases"]],
                         [("inhale", 4), ("hold", 7), ("exhale", 8)])
        self.assertEqual(result["total_seconds"], 19)

    def test_cycles_are_clamped_and_offsets_accumulate(self):
        one = breathing_guide("box", cycles=0)
        self.assertEqual(one["cycles"], 1)
        ten = breathing_guide("box", cycles=99)
        self.assertEqual(ten["cycles"], 10)
        self.assertEqual(ten["total_seconds"], 160)
        self.assertEqual(ten["phases"][4]["cycle"], 2)
        self.assertEqual(ten["phases"][4]["starts_at_s"], 16)
        self.assertEqual(ten["phases"][4]["phase"], "inhale")

    def test_unknown_pattern_is_refused_plainly(self):
        result = breathing_guide("wimhof", cycles=2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"],
                         "Breathing patterns are 'box' and 'relaxing'.")

    def test_cues_are_plain_language(self):
        result = breathing_guide("relaxing", cycles=1)
        cues = {p["cue"] for p in result["phases"]}
        self.assertEqual(cues, {"Breathe in slowly", "Hold",
                                "Breathe out slowly"})
        self.assertIn("suggestion, not a treatment", result["note"])


class TestGlasses(unittest.TestCase):
    def setUp(self):
        self._real_store = eye_rest._memory_store
        self.fake = _FakeStore()
        eye_rest._memory_store = self.fake

    def tearDown(self):
        eye_rest._memory_store = self._real_store

    def test_log_increments_and_status_reports_millilitres(self):
        self.assertEqual(log_glass("log"),
                         "Logged one glass — that's 1 today (250 ml).")
        self.assertEqual(log_glass("log"),
                         "Logged one glass — that's 2 today (500 ml).")
        self.assertEqual(log_glass("status"), "2 glasses today (500 ml).")

    def test_status_when_empty_says_so(self):
        self.assertEqual(log_glass("status"),
                         "No glasses logged today yet.")

    def test_reset_clears_the_count(self):
        log_glass("log")
        log_glass("log")
        self.assertEqual(log_glass("reset"),
                         "Today's glass count is back to zero.")
        self.assertEqual(log_glass("status"),
                         "No glasses logged today yet.")

    def test_unknown_action_is_refused_plainly(self):
        self.assertEqual(log_glass("drink"),
                         "Glass actions are 'log', 'status' and 'reset'.")

    def test_count_is_re_read_after_the_write(self):
        # The handler must report what the store actually holds, not
        # what it computed: rig the store so the re-read disagrees
        # with the write, and the reported count follows the re-read.
        class _RiggedStore(_FakeStore):
            def get_fact(self, category, key):
                return {"value": json.dumps({"glasses": 9, "ml": 2250})}

        eye_rest._memory_store = _RiggedStore()
        self.assertEqual(
            log_glass("log"),
            "Logged one glass — that's 9 today (2250 ml).")

    def test_real_store_round_trip_then_restored(self):
        eye_rest._memory_store = self._real_store
        today = datetime.date.today().isoformat()
        original = eye_rest._load_glasses(today)
        try:
            out = log_glass("log")
            self.assertIn(f"that's {original + 1} today", out)
            self.assertEqual(eye_rest._load_glasses(today), original + 1)
        finally:
            eye_rest._save_glasses(today, original)


class TestRegistryAndBoundary(unittest.TestCase):
    def test_registry_total_is_756(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        self.assertEqual(len(_registry.get_declarations()), 756)  # INTENTIONAL — 681 -> 684: eye_rest_timer, breathing_guide, log_glass (R8-4 screen-break port, 2026-11-10); R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine

    def test_three_tools_registered_with_expected_gates(self):
        names = {d["name"] for d in _registry.get_declarations()}
        for name in ("eye_rest_timer", "breathing_guide", "log_glass"):
            self.assertIn(name, names)
        self.assertEqual(_FREE_BY_DESIGN.get("eye_rest_timer"), "app-state")
        self.assertEqual(_FREE_BY_DESIGN.get("log_glass"), "app-state")
        self.assertIn("breathing_guide", _READ_ONLY_TOOLS)
        self.assertNotIn("eye_rest_timer", _READ_ONLY_TOOLS)
        self.assertNotIn("log_glass", _READ_ONLY_TOOLS)

    def test_three_tools_grouped_in_everyday_skills(self):
        from core.catalogue_snapshot import GROUP_MEMBERS
        group = GROUP_MEMBERS.get("Everyday skills", ())
        for name in ("eye_rest_timer", "breathing_guide", "log_glass"):
            self.assertIn(name, group)

    def test_no_trademarked_names_or_enforcement_in_copy(self):
        source = (ROOT / "core" / "eye_rest.py").read_text(encoding="utf-8")
        lowered = source.lower()
        # Trademarked names appear nowhere, not even in the docstring.
        for banned in ("pomodoro", "wim hof", "recovery score"):
            self.assertNotIn(banned, lowered, banned)
        # "enforcement" names the forbidden behaviour in the module's
        # own boundary docstring, so strip the docstring first (the
        # R7-8 lesson) and scan the code + user-facing copy only.
        tree = ast.parse(source)
        docstring = ast.get_docstring(tree) or ""
        code = source.replace(docstring, "")
        for banned in ("enforce", "lock input"):
            self.assertNotIn(banned, code.lower(), banned)
        self.assertIn("No diagnosis, no scoring", source)
        for decl in _registry.get_declarations():
            if decl["name"] in ("eye_rest_timer", "breathing_guide",
                                "log_glass"):
                lowered = decl["description"].lower()
                self.assertNotIn("pomodoro", lowered)
                self.assertNotIn("wim hof", lowered)

    def test_no_tkinter_and_no_new_dependency(self):
        tree = ast.parse((ROOT / "core" / "eye_rest.py").read_text(
            encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("tkinter", imported)
        self.assertNotIn("pystray", imported)
        requirements = (ROOT / "requirements.txt").read_text(
            encoding="utf-8").lower()
        for banned in ("screen-break", "screen_break", "pystray",
                       "tkinter"):
            self.assertNotIn(banned, requirements, banned)
        third_party = (ROOT / "THIRD_PARTY_LICENSES.md").read_text(
            encoding="utf-8").lower()
        self.assertNotIn("screen-break", third_party)


if __name__ == "__main__":
    unittest.main()
