"""Audit fix batch 1 regression tests (2026-10-27 audit, six bugs).

Build only, working copy only. NO new tools: the registry guard below
pins 655 declarations (wallpaper-revert batch 2026-11-05 added 1: wallpaper_revert; wallpaper-apply batch 2026-11-03 added 1: wallpaper_apply; §7 consumers batch 8 added 1; §7 consumers batch 2 added 3; libwiring sweep added 4: teach_record_sequence,
teach_list_sequences, teach_replay_sequence, wallpaper_state) and the 242,780-byte fitted figure
(the owner's cap decision is still pending and is not fixed here).

Each test pins one fix. A fix that regresses fails here first, not
in a full run. The palette behavioural test runs only where PySide6
imports (the venv-qt interpreter); everywhere else the AST test for
the same fix still runs.
"""

import ast
import io
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _register_package():
    """Mirror doctor._register_package: make voice_assistant.* importable
    from this folder whatever it is called."""
    if "voice_assistant" not in sys.modules:
        pkg = types.ModuleType("voice_assistant")
        pkg.__path__ = [str(ROOT)]
        sys.modules["voice_assistant"] = pkg


def _exec_function(source_path, func_name):
    """Exec one standalone function from a source file in isolation."""
    tree = ast.parse(Path(source_path).read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            namespace = {}
            exec(compile(ast.Module(body=[node], type_ignores=[]),
                         str(source_path), "exec"), namespace)
            return namespace[func_name]
    raise AssertionError(f"{func_name} not found in {source_path}")


_register_package()
try:
    import gui.window_widgets as _ww
    _WW_AVAILABLE = True
except Exception:  # noqa: BLE001 — no PySide6 on this interpreter
    _ww = None
    _WW_AVAILABLE = False


class TestNoNewTools(unittest.TestCase):
    def test_registry_unchanged(self):
        from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Desktop-input batch (D-07): INTENTIONAL — cursor_control
        # schema gains x2/y2/points/pause_s (no new tool, registry
        # stays 641).
        # Desktop-reach batch (F-04 + D-09): INTENTIONAL —
        # screen_look + take_screenshot gain a scope parameter
        # and cursor_control's description gains the honest
        # pointer-gesture sentence (no new tool, registry
        # stays 641).
        # Audit fix Batch 2 (TOOL-03) — INTENTIONAL:
        # 75 previously description-less properties gain
        # descriptions and 4 typeless props gain anyOf types
        # (no new tool, registry stays 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 772)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        # Audit fix Batch 4 (DESK/DISPLAY) — INTENTIONAL:
        # take_screenshot gains a monitor selector, screen_diff
        # gains a monitor selector, focus_window_by_text gains a
        # title_hint scope parameter and a rewritten description
        # stating the ask-on-ambiguity tie policy (no new tool,
        # registry stays 641).


class TestRecordAudioRate(unittest.TestCase):
    def test_record_path_no_longer_raises_name_error(self):
        from core import skills_media
        from core import skills_media_audio  # SKILLS SPLIT PHASE A BATCH A21 INTENTIONAL: seam patch target (see below)
        self.assertEqual(skills_media._RECORD_RATE, 44100)

        class FakeSD:
            def query_devices(self):
                return [{"max_input_channels": 2, "name": "Fake mic"}]

            def rec(self, *args, **kwargs):
                return object()

            def wait(self):
                return None

        class FakeArray:
            def tobytes(self):
                return b"\x00\x00"

        class FakeNP:
            def asarray(self, obj, dtype=None):
                return FakeArray()

        import core.capture_guard as capture_guard
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with mock.patch.dict(sys.modules, {"sounddevice": FakeSD(),
                                               "numpy": FakeNP()}), \
                 mock.patch.object(capture_guard, "ensure_capture_allowed",
                                   return_value=(True, "")), \
                 mock.patch.object(capture_guard, "note_capture_started",
                                   return_value=None), \
                 mock.patch.object(capture_guard, "note_capture_ended",
                                   return_value=None), \
                 mock.patch.object(skills_media, "_DATA_DIR", tmp_path), \
                 mock.patch.object(skills_media_audio, "_DATA_DIR", tmp_path):
                # SKILLS SPLIT PHASE A BATCH A21 INTENTIONAL: record_audio now
                # lives in core/skills_media_audio.py and resolves _DATA_DIR in
                # that module's globals, so the seam must be patched in BOTH
                # bindings (facade + impl); patching the facade alone would
                # miss the impl's direct use. No fake value or assertion changed.
                result = skills_media.record_audio(1, "clip.wav")
            self.assertTrue(result.startswith("Recorded"), result)
            self.assertNotIn("Recording failed", result)
            self.assertTrue((tmp_path / "recordings" / "clip.wav").is_file())


class TestFindingToDict(unittest.TestCase):
    def test_all_fields_round_trip(self):
        from core.skill_format import Finding
        finding = Finding(code="E1", severity="error",
                          message="boom", field="name")
        self.assertEqual(finding.to_dict(),
                         {"code": "E1", "severity": "error",
                          "message": "boom", "field": "name"})
        defaulted = Finding(code="W2", severity="warning",
                            message="careful")
        self.assertEqual(defaulted.to_dict()["field"], "")


class TestPsStructuredSep(unittest.TestCase):
    def test_splits_on_unit_separator(self):
        # INTENTIONAL (Phase C batch 14): skills_system5 renamed to skills_system_bench; alias kept, behaviour unchanged.
        from core import skills_system_bench as system5
        self.assertEqual(system5._SEP, "\x1f")
        sample = ("GPU Name\x1f4096\x1fDriver 1.0\n"
                  "Other GPU\x1f2048\x1fDriver 2.0\n")
        with mock.patch.object(system5, "_run",
                               return_value=(0, sample, "")) as run:
            rc, rows, err = system5._ps_structured(
                "Get-CimInstance Win32_VideoController", timeout=10)
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual(rows, [["GPU Name", "4096", "Driver 1.0"],
                                ["Other GPU", "2048", "Driver 2.0"]])
        run.assert_called_once()


class TestPaletteHumanise(unittest.TestCase):
    def test_humanise_defined_and_matches_catalogue(self):
        window_humanise = _exec_function(
            # INTENTIONAL (refactor P1 step 5): _humanise moved verbatim
            # to window_widgets_helpers.py. Assertion unchanged.
            ROOT / "gui" / "window_widgets_helpers.py", "_humanise")
        catalogue_humanise = _exec_function(
                        # GUI split #2 INTENTIONAL: catalogue _humanise moved verbatim
            # to skills_catalog_widget.py.
            ROOT / "gui" / "skills_catalog_widget.py", "_humanise")
        for sample in ["some_tool", "open_url", "x", "",
                       "already friendly", "a_b_c"]:
            self.assertEqual(window_humanise(sample),
                             catalogue_humanise(sample), sample)
        self.assertEqual(window_humanise("some_tool"), "Some tool")

    @unittest.skipUnless(_WW_AVAILABLE, "PySide6 unavailable")
    def test_tool_rows_populate_past_unfriendly_names(self):
        friendly_key = next(iter(_ww._TOOL_FRIENDLY_NAMES))
        decls = [
            {"name": "some_new_tool_xyz", "description": "Does a new thing."},
            {"name": friendly_key, "description": "A friendly tool."},
            {"name": "another_missing_tool", "description": "Does another thing."},
            {"name": "", "description": "Nameless, skipped."},
            {"name": "third_missing_tool", "description": "Still here."},
        ]
        fake_tools = types.SimpleNamespace(get_declarations=lambda: decls)
        fake_mod = types.ModuleType("voice_assistant.core.funnel.tools_registry")  # Phase 3 F3 INTENTIONAL: stub key follows the moved module
        fake_mod.tools = fake_tools
        saved = sys.modules.get("voice_assistant.core.funnel.tools_registry")  # Phase 3 F3 INTENTIONAL: stub key follows the moved module

        class FakeWin:
            settings_content = None

        class FakePalette:
            def parent(self):
                return FakeWin()

        sys.modules["voice_assistant.core.funnel.tools_registry"] = fake_mod  # Phase 3 F3 INTENTIONAL: stub key follows the moved module
        try:
            rows = _ww.CommandPalette._build_commands(FakePalette())
        finally:
            if saved is None:
                sys.modules.pop("voice_assistant.core.funnel.tools_registry", None)  # Phase 3 F3 INTENTIONAL: stub key follows the moved module
            else:
                sys.modules["voice_assistant.core.funnel.tools_registry"] = saved  # Phase 3 F3 INTENTIONAL: stub key follows the moved module
        tool_rows = [r for r in rows if r["kind"] == "TOOL"]
        self.assertEqual(
            [r["action_id"] for r in tool_rows],
            ["tool_some_new_tool_xyz", "tool_" + friendly_key,
             "tool_another_missing_tool", "tool_third_missing_tool"])
        self.assertEqual(tool_rows[0]["label"], "Some new tool xyz")
        self.assertEqual(tool_rows[2]["label"], "Another missing tool")


class TestDictationSingleInstance(unittest.TestCase):
    def test_flag_reads_identically_through_package_path(self):
        _register_package()
        from voice_assistant.core.brain import turn_context, turn_machine  # Phase 3 B1a-1 INTENTIONAL: moved to core/brain/
        mode = turn_machine.DictationMode()
        try:
            turn_context.set_dictation_active(False)
            self.assertFalse(turn_context.dictation_active())
            mode.enter()
            self.assertTrue(mode.active)
            self.assertTrue(turn_context.dictation_active())
            mode.exit()
            self.assertFalse(turn_context.dictation_active())
        finally:
            turn_context.set_dictation_active(False)

    def test_import_styles_unified_in_source(self):
        turn_machine_src = (ROOT / "core" / "brain" / "turn_machine.py").read_text()  # Phase 3 B1a-1 INTENTIONAL: moved to core/brain/
        self.assertNotIn("from core import", turn_machine_src)
        window_turns_src = (ROOT / "gui" / "window_turns.py").read_text()
        # GAPS-15: the turn_machine names now arrive via the facade —
        # the unified style is the same try-first/except-fallback
        # pair (package spelling first, install-root spelling
        # second), now on the facade import.
        self.assertLess(
            window_turns_src.index(
                "from voice_assistant.core.gui_facade import"),
            window_turns_src.index("from core.gui_facade import"))
        turn_context_src = (ROOT / "core" / "brain" / "turn_context.py").read_text()  # Phase 3 B1a-1 INTENTIONAL: moved to core/brain/
        self.assertNotIn("NO dictation mode exists", turn_context_src)


class TestDoctorRealChrome(unittest.TestCase):
    def _run_check(self, reachable, found):
        import doctor as doctor_mod
        out = io.StringIO()
        with mock.patch.object(doctor_mod, "_cdp_port_reachable",
                               return_value=reachable), \
             mock.patch.object(doctor_mod, "_find_chrome_binary",
                               return_value=found), \
             redirect_stdout(out):
            doctor_mod.check_real_chrome()
        return out.getvalue()

    def test_reachable_reports_pass(self):
        text = self._run_check(True, None)
        self.assertIn("[PASS]", text)
        self.assertIn("Real Chrome (CDP)", text)
        self.assertNotIn("browser tools are unavailable", text)

    def test_installed_but_closed_reports_warn_not_fail(self):
        text = self._run_check(False, "/usr/bin/google-chrome")
        self.assertIn("[WARN]", text)
        self.assertNotIn("[FAIL]", text)

    def test_no_chrome_reports_warn_not_fail(self):
        text = self._run_check(False, None)
        self.assertIn("[WARN]", text)
        self.assertNotIn("[FAIL]", text)
        self.assertNotIn("AttributeError", text)

    def test_old_check_gone_from_checklist(self):
        import doctor as doctor_mod
        self.assertFalse(hasattr(doctor_mod, "check_bundled_chromium"))
        self.assertIn(doctor_mod.check_real_chrome, doctor_mod._CHECKS)

    def test_helpers_never_raise_and_return_typed_results(self):
        import doctor as doctor_mod
        self.assertIsInstance(doctor_mod._cdp_port_reachable(), bool)
        found = doctor_mod._find_chrome_binary()
        self.assertTrue(found is None or isinstance(found, str))


if __name__ == "__main__":
    unittest.main()
