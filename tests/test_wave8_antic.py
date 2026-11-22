"""Wave 8 Build 1 tests — anticipation-grant path, suggestion
cards only.

Proves the path, not the freedom: cards are produced ONLY from
caller-supplied context under a granted suggestion-cards grant;
without a grant, with a False grant, with a wrong-scope grant,
or with an incomplete grant, the answer is zero cards with a
stated reason. Capture-derived input is refused whole. The
module imports no capture, watcher, scheduler, or screen /
clipboard code, starts no background watcher, schedules no job,
and pre-warms nothing — proven by AST test and by a runtime
test that no watcher/scheduler object exists after the path
runs. The grant itself is NOT granted by any function here.

Stdlib unittest, no GUI, no network, no code execution.
The confirm handler is cleared in setUp/tearDown, so no
test here ever writes the real data/ tree through a tool.
Run from the tree root with PYTHONPATH=. (as the other
suites are run by tests/run_tests.py).
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from core import anticipation_grant_path as antic_mod
from core import anticipation_grant_tools as antic_tools
from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

try:
    from _budget_isolation import restore_budget, snapshot_budget
except ImportError:  # run as a package module instead of top-level
    from tests._budget_isolation import (  # type: ignore
        restore_budget, snapshot_budget)



def _grant(**overrides):
    record = {"granted": True, "grantor": "owner",
              "scope": "suggestion-cards",
              "timestamp": "2026-10-26T21:00:00+05:30"}
    record.update(overrides)
    return record


def _context():
    return [
        {"topic": "Review the BB101 notes",
         "reason": "quiz tomorrow",
         "action": "Open the notes file and read one lecture"},
        {"topic": "Charge the laptop",
         "reason": "battery below half",
         "action": "Plug in the charger"},
    ]


def _result(payload):
    assert payload["status"] == "success", payload
    return json.loads(payload["result"])


class GrantGateTests(unittest.TestCase):
    def test_cards_with_valid_suggestion_cards_grant(self):
        result = antic_mod.gate_cards(_context(), _grant_record())
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["cards"]), 2)
        card = result["cards"][0]
        self.assertEqual(card["kind"], "SUGGESTION_CARD")
        self.assertEqual(card["text"], "Consider: Review the BB101 notes")
        self.assertEqual(card["source"], "caller-supplied context")
        self.assertEqual(card["reason"], "quiz tomorrow")
        self.assertEqual(card["action"],
                         "Open the notes file and read one lecture")
        self.assertFalse(card["executed"])

    def test_no_grant_record_is_zero_cards_with_reason(self):
        result = antic_mod.gate_cards(_context(), None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["cards"], [])
        self.assertIn("no anticipation grant", result["reason"])

    def test_false_grant_is_zero_cards(self):
        result = antic_mod.gate_cards(_context(), _grant_record(granted=False))
        self.assertFalse(result["ok"])
        self.assertEqual(result["cards"], [])
        self.assertIn("not granted", result["reason"])

    def test_wrong_scope_grants_refused(self):
        for scope in ("capture", "anticipation", "pre-warming",
                      "suggestion-cards-and-capture", "watch-and-learn",
                      "screen-memory", "clipboard-history", ""):
            with self.subTest(scope=scope):
                result = antic_mod.gate_cards(
                    _context(), _grant_record(scope=scope))
                self.assertFalse(result["ok"], scope)
                self.assertEqual(result["cards"], [])

    def test_incomplete_grant_refused(self):
        for overrides in ({"grantor": ""}, {"timestamp": ""}):
            with self.subTest(overrides=overrides):
                result = antic_mod.gate_cards(
                    _context(), _grant_record(**overrides))
                self.assertFalse(result["ok"])
                self.assertEqual(result["cards"], [])

    def test_inspect_grant_states_path_plainly(self):
        self.assertFalse(antic_mod.inspect_grant(None)["path_open"])
        self.assertIn("explicit", antic_mod.inspect_grant(None)["reason"])
        opened = antic_mod.inspect_grant(_grant_record())
        self.assertTrue(opened["path_open"])
        self.assertEqual(opened["allowed_scope"], "suggestion-cards")
        self.assertFalse(
            antic_mod.inspect_grant(_grant_record(scope="capture"))["path_open"])
        self.assertFalse(antic_mod.inspect_grant("not-a-record")["ok"])

    def test_build_grant_record_never_grants_by_itself(self):
        # The builder only validates shape: a record it returns
        # with granted=False stays False, and the gate still
        # refuses it. Granting authority lives with the live
        # caller, never with this function.
        record = antic_mod.build_grant_record()
        self.assertFalse(record.granted)
        self.assertFalse(antic_mod.gate_cards(_context(), record)["ok"])
        with self.assertRaises(ValueError):
            antic_mod.build_grant_record(grantor=123)


class CaptureRefusalTests(unittest.TestCase):
    def test_capture_derived_item_refuses_whole_call(self):
        tainted = _context() + [
            {"topic": "What is on screen now", "source": "screen"}]
        result = antic_mod.gate_cards(tainted, _grant_record())
        self.assertFalse(result["ok"])
        self.assertEqual(result["cards"], [])

    def test_captured_flag_and_each_capture_source_refused(self):
        for source in ("clipboard", "screenshot", "window-title",
                       "keystroke", "watcher", "continuous",
                       "screen-memory", "clipboard-history"):
            with self.subTest(source=source):
                result = antic_mod.gate_cards(
                    [{"topic": "Something", "source": source}],
                    _grant_record())
                self.assertFalse(result["ok"])
                self.assertEqual(result["cards"], [])
        flagged = antic_mod.gate_cards(
            [{"topic": "Something", "captured": True}], _grant_record())
        self.assertFalse(flagged["ok"])
        self.assertEqual(flagged["cards"], [])

    def test_empty_topic_and_oversize_refused_whole(self):
        self.assertFalse(antic_mod.gate_cards(
            [{"topic": "  "}], _grant_record())["ok"])
        self.assertFalse(antic_mod.gate_cards(
            [{"topic": "x" * 501}], _grant_record())["ok"])
        self.assertFalse(antic_mod.gate_cards(
            [{"topic": f"item {i}"} for i in range(51)],
            _grant_record())["ok"])
        self.assertFalse(antic_mod.gate_cards(
            "not-a-list", _grant_record())["ok"])

    def test_empty_context_with_valid_grant_is_zero_cards_ok(self):
        # Zero items in, zero cards out — but ok, because the
        # gate was open and nothing was asked for. This is the
        # only ok-but-empty case, and it says so plainly.
        result = antic_mod.gate_cards([], _grant_record())
        self.assertTrue(result["ok"])
        self.assertEqual(result["cards"], [])


class AbsenceOfCaptureTests(unittest.TestCase):
    def test_no_capture_watcher_scheduler_imports(self):
        source = Path(antic_mod.__file__).read_text()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        banned = {"threading", "sched", "schedule", "subprocess",
                  # INTENTIONAL (Phase 2 B1b-1 rename): was "anticipation_v562" —
                  # the guard must name the CURRENT module or it silently dies.
                  "watchers", "anticipation", "pyautogui", "mss",
                  "win32gui", "PIL", "cv2", "pyperclip",
                  "keyboard", "mouse"}
        self.assertFalse(banned.intersection(imported), imported)
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Name)}
        self.assertFalse({"eval", "exec", "open"}.intersection(called)
                         or {"Thread", "Timer"}.intersection(called))
        # No function in the module can grant, watch, capture,
        # or pre-warm: the names themselves must not exist.
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for forbidden in ("grant_anticipation", "start_watcher",
                          "watch", "capture", "prewarm", "pre_warm",
                          "schedule", "listen"):
            self.assertNotIn(forbidden, names)

    def test_no_watcher_or_scheduler_object_after_path_runs(self):
        result = antic_mod.gate_cards(_context(), _grant_record())
        self.assertTrue(result["ok"])
        # CAPTURE_SOURCES is the refusal list itself (the names
        # that mark input as capture-derived), not a capture
        # object — it is excluded here deliberately.
        module_attrs = [name for name in dir(antic_mod)
                        if name != "CAPTURE_SOURCES"
                        and ("watch" in name.lower()
                             or "schedul" in name.lower()
                             or "listen" in name.lower())]
        self.assertEqual(module_attrs, [])


class ToolWiringTests(unittest.TestCase):
    def setUp(self):
        from core import confirm_gate
        confirm_gate.register_handler(None)
        self._confirm_gate = confirm_gate

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        self.assertIn("antic_suggest_cards", names)
        self.assertIn("antic_grant_status", names)
        self.assertIn("antic_cards_report", names)
        self.assertIn("antic_suggest_cards", gate._READ_ONLY_TOOLS)
        self.assertIn("antic_grant_status", gate._READ_ONLY_TOOLS)
        self.assertIn("antic_cards_report", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["antic_cards_report"],
            ("output_path",))
        # Wave 8 Build 1 added 3 anticipation tools (563 -> 566); Wave 8 Build 2 added 3 Track L tools (566 -> 575); Wave 8 Build 3 added 6 Phase 5 tools (569 -> 575); Wave 8 Build 4 added 3 feel tools (575 -> 578); Wave 8 Build 5 added 2 learning-last tools (578 -> 580; first-open opt-in added 4 continuous tools (580 -> 584)). Wave 9 Build 1 added 5 wellbeing tools (584 -> 589). Wave 9 Build 2 added 4 communications tools (589 -> 593). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 4 added 4 hardware tools (597 -> 601). Wave 9 Build 5 added 4 accessibility tools (601 -> 605). Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 764)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169

    def test_cards_through_funnel_without_grant_and_with_grant(self):
        refused = _result(tools.execute("antic_suggest_cards", {
            "context_json": json.dumps(_context())}))
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["cards"], [])
        granted = _result(tools.execute("antic_suggest_cards", {
            "context_json": json.dumps(_context()),
            "grant_json": json.dumps(_grant())}))
        self.assertTrue(granted["ok"], granted)
        self.assertEqual(len(granted["cards"]), 2)
        status = _result(tools.execute("antic_grant_status", {}))
        self.assertFalse(status["path_open"])
        self.assertIn("explicit", status["reason"])

    def test_report_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "cards.json")
            result = tools.execute("antic_cards_report", {
                "context_json": json.dumps(_context()),
                "grant_json": json.dumps(_grant()),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_report_with_confirm_writes_card_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "cards.json")
            data = _result(tools.execute("antic_cards_report", {
                "context_json": json.dumps(_context()),
                "grant_json": json.dumps(_grant()),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["executed"])
            self.assertEqual(data["cards"], 2)
            written = json.loads(Path(target).read_text())
            self.assertEqual(len(written["cards"]), 2)
            self.assertFalse(written["cards"][0]["executed"])


def _grant_record(**overrides):
    return antic_mod.build_grant_record(**_grant(**overrides))


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
