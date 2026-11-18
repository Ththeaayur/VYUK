"""Audit-fix Batch 4 (DESKTOP / DISPLAY): DESK-01/02/03/04 + DISPLAY-01.

DESK-03/04: focus_window_by_text searches WITHOUT bringing any
candidate forward (one desktop frame, OCR per candidate rect),
restores the original foreground on every non-success path, and
never silently focuses the first of several matching windows —
the decided tie policy is ASK: the ambiguous result names the
candidates and focuses none.
DESK-01/DISPLAY-01: an explicit monitor selector on
take_screenshot (via vision's crop_rect) and on screen_diff
(via the shared _grab_screen_image seam, with a baseline sidecar
so a baseline is never compared against another monitor), plus
the same seam on the UIA probe image.
DESK-02: probe_suffix's pixel sentence reports pixels only and
disclaims success in the same sentence.

Windows-gated behaviour is tested purely with the established
stub seams; nothing here proves real multi-monitor/DPI/rotation
behaviour on Windows — that stays field-owed (see the batch
report). Tests that touch the screenshot handler clear taint in
a finally block, per the standing guest rule.
"""

import tempfile
import unittest
from pathlib import Path

from core import skills_window_control as h3  # Phase C C6 INTENTIONAL: hands3 renamed to window_control
# SKILLS SPLIT PHASE A BATCH 2 INTENTIONAL: capture-scroll
# implementations moved to this module; the capture-function
# patch sites below target it (patch where bound), not the facade.
from core import skills_hands3_capture_scroll as h3c
# SKILLS SPLIT PHASE A BATCH 1 INTENTIONAL: focus_window_by_text reads
# its seams from the window-focus module's globals now, so _FocusStubs
# patches that module, not the facade. Imported after the facade:
# facade-first order is no longer required (batch 2 moved the
# remainder out; impl modules import each other at the bottom,
# after all definitions, so any import order works).
from core import skills_hands3_window_focus as _h3focus
from core.funnel import tools_handlers as th  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel.tools_registry import tools  # GAPS-23 (F-V7): screenshot tests run through the funnel
from core.desktop import uia as _uia  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
from core.desktop import vision as _vision  # Phase 3 D4c INTENTIONAL: vision hub moved to core/desktop/
from core.desktop import vision_manager as _vision_mgr  # INTENTIONAL (skills split B16): capture_screen lives here; see VisionCropTests note below


def _clear_taint():
    try:
        from core import injection as _injection

        _injection.clear_taint(force=True)
    except Exception:  # noqa: BLE001 — hygiene only
        pass


WORDS = [{"text": "quarterly", "left": 350, "top": 280,
          "right": 420, "bottom": 320},
         {"text": "report", "left": 430, "top": 280,
          "right": 500, "bottom": 320}]


def _words_centred(rect):
    """The phrase's words centred in `rect` — per-window boxes in
    true screen pixels, as real rect-scoped OCR returns them.
    (Sharing one absolute box across windows would put the second
    window's centre inside the first window's rect, which the
    occlusion filter must — and does — distrust.)"""
    cx = (rect[0] + rect[2]) // 2
    cy = (rect[1] + rect[3]) // 2
    return [{"text": "quarterly", "left": cx - 75, "top": cy - 20,
             "right": cx - 5, "bottom": cy + 20},
            {"text": "report", "left": cx + 5, "top": cy - 20,
             "right": cx + 75, "bottom": cy + 20}]
EMPTY_WORDS = [{"text": "nothing", "left": 10, "top": 10,
                "right": 80, "bottom": 30}]

MONITORS = [
    {"index": 1, "device": "\\\\DISPLAY1", "primary": True,
     "left": 0, "top": 0, "right": 1920, "bottom": 1080,
     "width": 1920, "height": 1080,
     "work_left": 0, "work_top": 0, "work_right": 1920,
     "work_bottom": 1040},
    {"index": 2, "device": "\\\\DISPLAY2", "primary": False,
     "left": 1920, "top": 0, "right": 3840, "bottom": 1080,
     "width": 1920, "height": 1080,
     "work_left": 1920, "work_top": 0, "work_right": 3840,
     "work_bottom": 1040},
]


class FakeUser32:
    """user32 stand-in: records activations, optionally refuses them."""

    def __init__(self, fg=100, refuse_setfg=False):
        self.fg = fg
        self.refuse_setfg = refuse_setfg
        self.show_calls = []
        self.setfg_calls = []

    def IsIconic(self, hwnd):
        return hwnd in getattr(self, "iconic", set())

    def ShowWindow(self, hwnd, cmd):
        self.show_calls.append((hwnd, cmd))
        return True

    def SetForegroundWindow(self, hwnd):
        self.setfg_calls.append(hwnd)
        if not self.refuse_setfg:
            self.fg = hwnd
        return True

    def GetForegroundWindow(self):
        return self.fg


class _FocusStubs:
    """Stub the focus search's Windows seams (no real activation)."""

    def __init__(self, test, windows, rects, words_by_rect,
                 fake=None, steal_on_first_ocr=None):
        self.test = test
        self.windows = windows
        self.rects = rects
        self.words_by_rect = words_by_rect
        self.fake = fake or FakeUser32()
        self.steal_on_first_ocr = steal_on_first_ocr
        self.ocr_calls = 0

    def __enter__(self):
        import core.desktop.control as ctl  # Phase 3 D4a INTENTIONAL: control hub moved to core/desktop/

        # SKILLS SPLIT PHASE A BATCH 1 INTENTIONAL (patch where bound):
        # focus_window_by_text now lives in the window-focus module and
        # reads all eight seams below from that module's globals, so
        # the stubs patch the focus module, not the facade. No fake
        # value or assertion changed.
        self.saved = {
            "is_windows": _h3focus._is_windows,
            "user32": _h3focus._user32,
            "fg": _h3focus._foreground_hwnd,
            "rect": _h3focus._window_rect,
            "blocked": _h3focus._input_blocked,
            "frame": _h3focus._grab_desktop_frame,
            "ocr_rect": _h3focus._ocr_words_in_rect,
            "title": _h3focus._title_of,
            "enum": ctl._enum_windows,
        }
        fake = self.fake

        def ocr_rect(frame, rect, monitors=None):
            self.ocr_calls += 1
            if (self.steal_on_first_ocr is not None
                    and self.ocr_calls == 1):
                fake.fg = self.steal_on_first_ocr
            return list(self.words_by_rect.get(tuple(rect), [])), None

        _h3focus._is_windows = lambda: True
        _h3focus._user32 = lambda: fake
        _h3focus._foreground_hwnd = lambda: fake.fg
        _h3focus._window_rect = lambda hwnd: self.rects.get(hwnd)
        _h3focus._input_blocked = lambda: None
        _h3focus._grab_desktop_frame = lambda: (object(), None)
        _h3focus._ocr_words_in_rect = ocr_rect
        _h3focus._title_of = lambda hwnd: f"Window {hwnd}"
        ctl._enum_windows = lambda: list(self.windows)
        return self

    def __exit__(self, *exc):
        import core.desktop.control as ctl  # Phase 3 D4a INTENTIONAL: control hub moved to core/desktop/

        _h3focus._is_windows = self.saved["is_windows"]
        _h3focus._user32 = self.saved["user32"]
        _h3focus._foreground_hwnd = self.saved["fg"]
        _h3focus._window_rect = self.saved["rect"]
        _h3focus._input_blocked = self.saved["blocked"]
        _h3focus._grab_desktop_frame = self.saved["frame"]
        _h3focus._ocr_words_in_rect = self.saved["ocr_rect"]
        _h3focus._title_of = self.saved["title"]
        ctl._enum_windows = self.saved["enum"]
        return False


class FocusSearchTests(unittest.TestCase):
    def test_multi_match_names_candidates_and_focuses_none(self):
        # DESK-04, decided tie policy: ASK, never silent first-match.
        fake = FakeUser32()
        windows = [(11, "Notes"), (22, "Browser")]
        rects = {11: (0, 0, 800, 600), 22: (900, 0, 1700, 600)}
        words = {r: _words_centred(r) for r in rects.values()}
        with _FocusStubs(self, windows, rects, words, fake) as stubs:
            result = h3.focus_window_by_text("quarterly report")
        self.assertIn("matches 2 windows", result)
        self.assertIn("'Notes'", result)
        self.assertIn("'Browser'", result)
        self.assertIn("didn't bring any", result)
        self.assertEqual(fake.setfg_calls, [])
        self.assertEqual(fake.show_calls, [])
        self.assertEqual(fake.fg, 100)
        # Both windows were examined — the old code stopped at one.
        self.assertEqual(stubs.ocr_calls, 2)

    def test_no_match_leaves_foreground_untouched(self):
        fake = FakeUser32()
        windows = [(11, "Notes"), (22, "Browser"), (33, "Mail")]
        rects = {h: (i * 900, 0, i * 900 + 800, 600)
                 for i, h in enumerate((11, 22, 33))}
        words = {r: EMPTY_WORDS for r in rects.values()}
        with _FocusStubs(self, windows, rects, words, fake):
            result = h3.focus_window_by_text("quarterly report")
        self.assertIn("none shows", result)
        self.assertEqual(fake.setfg_calls, [])
        self.assertEqual(fake.fg, 100)

    def test_no_match_restores_foreground_moved_mid_search(self):
        # DESK-03 restore path: focus stolen during the search
        # (simulated by the OCR stub) is put back on no-match.
        fake = FakeUser32()
        windows = [(11, "Notes"), (22, "Browser")]
        rects = {11: (0, 0, 800, 600), 22: (900, 0, 1700, 600)}
        words = {r: EMPTY_WORDS for r in rects.values()}
        with _FocusStubs(self, windows, rects, words, fake,
                         steal_on_first_ocr=22):
            result = h3.focus_window_by_text("quarterly report")
        self.assertIn("none shows", result)
        self.assertEqual(fake.fg, 100)
        self.assertEqual(fake.setfg_calls, [100])

    def test_single_match_focuses_exactly_once(self):
        fake = FakeUser32()
        windows = [(11, "Notes"), (22, "Browser"), (33, "Mail")]
        rects = {h: (i * 900, 0, i * 900 + 800, 600)
                 for i, h in enumerate((11, 22, 33))}
        words = {rects[11]: EMPTY_WORDS,
                 rects[22]: _words_centred(rects[22]),
                 rects[33]: EMPTY_WORDS}
        with _FocusStubs(self, windows, rects, words, fake):
            result = h3.focus_window_by_text("quarterly report")
        self.assertIn("Brought 'Browser' to the front", result)
        self.assertEqual(fake.setfg_calls, [22])
        self.assertEqual(fake.show_calls, [(22, 9)])
        self.assertEqual(fake.fg, 22)

    def test_single_window_multi_centre_lists_every_place(self):
        # One window, phrase twice: the window IS focused (the
        # window is unambiguous), but no silent "first of N" pick.
        fake = FakeUser32()
        windows = [(11, "Notes")]
        rects = {11: (0, 0, 800, 600)}
        two = WORDS + [{"text": "quarterly", "left": 100, "top": 400,
                        "right": 170, "bottom": 440},
                       {"text": "report", "left": 180, "top": 400,
                        "right": 250, "bottom": 440}]
        with _FocusStubs(self, windows, rects, {rects[11]: two}, fake):
            result = h3.focus_window_by_text("quarterly report")
        self.assertIn("Brought 'Notes' to the front", result)
        self.assertNotIn("first of", result)
        self.assertIn("more place(s) in the same window", result)
        self.assertEqual(fake.setfg_calls, [11])

    def test_occluded_match_is_distrusted_not_attributed(self):
        # The back window's phrase centre sits under the front
        # window's rect, so the composited frame shows the front
        # window's pixels there — it must not count as a match.
        fake = FakeUser32()
        windows = [(11, "Front"), (22, "Back")]
        rects = {11: (0, 0, 800, 600), 22: (100, 100, 900, 700)}
        words = {rects[11]: EMPTY_WORDS, rects[22]: WORDS}
        with _FocusStubs(self, windows, rects, words, fake):
            result = h3.focus_window_by_text("quarterly report")
        self.assertIn("none shows", result)
        self.assertIn("weren't trusted", result)
        self.assertEqual(fake.setfg_calls, [])

    def test_refused_activation_reports_honestly(self):
        fake = FakeUser32(refuse_setfg=True)
        windows = [(11, "Notes")]
        rects = {11: (0, 0, 800, 600)}
        with _FocusStubs(self, windows, rects, {rects[11]: WORDS}, fake):
            result = h3.focus_window_by_text("quarterly report")
        self.assertIn("refused to bring", result)
        self.assertEqual(fake.fg, 100)

    def test_title_hint_narrows_ambiguous_search(self):
        fake = FakeUser32()
        windows = [(11, "Notes"), (22, "Browser")]
        rects = {11: (0, 0, 800, 600), 22: (900, 0, 1700, 600)}
        words = {r: _words_centred(r) for r in rects.values()}
        with _FocusStubs(self, windows, rects, words, fake):
            result = h3.focus_window_by_text("quarterly report",
                                             title_hint="browser")
        self.assertIn("Brought 'Browser' to the front", result)
        self.assertEqual(fake.setfg_calls, [22])

    def test_minimised_windows_reported_not_checked(self):
        fake = FakeUser32()
        fake.iconic = {11}
        windows = [(11, "Notes"), (22, "Browser")]
        rects = {11: (0, 0, 800, 600), 22: (900, 0, 1700, 600)}
        words = {rects[22]: EMPTY_WORDS}
        with _FocusStubs(self, windows, rects, words, fake):
            result = h3.focus_window_by_text("quarterly report")
        self.assertIn("minimised window(s) weren't checked", result)
        self.assertEqual(fake.setfg_calls, [])


class GrabSeamTests(unittest.TestCase):
    def test_monitor_crop_translates_by_virtual_origin(self):
        # A monitor left of the primary has a negative origin; the
        # bbox handed to the all-screens grab must translate by it.
        seen = {}

        def grab(bbox=None):
            # Stands in for vision._grab_image, whose own contract
            # (all_screens=True to ImageGrab) is pinned by the
            # desktopreach F-05 tests; this seam asserts only the
            # bbox the shared capture seam hands it.
            from PIL import Image

            seen["bbox"] = bbox
            return Image.new("RGB", (3840, 1080), (10, 20, 30))

        saved_grab = _vision._grab_image
        # SKILLS SPLIT PHASE A BATCH 2 INTENTIONAL: _grab_screen_image reads _monitors from the capture-scroll module's globals now, so this patch targets that module, not the facade.
        saved_monitors = h3c._monitors
        try:
            _vision._grab_image = grab
            h3c._monitors = lambda: [
                {"left": -1920, "top": 0, "right": 0, "bottom": 1080},
                {"left": 0, "top": 0, "right": 1920, "bottom": 1080},
            ]
            image, err = h3._grab_screen_image((-1920, 0, 0, 1080))
        finally:
            _vision._grab_image = saved_grab
            h3c._monitors = saved_monitors
        self.assertIsNone(err)
        self.assertEqual(seen["bbox"], (0, 0, 1920, 1080))
        self.assertEqual(image.mode, "RGB")


class ScreenDiffMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # SKILLS SPLIT PHASE A BATCH 2 INTENTIONAL: screen_diff reads _DATA_DIR/_monitors/_grab_screen_image from the capture-scroll module's globals now, so setUp/tearDown patch that module.
        self.saved_data = h3c._DATA_DIR
        self.saved_monitors = h3c._monitors
        self.saved_grab = h3c._grab_screen_image
        h3c._DATA_DIR = Path(self.tmp.name)
        h3c._monitors = lambda: [dict(m) for m in MONITORS]
        from PIL import Image

        self.frames = {}

        def grab(rect=None):
            if rect is None:
                return Image.new("RGB", (100, 100), (200, 0, 0)), None
            return self.frames.get(tuple(rect),
                                   Image.new("RGB", (100, 100),
                                             (0, 0, 200))), None

        h3c._grab_screen_image = grab

    def tearDown(self):
        h3c._DATA_DIR = self.saved_data
        h3c._monitors = self.saved_monitors
        h3c._grab_screen_image = self.saved_grab
        self.tmp.cleanup()

    def test_monitor_baseline_and_compare_honour_monitor(self):
        from PIL import Image

        self.frames[(1920, 0, 3840, 1080)] = Image.new(
            "RGB", (100, 100), (0, 200, 0))
        saved = h3.screen_diff("save_baseline", monitor="2")
        self.assertIn("monitor 2", saved)
        self.assertTrue((Path(self.tmp.name)
                         / h3._BASELINE_SIDECAR_NAME).exists())
        compared = h3.screen_diff("compare", monitor="2")
        self.assertIn("essentially unchanged", compared)
        self.assertIn("monitor 2", compared)

    def test_compare_against_other_monitor_refuses(self):
        from PIL import Image

        self.frames[(1920, 0, 3840, 1080)] = Image.new(
            "RGB", (100, 100), (0, 200, 0))
        h3.screen_diff("save_baseline", monitor="2")
        refused = h3.screen_diff("compare", monitor="1")
        self.assertIn("monitor 2's picture", refused)
        self.assertIn("monitor 1", refused)
        self.assertNotIn("essentially unchanged", refused)

    def test_monitor_baseline_without_monitor_refuses(self):
        h3.screen_diff("save_baseline", monitor="2")
        refused = h3.screen_diff("compare")
        self.assertIn("monitor 2", refused)
        self.assertNotIn("essentially unchanged", refused)

    def test_legacy_baseline_with_monitor_refuses(self):
        h3.screen_diff("save_baseline")
        self.assertFalse((Path(self.tmp.name)
                          / h3._BASELINE_SIDECAR_NAME).exists())
        refused = h3.screen_diff("compare", monitor="2")
        self.assertIn("predates monitor tracking", refused)

    def test_no_monitor_baseline_unchanged(self):
        saved = h3.screen_diff("save_baseline")
        self.assertNotIn("monitor", saved)
        compared = h3.screen_diff("compare")
        self.assertIn("essentially unchanged", compared)
        self.assertNotIn("monitor", compared)

    def test_unknown_monitor_errors_honestly(self):
        result = h3.screen_diff("compare", monitor="9")
        self.assertIn("There is no monitor 9", result)


class TakeScreenshotMonitorTests(unittest.TestCase):
    def _handler_with_stubs(self, calls):
        handler = object.__new__(th.ToolHandlersMixin)
        saved_capture = _vision.vision_manager.capture_screen
        saved_info = _vision.vision_manager._last_capture_info
        saved_frame = th._frame_result_text
        saved_marks = th._marks_suffix
        # INTENTIONAL (skills split B49): _tool_take_screenshot /
        # _tool_screen_look moved to the capture_launch mixin and
        # resolve _frame_result_text / _marks_suffix from the mixin
        # module's globals, so the facade patches alone no longer
        # steer them — patch BOTH bindings (B16/B47 pattern), same
        # fakes, both restored.
        from core.funnel import tools_handlers_capture_launch as _th_m2
        saved_frame_m2 = _th_m2._frame_result_text
        saved_marks_m2 = _th_m2._marks_suffix
        import core.capture_guard as cg

        saved_allowed = cg.ensure_capture_allowed
        saved_started = cg.note_capture_started
        saved_ended = cg.note_capture_ended
        # SKILLS SPLIT PHASE A BATCH 2 INTENTIONAL: _resolve_capture_monitor reads _monitors/_foreground_hwnd from the capture-scroll module's globals now, so these patches target that module, not the facade.
        saved_monitors = h3c._monitors
        saved_fg = h3c._foreground_hwnd

        def capture(**kw):
            calls.append(dict(kw))
            return (b"fakejpeg", "image/jpeg")

        _vision.vision_manager.capture_screen = capture
        _vision.vision_manager._last_capture_info = None
        th._frame_result_text = lambda *a, **k: "FRAME-RESULT"
        th._marks_suffix = lambda: ""
        _th_m2._frame_result_text = lambda *a, **k: "FRAME-RESULT"
        _th_m2._marks_suffix = lambda: ""
        cg.ensure_capture_allowed = lambda *a, **k: (True, "")
        cg.note_capture_started = lambda *a, **k: None
        cg.note_capture_ended = lambda *a, **k: None
        h3c._monitors = lambda: [dict(m) for m in MONITORS]
        h3c._foreground_hwnd = lambda: None

        def restore():
            _vision.vision_manager.capture_screen = saved_capture
            _vision.vision_manager._last_capture_info = saved_info
            th._frame_result_text = saved_frame
            th._marks_suffix = saved_marks
            _th_m2._frame_result_text = saved_frame_m2
            _th_m2._marks_suffix = saved_marks_m2
            cg.ensure_capture_allowed = saved_allowed
            cg.note_capture_started = saved_started
            cg.note_capture_ended = saved_ended
            h3c._monitors = saved_monitors
            h3c._foreground_hwnd = saved_fg
            _clear_taint()

        return handler, restore

    def test_named_monitor_captures_that_monitor(self):
        calls = []
        _, restore = self._handler_with_stubs(calls)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dest = str(Path(tmp) / "shot.jpg")
                # GAPS-23 (F-V7): through the funnel — the stubs
                # steer the handler identically; the string result
                # lands in result["result"].
                result = tools.execute(
                    "take_screenshot",
                    {"save_path": dest, "scope": "whole_desktop",
                     "monitor": "2"})["result"]
        finally:
            restore()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["full_screen"])
        self.assertEqual(calls[0]["crop_rect"], (1920, 0, 3840, 1080))
        self.assertIn("monitor 2", result)
        self.assertIn("Screenshot saved", result)

    def test_unknown_monitor_captures_nothing(self):
        calls = []
        _, restore = self._handler_with_stubs(calls)
        try:
            result = tools.execute(
                "take_screenshot",
                {"scope": "whole_desktop", "monitor": "9"})["result"]
        finally:
            restore()
        self.assertIn("There is no monitor 9", result)
        self.assertEqual(calls, [])

    def test_monitor_with_foreground_scope_conflicts(self):
        calls = []
        _, restore = self._handler_with_stubs(calls)
        try:
            result = tools.execute(
                "take_screenshot",
                {"scope": "foreground", "monitor": "2"})["result"]
        finally:
            restore()
        self.assertIn("conflict", result)
        self.assertEqual(calls, [])

    def test_whole_desktop_without_monitor_unchanged(self):
        calls = []
        _, restore = self._handler_with_stubs(calls)
        try:
            result = tools.execute(
                "take_screenshot",
                {"scope": "whole_desktop"})["result"]
        finally:
            restore()
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0]["crop_rect"])
        self.assertNotIn("monitor", result)

    def test_schema_exposes_monitor(self):
        regs = []

        def record(**kw):
            regs.append(kw)

        handler = object.__new__(th.ToolHandlersMixin)
        handler.register = record
        th.ToolHandlersMixin._register_control_tools(handler)
        shot = [r for r in regs if r.get("name") == "take_screenshot"]
        self.assertEqual(len(shot), 1)
        self.assertIn("monitor", shot[0]["parameters"]["properties"])
        self.assertIn("whole_desktop",
                      shot[0]["parameters"]["properties"]["monitor"]
                      ["description"])


class VisionCropTests(unittest.TestCase):
    # INTENTIONAL (skills split B16): VisionManager.capture_screen moved
    # from core/desktop/vision.py to core/desktop/vision_manager.py, and it
    # resolves _virtual_screen_bounds / ground_marks_for_frame by bare name
    # in the manager module's globals — so the fakes below must land in
    # BOTH the facade and the manager module (same B15 seam-tuple rule: a
    # fake must land in every module that binds the name). Same fakes,
    # same assertions; patching the facade alone silently misses the moved
    # capture_screen (proven by test_monitor_crop_geometry_and_marks_filter
    # failing live pre-fix).
    def test_monitor_crop_records_monitor_geometry(self):
        from PIL import Image

        saved_native = _vision.vision_manager.capture_native_image
        saved_bounds = _vision._virtual_screen_bounds
        saved_bounds_mgr = _vision_mgr._virtual_screen_bounds
        saved_info = _vision.vision_manager._last_capture_info
        info_seen = {}
        try:
            meta = {"screen_width": 3840, "screen_height": 1080,
                    "cropped": False, "crop_left": 0, "crop_top": 0,
                    "crop_width": 3840, "crop_height": 1080}
            _vision.vision_manager.capture_native_image = (
                lambda full_screen=False: (
                    Image.new("RGB", (3840, 1080), (5, 5, 5)),
                    dict(meta)))
            _vision._virtual_screen_bounds = lambda: (0, 0, 3840, 1080)
            _vision_mgr._virtual_screen_bounds = lambda: (0, 0, 3840, 1080)
            data, mime = _vision.vision_manager.capture_screen(
                full_screen=True, crop_rect=(1920, 0, 3840, 1080))
            info_seen.update(
                _vision.vision_manager.last_capture_info() or {})
        finally:
            _vision.vision_manager.capture_native_image = saved_native
            _vision._virtual_screen_bounds = saved_bounds
            _vision_mgr._virtual_screen_bounds = saved_bounds_mgr
            _vision.vision_manager._last_capture_info = saved_info
        self.assertEqual(mime, "image/jpeg")
        self.assertTrue(len(data) > 0)
        self.assertTrue(info_seen.get("cropped"))
        self.assertEqual(info_seen.get("crop_left"), 1920)
        self.assertEqual(info_seen.get("crop_top"), 0)
        self.assertEqual(info_seen.get("crop_width"), 1920)
        self.assertEqual(info_seen.get("crop_height"), 1080)
        # The sent thumbnail is the monitor crop scaled to max_dim.
        self.assertEqual(info_seen.get("image_width"), 1024)
        self.assertEqual(info_seen.get("image_height"), 576)

    def test_monitor_crop_geometry_and_marks_filter(self):
        from PIL import Image

        saved_native = _vision.vision_manager.capture_native_image
        saved_bounds = _vision._virtual_screen_bounds
        saved_bounds_mgr = _vision_mgr._virtual_screen_bounds
        saved_info = _vision.vision_manager._last_capture_info
        saved_marks = _vision.vision_manager._last_marks
        saved_ground = _vision.ground_marks_for_frame
        saved_ground_mgr = _vision_mgr.ground_marks_for_frame
        saved_clear = None
        try:
            import core.desktop.uia as uia_mod  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/

            saved_clear = uia_mod.clear_last_marks
            uia_mod.clear_last_marks = lambda: None
        except Exception:  # noqa: BLE001
            pass
        info_seen = {}
        try:
            meta = {"screen_width": 3840, "screen_height": 1080,
                    "cropped": False, "crop_left": 0, "crop_top": 0,
                    "crop_width": 3840, "crop_height": 1080}
            _vision.vision_manager.capture_native_image = (
                lambda full_screen=False: (
                    Image.new("RGB", (3840, 1080), (5, 5, 5)),
                    dict(meta)))
            _vision._virtual_screen_bounds = lambda: (0, 0, 3840, 1080)
            _vision_mgr._virtual_screen_bounds = lambda: (0, 0, 3840, 1080)
            _vision.ground_marks_for_frame = lambda image, meta: [
                {"id": 1, "rect": (100, 100, 200, 200)},
                {"id": 2, "rect": (2000, 100, 2100, 200)},
            ]
            _vision_mgr.ground_marks_for_frame = lambda image, meta: [
                {"id": 1, "rect": (100, 100, 200, 200)},
                {"id": 2, "rect": (2000, 100, 2100, 200)},
            ]
            _vision.vision_manager.capture_screen(
                full_screen=True, marks=True,
                crop_rect=(1920, 0, 3840, 1080))
            info_seen.update(_vision.vision_manager.last_capture_info()
                             or {})
            kept = [m["id"] for m in _vision.vision_manager.last_marks()]
        finally:
            _vision.vision_manager.capture_native_image = saved_native
            _vision._virtual_screen_bounds = saved_bounds
            _vision_mgr._virtual_screen_bounds = saved_bounds_mgr
            _vision.vision_manager._last_capture_info = saved_info
            _vision.vision_manager._last_marks = saved_marks
            _vision.ground_marks_for_frame = saved_ground
            _vision_mgr.ground_marks_for_frame = saved_ground_mgr
            if saved_clear is not None:
                import core.desktop.uia as uia_mod  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/

                uia_mod.clear_last_marks = saved_clear
        self.assertTrue(info_seen.get("cropped"))
        self.assertEqual(info_seen.get("crop_left"), 1920)
        self.assertEqual(info_seen.get("crop_width"), 1920)
        self.assertEqual(kept, [2])

    def test_crop_outside_frame_fails_loud(self):
        from PIL import Image

        saved_native = _vision.vision_manager.capture_native_image
        saved_bounds = _vision._virtual_screen_bounds
        saved_bounds_mgr = _vision_mgr._virtual_screen_bounds
        try:
            _vision.vision_manager.capture_native_image = (
                lambda full_screen=False: (
                    Image.new("RGB", (1920, 1080), (5, 5, 5)),
                    {"screen_width": 1920, "screen_height": 1080,
                     "cropped": False, "crop_left": 0, "crop_top": 0,
                     "crop_width": 1920, "crop_height": 1080}))
            _vision._virtual_screen_bounds = lambda: (0, 0, 1920, 1080)
            _vision_mgr._virtual_screen_bounds = lambda: (0, 0, 1920, 1080)
            with self.assertRaises(ValueError):
                _vision.vision_manager.capture_screen(
                    full_screen=True, crop_rect=(5000, 0, 6000, 1080))
        finally:
            _vision.vision_manager.capture_native_image = saved_native
            _vision._virtual_screen_bounds = saved_bounds
            _vision_mgr._virtual_screen_bounds = saved_bounds_mgr


class UiaProbeTests(unittest.TestCase):
    def test_probe_suffix_reports_pixels_not_success(self):
        from PIL import Image

        before = Image.new("RGB", (100, 100), (0, 0, 0))
        after = Image.new("RGB", (100, 100), (255, 255, 255))
        suffix = _uia.probe_suffix(None, None, before, after, "coords")
        self.assertIn("pixels changed", suffix)
        self.assertIn("not proof", suffix)
        self.assertNotIn("The screen changed", suffix)

    def test_probe_suffix_no_change_sentence_unchanged(self):
        from PIL import Image

        before = Image.new("RGB", (100, 100), (0, 0, 0))
        suffix = _uia.probe_suffix(None, None, before, before, "coords")
        self.assertIn("No visible change", suffix)

    def test_screen_image_forwards_monitor_rect(self):
        from PIL import Image

        seen = []
        # SKILLS SPLIT PHASE A BATCH 2 INTENTIONAL (deliberate non-move): uia_probe imports _grab_screen_image from the facade at call time via a function-local import, so patching the facade still reaches it; patching the capture module would not. This site stays on h3.
        saved = h3._grab_screen_image
        try:
            def grab(rect=None):
                seen.append(rect)
                return Image.new("RGB", (10, 10), (1, 2, 3)), None

            h3._grab_screen_image = grab
            image = _uia._screen_image((1920, 0, 3840, 1080))
            default = _uia._screen_image()
        finally:
            h3._grab_screen_image = saved
        self.assertIsNotNone(image)
        self.assertIsNotNone(default)
        self.assertEqual(seen, [(1920, 0, 3840, 1080), None])


class RegistrationTests(unittest.TestCase):
    def test_skills_registration_states_tie_policy(self):
        regs = []

        def record(**kw):
            regs.append(kw)

        class _Reg:
            register = staticmethod(record)

        h3.register_all(_Reg())
        focus = [r for r in regs if r.get("name") == "focus_window_by_text"]
        self.assertEqual(len(focus), 1)
        self.assertIn("title_hint",
                      focus[0]["parameters"]["properties"])
        self.assertNotIn("first match is focused",
                         focus[0]["description"])
        self.assertIn("never silently resolved to the first",
                      focus[0]["description"])
        diff = [r for r in regs if r.get("name") == "screen_diff"]
        self.assertEqual(len(diff), 1)
        self.assertIn("monitor", diff[0]["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()
