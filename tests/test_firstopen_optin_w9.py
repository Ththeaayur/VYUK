"""First-open continuous opt-in tests — the choice mechanism,
not continuous capture itself.

Proves: the first-open prompt is plain language (no jargon
term from the stated list) and states what it watches, that
everything stays on the user's own machine, that it is OFF
until they say yes, that a small sign shows while it is on,
and how to switch it off and delete what it kept; the
settings entry carries the same words and stays available
all the way (reversible at any time); the choice record
defaults to disabled; a wrong-scope record (including the
Wave 8 suggestion-cards scope), a missing record, a False
record, and an incomplete record (no chooser, timestamp,
retention rule, or deletion rule) all return disabled with
a stated reason; recording a choice with confirm works and
disabling stops the gate; the per-feature delete is
confirm-gated, refuses without a handler, and refuses whole
on a missing folder, an app core/data path, a credential
file inside, or a symlink entry; no watcher or capture
code exists in the module (AST); tool classifications,
grouping in GROUP_MEMBERS with friendly plain-language
names, and funnel confirm refusal without a handler.
Registry moves 580 -> 584. Nothing here writes a real file
except the confirm-gated record tool under a registered
handler, into temp dirs, and the delete tool on temp-dir
fixtures only.

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

from core import firstopen_optin as optin_mod
from core import firstopen_tools as optin_tools
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


def _choice(**overrides):
    record = {
        "allowed": True,
        "chooser": "the user",
        "scope": "continuous-display",
        "timestamp": "2026-10-26T22:00:00+05:30",
        "retention_rule": "kept notes are thrown away after 7 days",
        "deletion_rule": "you can delete everything it kept at any time",
    }
    record.update(overrides)
    return record


def _choice_record(**overrides):
    return optin_mod.build_choice_record(**_choice(**overrides))


class PromptTests(unittest.TestCase):
    def test_prompt_shape_and_defaults(self):
        prompt = optin_mod.first_open_prompt()
        self.assertEqual(prompt["kind"], "FIRST_OPEN_PROMPT")
        self.assertEqual(prompt["choices"],
                         ["Yes, switch it on", "No, keep it off"])
        self.assertEqual(prompt["default_choice"], "No, keep it off")
        self.assertTrue(prompt["default_off"])
        self.assertTrue(prompt["local_only"])

    def test_prompt_states_what_it_watches(self):
        text = " ".join(optin_mod.first_open_prompt()["paragraphs"])
        self.assertIn("what is on your screen while you work", text)

    def test_prompt_states_local_only(self):
        text = " ".join(optin_mod.first_open_prompt()["paragraphs"])
        self.assertIn("stays on this computer only", text)
        self.assertIn("Nothing is sent anywhere else", text)

    def test_prompt_states_off_by_default(self):
        text = " ".join(optin_mod.first_open_prompt()["paragraphs"])
        self.assertIn("switched off to start with", text)
        self.assertIn("until you say yes", text)

    def test_prompt_states_sign_while_on(self):
        text = " ".join(optin_mod.first_open_prompt()["paragraphs"])
        self.assertIn("small sign on screen", text)
        self.assertIn("tells you it is on", text)

    def test_prompt_states_turn_off_and_delete_paths(self):
        text = " ".join(optin_mod.first_open_prompt()["paragraphs"])
        self.assertIn("switch it off at any time in Settings", text)
        self.assertIn("Switching it off stops it watching anything new", text)
        self.assertIn("delete everything it kept at any time", text)
        self.assertIn("same place in Settings", text)

    def test_prompt_contains_no_jargon(self):
        prompt = optin_mod.first_open_prompt()
        text = " ".join([prompt["title"]] + prompt["paragraphs"]
                         + prompt["choices"])
        for term in sorted(optin_mod.JARGON_TERMS):
            self.assertIsNone(
                re.search(r"\b" + re.escape(term) + r"\b", text,
                          flags=re.IGNORECASE),
                f"jargon term {term!r} in first-open prompt: {text!r}")

    def test_settings_entry_same_words_stays_available(self):
        entry = optin_mod.settings_entry()
        prompt = optin_mod.first_open_prompt()
        self.assertEqual(entry["kind"], "SETTINGS_ENTRY")
        self.assertEqual(entry["paragraphs"], prompt["paragraphs"])
        self.assertEqual(entry["title"], prompt["title"])
        self.assertTrue(entry["reversible"])
        self.assertTrue(entry["available_always"])
        self.assertEqual(entry["current"], "off")
        self.assertEqual(entry["current_reason"],
                         optin_mod.inspect_choice(None)["reason"])

    def test_settings_entry_reflects_a_live_choice(self):
        entry = optin_mod.settings_entry(_choice_record())
        self.assertEqual(entry["current"], "on")
        off_entry = optin_mod.settings_entry(
            _choice_record(allowed=False))
        self.assertEqual(off_entry["current"], "off")


class GateTests(unittest.TestCase):
    def test_no_record_is_disabled(self):
        result = optin_mod.gate_continuous(None)
        self.assertFalse(result["enabled"])
        self.assertIn("stays OFF", result["reason"])

    def test_default_record_is_disabled(self):
        result = optin_mod.gate_continuous(
            optin_mod.build_choice_record())
        self.assertFalse(result["enabled"])

    def test_valid_choice_is_enabled(self):
        result = optin_mod.gate_continuous(_choice_record())
        self.assertTrue(result["enabled"], result)
        self.assertTrue(result["local_only"])
        self.assertTrue(result["default_off"])

    def test_wrong_scope_is_disabled(self):
        for scope in ("suggestion-cards", "capture", "anticipation",
                      "continuous", "watch-and-learn", ""):
            result = optin_mod.gate_continuous(
                _choice_record(scope=scope))
            self.assertFalse(result["enabled"], scope)
            self.assertIn("continuous-display", result["reason"])

    def test_suggestion_cards_scope_does_not_open_continuous(self):
        # The Wave 8 suggestion-cards scope opens suggestion
        # cards only; it must never open continuous display.
        result = optin_mod.gate_continuous(
            _choice_record(scope="suggestion-cards"))
        self.assertFalse(result["enabled"])

    def test_incomplete_choice_is_disabled(self):
        for overrides in ({"chooser": ""}, {"timestamp": ""},
                          {"retention_rule": ""},
                          {"deletion_rule": ""},
                          {"allowed": False}):
            result = optin_mod.gate_continuous(
                _choice_record(**overrides))
            self.assertFalse(result["enabled"], overrides)

    def test_malformed_choice_is_not_enabled(self):
        result = optin_mod.gate_continuous("yes please")
        self.assertFalse(result["ok"])
        self.assertFalse(result["enabled"])

    def test_builder_rejects_bad_fields(self):
        with self.assertRaises(ValueError):
            optin_mod.build_choice_record(chooser=42)
        with self.assertRaises(ValueError):
            optin_mod.build_choice_record(chooser="x" * 501)

    def test_no_watcher_or_capture_identifiers(self):
        source = Path(optin_mod.__file__).read_text()
        tree = ast.parse(source)
        banned_imports = {
            "threading", "sched", "subprocess", "watchers",
            # INTENTIONAL (Phase 2 B1b-1 rename): was "anticipation_v562" —
            # the guard must name the CURRENT module or it silently dies.
            # INTENTIONAL (Phase 2 B1d-1 rename): "antic_grant_path_w8" ->
            # "anticipation_grant_path", same guard rule.
            "anticipation", "anticipation_grant_path",
            "pyautogui", "mss", "win32gui", "PIL",
            "cv2", "pyperclip", "keyboard", "mouse", "socket",
            "requests",
        }
        banned_substrings = ("watch", "capture", "prewarm",
                             "schedule", "listen", "screenshot",
                             "clipboard")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0],
                                     banned_imports)
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0],
                                 banned_imports)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)):
                lowered = node.name.lower()
                for banned in banned_substrings:
                    self.assertNotIn(banned, lowered, node.name)
            elif isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", "") or getattr(func, "attr", "")
                self.assertNotIn(name, ("eval", "exec", "compile"))

    def test_no_watcher_or_scheduler_attribute_after_gate_runs(self):
        optin_mod.gate_continuous(_choice_record())
        # WATCHES_TEXT is the plain-language wording constant
        # ("what is on your screen while you work"), not a
        # watcher object — it is excluded here deliberately.
        attrs = [name for name in dir(optin_mod)
                 if name != "WATCHES_TEXT"
                 and ("watch" in name.lower()
                      or "schedul" in name.lower()
                      or "listen" in name.lower())]
        self.assertEqual(attrs, [])


class DeleteTests(unittest.TestCase):
    def test_delete_removes_files_only_and_leaves_sentinel(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "kept"
            folder.mkdir()
            (folder / "a.txt").write_text("a")
            (folder / "b.txt").write_text("b")
            (folder / "sub").mkdir()
            sentinel = Path(tmp) / "outside.txt"
            sentinel.write_text("keep me")
            result = optin_mod.delete_continuous_data(str(folder))
            self.assertTrue(result["ok"], result)
            self.assertEqual(sorted(result["deleted"]), ["a.txt", "b.txt"])
            self.assertEqual(result["left_alone"], ["sub"])
            self.assertTrue((folder / "sub").is_dir())
            self.assertEqual(sentinel.read_text(), "keep me")
            self.assertIn("PF-3", result["reason"])

    def test_delete_refuses_missing_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = optin_mod.delete_continuous_data(
                str(Path(tmp) / "nope"))
            self.assertFalse(result["ok"])
            self.assertEqual(result["deleted"], [])

    def test_delete_refuses_core_and_data_paths(self):
        tree = Path(__file__).resolve().parent.parent
        for path in (tree / "core", tree / "data", tree):
            result = optin_mod.delete_continuous_data(str(path))
            self.assertFalse(result["ok"], path)
            self.assertEqual(result["deleted"], [])

    def test_delete_refuses_credential_file_inside(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "kept"
            folder.mkdir()
            (folder / ".env").write_text("x")
            (folder / "a.txt").write_text("a")
            result = optin_mod.delete_continuous_data(str(folder))
            self.assertFalse(result["ok"])
            self.assertEqual(result["deleted"], [])
            self.assertTrue((folder / "a.txt").exists())
            self.assertTrue((folder / ".env").exists())

    def test_delete_refuses_symlink_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "kept"
            folder.mkdir()
            target = Path(tmp) / "target.txt"
            target.write_text("t")
            (folder / "link.txt").symlink_to(target)
            (folder / "a.txt").write_text("a")
            result = optin_mod.delete_continuous_data(str(folder))
            self.assertFalse(result["ok"])
            self.assertEqual(result["deleted"], [])
            self.assertTrue((folder / "a.txt").exists())
            self.assertTrue(target.exists())


class ToolWiringTests(unittest.TestCase):
    def setUp(self):
        confirm_gate.register_handler(None)
        self._confirm_gate = confirm_gate

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in ("continuous_first_open",
                     "continuous_choice_status",
                     "continuous_record_choice",
                     "continuous_delete_data"):
            self.assertIn(name, names)
        self.assertIn("continuous_first_open", gate._READ_ONLY_TOOLS)
        self.assertIn("continuous_choice_status", gate._READ_ONLY_TOOLS)
        self.assertIn("continuous_record_choice", gate._CONFIRM_GATE_TOOLS)
        self.assertIn("continuous_delete_data", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["continuous_record_choice"],
            ("output_path",))
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["continuous_delete_data"],
            ("data_dir",))
        # First-open opt-in added 4 continuous tools (580 -> 584). Wave 9 Build 1 added 5 wellbeing tools (584 -> 589). Wave 9 Build 2 added 4 communications tools (589 -> 593). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 4 added 4 hardware tools (597 -> 601). Wave 9 Build 5 added 4 accessibility tools (601 -> 605). Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 764)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in ("continuous_first_open",
                     "continuous_choice_status",
                     "continuous_record_choice",
                     "continuous_delete_data"):
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        self.assertEqual(names["continuous_first_open"],
                         "First-open continuous choice")
        self.assertEqual(names["continuous_choice_status"],
                         "Continuous choice status")
        self.assertEqual(names["continuous_record_choice"],
                         "Record continuous choice")
        self.assertEqual(names["continuous_delete_data"],
                         "Delete kept continuous data")

    def test_prompt_and_status_through_funnel(self):
        prompt = _result(tools.execute("continuous_first_open", {}))
        self.assertEqual(prompt["kind"], "FIRST_OPEN_PROMPT")
        self.assertTrue(prompt["default_off"])
        status = _result(tools.execute("continuous_choice_status", {}))
        self.assertFalse(status["enabled"])
        on = _result(tools.execute("continuous_choice_status", {
            "choice_json": json.dumps(_choice())}))
        self.assertTrue(on["enabled"])

    def test_record_choice_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "choice.json")
            result = tools.execute("continuous_record_choice", {
                "choice_json": json.dumps(_choice()),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_choice_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "choice.json")
            data = _result(tools.execute("continuous_record_choice", {
                "choice_json": json.dumps(_choice()),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["executed"])
            self.assertTrue(data["enabled"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["scope"], "continuous-display")
            self.assertTrue(written["allowed"])
            self.assertEqual(sorted(Path(tmp).iterdir()),
                             [Path(target)])

    def test_delete_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "kept"
            folder.mkdir()
            (folder / "a.txt").write_text("a")
            result = tools.execute("continuous_delete_data", {
                "data_dir": str(folder)})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertTrue((folder / "a.txt").exists())

    def test_delete_with_confirm_deletes_folder_files_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "kept"
            folder.mkdir()
            (folder / "a.txt").write_text("a")
            sentinel = Path(tmp) / "outside.txt"
            sentinel.write_text("keep me")
            data = _result(tools.execute("continuous_delete_data", {
                "data_dir": str(folder)}))
            self.assertTrue(data["ok"], data)
            self.assertEqual(data["deleted"], ["a.txt"])
            self.assertFalse((folder / "a.txt").exists())
            self.assertEqual(sentinel.read_text(), "keep me")


if __name__ == "__main__":
    unittest.main()
