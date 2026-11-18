"""Norm1000 capture-freshness tests (2026-11-03 investigation batch).

A norm1000 point carries no provenance, and the conversion grid is the
LAST capture's record read at action time — so the old path executed a
point read off one screenshot through whatever grid was current, even
after the window moved or the screen changed. The fix:
``core.desktop.cursor._capture_freshness_refusal`` refuses on positive
evidence the world moved (virtual size changed, or a foreground-window
crop's rectangle no longer matches the live foreground), with a
re-observe instruction — before any move/click executes.

Hermetic: the legacy Windows body is exercised by stubbing
``cursor._routed_backend`` to None (the established pattern — the
backend path converts against screen size, not the capture crop, so
the check lives in the legacy body only); the capture record is faked
on the real ``vision_manager`` singleton; the foreground rectangle
and virtual bounds are stubbed on their modules. Everything is
restored in tearDown. No real mouse, no real screen, no network.

Non-vacuity: test_check_absent_clicks_proves_the_gate stubs the
check itself to None and shows the stale click then dispatches —
the refusal tests fail without the check. On the pristine pre-change
tree the helper does not exist at all (AttributeError), so the
suite cannot pass there.
"""

import unittest

from core.desktop import cursor as _cursor
from core.desktop import cursor_control as _cursor_control  # B35 INTENTIONAL: control family moved here (part 2 of 2); cursor_control reads _routed_backend/click_cursor/_capture_freshness_refusal from the control module globals, so fakes land in both bindings below
from core.desktop import cursor_geometry as _cursor_geometry  # B35 INTENTIONAL: geometry family lives here (part 1 of 2); _screen_size/_fence_point read _virtual_bounds through the geometry module namespace, so the fake lands in both bindings below
from core.desktop import vision as _vision


_ABSENT = object()


def _record(*, cropped=True, sw=1920, sh=1080, cl=100, ct=50,
            cw=800, ch=600, monitor_crop=False, vb=(0, 0, 1920, 1080)):
    record = {
        "screen_width": sw,
        "screen_height": sh,
        "image_width": 800,
        "image_height": 600,
        "max_dim": 1024,
        "cropped": cropped,
        "crop_left": cl,
        "crop_top": ct,
        "crop_width": cw,
        "crop_height": ch,
        "monitor_crop": monitor_crop,
    }
    # vb=_ABSENT builds the old record shape (no key) — the shape the
    # shipped F-01 test fakes — which must skip the bounds branch.
    if vb is not _ABSENT:
        record["virtual_bounds"] = vb
    return record


class FreshnessTest(unittest.TestCase):
    def setUp(self):
        self._old_backend = _cursor._routed_backend
        _cursor._routed_backend = lambda: None
        # B35 INTENTIONAL: cursor_control reads _routed_backend from
        # the control module globals, so fake both bindings (same
        # fake, both restored) — B16 pattern.
        self._old_backend_ctrl = _cursor_control._routed_backend
        _cursor_control._routed_backend = lambda: None
        self._old_info = _vision.vision_manager._last_capture_info
        self._old_fg = _vision.foreground_window_rect
        self._old_bounds = _cursor._virtual_bounds
        # B35 INTENTIONAL: geometry functions read _virtual_bounds
        # from the geometry module globals, so fake both bindings
        # (same fake, both restored) — B34 fencing pattern.
        self._old_bounds_geom = _cursor_geometry._virtual_bounds
        _cursor_geometry._virtual_bounds = lambda: (0, 0, 1919, 1079)
        self._old_vb = _vision._virtual_screen_bounds
        self._old_click = _cursor.click_cursor
        # B35 INTENTIONAL: per-test click_cursor fakes below land in
        # the control module too, so save/restore that binding here.
        self._old_click_ctrl = _cursor_control.click_cursor
        # Default live world: matches the default record exactly.
        _cursor._virtual_bounds = lambda: (0, 0, 1919, 1079)
        _vision._virtual_screen_bounds = lambda: (0, 0, 1920, 1080)
        _vision.foreground_window_rect = lambda: (100, 50, 900, 650)

    def tearDown(self):
        _cursor._routed_backend = self._old_backend
        _cursor_control._routed_backend = self._old_backend_ctrl
        _vision.vision_manager._last_capture_info = self._old_info
        _vision.foreground_window_rect = self._old_fg
        _cursor._virtual_bounds = self._old_bounds
        _cursor_geometry._virtual_bounds = self._old_bounds_geom
        _vision._virtual_screen_bounds = self._old_vb
        _cursor.click_cursor = self._old_click
        _cursor_control.click_cursor = self._old_click_ctrl

    def _set_record(self, record):
        _vision.vision_manager._last_capture_info = record

    # -- helper level -------------------------------------------------
    def test_no_record_passes_through(self):
        self._set_record(None)
        self.assertIsNone(_cursor._capture_freshness_refusal())

    def test_matching_crop_passes(self):
        self._set_record(_record())
        self.assertIsNone(_cursor._capture_freshness_refusal())

    def test_arrangement_change_refuses(self):
        self._set_record(_record())
        _vision._virtual_screen_bounds = lambda: (0, 0, 2560, 1440)
        refusal = _cursor._capture_freshness_refusal()
        self.assertIn("screen arrangement changed", refusal)
        self.assertIn("(0, 0, 1920, 1080)", refusal)
        self.assertIn("(0, 0, 2560, 1440)", refusal)
        self.assertIn("Re-observe", refusal)

    def test_old_record_without_virtual_bounds_skips_check(self):
        # The shipped F-01 shape: a record with no virtual_bounds
        # key must not refuse, whatever the live bounds are — the
        # recorded screen size legitimately differs from the
        # virtual width on multi-monitor desktops.
        self._set_record(_record(vb=_ABSENT))
        _vision._virtual_screen_bounds = lambda: (-1920, 0, 1920, 1080)
        self.assertIsNone(_cursor._capture_freshness_refusal())

    def test_window_moved_refuses(self):
        self._set_record(_record())
        _vision.foreground_window_rect = lambda: (300, 50, 1100, 650)
        refusal = _cursor._capture_freshness_refusal()
        self.assertIn("foreground window moved", refusal)
        self.assertIn("Re-observe", refusal)

    def test_foreground_swapped_refuses(self):
        self._set_record(_record())
        _vision.foreground_window_rect = lambda: (0, 0, 1920, 1080)
        refusal = _cursor._capture_freshness_refusal()
        self.assertIsNotNone(refusal)
        self.assertIn("foreground window moved", refusal)

    def test_uncropped_ignores_foreground(self):
        self._set_record(_record(cropped=False))
        _vision.foreground_window_rect = lambda: (0, 0, 500, 500)
        self.assertIsNone(_cursor._capture_freshness_refusal())

    def test_uncropped_bounds_change_still_refuses(self):
        self._set_record(_record(cropped=False))
        _vision._virtual_screen_bounds = lambda: (0, 0, 1280, 720)
        refusal = _cursor._capture_freshness_refusal()
        self.assertIn("screen arrangement changed", refusal)

    def test_monitor_crop_skips_foreground_check(self):
        # DISPLAY-01: the crop is a monitor rectangle, not a window.
        self._set_record(_record(monitor_crop=True))
        _vision.foreground_window_rect = lambda: (0, 0, 500, 500)
        self.assertIsNone(_cursor._capture_freshness_refusal())

    def test_malformed_record_passes_without_raising(self):
        self._set_record({"cropped": True})
        self.assertIsNone(_cursor._capture_freshness_refusal())
        self._set_record({"cropped": True, "crop_left": 0, "crop_top": 0,
                          "crop_width": "wide", "crop_height": 10})
        self.assertIsNone(_cursor._capture_freshness_refusal())
        self._set_record(_record(vb="nonsense"))
        self.assertIsNone(_cursor._capture_freshness_refusal())

    def test_unreadable_live_world_passes_through(self):
        self._set_record(_record())
        _cursor._virtual_bounds = lambda: None
        _vision._virtual_screen_bounds = lambda: None
        _vision.foreground_window_rect = lambda: None
        self.assertIsNone(_cursor._capture_freshness_refusal())

    def test_malformed_live_rect_passes_without_raising(self):
        self._set_record(_record())
        _vision.foreground_window_rect = lambda: (100, 50)
        self.assertIsNone(_cursor._capture_freshness_refusal())

    # -- action level ---------------------------------------------------
    def test_stale_click_refuses_and_never_dispatches(self):
        self._set_record(_record())
        _vision.foreground_window_rect = lambda: (300, 50, 1100, 650)
        calls = []
        _cursor.click_cursor = lambda **kw: calls.append(kw) or "clicked"
        # B35 INTENTIONAL: cursor_control reads click_cursor from the
        # control module globals, so fake both bindings (same fake,
        # both restored by tearDown) — B16 pattern.
        _cursor_control.click_cursor = lambda **kw: calls.append(kw) or "clicked"
        result = _cursor.cursor_control(action="click", x=500, y=500)
        self.assertIn("foreground window moved", result)
        self.assertEqual(calls, [])

    def test_fresh_click_dispatches_converted_pixels(self):
        # Crop (100, 50, 800x600): norm1000 (500, 500) -> (100+400, 50+300).
        self._set_record(_record())
        calls = []
        _cursor.click_cursor = lambda **kw: calls.append(kw) or "clicked"
        # B35 INTENTIONAL: same dual-binding fake as the stale-click test.
        _cursor_control.click_cursor = lambda **kw: calls.append(kw) or "clicked"
        result = _cursor.cursor_control(action="click", x=500, y=500)
        self.assertEqual(result, "clicked")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["x"], 500)
        self.assertEqual(calls[0]["y"], 350)

    def test_pixels_mode_bypasses_the_check(self):
        self._set_record(_record())
        _vision.foreground_window_rect = lambda: (300, 50, 1100, 650)
        calls = []
        _cursor.click_cursor = lambda **kw: calls.append(kw) or "clicked"
        # B35 INTENTIONAL: same dual-binding fake as the stale-click test.
        _cursor_control.click_cursor = lambda **kw: calls.append(kw) or "clicked"
        result = _cursor.cursor_control(action="click", x=500, y=350,
                                        coords="pixels")
        self.assertEqual(result, "clicked")
        self.assertEqual(len(calls), 1)

    def test_stale_move_refuses(self):
        self._set_record(_record())
        _vision._virtual_screen_bounds = lambda: (0, 0, 1280, 720)
        result = _cursor.cursor_control(action="move", x=100, y=100)
        self.assertIn("screen arrangement changed", result)

    def test_check_absent_clicks_proves_the_gate(self):
        # Non-vacuity: without the check, the stale click dispatches.
        self._set_record(_record())
        _vision.foreground_window_rect = lambda: (300, 50, 1100, 650)
        calls = []
        _cursor.click_cursor = lambda **kw: calls.append(kw) or "clicked"
        # B35 INTENTIONAL: cursor_control reads click_cursor and
        # _capture_freshness_refusal from the control module
        # globals, so fake both bindings (same fakes, both
        # restored) — B16 pattern.
        _cursor_control.click_cursor = lambda **kw: calls.append(kw) or "clicked"
        old = _cursor._capture_freshness_refusal
        _cursor._capture_freshness_refusal = lambda: None
        old_ctrl = _cursor_control._capture_freshness_refusal
        _cursor_control._capture_freshness_refusal = lambda: None
        try:
            result = _cursor.cursor_control(action="click", x=500, y=500)
        finally:
            _cursor._capture_freshness_refusal = old
            _cursor_control._capture_freshness_refusal = old_ctrl
        self.assertEqual(result, "clicked")
        self.assertEqual(len(calls), 1)

    def test_record_carries_monitor_crop_flag(self):
        # Source pins for the vision.py record additions: the
        # check's monitor-crop branch is meaningless without the
        # flag, and its bounds branch without the recorded bounds.
        # INTENTIONAL (skills split B16): the record-writing code
        # moved from core/desktop/vision.py to
        # core/desktop/vision_manager.py (VisionManager.capture_screen),
        # so the pins read both files — same assertions, strictly
        # stronger coverage (a fake must land in every module that
        # binds the name; here the pinned text must exist in the
        # module that now owns it).
        with open("core/desktop/vision.py") as f:
            facade_source = f.read()
        with open("core/desktop/vision_manager.py") as f:
            manager_source = f.read()
        source = facade_source + manager_source
        self.assertIn('"monitor_crop": crop_rect is not None', source)
        self.assertIn('"virtual_bounds": _record_virtual_bounds()', source)


if __name__ == "__main__":
    unittest.main()
