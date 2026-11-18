"""Shipped unit tests (desktop programme, group i): the coordinate
contract — audit 8 items F-01, F-02, F-03.

F-01: cursor dispatch must map norm1000 against the LAST CAPTURED
IMAGE's rectangle, not the primary screen. The audit's isolated
reproduction is pinned exactly: crop at (800, 100) sized 800x600,
norm1000 (500, 500) dispatches to (1200, 400) — the old code sent
(960, 540).

F-02: with no crop to map against, norm1000 maps against the
VIRTUAL desktop bounds (every monitor stitched together), never
the primary screen alone. A secondary monitor at negative x must
shift the grid, and an uncropped full-desktop capture takes the
virtual origin when its frame IS the virtual desktop.

F-03: screen_look gains the same capture-geometry line
take_screenshot appends, on both its vision and text paths.

Geometry is faked through the modules' existing seams (a patched
cursor._virtual_bounds, a patched vision last_capture_info, a
recording move_cursor) — no display is touched. Windows behaviour
is NOT proven here: the D-10 matrix on real Windows is the
programme's release gate and stays field work.

Stdlib unittest only. Run via tests/run_tests.py.
"""

from __future__ import annotations

import unittest

try:
    from core.desktop import cursor  # Phase 3 D4d INTENTIONAL: cursor hub moved to core/desktop/
    from core.desktop import cursor_geometry  # B34 INTENTIONAL: geometry-freshness family moved here (part 1 of 2); _fence_point reads _virtual_bounds through its own module namespace, so geometry fakes land in both bindings below
    from core.desktop import cursor_control as cursor_control_mod  # B35 INTENTIONAL: control family moved here (part 2 of 2); cursor_control reads _routed_backend/move_cursor from the control module globals, so control fakes land in both bindings below
    from core.desktop import vision as _vision  # Phase 3 D4c INTENTIONAL: vision hub moved to core/desktop/
    from core import w4_wiring
    from core.funnel import tools_handlers as _th  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
    from core.funnel.tools_registry import tools  # GAPS-23 (F-V7): screen_look runs through the funnel
except Exception as exc:  # noqa: BLE001
    raise unittest.SkipTest(f"desktop modules unavailable: {exc}")

# Dual-monitor virtual screen: primary 1920x1080 at origin, a
# secondary 1920x1080 parked to its LEFT (negative x).
DUAL = (-1920, 0, 1919, 1079)
SINGLE = (0, 0, 1919, 1079)

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


def _patch_vision_info(info):
    """Patch core.desktop.vision.last_capture_info; returns a restore fn."""
    orig = _vision.last_capture_info
    _vision.last_capture_info = lambda: dict(info) if info else None
    return lambda: setattr(_vision, "last_capture_info", orig)


def _patch_virtual(bounds):
    # B34 INTENTIONAL: _norm1000_reference/_fence_point now resolve
    # _virtual_bounds from the geometry module's globals, so the fake
    # must land in both bindings (same fake, both restored) — the
    # B16 patch-both-bindings pattern, same assertions, no weakening.
    orig = cursor._virtual_bounds
    orig_geo = cursor_geometry._virtual_bounds
    cursor._virtual_bounds = lambda: bounds
    cursor_geometry._virtual_bounds = lambda: bounds

    def _restore():
        cursor._virtual_bounds = orig
        cursor_geometry._virtual_bounds = orig_geo

    return _restore


class ReferenceTests(unittest.TestCase):
    def test_crop_is_the_reference(self):
        restore_info = _patch_vision_info(CROP_INFO)
        restore_vb = _patch_virtual(SINGLE)
        try:
            self.assertEqual(cursor._norm1000_reference(),
                             (800, 100, 800, 600))
        finally:
            restore_info()
            restore_vb()

    def test_no_capture_uses_virtual_bounds_not_primary(self):
        restore_info = _patch_vision_info(None)
        restore_vb = _patch_virtual(DUAL)
        try:
            self.assertEqual(cursor._norm1000_reference(),
                             (-1920, 0, 3840, 1080))
        finally:
            restore_info()
            restore_vb()

    def test_uncropped_full_desktop_takes_virtual_origin(self):
        info = dict(CROP_INFO, cropped=False,
                    screen_width=3840, screen_height=1080)
        restore_info = _patch_vision_info(info)
        restore_vb = _patch_virtual(DUAL)
        try:
            self.assertEqual(cursor._norm1000_reference(),
                             (-1920, 0, 3840, 1080))
        finally:
            restore_info()
            restore_vb()

    def test_uncropped_primary_only_frame_keeps_zero_origin(self):
        info = dict(CROP_INFO, cropped=False,
                    screen_width=1920, screen_height=1080)
        restore_info = _patch_vision_info(info)
        restore_vb = _patch_virtual(DUAL)
        try:
            # Frame size differs from the virtual size, so this
            # frame is NOT the virtual desktop — origin stays 0.
            self.assertEqual(cursor._norm1000_reference(),
                             (0, 0, 1920, 1080))
        finally:
            restore_info()
            restore_vb()

    def test_malformed_crop_falls_through_to_virtual_bounds(self):
        info = dict(CROP_INFO, crop_width=0)
        restore_info = _patch_vision_info(info)
        restore_vb = _patch_virtual(DUAL)
        try:
            self.assertEqual(cursor._norm1000_reference(),
                             (-1920, 0, 3840, 1080))
        finally:
            restore_info()
            restore_vb()

    def test_missing_keys_fall_through_to_virtual_bounds(self):
        restore_info = _patch_vision_info({"cropped": True})
        restore_vb = _patch_virtual(DUAL)
        try:
            self.assertEqual(cursor._norm1000_reference(),
                             (-1920, 0, 3840, 1080))
        finally:
            restore_info()
            restore_vb()

    def test_nothing_measurable_is_none(self):
        restore_info = _patch_vision_info(None)
        restore_vb = _patch_virtual(None)
        try:
            self.assertIsNone(cursor._norm1000_reference())
            self.assertIsNone(cursor._norm1000_to_screen(500, 500))
        finally:
            restore_info()
            restore_vb()


class DispatchTests(unittest.TestCase):
    def _run_move(self, info, bounds, x=500, y=500):
        # The fixed body IS the Windows implementation: on this
        # Linux VM a non-Windows backend serves instead, so the
        # backend seam is stubbed to None (the Windows-serving
        # shape) and move_cursor is recorded, not driven.
        restore_info = _patch_vision_info(info)
        restore_vb = _patch_virtual(bounds)
        orig_backend = cursor._routed_backend
        cursor._routed_backend = lambda: None
        orig_move = cursor.move_cursor
        seen = {}
        cursor.move_cursor = lambda **kw: seen.update(kw) or "moved"
        # B35 INTENTIONAL: cursor_control reads _routed_backend and
        # move_cursor from the control module globals, so fake both
        # bindings (same fakes, both restored) — B16 pattern.
        orig_backend_ctrl = cursor_control_mod._routed_backend
        cursor_control_mod._routed_backend = lambda: None
        orig_move_ctrl = cursor_control_mod.move_cursor
        cursor_control_mod.move_cursor = lambda **kw: seen.update(kw) or "moved"
        try:
            result = cursor.cursor_control("move", x=x, y=y,
                                           coords="norm1000")
        finally:
            restore_info()
            restore_vb()
            cursor._routed_backend = orig_backend
            cursor.move_cursor = orig_move
            cursor_control_mod._routed_backend = orig_backend_ctrl
            cursor_control_mod.move_cursor = orig_move_ctrl
        return result, seen

    def test_f01_audit_reproduction_crop_500_500_is_1200_400(self):
        result, seen = self._run_move(CROP_INFO, SINGLE)
        self.assertEqual((seen.get("x"), seen.get("y")), (1200, 400))

    def test_f01_crop_wins_over_virtual_bounds(self):
        # Even on a dual-monitor virtual desktop, a crop maps
        # across the crop — the virtual bounds are irrelevant.
        result, seen = self._run_move(CROP_INFO, DUAL)
        self.assertEqual((seen.get("x"), seen.get("y")), (1200, 400))

    def test_f02_no_capture_dual_monitor_grid_shifts_left(self):
        result, seen = self._run_move(None, DUAL)
        # Virtual width 3840: round(0.5 * 3839) = 1920, so the
        # centre of the virtual desktop is screen x = 0 — NOT
        # the primary-only 960 the old code dispatched.
        self.assertEqual((seen.get("x"), seen.get("y")), (0, 540))

    def test_f02_single_monitor_unchanged(self):
        result, seen = self._run_move(None, SINGLE)
        self.assertEqual((seen.get("x"), seen.get("y")), (960, 540))

    def test_unknown_reference_reports_impossible(self):
        result, seen = self._run_move(None, None)
        self.assertIn("Could not determine the screen size", result)
        self.assertEqual(seen, {})

    def test_non_numeric_still_reports_numbers_message(self):
        result, seen = self._run_move(CROP_INFO, SINGLE,
                                      x="abc", y=500)
        self.assertIn("must be numbers in 0..1000", result)
        self.assertEqual(seen, {})

    def test_pixels_mode_untouched_by_contract(self):
        restore_info = _patch_vision_info(CROP_INFO)
        restore_vb = _patch_virtual(DUAL)
        orig_backend = cursor._routed_backend
        cursor._routed_backend = lambda: None
        orig_move = cursor.move_cursor
        seen = {}
        cursor.move_cursor = lambda **kw: seen.update(kw) or "moved"
        # B35 INTENTIONAL: same dual-binding fake as _run_move above.
        orig_backend_ctrl = cursor_control_mod._routed_backend
        cursor_control_mod._routed_backend = lambda: None
        orig_move_ctrl = cursor_control_mod.move_cursor
        cursor_control_mod.move_cursor = lambda **kw: seen.update(kw) or "moved"
        try:
            cursor.cursor_control("move", x=42, y=43, coords="pixels")
        finally:
            restore_info()
            restore_vb()
            cursor._routed_backend = orig_backend
            cursor.move_cursor = orig_move
            cursor_control_mod._routed_backend = orig_backend_ctrl
            cursor_control_mod.move_cursor = orig_move_ctrl
        self.assertEqual((seen.get("x"), seen.get("y")), (42, 43))


class FencePreservedTests(unittest.TestCase):
    def test_fence_still_clamps_into_virtual_bounds(self):
        self.assertEqual(cursor._fence_point(5000, 200, DUAL), (1919, 200))
        self.assertEqual(cursor._fence_point(-5000, 200, DUAL),
                         (-1920, 200))
        self.assertEqual(cursor._fence_point(-1000, 100, DUAL),
                         (-1000, 100))


class PilotPathTests(unittest.TestCase):
    def _run_pilot(self, info, bounds, size_fn_marker):
        restore_info = _patch_vision_info(info)
        restore_vb = _patch_virtual(bounds)
        seen = {}
        stubs = {"ole_drag_drop": lambda *a, **k: seen.update(
            {"args": a}) or "dropped"}
        try:
            if size_fn_marker == "injected":
                result = w4_wiring.cursor_pilot_action(
                    "ole_drag_drop", x=500, y=500, x2=500, y2=500,
                    coords="norm1000", _pilots=stubs,
                    _screen_size=lambda: (1920, 1080))
            else:
                result = w4_wiring.cursor_pilot_action(
                    "ole_drag_drop", x=500, y=500, x2=500, y2=500,
                    coords="norm1000", _pilots=stubs)
        finally:
            restore_info()
            restore_vb()
        return result, seen

    def test_pilot_production_path_uses_crop(self):
        result, seen = self._run_pilot(CROP_INFO, SINGLE, "none")
        self.assertEqual(seen["args"], (1200, 400, 1200, 400))

    def test_pilot_injected_size_keeps_old_grid(self):
        # The injected-size seam is test control: the caller owns
        # the grid, exactly as before.
        result, seen = self._run_pilot(CROP_INFO, SINGLE, "injected")
        self.assertEqual(seen["args"], (960, 540, 960, 540))

    def test_pilot_unknown_reference_reports_impossible(self):
        result, seen = self._run_pilot(None, None, "none")
        self.assertIn("Could not determine the screen size", result)
        self.assertEqual(seen, {})


class ScreenLookGeometryTests(unittest.TestCase):
    def _run_look(self, info, kind):
        from core.desktop import control as _control  # Phase 3 D4a INTENTIONAL: control hub moved to core/desktop/

        orig_capture = _vision.vision_manager.capture_screen
        orig_info = _vision.vision_manager._last_capture_info
        orig_deliver = _th._deliver_frame
        orig_ocr = _th._ocr_captured_frame
        orig_marks = _th._marks_suffix
        orig_title = _control.get_active_window
        # INTENTIONAL (skills split B49): _tool_screen_look moved to the
        # capture_launch mixin and resolves _deliver_frame /
        # _ocr_captured_frame / _marks_suffix from the mixin module's
        # globals, so the facade patches alone no longer steer it —
        # patch BOTH bindings (B16/B47 pattern), same fakes, both
        # restored.
        from core.funnel import tools_handlers_capture_launch as _th_m2
        orig_deliver_m2 = _th_m2._deliver_frame
        orig_ocr_m2 = _th_m2._ocr_captured_frame
        orig_marks_m2 = _th_m2._marks_suffix
        _vision.vision_manager.capture_screen = (
            lambda **kw: (b"fakejpeg", "image/jpeg"))
        _vision.vision_manager._last_capture_info = (
            dict(info) if info else None)
        _th._deliver_frame = lambda data, mime: (kind, None)
        _th._ocr_captured_frame = lambda data: "Hello world"
        _th._marks_suffix = lambda: ""
        _th_m2._deliver_frame = lambda data, mime: (kind, None)
        _th_m2._ocr_captured_frame = lambda data: "Hello world"
        _th_m2._marks_suffix = lambda: ""
        _control.get_active_window = lambda: "Fake Window"
        try:
            # GAPS-23 (F-V7): through the funnel — the stubs steer
            # the handler identically; the string lands in ["result"].
            return tools.execute("screen_look", {})["result"]
        finally:
            _vision.vision_manager.capture_screen = orig_capture
            _vision.vision_manager._last_capture_info = orig_info
            _th._deliver_frame = orig_deliver
            _th._ocr_captured_frame = orig_ocr
            _th._marks_suffix = orig_marks
            _th_m2._deliver_frame = orig_deliver_m2
            _th_m2._ocr_captured_frame = orig_ocr_m2
            _th_m2._marks_suffix = orig_marks_m2
            _control.get_active_window = orig_title
            # Test hygiene (the guest rule): _tool_screen_look
            # wraps OCR text via injection.wrap_untrusted, and
            # "screen text" is a soft-taint source — arrival alone
            # taints the session, which makes LATER suites' tool
            # dispatches demand confirmation and fail. Clear it
            # here so no taint leaks into the next suite in this
            # process (same clear_taint(force=True) other suites
            # use); without this, domain_routers + egress fail
            # when they run after this suite.
            try:
                from core import injection as _injection

                _injection.clear_taint(force=True)
            except Exception:  # noqa: BLE001 — hygiene only
                pass

    def test_text_path_carries_crop_geometry(self):
        result = self._run_look(CROP_INFO, "text")
        self.assertIn("The image shows the foreground window at "
                      "(800, 100), 800x600.", result)

    def test_vision_path_carries_crop_geometry(self):
        result = self._run_look(CROP_INFO, "vision")
        self.assertIn("The image shows the foreground window at "
                      "(800, 100), 800x600.", result)

    def test_no_geometry_on_record_still_returns_cleanly(self):
        result = self._run_look(None, "text")
        self.assertNotIn("Screen is", result)
        self.assertIn("Hello world", result)


if __name__ == "__main__":
    unittest.main()
