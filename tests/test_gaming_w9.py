"""Gaming deltas tests — descriptors and records only,
under the shipped v5.78 Gaming wall, no game read,
controlled, or automated, no GPL tool bundled.

Proves: the session descriptor echoes exactly
caller-supplied title, mode, and notes, with read_live,
controlled, and automated all False, and a note saying
no game was read, controlled, automated, or observed;
the macro descriptor echoes caller-supplied steps in
order, duplicates kept once and reported, executed
False; the drive-only media note carries bundled,
integrated, and shipped all False, drive_only True,
and the plan-recorded GPL label for OBS, FFmpeg GPL
builds with libx264, and LosslessCut; a gaming record
re-validates and refuses a tampered descriptor, a
competitive mode, or a bundled claim; any competitive
mode, memory-read, injection, cheat, or anti-cheat
input refuses whole; any automation-shaped macro step
refuses whole; no game-memory, injection,
input-automation, or screen-capture code exists in the
module (AST on imports and banned call names, not on
prose); tool classifications, grouping in GROUP_MEMBERS
with friendly plain-language names, and funnel confirm
refusal without a handler. Registry moves 609 -> 613.
Nothing here writes a real file except the confirm-gated
record tool under a registered handler, into temp dirs
only.

Stdlib unittest, no GUI, no game, no network, no code
execution. Run from the tree root with PYTHONPATH=. (as
the other suites are run by tests/run_tests.py).
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from core import gaming as gaming_mod
from core import gaming_tools as gaming_tools
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


GAMING_TOOLS = ("game_session_describe", "game_macro_describe",
                "game_media_note", "game_record_gaming")


class DescriptorTests(unittest.TestCase):
    def test_session_echoes_caller_fields_only(self):
        desc = gaming_mod.build_game_session(
            "Stardew Valley", "single-player",
            notes="cozy evening session")
        self.assertEqual(desc["kind"], "GAME_SESSION")
        self.assertEqual(desc["game_title"], "Stardew Valley")
        self.assertEqual(desc["mode"], "single-player")
        self.assertEqual(desc["notes"], "cozy evening session")
        for flag in ("read_live", "controlled", "automated"):
            self.assertFalse(desc[flag], flag)
        self.assertIn("No game was read", desc["note"])

    def test_session_mode_normalisation_and_refusal(self):
        desc = gaming_mod.build_game_session("It Takes Two",
                                             "co op")
        self.assertEqual(desc["mode"], "co-op")
        desc = gaming_mod.build_game_session("Hades",
                                             "single player")
        self.assertEqual(desc["mode"], "single-player")
        for mode in ("competitive", "ranked", "multiplayer",
                     "online", "pvp", "tournament", "arcade"):
            with self.assertRaises(ValueError, msg=mode):
                gaming_mod.build_game_session("Hades", mode)

    def test_session_action_describe_accepted_others_refused(self):
        gaming_mod.build_game_session("Hades", "single-player",
                                      action="describe")
        for action in ("play", "launch", "automate", "control",
                       "read"):
            with self.assertRaises(ValueError, msg=action):
                gaming_mod.build_game_session("Hades", "single-player",
                                              action=action)

    def test_session_banned_phrases_refused_whole(self):
        for notes in ("read memory for health values",
                      "inject a dll into the game",
                      "use an aimbot for ranked play",
                      "bypass the anti-cheat check",
                      "the game was read by a trainer"):
            with self.assertRaises(ValueError, msg=notes):
                gaming_mod.build_game_session("Hades", "single-player",
                                              notes=notes)

    def test_session_live_claim_words_refused(self):
        for notes in ("the game was controlled by a script",
                      "the game was automated overnight"):
            with self.assertRaises(ValueError, msg=notes):
                gaming_mod.build_game_session("Hades", "single-player",
                                              notes=notes)

    def test_session_missing_or_empty_fields_refused(self):
        for title, mode in (("", "single-player"),
                            ("Hades", ""),
                            (None, "single-player"),
                            ("Hades", None)):
            with self.assertRaises(ValueError):
                gaming_mod.build_game_session(title, mode)

    def test_macro_echoes_steps_in_order_duplicates_once(self):
        macro = gaming_mod.build_game_macro(
            "single-player",
            ["cast fireball", "open inventory", "cast fireball"])
        self.assertEqual(macro["kind"], "GAME_MACRO")
        self.assertEqual(macro["mode"], "single-player")
        self.assertEqual(macro["steps"],
                         ["cast fireball", "open inventory"])
        self.assertEqual(macro["duplicates_removed"],
                         ["cast fireball"])
        self.assertFalse(macro["executed"])
        self.assertFalse(macro["automated"])

    def test_macro_competitive_mode_refused_never_downgraded(self):
        for mode in ("competitive", "ranked", "multiplayer"):
            with self.assertRaises(ValueError, msg=mode):
                gaming_mod.build_game_macro(mode, ["cast fireball"])

    def test_macro_automation_shaped_steps_refused_whole(self):
        for step in ("press F to cast", "click the enemy",
                     "move mouse to target", "sendinput F",
                     "automate the combo"):
            with self.assertRaises(ValueError, msg=step):
                gaming_mod.build_game_macro("single-player", [step])

    def test_macro_empty_or_oversize_refused(self):
        with self.assertRaises(ValueError):
            gaming_mod.build_game_macro("single-player", [])
        with self.assertRaises(ValueError):
            gaming_mod.build_game_macro("single-player", "not a list")

    def test_media_note_gpl_tools_drive_only(self):
        for tool, label in (("OBS", "GPL-2.0"),
                            ("FFmpeg", "GPL build with libx264"),
                            ("LosslessCut", "GPL-2.0-only")):
            note = gaming_mod.build_media_note(tool)
            self.assertEqual(note["kind"], "MEDIA_NOTE")
            self.assertEqual(note["licence_label"], label)
            self.assertFalse(note["bundled"])
            self.assertFalse(note["integrated"])
            self.assertFalse(note["shipped"])
            self.assertTrue(note["drive_only"])
            self.assertIn("NOT bundled", note["note"])

    def test_media_note_softened_gpl_label_refused(self):
        with self.assertRaises(ValueError):
            gaming_mod.build_media_note("OBS", "MIT")

    def test_media_note_bundled_claim_refused(self):
        for kwargs in ({"bundled": True}, {"integrated": True},
                       {"shipped": True}):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                gaming_mod.build_media_note("OBS", **kwargs)

    def test_media_note_unknown_tool_needs_label(self):
        with self.assertRaises(ValueError):
            gaming_mod.build_media_note("Some New Tool")
        note = gaming_mod.build_media_note("Some New Tool", "CC0")
        self.assertEqual(note["licence_label"], "CC0")
        self.assertFalse(note["bundled"])

    def test_record_rebuilds_and_refuses_tampered(self):
        session = gaming_mod.validate_recordable_gaming(
            "session", {"game_title": "Hades", "mode": "single-player",
                        "notes": "evening run"})
        self.assertEqual(session["game_title"], "Hades")
        macro = gaming_mod.validate_recordable_gaming(
            "macro", {"mode": "co-op",
                      "steps": ["cast fireball", "cast fireball"]})
        self.assertEqual(macro["steps"], ["cast fireball"])
        note = gaming_mod.validate_recordable_gaming(
            "media-note", {"tool_name": "OBS",
                           "licence_label": "GPL-2.0"})
        self.assertEqual(note["licence_label"], "GPL-2.0")
        for kind, fields in (
                ("session", {"game_title": "Hades",
                             "mode": "competitive"}),
                ("session", {"game_title": "Hades",
                             "mode": "single-player",
                             "read_live": True}),
                ("macro", {"mode": "single-player",
                           "steps": ["press F"]}),
                ("media-note", {"tool_name": "OBS",
                                "licence_label": "GPL-2.0",
                                "bundled": True}),
                ("unknown", {})):
            with self.assertRaises(ValueError, msg=str((kind, fields))):
                gaming_mod.validate_recordable_gaming(kind, fields)

    def test_no_game_or_automation_imports_ast(self):
        banned_imports = {"pymem", "ctypes", "pyautogui",
                          "keyboard", "mouse", "mss", "cv2", "PIL",
                          "win32gui", "win32api", "obswebsocket"}
        banned_calls = {"read_process_memory", "write_process_memory",
                        "inject", "press", "click", "move", "hotkey",
                        "screenshot"}
        for filename in ("core/gaming.py",
                         "core/gaming_tools.py"):
            path = Path(__file__).resolve().parent.parent / filename
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0],
                                         banned_imports, filename)
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotIn((node.module or "").split(".")[0],
                                     banned_imports, filename)
                elif isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name):
                        called = func.id
                    elif isinstance(func, ast.Attribute):
                        called = func.attr
                    else:
                        continue
                    self.assertNotIn(called, banned_calls,
                                     f"banned call {called} in {filename}")


class WiringTests(unittest.TestCase):
    def setUp(self):
        self._confirm_gate = confirm_gate
        self._confirm_gate.register_handler(None)

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in GAMING_TOOLS:
            self.assertIn(name, names)
        for name in GAMING_TOOLS[:3]:
            self.assertIn(name, gate._READ_ONLY_TOOLS)
        self.assertIn("game_record_gaming", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["game_record_gaming"],
            ("output_path",))
        # Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 758)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in GAMING_TOOLS:
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        self.assertEqual(names["game_session_describe"],
                         "Game session")
        self.assertEqual(names["game_macro_describe"], "Game macro")
        self.assertEqual(names["game_media_note"],
                         "Drive-only media note")
        self.assertEqual(names["game_record_gaming"], "Record gaming")

    def test_readonly_tools_through_funnel(self):
        desc = _result(tools.execute("game_session_describe", {
            "game_title": "Hades", "mode": "single-player",
            "notes": "evening run"}))
        self.assertEqual(desc["kind"], "GAME_SESSION")
        self.assertFalse(desc["read_live"])
        self.assertFalse(desc["controlled"])
        macro = _result(tools.execute("game_macro_describe", {
            "mode": "single-player",
            "steps": ["cast fireball", "open inventory"]}))
        self.assertEqual(macro["kind"], "GAME_MACRO")
        self.assertFalse(macro["executed"])
        note = _result(tools.execute("game_media_note", {
            "tool_name": "OBS"}))
        self.assertEqual(note["kind"], "MEDIA_NOTE")
        self.assertFalse(note["bundled"])
        self.assertFalse(note["shipped"])
        # A handler-level refusal flows through the funnel
        # as a success envelope carrying ok False — the
        # refusal itself is in the result, not the envelope.
        refused = _result(tools.execute("game_session_describe", {
            "game_title": "Hades", "mode": "ranked"}))
        self.assertFalse(refused["ok"], refused)
        self.assertIn("single-player", refused["reason"], refused)

    def test_record_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "gaming.json")
            result = tools.execute("game_record_gaming", {
                "record_kind": "session",
                "record_json": json.dumps(
                    {"game_title": "Hades", "mode": "single-player",
                     "notes": "evening run"}),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "gaming.json")
            data = _result(tools.execute("game_record_gaming", {
                "record_kind": "macro",
                "record_json": json.dumps(
                    {"mode": "co-op",
                     "steps": ["cast fireball"]}),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["executed"])
            self.assertFalse(data["read_live"])
            self.assertFalse(data["bundled"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["gaming"]["mode"], "co-op")
            self.assertFalse(written["read_live"])
            self.assertFalse(written["bundled"])
            self.assertEqual(sorted(Path(tmp).iterdir()), [Path(target)])

    def test_record_refuses_tampered_or_competitive_gaming(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "gaming.json")
            for kind, fields in (
                    ("session", {"game_title": "Hades",
                                 "mode": "competitive"}),
                    ("session", {"game_title": "Hades",
                                 "mode": "single-player",
                                 "read_live": True}),
                    ("media-note", {"tool_name": "OBS",
                                    "licence_label": "GPL-2.0",
                                    "bundled": True})):
                data = _result(tools.execute("game_record_gaming", {
                    "record_kind": kind,
                    "record_json": json.dumps(fields),
                    "output_path": target}))
                self.assertFalse(data["ok"], data)
            self.assertFalse(Path(target).exists())


if __name__ == "__main__":
    unittest.main()
