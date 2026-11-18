"""Tests for §7 item 6 — core.desktop.monitors (mock monitor tables only).

Live multi-monitor + mixed-DPI behaviour on real Windows stays
field work: this lab has no such setup, and no test here claims
it. Every test drives the backend protocol with a fake backend
or an explicit table.
"""

import unittest

from core.desktop import monitors  # INTENTIONAL (Phase 3 S7a): monitors moved to core/desktop/
from core.desktop.monitors import (  # INTENTIONAL (Phase 3 S7a): monitors moved to core/desktop/
    canonical_sort,
    describe_table,
    logical_to_physical,
    physical_to_logical,
    place_rect_on_monitor,
    read_table,
    re_resolve,
    receipt_line,
    resolve_for_action,
    resolve_monitor,
    norm1000_to_physical,
    validate_on_monitor,
    virtual_bounds,
)
from core.platform.windows import _norm1000_to_pixels


def _d1(**over):
    entry = {
        "monitor_id": "\\\\.\\DISPLAY1",
        "device": "\\\\.\\DISPLAY1",
        "left": 0,
        "top": 0,
        "width": 1920,
        "height": 1080,
        "work_left": 0,
        "work_top": 0,
        "work_right": 1919,
        "work_bottom": 1079,
        "primary": True,
        "scale_x": 1.0,
        "scale_y": 1.0,
    }
    entry.update(over)
    return entry


def _d2(**over):
    entry = {
        "monitor_id": "\\\\.\\DISPLAY2",
        "device": "\\\\.\\DISPLAY2",
        "left": 1920,
        "top": 0,
        "width": 2560,
        "height": 1440,
        "work_left": 1920,
        "work_top": 0,
        "work_right": 4479,
        "work_bottom": 1439,
        "primary": False,
        "scale_x": 1.5,
        "scale_y": 1.5,
    }
    entry.update(over)
    return entry


def _table_a():
    # Primary 100% at 0,0; secondary 150% to its right.
    return canonical_sort([_d2(), _d1()])


class FakeBackend:
    """Backend protocol: enumerate() returns the current table."""

    def __init__(self, table):
        self.table = table

    def enumerate(self):
        return self.table


class TestConversion(unittest.TestCase):
    def test_per_monitor_conversion(self):
        table = _table_a()
        vb = virtual_bounds(table)
        self.assertEqual(vb, (0, 0, 4479, 1439))
        d1, d2 = table[0], table[1]
        point, refusal = norm1000_to_physical(250, 500, d1, vb)
        self.assertEqual(refusal, "")
        self.assertEqual(point, (1120, 720))
        point, refusal = norm1000_to_physical(750, 500, d2, vb)
        self.assertEqual(refusal, "")
        self.assertEqual(point, (3359, 720))

    def test_mapping_matches_platform_helper(self):
        table = _table_a()
        vb = virtual_bounds(table)
        for nx, ny in ((0, 0), (250, 500), (500, 500), (1000, 1000)):
            expected = _norm1000_to_pixels(nx, ny, 4480, 1440)
            point, _ = norm1000_to_physical(nx, ny, table[0], vb) \
                if nx <= 428 else (None, None)
            # The platform helper maps the same grid; compare the
            # raw grid point, not the monitor-validated one.
            grid = monitors._norm1000_point(nx, ny, vb)
            self.assertEqual(grid, expected)

    def test_logical_scale_arithmetic(self):
        d2 = _table_a()[1]
        point, refusal = logical_to_physical(1920 + 640, 360, d2)
        self.assertEqual(refusal, "")
        self.assertEqual(point, (1920 + 960, 540))
        back, refusal = physical_to_logical(point[0], point[1], d2)
        self.assertEqual(refusal, "")
        self.assertEqual(back, (1920 + 640, 360))
        d1 = _table_a()[0]
        point, _ = logical_to_physical(100, 200, d1)
        self.assertEqual(point, (100, 200))  # 100% is the identity

    def test_validate_on_monitor(self):
        d1 = _table_a()[0]
        ok, _ = validate_on_monitor(0, 0, d1)
        self.assertTrue(ok)
        ok, _ = validate_on_monitor(1919, 1079, d1)
        self.assertTrue(ok)
        ok, refusal = validate_on_monitor(1920, 0, d1)
        self.assertFalse(ok)
        self.assertIn("not on monitor", refusal)


class TestResolution(unittest.TestCase):
    def test_spoken_index_shorthand_confirmed_against_table(self):
        table = _table_a()
        for ref in ("2", "monitor 2", "screen 2", "second monitor"):
            m, refusal = resolve_monitor(table, ref)
            self.assertEqual(refusal, "", ref)
            self.assertEqual(m["monitor_id"], "\\\\.\\DISPLAY2", ref)
        m, _ = resolve_monitor(table, "first monitor")
        self.assertEqual(m["monitor_id"], "\\\\.\\DISPLAY1")

    def test_device_name_is_identity(self):
        table = _table_a()
        for ref in ("\\\\.\\DISPLAY2", "DISPLAY2", "display2"):
            m, refusal = resolve_monitor(table, ref)
            self.assertEqual(refusal, "", ref)
            self.assertEqual(m["monitor_id"], "\\\\.\\DISPLAY2", ref)

    def test_position_words(self):
        table = _table_a()
        cases = {
            "primary": "\\\\.\\DISPLAY1",
            "main monitor": "\\\\.\\DISPLAY1",
            "left": "\\\\.\\DISPLAY1",
            "right": "\\\\.\\DISPLAY2",
        }
        for ref, want in cases.items():
            m, refusal = resolve_monitor(table, ref)
            self.assertEqual(refusal, "", ref)
            self.assertEqual(m["monitor_id"], want, ref)

    def test_unknown_reference_lists_current_monitors(self):
        table = _table_a()
        m, refusal = resolve_monitor(table, "monitor 9")
        self.assertIsNone(m)
        self.assertIn("DISPLAY1", refusal)
        self.assertIn("DISPLAY2", refusal)
        self.assertIn("150%", refusal)
        self.assertIn("1920,0", refusal)

    def test_ambiguous_position_tie_refuses(self):
        stacked = canonical_sort([
            _d1(),
            _d2(left=0, top=1080, width=1920, height=1080,
                work_left=0, work_top=1080, work_right=1919,
                work_bottom=2159, primary=False),
        ])
        m, refusal = resolve_monitor(stacked, "left")
        self.assertIsNone(m)
        self.assertIn("more than one", refusal)

    def test_relative_words(self):
        table = _table_a()
        d1, d2 = table[0], table[1]
        m, _ = resolve_monitor(table, "next", current=d1)
        self.assertEqual(m["monitor_id"], "\\\\.\\DISPLAY2")
        m, _ = resolve_monitor(table, "next", current=d2)
        self.assertEqual(m["monitor_id"], "\\\\.\\DISPLAY1")
        m, _ = resolve_monitor(table, "other screen", current=d1)
        self.assertEqual(m["monitor_id"], "\\\\.\\DISPLAY2")
        m, refusal = resolve_monitor(table, "next")
        self.assertIsNone(m)
        self.assertIn("which screen you are on", refusal)
        three = canonical_sort([_d1(), _d2(),
                                _d2(monitor_id="\\\\.\\DISPLAY3",
                                    device="\\\\.\\DISPLAY3",
                                    left=4480, primary=False)])
        m, refusal = resolve_monitor(three, "other screen", current=d1)
        self.assertIsNone(m)
        self.assertIn("ambiguous", refusal)

    def test_empty_table_refuses(self):
        m, refusal = resolve_monitor([], "2")
        self.assertIsNone(m)
        self.assertIn("No monitors", refusal)


class TestReorder(unittest.TestCase):
    def test_identity_survives_reorder_shorthand_follows_table(self):
        # Before: D1 primary at 0, D2 at 1920, D3 at 4480.
        before = canonical_sort([_d1(), _d2(),
                                 _d2(monitor_id="\\\\.\\DISPLAY3",
                                     device="\\\\.\\DISPLAY3",
                                     left=4480, primary=False)])
        m, _ = resolve_monitor(before, "monitor 2")
        self.assertEqual(m["monitor_id"], "\\\\.\\DISPLAY2")
        # After the reorder: D3 moved left of D2.
        after = canonical_sort([_d1(),
                                _d2(left=4480, work_left=4480,
                                    work_right=7039, primary=False),
                                _d2(monitor_id="\\\\.\\DISPLAY3",
                                    device="\\\\.\\DISPLAY3",
                                    left=1920, work_left=1920,
                                    work_right=4479, primary=False)])
        m, _ = resolve_monitor(after, "monitor 2")
        self.assertEqual(m["monitor_id"], "\\\\.\\DISPLAY3")
        # Identity still finds the same device, at its new bounds.
        m, refusal = re_resolve("\\\\.\\DISPLAY2", FakeBackend(after))
        self.assertEqual(refusal, "")
        self.assertEqual(m["left"], 4480)

    def test_unresolvable_identity_refuses_and_reasks(self):
        backend = FakeBackend(_table_a())
        m, refusal = re_resolve("\\\\.\\DISPLAY9", backend)
        self.assertIsNone(m)
        self.assertIn("no longer present", refusal)
        self.assertIn("DISPLAY1", refusal)


class TestRefusals(unittest.TestCase):
    def test_unreadable_scale_refuses_and_stays_listed(self):
        table = _table_a()
        blind = _d2(scale_x=None, scale_y=None)
        table = canonical_sort([table[0], blind])
        vb = virtual_bounds(table)
        point, refusal = norm1000_to_physical(750, 500, blind, vb)
        self.assertIsNone(point)
        self.assertIn("unreadable scale", refusal)
        self.assertIn("unreadable", describe_table(table))
        self.assertIn("DISPLAY2", describe_table(table))
        result, refusal = resolve_for_action(
            "second monitor", 750, 500, backend=FakeBackend(table))
        self.assertIsNone(result)
        self.assertIn("unreadable scale", refusal)

    def test_bounds_violation_refuses_never_clamps(self):
        table = _table_a()
        vb = virtual_bounds(table)
        d1 = table[0]
        # (750, 500) maps to (3359, 720): on D2, not on D1.
        point, refusal = norm1000_to_physical(750, 500, d1, vb)
        self.assertIsNone(point)
        self.assertIn("not on monitor", refusal)
        self.assertIn("3359", refusal)  # the true point, named, not moved
        self.assertIn("never moved onto another monitor", refusal)

    def test_resolve_for_action_reads_fresh_every_call(self):
        backend = FakeBackend(_table_a())
        result, refusal = resolve_for_action(
            "second monitor", 750, 500, backend=backend)
        self.assertEqual(refusal, "")
        self.assertEqual(result["point"], (3359, 720))
        # The arrangement changes between calls: D2 unplugged.
        backend.table = canonical_sort([_d1()])
        result, refusal = resolve_for_action(
            "second monitor", 750, 500, backend=backend)
        self.assertIsNone(result)
        self.assertIn("Unknown monitor", refusal)

    def test_live_reader_degrades_honestly_off_windows(self):
        table, note = read_table()
        import sys
        if not sys.platform.startswith("win"):
            self.assertEqual(table, [])
            self.assertIn("Windows", note)


class TestPlacementAndReceipt(unittest.TestCase):
    def test_place_rect_preserves_relative_place(self):
        table = _table_a()
        rect, refusal = place_rect_on_monitor(
            (100, 100, 500, 400), table[0], table[1])
        self.assertEqual(refusal, "")
        self.assertEqual(rect, (2025, 96, 2559, 496))
        self.assertTrue(rect[0] >= table[1]["work_left"])
        self.assertTrue(rect[2] <= table[1]["work_right"])

    def test_place_rect_refuses_on_unreadable_scale(self):
        table = _table_a()
        blind = _d2(scale_x=None, scale_y=None)
        rect, refusal = place_rect_on_monitor(
            (100, 100, 500, 400), table[0], blind)
        self.assertIsNone(rect)
        self.assertIn("unreadable scale", refusal)

    def test_receipt_names_identity_and_point(self):
        table = _table_a()
        line = receipt_line(table[1], (3359, 720))
        self.assertIn("\\\\.\\DISPLAY2", line)
        self.assertIn("(3359, 720)", line)
        self.assertIn("150%", line)


if __name__ == "__main__":
    unittest.main()
