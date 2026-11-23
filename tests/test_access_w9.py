"""Accessibility deltas tests — descriptors and records
only, nothing read live, nothing inferred, no one
labelled, nothing scored.

Proves: the text-clarity descriptor counts sentences,
words, long sentences, and fixed-list jargon words, and
says nothing about the reader; a score request refuses
whole; the window description lists a caller-supplied
snapshot's controls in order and states nothing was
read live, while a live or captured snapshot refuses
whole; the voice-first routine takes fixed-list steps
only, in order, duplicates kept once and reported, and
an unknown step refuses whole; a preference record
re-validates and refuses a disability or health label,
a score or unexpected field, or an inferred flag; no
screen-capture, OCR, or sensor code exists in the
module (AST on imports and capture-shaped call names,
not on prose); tool classifications, grouping in
GROUP_MEMBERS with friendly plain-language names, and
funnel confirm refusal without a handler. Registry
moves 601 -> 605.
Nothing here writes a real file except the confirm-gated
record tool under a registered handler, into temp dirs
only.

Stdlib unittest, no GUI, no network, no code execution.
Run from the tree root with PYTHONPATH=. (as the other
suites are run by tests/run_tests.py).
"""

import ast
import json
import re
import tempfile
import unittest
from pathlib import Path

from core import accessibility as access_mod
from core import accessibility_tools as access_tools
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


ACCESS_TOOLS = ("access_text_clarity", "access_window_describe",
                "access_voice_first_routine",
                "access_record_preference")


class ClarityTests(unittest.TestCase):
    def test_clarity_counts_only_never_about_reader(self):
        note = access_mod.build_text_clarity(
            "Keep it short. Use plain words and say one thing at "
            "a time, so the reader can follow without effort.")
        self.assertEqual(note["kind"], "TEXT_CLARITY")
        self.assertEqual(note["sentence_count"], 2)
        self.assertEqual(note["long_sentence_count"], 0)
        self.assertFalse(note["about_reader"])
        self.assertIsNone(note["score"])
        self.assertIn("not a reading level", note["note"])
        self.assertIn("nothing about the reader", note["note"])

    def test_clarity_long_sentences_and_jargon_flagged(self):
        long_sentence = " ".join(["word"] * 31) + "."
        note = access_mod.build_text_clarity(
            "We will utilize the new system to leverage "
            "synergy across teams. " + long_sentence)
        self.assertEqual(note["sentence_count"], 2)
        self.assertEqual(note["long_sentence_count"], 1)
        self.assertIn("utilize", note["jargon_found"])
        self.assertIn("leverage", note["jargon_found"])
        self.assertIn("synergy", note["jargon_found"])

    def test_clarity_score_request_refused_whole(self):
        with self.assertRaises(ValueError):
            access_mod.build_text_clarity("Keep it short.",
                                          want_score=True)
        with self.assertRaises(ValueError):
            access_mod.build_text_clarity(
                "Keep it short.", want_score="reading level")
        for text in (None, "", "   ", 42):
            with self.assertRaises(ValueError, msg=str(text)):
                access_mod.build_text_clarity(text)
        with self.assertRaises(ValueError):
            access_mod.build_text_clarity("x" * 10001)

    def test_clarity_prose_is_not_a_label(self):
        # A word in prose is not a label placed on anyone:
        # the descriptor counts and never repeats the
        # text, so prose mentioning a condition is counted,
        # not refused. Labels are refused on preference
        # records, where a label is actually placed.
        note = access_mod.build_text_clarity(
            "The guide covers disability rights in plain words.")
        self.assertEqual(note["sentence_count"], 1)
        self.assertNotIn("disability", json.dumps(note))


class WindowTests(unittest.TestCase):
    def _snapshot(self):
        return {"controls": [
            {"name": "Search box", "role": "text field"},
            {"name": "Send", "role": "button"},
            {"name": "Messages", "role": "list"},
        ]}

    def test_window_description_echoes_snapshot_in_order(self):
        desc = access_mod.describe_window(self._snapshot())
        self.assertEqual(desc["kind"], "WINDOW_DESCRIPTION")
        self.assertEqual(desc["controls_count"], 3)
        self.assertEqual(desc["layout"],
                         "Top to bottom: Search box (text field); "
                         "Send (button); Messages (list)")
        self.assertFalse(desc["live_read"])
        self.assertIn("Nothing was read from a live screen",
                      desc["note"])

    def test_window_live_or_captured_snapshot_refused(self):
        for snapshot in (
                dict(self._snapshot(), live=True),
                dict(self._snapshot(), captured=True),
                dict(self._snapshot(), source="screenshot"),
                dict(self._snapshot(), source="live"),
                dict(self._snapshot(), source="capture")):
            with self.assertRaises(ValueError, msg=str(snapshot)):
                access_mod.describe_window(snapshot)

    def test_window_malformed_snapshot_refused(self):
        for snapshot in (None, "snapshot", {},
                         {"controls": []},
                         {"controls": "Search box"},
                         {"controls": [{"name": "Send"}]},
                         {"controls": [{"role": "button"}]},
                         {"controls": [{"name": "", "role": "button"}]},
                         {"controls": [{"name": "Send", "role": "button"}] * 201}):
            with self.assertRaises(ValueError, msg=str(snapshot)):
                access_mod.describe_window(snapshot)


class RoutineTests(unittest.TestCase):
    def test_routine_fixed_list_in_order(self):
        routine = access_mod.build_routine(
            ["voice command", "larger target", "pause"])
        self.assertEqual(routine["kind"], "VOICE_FIRST_ROUTINE")
        self.assertEqual(routine["steps"],
                         ["voice command", "larger target", "pause"])
        self.assertEqual(routine["duplicates_removed"], 0)

    def test_routine_duplicates_kept_once_and_reported(self):
        routine = access_mod.build_routine(
            ["pause", "voice command", "pause",
             "keyboard alternative", "pause"])
        self.assertEqual(routine["steps"],
                         ["pause", "voice command",
                          "keyboard alternative"])
        self.assertEqual(routine["duplicates_removed"], 2)

    def test_routine_unknown_step_refused_whole(self):
        for steps in (["voice command", "eye tracking"],
                      ["read my mind"],
                      "voice command",
                      [],
                      [42],
                      ["voice command"] * 21):
            with self.assertRaises(ValueError, msg=str(steps)):
                access_mod.build_routine(steps)


class PreferenceTests(unittest.TestCase):
    def test_preference_rebuild_ok(self):
        pref = access_mod.validate_recordable_preference(
            {"label": "Larger text", "enabled": True})
        self.assertEqual(pref["kind"], "ACCESS_PREFERENCE")
        self.assertEqual(pref["label"], "Larger text")
        self.assertTrue(pref["enabled"])
        self.assertIn("never a person", pref["note"])

    def test_preference_label_words_refused(self):
        for label in ("Disabled mode", "blind users setting",
                      "dyslexia helper", "diagnosis view",
                      "cure mode", "medical panel"):
            with self.assertRaises(ValueError, msg=label):
                access_mod.validate_recordable_preference(
                    {"label": label, "enabled": True})

    def test_preference_unexpected_or_inferred_refused(self):
        for fields in ({"label": "Larger text", "enabled": True,
                        "score": 8},
                       {"label": "Larger text", "enabled": True,
                        "disability": "x"},
                       {"label": "Larger text", "enabled": True,
                        "inferred": True},
                       {"label": "Larger text"},
                       {"label": "Larger text", "enabled": "yes"},
                       {"label": "", "enabled": True},
                       {"label": "Larger text", "enabled": True,
                        "extra": "x"},
                       "not a preference"):
            with self.assertRaises(ValueError, msg=str(fields)):
                access_mod.validate_recordable_preference(fields)


class NoCaptureTests(unittest.TestCase):
    def test_no_capture_ocr_or_sensor_code(self):
        for filename in ("core/accessibility.py",
                         "core/accessibility_tools.py"):
            source = Path(__file__).resolve().parent.parent.joinpath(
                filename).read_text()
            tree = ast.parse(source)
            banned_imports = {
                "mss", "PIL", "cv2", "pyautogui", "pytesseract",
                "win32gui", "uiautomation", "pywinauto",
                "pyaudio", "sounddevice", "socket", "requests",
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(
                            alias.name.split(".")[0], banned_imports,
                            alias.name)
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(
                        node.module.split(".")[0], banned_imports,
                        node.module)
                if isinstance(node, ast.Call):
                    func = node.func
                    called = None
                    if isinstance(func, ast.Name):
                        called = func.id
                    elif isinstance(func, ast.Attribute):
                        called = func.attr
                    self.assertNotIn(
                        called, {"screenshot", "capture_screen",
                                 "grab_screen", "read_screen",
                                 "image_to_string", "listen",
                                 "start_listening"},
                        f"capture-shaped call {called} in {filename}")
        # The module itself imports nothing beyond
        # __future__ annotations and the stdlib re
        # module: there is nothing here to capture
        # with, by construction.
        tree = ast.parse(Path(__file__).resolve().parent.parent.joinpath(
            "core", "accessibility.py").read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name.split(".")[0]
                               for a in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
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
        for name in ACCESS_TOOLS:
            self.assertIn(name, names)
        for name in ACCESS_TOOLS[:3]:
            self.assertIn(name, gate._READ_ONLY_TOOLS)
        self.assertIn("access_record_preference",
                      gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["access_record_preference"],
            ("output_path",))
        # Wave 9 Build 5 adds 4 accessibility tools (601 -> 605). Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 772)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in ACCESS_TOOLS:
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        self.assertEqual(names["access_text_clarity"], "Text clarity")
        self.assertEqual(names["access_window_describe"],
                         "Window description")
        self.assertEqual(names["access_voice_first_routine"],
                         "Voice-first routine")
        self.assertEqual(names["access_record_preference"],
                         "Record accessibility preference")

    def test_readonly_tools_through_funnel(self):
        note = _result(tools.execute("access_text_clarity", {
            "text": "Keep it short. Say one thing at a time."}))
        self.assertEqual(note["kind"], "TEXT_CLARITY")
        self.assertIsNone(note["score"])
        desc = _result(tools.execute("access_window_describe", {
            "snapshot_json": json.dumps({"controls": [
                {"name": "Send", "role": "button"}]})}))
        self.assertEqual(desc["controls_count"], 1)
        self.assertFalse(desc["live_read"])
        routine = _result(tools.execute(
            "access_voice_first_routine", {
                "steps_json": json.dumps(["voice command",
                                          "pause"])}))
        self.assertEqual(routine["steps"],
                         ["voice command", "pause"])
        # A handler-level refusal flows through the funnel
        # as a success envelope carrying ok False — the
        # refusal itself is in the result, not the envelope.
        refused = _result(tools.execute("access_text_clarity", {
            "text": "Keep it short.", "want_score": True}))
        self.assertFalse(refused["ok"], refused)
        self.assertIn("score", refused["reason"], refused)
        refused_live = _result(tools.execute(
            "access_window_describe", {
                "snapshot_json": json.dumps(
                    {"controls": [{"name": "Send",
                                    "role": "button"}],
                     "live": True})}))
        self.assertFalse(refused_live["ok"], refused_live)

    def test_record_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "preference.json")
            result = tools.execute("access_record_preference", {
                "preference_json": json.dumps(
                    {"label": "Larger text", "enabled": True}),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "preference.json")
            data = _result(tools.execute("access_record_preference", {
                "preference_json": json.dumps(
                    {"label": "Larger text", "enabled": True}),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["executed"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["preference"]["label"],
                             "Larger text")
            self.assertTrue(written["preference"]["enabled"])
            self.assertEqual(sorted(Path(tmp).iterdir()), [Path(target)])

    def test_record_refuses_labelled_or_tampered_preference(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "preference.json")
            for fields in ({"label": "Disabled mode",
                            "enabled": True},
                           {"label": "Larger text", "enabled": True,
                            "score": 8},
                           {"label": "Larger text", "enabled": True,
                            "extra": "x"}):
                data = _result(tools.execute(
                    "access_record_preference", {
                        "preference_json": json.dumps(fields),
                        "output_path": target}))
                self.assertFalse(data["ok"], data)
            self.assertFalse(Path(target).exists())


if __name__ == "__main__":
    unittest.main()
