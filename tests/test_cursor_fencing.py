"""Shipped unit tests (v5.45, P-1): cursor fencing.

Every dispatched cursor target must be clamped into the REAL
monitor bounds (the virtual screen — secondary monitors can live
at negative coordinates). The monitor geometry is faked through
the module's existing seams: _fence_point's explicit `bounds`
argument, _clamp_to_screen's `size` argument, and a monkeypatched
_virtual_bounds for the implicit path. No display is touched.

Stdlib unittest only. Run via tests/run_tests.py.
"""

from __future__ import annotations

import unittest

try:
    from core.desktop import cursor  # Phase 3 D4d INTENTIONAL: cursor hub moved to core/desktop/
    from core.desktop import cursor_geometry  # B34 INTENTIONAL: geometry-freshness family moved here (part 1 of 2); _fence_point reads _virtual_bounds through its own module namespace, so geometry fakes land in both bindings below
except Exception as exc:  # noqa: BLE001
    raise unittest.SkipTest(f"core.desktop.cursor unavailable: {exc}")

# A dual-monitor virtual screen: primary 1920x1080 at origin, a
# secondary 1920x1080 parked to its LEFT (negative x).
DUAL = (-1920, 0, 1919, 1079)
SINGLE = (0, 0, 1919, 1079)


class FencePointTests(unittest.TestCase):
    def test_inside_point_unchanged(self):
        self.assertEqual(cursor._fence_point(100, 200, DUAL), (100, 200))
        self.assertEqual(cursor._fence_point(-1500, 500, DUAL), (-1500, 500))

    def test_outside_point_clamped_to_nearest_edge(self):
        self.assertEqual(cursor._fence_point(5000, 200, DUAL), (1919, 200))
        self.assertEqual(cursor._fence_point(-5000, 200, DUAL), (-1920, 200))
        self.assertEqual(cursor._fence_point(0, 9999, DUAL), (0, 1079))
        self.assertEqual(cursor._fence_point(0, -50, DUAL), (0, 0))

    def test_negative_origin_monitor_is_reachable(self):
        # A naive 0..width-1 clamp would fence the left monitor out
        # entirely; the virtual-bounds fence must not.
        x, _y = cursor._fence_point(-1000, 100, DUAL)
        self.assertEqual(x, -1000)

    def test_single_monitor_bounds(self):
        self.assertEqual(cursor._fence_point(-5, -5, SINGLE), (0, 0))
        self.assertEqual(cursor._fence_point(4000, 4000, SINGLE),
                         (1919, 1079))

    def test_non_numeric_passes_through_for_caller_validation(self):
        self.assertEqual(cursor._fence_point("abc", None, DUAL),
                         ("abc", None))

    def test_implicit_bounds_come_from_virtual_bounds_seam(self):
        # B34 INTENTIONAL: _fence_point now resolves _virtual_bounds
        # from the geometry module's globals — fake both bindings
        # (same fake, both restored), B16 pattern, same assertions.
        orig = cursor._virtual_bounds
        orig_geo = cursor_geometry._virtual_bounds
        cursor._virtual_bounds = lambda: DUAL
        cursor_geometry._virtual_bounds = lambda: DUAL
        try:
            self.assertEqual(cursor._fence_point(99999, 50), (1919, 50))
            self.assertEqual(cursor._fence_point(-99999, 50), (-1920, 50))
        finally:
            cursor._virtual_bounds = orig
            cursor_geometry._virtual_bounds = orig_geo

    def test_unknown_bounds_pass_point_through(self):
        # B34 INTENTIONAL: same dual-binding fake as above.
        orig = cursor._virtual_bounds
        orig_geo = cursor_geometry._virtual_bounds
        cursor._virtual_bounds = lambda: None
        cursor_geometry._virtual_bounds = lambda: None
        try:
            self.assertEqual(cursor._fence_point(123, 456), (123, 456))
        finally:
            cursor._virtual_bounds = orig
            cursor_geometry._virtual_bounds = orig_geo


class ClampAndConvertTests(unittest.TestCase):
    def test_clamp_to_screen_with_fake_size(self):
        self.assertEqual(cursor._clamp_to_screen(2000, -3, (1920, 1080)),
                         (1919, 0))
        self.assertEqual(cursor._clamp_to_screen(10, 10, (1920, 1080)),
                         (10, 10))
        self.assertEqual(cursor._clamp_to_screen(10, 10, None), (10, 10))

    def test_fraction_and_norm1000_conversions_agree(self):
        self.assertEqual(cursor._to_pixels(0.0, 0.0, 1920, 1080), (0, 0))
        self.assertEqual(cursor._to_pixels(1.0, 1.0, 1920, 1080),
                         (1919, 1079))
        self.assertEqual(cursor._norm1000_to_pixels(500, 500, 1920, 1080),
                         cursor._to_pixels(0.5, 0.5, 1920, 1080))
        # Out-of-range norm1000 clamps to the edge, never beyond.
        self.assertEqual(cursor._norm1000_to_pixels(1500, -20, 1920, 1080),
                         (1919, 0))


if __name__ == "__main__":
    unittest.main()
