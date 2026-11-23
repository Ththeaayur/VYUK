"""Desktop-control programme, group (iv) REACH: F-04, F-05, D-08, D-09.

F-04: the user-facing look tools (screen_look, take_screenshot) gain
an explicit whole-desktop capture path (scope="whole_desktop") wired
through the internal full-screen mechanism, with geometry metadata
carried on it. A foreground look is unchanged.
F-05: the legacy Pillow fallback grabs all screens with or without a
bounding box (the no-bbox branch used to capture the primary screen
only).
D-08: file drag binds its Explorer window by the target item itself,
never by folder-title substring — same-leaf-name folders no longer
collide silently, and a true collision refuses instead of guessing.
D-09: honest labelling — cursor_control's description states plainly
that ole_drag_drop is a pointer-gesture drag, not COM DoDragDrop;
the tool name and registry count are unchanged, and true COM
DoDragDrop stays future work (recorded, not built here).

Test hygiene (the guest rule, learned in group (i)): any test that
drives _tool_screen_look clears taint with injection.clear_taint(
force=True) in a finally block — screen text is a soft-taint source
and arrival alone taints the session, which makes LATER suites'
tool dispatches demand confirmation and fail.
"""

import tempfile
import unittest
from pathlib import Path

from core.desktop import vision as _vision  # Phase 3 D4c INTENTIONAL: vision hub moved to core/desktop/
from core.desktop import screen_capture as _sc  # GAPS-19 INTENTIONAL: the capture owner; see _run_grab note below
from core.funnel import tools_handlers as _th  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel.tools_registry import tools  # GAPS-23 (F-V7): look/screenshot tests run through the funnel
from core.desktop import control as _control  # Phase 3 D4a INTENTIONAL: control hub moved to core/desktop/

CROP_INFO = {
    "screen_width": 1920,
    "screen_height": 1080,
    "image_width": 800,
    "image_height": 600,
    "max_dim": 1024,
    "cropped": True,
    "crop_left": 800,
    "crop_top": 100,
    "crop_width": 800,
    "crop_height": 600,
}

FULL_INFO = {
    "screen_width": 1920,
    "screen_height": 1080,
    "image_width": 1024,
    "image_height": 576,
    "max_dim": 1024,
    "cropped": False,
    "crop_left": 0,
    "crop_top": 0,
    "crop_width": 1920,
    "crop_height": 1080,
}


def _clear_taint():
    try:
        from core import injection as _injection

        _injection.clear_taint(force=True)
    except Exception:  # noqa: BLE001 — hygiene only
        pass


class _LookStubs:
    """Context manager stubbing the look-tool seams (group (i) pattern)."""

    def __init__(self, info, capture_calls):
        self.info = info
        self.capture_calls = capture_calls

    def __enter__(self):
        self.orig_capture = _vision.vision_manager.capture_screen
        self.orig_info = _vision.vision_manager._last_capture_info
        self.orig_deliver = _th._deliver_frame
        self.orig_ocr = _th._ocr_captured_frame
        self.orig_marks = _th._marks_suffix
        self.orig_title = _control.get_active_window
        # INTENTIONAL (skills split B49): _tool_screen_look moved to the
        # capture_launch mixin and resolves _deliver_frame /
        # _ocr_captured_frame / _marks_suffix from the mixin module's
        # globals, so the facade patches alone no longer steer it —
        # patch BOTH bindings (B16/B47 pattern), same fakes, both
        # restored.
        from core.funnel import tools_handlers_capture_launch as _th_m2
        self._th_m2 = _th_m2
        self.orig_deliver_m2 = _th_m2._deliver_frame
        self.orig_ocr_m2 = _th_m2._ocr_captured_frame
        self.orig_marks_m2 = _th_m2._marks_suffix

        def capture(**kw):
            self.capture_calls.append(dict(kw))
            return (b"fakejpeg", "image/jpeg")

        _vision.vision_manager.capture_screen = capture
        _vision.vision_manager._last_capture_info = (
            dict(self.info) if self.info else None)
        _th._deliver_frame = lambda data, mime: ("unavailable", None)
        _th._ocr_captured_frame = lambda data: "Hello world"
        _th._marks_suffix = lambda: ""
        _th_m2._deliver_frame = lambda data, mime: ("unavailable", None)
        _th_m2._ocr_captured_frame = lambda data: "Hello world"
        _th_m2._marks_suffix = lambda: ""
        _control.get_active_window = lambda: "Fake Window"
        return self

    def __exit__(self, *exc):
        _vision.vision_manager.capture_screen = self.orig_capture
        _vision.vision_manager._last_capture_info = self.orig_info
        _th._deliver_frame = self.orig_deliver
        _th._ocr_captured_frame = self.orig_ocr
        _th._marks_suffix = self.orig_marks
        self._th_m2._deliver_frame = self.orig_deliver_m2
        self._th_m2._ocr_captured_frame = self.orig_ocr_m2
        self._th_m2._marks_suffix = self.orig_marks_m2
        _control.get_active_window = self.orig_title
        _clear_taint()
        return False


# GAPS-23 (F-V7): _look_handler() (a bare-mixin factory) is gone —
# every site below calls tools.execute() directly; the _LookStubs
# context manager still steers the handler identically.


class ScreenLookScopeTests(unittest.TestCase):
    def test_unknown_scope_refuses_and_captures_nothing(self):
        calls = []
        with _LookStubs(CROP_INFO, calls):
            # GAPS-23 (F-V7): an unknown scope never reaches the
            # handler's "unknown scope" message — the funnel's schema
            # gate refuses it first (that check is defence-in-depth
            # behind the gate). Pin the refusal + that nothing was
            # captured, which is what the test is for.
            out = tools.execute("screen_look", {"scope": "full"})
        self.assertEqual(out.get("status"), "refused", out)
        self.assertEqual(out.get("kind"), "validation", out)
        self.assertIn("whole_desktop", out.get("message", ""))
        self.assertIn("full", out.get("message", ""))
        self.assertEqual(calls, [])

    def test_whole_desktop_capture_and_sentence(self):
        calls = []
        with _LookStubs(FULL_INFO, calls):
            result = tools.execute(
                "screen_look", {"scope": "whole_desktop"})["result"]
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].get("full_screen"))
        self.assertTrue(calls[0].get("marks"))
        self.assertIn("Whole-desktop capture: this image shows the "
                      "entire desktop, not just the focused window.",
                      result)
        self.assertIn("Screen is 1920x1080", result)
        self.assertNotIn("shows the foreground window at", result)

    def test_foreground_default_unchanged(self):
        calls = []
        with _LookStubs(CROP_INFO, calls):
            result = tools.execute("screen_look", {})["result"]
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0].get("full_screen"))
        self.assertNotIn("Whole-desktop capture", result)
        self.assertIn("The image shows the foreground window at "
                      "(800, 100), 800x600.", result)

    def test_whole_desktop_vision_path_sentence(self):
        # The vision branch carries the sentence too (sink consumed
        # the frame as an image).
        calls = []
        with _LookStubs(FULL_INFO, calls):
            # INTENTIONAL (skills split B49): _tool_screen_look moved to
            # the capture_launch mixin and resolves _deliver_frame from
            # the mixin module's globals — patch BOTH bindings.
            from core.funnel import tools_handlers_capture_launch as _th_m2
            orig_deliver = _th._deliver_frame
            orig_deliver_m2 = _th_m2._deliver_frame
            _th._deliver_frame = lambda data, mime: ("vision", None)
            _th_m2._deliver_frame = lambda data, mime: ("vision", None)
            try:
                result = tools.execute(
                    "screen_look", {"scope": "whole_desktop"})["result"]
            finally:
                _th._deliver_frame = orig_deliver
                _th_m2._deliver_frame = orig_deliver_m2
        self.assertIn("Whole-desktop capture", result)


class TakeScreenshotScopeTests(unittest.TestCase):
    def _stub_guard(self):
        from core import capture_guard as _cg

        orig_allowed = _cg.ensure_capture_allowed
        orig_started = _cg.note_capture_started
        orig_ended = _cg.note_capture_ended
        _cg.ensure_capture_allowed = lambda kind: (True, "")
        _cg.note_capture_started = lambda kind: None
        _cg.note_capture_ended = lambda kind: None
        return orig_allowed, orig_started, orig_ended

    def _unstub_guard(self, orig):
        from core import capture_guard as _cg

        _cg.ensure_capture_allowed, _cg.note_capture_started, \
            _cg.note_capture_ended = orig

    def test_unknown_scope_refuses_before_capture(self):
        calls = []
        with _LookStubs(CROP_INFO, calls):
            # GAPS-23 (F-V7): as above — the schema gate refuses an
            # unknown scope first; pin the refusal + no capture.
            out = tools.execute(
                "take_screenshot", {"scope": "everything"})
        self.assertEqual(out.get("status"), "refused", out)
        self.assertEqual(out.get("kind"), "validation", out)
        self.assertIn("whole_desktop", out.get("message", ""))
        self.assertIn("everything", out.get("message", ""))
        self.assertEqual(calls, [])

    def test_whole_desktop_look(self):
        calls = []
        with _LookStubs(FULL_INFO, calls):
            result = tools.execute(
                "take_screenshot", {"scope": "whole_desktop"})["result"]
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].get("full_screen"))
        self.assertIn("(whole-desktop capture)", result)
        self.assertIn("Screen is 1920x1080", result)

    def test_foreground_look_unchanged(self):
        calls = []
        with _LookStubs(CROP_INFO, calls):
            result = tools.execute("take_screenshot", {})["result"]
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0].get("full_screen"))
        self.assertNotIn("whole-desktop capture", result)

    def test_whole_desktop_save_writes_bytes_not_delegation(self):
        calls = []
        delegated = []
        orig_take = _control.take_screenshot
        _control.take_screenshot = lambda **kw: delegated.append(kw) or "delegated"
        guard_orig = self._stub_guard()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dest = str(Path(tmp) / "shot.jpg")
                with _LookStubs(FULL_INFO, calls):
                    result = tools.execute(
                        "take_screenshot",
                        {"save_path": dest,
                         "scope": "whole_desktop"})["result"]
                self.assertEqual(delegated, [])
                self.assertEqual(len(calls), 1)
                self.assertTrue(calls[0].get("full_screen"))
                self.assertEqual(Path(dest).read_bytes(), b"fakejpeg")
                self.assertIn("Screenshot saved to", result)
                self.assertIn("(whole-desktop capture)", result)
        finally:
            _control.take_screenshot = orig_take
            self._unstub_guard(guard_orig)

    def test_foreground_save_still_delegates(self):
        calls = []
        delegated = []
        orig_take = _control.take_screenshot
        _control.take_screenshot = lambda **kw: delegated.append(kw) or "delegated"
        guard_orig = self._stub_guard()
        try:
            with _LookStubs(CROP_INFO, calls):
                result = tools.execute(
                    "take_screenshot",
                    {"save_path": "/tmp/nowhere.jpg"})["result"]
            self.assertEqual(len(delegated), 1)
            self.assertEqual(calls, [])
            self.assertIn("delegated", result)
        finally:
            _control.take_screenshot = orig_take
            self._unstub_guard(guard_orig)


class GrabScopeTests(unittest.TestCase):
    def _run_grab(self, bbox, fail_on_all_screens):
        seen = []

        class FakeGrab:
            """Stands in for PIL.ImageGrab at the owner's seam:
            a real PIL image per grab (sized by the bbox, or the
            1920x1080 virtual screen for a full grab), recording
            every call. Raises TypeError on all_screens when the
            Pillow build under test lacks the keyword."""
            @staticmethod
            def grab(*args, **kwargs):
                seen.append((args, kwargs))
                if fail_on_all_screens and "all_screens" in kwargs:
                    raise TypeError("no all_screens keyword")
                from PIL import Image

                box = kwargs.get("bbox")
                if box is None:
                    size = (1920, 1080)
                else:
                    size = (int(box[2]) - int(box[0]),
                            int(box[3]) - int(box[1]))
                return Image.new("RGB", size, (10, 20, 30))

        orig_loader = _sc._imagegrab_module
        # GAPS-19: _grab_image delegates to the capture owner, and
        # the owner resolves ImageGrab through exactly one seam
        # (screen_capture._imagegrab_module) — so the fake lands in
        # ONE module now, not the B15 seam-tuple of vision +
        # vision_capture. Patching vision.ImageGrab would silently
        # do nothing: the owner never reads it.
        _sc._imagegrab_module = lambda: FakeGrab
        try:
            if bbox is None:
                return _vision._grab_image(), seen
            return _vision._grab_image(bbox=bbox), seen
        finally:
            _sc._imagegrab_module = orig_loader

    def test_no_bbox_grabs_all_screens(self):
        # The owner's size probe is a bare grab(); the PIXEL grab
        # is the all_screens one, covering the whole virtual screen.
        img, seen = self._run_grab(None, False)
        self.assertEqual(img.size, (1920, 1080))
        pixel = [c for c in seen if c[1].get("all_screens") is True]
        self.assertEqual(len(pixel), 1)
        self.assertEqual(pixel[0][1].get("bbox"), (0, 0, 1920, 1080))

    def test_bbox_grabs_all_screens(self):
        img, seen = self._run_grab((0, 0, 100, 100), False)
        self.assertEqual(img.size, (100, 100))
        pixel = [c for c in seen if c[1].get("all_screens") is True]
        self.assertEqual(len(pixel), 1)
        self.assertEqual(pixel[0][1].get("bbox"), (0, 0, 100, 100))

    def test_no_bbox_falls_back_without_all_screens(self):
        img, seen = self._run_grab(None, True)
        self.assertEqual(img.size, (1920, 1080))
        plain = [c for c in seen if "all_screens" not in c[1]
                 and c[1].get("bbox") is not None]
        self.assertEqual(len(plain), 1)
        self.assertEqual(plain[0][1], {"bbox": (0, 0, 1920, 1080)})

    def test_bbox_falls_back_without_all_screens(self):
        img, seen = self._run_grab((0, 0, 100, 100), True)
        self.assertEqual(img.size, (100, 100))
        plain = [c for c in seen if "all_screens" not in c[1]
                 and c[1].get("bbox") is not None]
        self.assertEqual(len(plain), 1)
        self.assertEqual(plain[0][1].get("bbox"), (0, 0, 100, 100))
        self.assertNotIn("all_screens", plain[0][1])


class ExplorerPickTests(unittest.TestCase):
    def _pick(self, windows, found, deadline_s=0.0):
        from core import skills_window_control as _h3  # Phase C C6 INTENTIONAL: hands3 renamed to window_control

        def enum_windows():
            return list(windows)

        def find_centre(hwnd, names):
            if hwnd in found:
                found_value = found[hwnd]
                if isinstance(found_value, str) and found_value.startswith("ERR:"):
                    return None, found_value[4:]
                return found_value, None
            return None, None

        return _h3._pick_explorer_window(
            "Reports", ["budget.xlsx", "budget"],
            enum_windows=enum_windows, find_centre=find_centre,
            deadline_s=deadline_s, sleep_s=0.0)

    def test_collision_resolves_to_window_holding_the_file(self):
        # Same leaf name twice; the file is only in the second
        # window — the old first-title-match code bound the first.
        hwnd, centre, err, saw = self._pick(
            [(11, "Reports"), (22, "Reports")],
            {22: (300, 400)})
        self.assertEqual(hwnd, 22)
        self.assertEqual(centre, (300, 400))
        self.assertIsNone(err)
        self.assertTrue(saw)

    def test_substring_candidate_still_binds_when_verified(self):
        # Substring capability is preserved — now verified by the
        # item instead of trusted by the title.
        hwnd, centre, err, saw = self._pick(
            [(11, "Reports - File Explorer")],
            {11: (50, 60)})
        self.assertEqual(hwnd, 11)
        self.assertEqual(centre, (50, 60))
        self.assertIsNone(err)

    def test_true_collision_refuses_instead_of_guessing(self):
        hwnd, centre, err, saw = self._pick(
            [(11, "Reports"), (22, "Reports")],
            {11: (10, 20), 22: (30, 40)})
        self.assertIsNone(hwnd)
        self.assertIsNone(centre)
        self.assertIn("won't guess", err)
        self.assertTrue(saw)

    def test_no_window_vs_no_item_distinguished(self):
        _h, _c, err, saw = self._pick([], {})
        self.assertIsNone(err)
        self.assertFalse(saw)
        _h, _c, err, saw = self._pick([(11, "Reports")], {})
        self.assertIsNone(err)
        self.assertTrue(saw)

    def test_uia_error_propagates_when_nothing_verified(self):
        _h, _c, err, saw = self._pick(
            [(11, "Reports")], {11: "ERR:UI Automation exploded"})
        self.assertIn("UI Automation exploded", err)

    def test_dead_window_never_sinks_a_good_binding(self):
        hwnd, centre, err, saw = self._pick(
            [(11, "Reports"), (22, "Reports")],
            {11: "ERR:UI Automation exploded", 22: (70, 80)})
        self.assertEqual(hwnd, 22)
        self.assertEqual(centre, (70, 80))
        self.assertIsNone(err)

    def test_drag_file_onto_uses_verified_picker(self):
        import inspect

        from core import skills_window_control as _h3  # Phase C C6 INTENTIONAL: hands3 renamed to window_control

        src = inspect.getsource(_h3.drag_file_onto)
        self.assertIn("_pick_explorer_window", src)
        self.assertNotIn("folder.name in t", src)


class OleLabellingTests(unittest.TestCase):
    def _cursor_control_registration(self):
        from core.desktop import cursor as _cursor  # Phase 3 D4d INTENTIONAL: cursor hub moved to core/desktop/

        seen = {}

        class FakeRegistry:
            def register(self, **kw):
                seen.update(kw)

        _cursor.register_all(FakeRegistry())
        return seen

    def test_description_states_pointer_gesture_truth(self):
        reg = self._cursor_control_registration()
        desc = reg["description"]
        self.assertIn("ole_drag_drop", desc)
        self.assertIn("pointer-gesture drag", desc)
        self.assertIn("NOT a COM OLE", desc)
        self.assertIn("carries no OLE data object", desc)

    def test_tool_name_and_action_word_unchanged(self):
        reg = self._cursor_control_registration()
        self.assertEqual(reg["name"], "cursor_control")
        enum = reg["parameters"]["properties"]["action"]["enum"]
        self.assertIn("ole_drag_drop", enum)

    def test_registry_count_unchanged(self):
        from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 772)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_pilot_result_still_carries_disclaimer(self):
        from core.desktop import desktop as _d  # INTENTIONAL (Phase 3 S7a): desktop.py moved to core/desktop/desktop.py

        result = _d.ole_drag_drop(0, 0, 10, 10, duration=0.1)
        self.assertIn("not a COM OLE DoDragDrop", result)


if __name__ == "__main__":
    unittest.main()
