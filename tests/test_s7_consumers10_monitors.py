"""§7 consumers batch 10 — monitor wiring: resolve_for_action into
cursor dispatch + perception-mark monitor validation.

Lab honesty: every rule here is drivable headless with fake monitor
tables. Real Windows behaviour — live multi-monitor / mixed-DPI
clicks, real capture grids — stays field work and is not claimed
here. The norm1000 capture-freshness investigation stays open and
is not closed by this batch.

Every test restores what it patches (addCleanup) and resets the
handle store, so it cannot poison later suites in the same process.
"""

import copy
import unittest
from unittest import mock

from core.desktop import cursor  # Phase 3 D4d INTENTIONAL: cursor hub moved to core/desktop/
from core.desktop import cursor_monitor as cm  # INTENTIONAL (Phase 3 S7a-2): moved to core/desktop/
from core.desktop import monitors as mon  # INTENTIONAL (Phase 3 S7a): monitors moved to core/desktop/
from core.desktop import perception_adapter as pa  # INTENTIONAL (Phase 3 S7a): perception_adapter moved to core/desktop/
from core.desktop import target_handles as th  # INTENTIONAL (Phase 3 S7a): target_handles moved to core/desktop/
from core.desktop import uia  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
from core.desktop import uia_marks as uia_marks_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/


def _patch(test, target, new):
    patcher = mock.patch(target, new)
    patched = patcher.start()
    test.addCleanup(patcher.stop)
    return patched


class _FakeMonitorBackend:
    """Monitor-protocol backend returning fixed entries."""

    def __init__(self, entries):
        self._entries = entries
        self.calls = 0

    def enumerate(self):
        self.calls += 1
        return [dict(e) for e in self._entries]


class _SwitchingBackend:
    """Returns table A on the first call, table B after."""

    def __init__(self, first, second):
        self._first = first
        self._second = second
        self.calls = 0

    def enumerate(self):
        self.calls += 1
        return [dict(e) for e in (self._first if self.calls == 1
                                  else self._second)]


class _RaisingBackend:
    def enumerate(self):
        raise RuntimeError("no display API here")


def _two_monitor_table():
    """Primary 1920x1080 @0,0 scale 1.0; secondary 1920x1080
    @1920,0 scale 1.5. Virtual bounds 0,0..3839,1079."""
    return [
        {"monitor_id": "\\\\.\\DISPLAY1", "device": "\\\\.\\DISPLAY1",
         "left": 0, "top": 0, "width": 1920, "height": 1080,
         "primary": True, "scale_x": 1.0, "scale_y": 1.0},
        {"monitor_id": "\\\\.\\DISPLAY2", "device": "\\\\.\\DISPLAY2",
         "left": 1920, "top": 0, "width": 1920, "height": 1080,
         "primary": False, "scale_x": 1.5, "scale_y": 1.5},
    ]


def _staggered_table():
    """Secondary sits lower than primary, leaving a gap: points
    inside the virtual union (0,0..3199,1079) but above the
    secondary (y < 300, x >= 1920) are on NO monitor."""
    return [
        {"monitor_id": "\\\\.\\DISPLAY1", "device": "\\\\.\\DISPLAY1",
         "left": 0, "top": 0, "width": 1920, "height": 1080,
         "primary": True, "scale_x": 1.0, "scale_y": 1.0},
        {"monitor_id": "\\\\.\\DISPLAY2", "device": "\\\\.\\DISPLAY2",
         "left": 1920, "top": 300, "width": 1280, "height": 1024,
         "primary": False, "scale_x": 1.0, "scale_y": 1.0},
    ]


def _unreadable_table():
    table = _two_monitor_table()
    table[1]["scale_x"] = None
    table[1]["scale_y"] = None
    return table


# ---------------------------------------------------------------------------
# Engine seam: resolve_click_point
# ---------------------------------------------------------------------------


class ResolveClickPointTests(unittest.TestCase):
    def test_second_monitor_selection_virtual_mapping(self):
        # Virtual w=3840: nx=651 -> round(0.651*3839)=2499, on DISPLAY2.
        resolved, refusal = cm.resolve_click_point(
            "DISPLAY2", 651, 500, backend=_FakeMonitorBackend(
                _two_monitor_table()))
        self.assertEqual(refusal, "")
        self.assertEqual(resolved["monitor"]["monitor_id"],
                         "\\\\.\\DISPLAY2")
        self.assertEqual(resolved["point"], (2499, 540))

    def test_150_percent_monitor_is_not_double_scaled(self):
        # Per-monitor DPI awareness: the virtual frame IS physical
        # pixels, so the point equals the virtual mapping — a
        # scale-multiplied mapping would land at 2499*1.5.
        resolved, _ = cm.resolve_click_point(
            "DISPLAY2", 651, 500, backend=_FakeMonitorBackend(
                _two_monitor_table()))
        self.assertEqual(resolved["point"], (2499, 540))

    def test_bounds_violation_refuses_never_lands_elsewhere(self):
        # nx=100 -> pixel 384, which is on DISPLAY1, not DISPLAY2.
        resolved, refusal = cm.resolve_click_point(
            "DISPLAY2", 100, 500, backend=_FakeMonitorBackend(
                _two_monitor_table()))
        self.assertIsNone(resolved)
        self.assertIn("DISPLAY2", refusal)
        self.assertIn("(384, 540)", refusal)
        self.assertIn("1920,0 to 3839,1079", refusal)

    def test_unknown_reference_lists_monitors(self):
        resolved, refusal = cm.resolve_click_point(
            "third monitor", 500, 500, backend=_FakeMonitorBackend(
                _two_monitor_table()))
        self.assertIsNone(resolved)
        self.assertIn("Unknown monitor reference", refusal)
        self.assertIn("DISPLAY1", refusal)
        self.assertIn("DISPLAY2", refusal)

    def test_ambiguous_position_tie_refuses(self):
        tied = [
            {"monitor_id": "\\\\.\\DISPLAY1", "device": "\\\\.\\DISPLAY1",
             "left": 0, "top": 0, "width": 1920, "height": 1080,
             "primary": True, "scale_x": 1.0, "scale_y": 1.0},
            {"monitor_id": "\\\\.\\DISPLAY2", "device": "\\\\.\\DISPLAY2",
             "left": 0, "top": 1080, "width": 1920, "height": 1080,
             "primary": False, "scale_x": 1.0, "scale_y": 1.0},
        ]
        resolved, refusal = cm.resolve_click_point(
            "left", 500, 500, backend=_FakeMonitorBackend(tied))
        self.assertIsNone(resolved)
        self.assertIn("more than one monitor", refusal)

    def test_unreadable_scale_refuses_no_fallback(self):
        resolved, refusal = cm.resolve_click_point(
            "DISPLAY2", 651, 500, backend=_FakeMonitorBackend(
                _unreadable_table()))
        self.assertIsNone(resolved)
        self.assertIn("DISPLAY2", refusal)
        self.assertIn("unreadable scale", refusal)

    def test_fresh_table_per_call_no_caching(self):
        moved = _two_monitor_table()
        moved[1]["left"] = 0
        moved[1]["top"] = 1080
        moved[0]["left"] = 0
        backend = _SwitchingBackend(_two_monitor_table(), moved)
        first, _ = cm.resolve_click_point(
            "DISPLAY2", 651, 500, backend=backend)
        self.assertEqual(first["monitor"]["monitor_id"],
                         "\\\\.\\DISPLAY2")
        self.assertEqual(first["point"], (2499, 540))
        # Second call reads the MOVED table: virtual bounds are now
        # 0,0..1919,2159, so the same norm1000 pair lands at a
        # different point (1249, 2159) — still on DISPLAY2, but
        # only because the fresh table was honoured. A cached
        # table would have repeated (2499, 540).
        second, refusal = cm.resolve_click_point(
            "DISPLAY2", 651, 1000, backend=backend)
        self.assertEqual(backend.calls, 2)
        self.assertEqual(refusal, "")
        self.assertEqual(second["monitor"]["monitor_id"],
                         "\\\\.\\DISPLAY2")
        self.assertEqual(second["point"], (1249, 2159))
        self.assertNotEqual(second["point"], first["point"])

    def test_norm1000_edge_clamp_preserved(self):
        # nx=1500 clamps to the grid edge (1000) -> x=3839, the
        # virtual right edge, still on DISPLAY2.
        resolved, refusal = cm.resolve_click_point(
            "DISPLAY2", 1500, 500, backend=_FakeMonitorBackend(
                _two_monitor_table()))
        self.assertEqual(refusal, "")
        self.assertEqual(resolved["point"], (3839, 540))

    def test_logical_physical_round_trip_150_percent(self):
        monitor = _two_monitor_table()[1]
        point, refusal = mon.logical_to_physical(1920 + 640, 540, monitor)
        self.assertEqual(refusal, "")
        # Logical width is 2/3 of physical, and both axes scale:
        # 640 logical -> 960 physical on x, 540 logical -> 810 on y.
        self.assertEqual(point, (2880, 810))
        back, refusal = mon.physical_to_logical(2880, 810, monitor)
        self.assertEqual(refusal, "")
        self.assertEqual(back, (2560, 540))


# ---------------------------------------------------------------------------
# validate_point / validate_pixel_on_monitor
# ---------------------------------------------------------------------------


class ValidatePointTests(unittest.TestCase):
    def test_in_monitor_ok_with_identity(self):
        monitor, status, note = cm.validate_point(
            2500, 500, backend=_FakeMonitorBackend(_two_monitor_table()))
        self.assertEqual(status, "ok")
        self.assertEqual(note, "")
        self.assertEqual(monitor["monitor_id"], "\\\\.\\DISPLAY2")

    def test_gap_point_refused_not_moved(self):
        # (2500, 50) is inside the virtual union but above the
        # staggered secondary: on NO monitor.
        monitor, status, note = cm.validate_point(
            2500, 50, backend=_FakeMonitorBackend(_staggered_table()))
        self.assertIsNone(monitor)
        self.assertEqual(status, "refused")
        self.assertIn("(2500, 50)", note)
        self.assertIn("not on any monitor", note)

    def test_unreadable_scale_refused(self):
        monitor, status, note = cm.validate_point(
            2500, 500, backend=_FakeMonitorBackend(_unreadable_table()))
        self.assertIsNone(monitor)
        self.assertEqual(status, "refused")
        self.assertIn("unreadable scale", note)

    def test_unreadable_table_is_unvalidated_not_refused(self):
        monitor, status, note = cm.validate_point(
            100, 100, backend=_RaisingBackend())
        self.assertIsNone(monitor)
        self.assertEqual(status, "unvalidated")
        self.assertIn("Could not read the monitor list", note)

    def test_pixel_on_named_monitor_ok(self):
        ok, refusal = cm.validate_pixel_on_monitor(
            "DISPLAY2", 2500, 500, backend=_FakeMonitorBackend(
                _two_monitor_table()))
        self.assertTrue(ok)
        self.assertEqual(refusal, "")

    def test_pixel_on_other_monitor_refused(self):
        ok, refusal = cm.validate_pixel_on_monitor(
            "DISPLAY2", 100, 500, backend=_FakeMonitorBackend(
                _two_monitor_table()))
        self.assertFalse(ok)
        self.assertIn("not on monitor", refusal)
        self.assertIn("DISPLAY2", refusal)

    def test_pixel_unknown_monitor_and_nonnumeric_refused(self):
        ok, refusal = cm.validate_pixel_on_monitor(
            "DISPLAY9", 100, 100, backend=_FakeMonitorBackend(
                _two_monitor_table()))
        self.assertFalse(ok)
        self.assertIn("Unknown monitor reference", refusal)
        ok, refusal = cm.validate_pixel_on_monitor(
            "DISPLAY1", "x", 100, backend=_FakeMonitorBackend(
                _two_monitor_table()))
        self.assertFalse(ok)
        self.assertIn("whole numbers", refusal)


# ---------------------------------------------------------------------------
# validate_marks (perception layer)
# ---------------------------------------------------------------------------


class ValidateMarksTests(unittest.TestCase):
    def test_marks_annotated_with_monitor_identity(self):
        snapshot = {"marks": [
            {"id": 1, "name": "Save", "rect": (2000, 100, 2100, 140)},
            {"id": 2, "name": "Open", "rect": (100, 100, 200, 140)},
        ]}
        result = pa.validate_marks(
            snapshot, monitor_backend=_FakeMonitorBackend(
                _two_monitor_table()))
        self.assertEqual(result["table_note"], "")
        self.assertEqual(result["flagged"], [])
        by_id = {m["id"]: m for m in result["valid"]}
        self.assertEqual(by_id[1]["monitor_id"], "\\\\.\\DISPLAY2")
        self.assertEqual(by_id[2]["monitor_id"], "\\\\.\\DISPLAY1")

    def test_gap_no_rect_and_nondict_flagged(self):
        snapshot = {"marks": [
            {"id": 1, "name": "Gap", "rect": (2450, 10, 2550, 90)},
            {"id": 2, "name": "NoRect"},
            "not-a-mark",
        ]}
        result = pa.validate_marks(
            snapshot, monitor_backend=_FakeMonitorBackend(
                _staggered_table()))
        self.assertEqual(result["valid"], [])
        self.assertEqual(len(result["flagged"]), 3)
        reasons = [f["reason"] for f in result["flagged"]]
        self.assertTrue(any("not on any monitor" in r for r in reasons))
        self.assertTrue(any("no usable rectangle" in r for r in reasons))
        self.assertTrue(any("not a mark record" in r for r in reasons))

    def test_unreadable_table_marks_unvalidated_input_untouched(self):
        snapshot = {"marks": [
            {"id": 1, "name": "Save", "rect": (2000, 100, 2100, 140)},
        ]}
        before = copy.deepcopy(snapshot)
        result = pa.validate_marks(snapshot, monitor_backend=_RaisingBackend())
        self.assertEqual(snapshot, before)
        self.assertEqual(len(result["valid"]), 1)
        self.assertNotIn("monitor_id", result["valid"][0])
        self.assertEqual(result["flagged"], [])
        self.assertIn("Could not read the monitor list",
                      result["table_note"])

    def test_snapshot_without_marks_validates_empty(self):
        result = pa.validate_marks(
            {}, monitor_backend=_FakeMonitorBackend(_two_monitor_table()))
        self.assertEqual(result["valid"], [])
        self.assertEqual(result["flagged"], [])


# ---------------------------------------------------------------------------
# click_by_mark monitor gate
# ---------------------------------------------------------------------------


class ClickByMarkMonitorTests(unittest.TestCase):
    def setUp(self):
        th.reset_store()
        self._saved = (uia_marks_mod._last_marks,
                       uia_marks_mod._last_marks_fg,
                       uia_marks_mod._last_marks_hwnd)
        self.addCleanup(uia.clear_last_marks)
        self.addCleanup(
            lambda: setattr(uia_marks_mod, "_last_marks",
                            self._saved[0]) or
            setattr(uia_marks_mod, "_last_marks_fg",
                    self._saved[1]) or
            setattr(uia_marks_mod, "_last_marks_hwnd",
                    self._saved[2]))

    def tearDown(self):
        th.reset_store()

    def _patch_click(self, virtual_bounds):
        _patch(self, "core.desktop.uia._failsafe_block", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._safe_snapshot", lambda: {})  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.probe_suffix", lambda *a, **k: "")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.foreground_title", lambda: "App")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.foreground_hwnd", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.cursor._virtual_bounds",
               lambda: virtual_bounds)  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        calls = []
        _patch(self, "core.desktop.cursor.click_cursor",
               lambda **k: calls.append(k) or
               "Clicked (left button) — landed at (60, 40).")  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        return calls

    def _stamp(self, rect, title="App", hwnd=None):
        uia._set_last_marks(
            [{"id": 1, "name": "Save", "control_type": "Button",
              "rect": rect}], title, hwnd)

    def test_gap_centre_refuses_without_dispatch(self):
        # Centre (2500, 50): pinned virtual-bounds check passes
        # (virtual union 0,0..3199,1079), monitor gate refuses.
        self._stamp((2450, 10, 2550, 90))
        calls = self._patch_click((0, 0, 3199, 1079))
        result = uia.click_by_mark(
            1, monitor_backend=_FakeMonitorBackend(_staggered_table()))
        self.assertIn("Not clicking mark 1", result)
        self.assertIn("not on any monitor", result)
        self.assertNotIn("Clicked mark", result)
        self.assertEqual(calls, [])

    def test_on_monitor_centre_proceeds_to_dispatch(self):
        self._stamp((10, 20, 110, 60))
        calls = self._patch_click((0, 0, 3839, 1079))
        result = uia.click_by_mark(
            1, monitor_backend=_FakeMonitorBackend(_two_monitor_table()))
        self.assertIn("Clicked mark 1", result)
        self.assertIn("[target handle th-mark-", result)
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["x"], calls[0]["y"]), (60, 40))

    def test_unreadable_scale_centre_refuses(self):
        self._stamp((2450, 450, 2550, 550))
        calls = self._patch_click((0, 0, 3839, 1079))
        result = uia.click_by_mark(
            1, monitor_backend=_FakeMonitorBackend(_unreadable_table()))
        self.assertIn("Not clicking mark 1", result)
        self.assertIn("unreadable scale", result)
        self.assertEqual(calls, [])

    def test_unreadable_table_skips_gate(self):
        self._stamp((10, 20, 110, 60))
        calls = self._patch_click((0, 0, 3839, 1079))
        result = uia.click_by_mark(1, monitor_backend=_RaisingBackend())
        self.assertIn("Clicked mark 1", result)
        self.assertEqual(len(calls), 1)

    def test_stale_precedence_preserved(self):
        self._stamp((10, 20, 110, 60), title="Notes", hwnd=111)
        self._patch_click((0, 0, 3839, 1079))
        _patch(self, "core.desktop.uia.foreground_hwnd", lambda: 222)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.foreground_title", lambda: "Notes")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        result = uia.click_by_mark(
            1, monitor_backend=_FakeMonitorBackend(_two_monitor_table()))
        self.assertIn("stale", result)
        self.assertNotIn("Clicked mark", result)
        self.assertNotIn("not on any monitor", result)


# ---------------------------------------------------------------------------
# cursor_control monitor branch
# ---------------------------------------------------------------------------


class _RecordingBackend:
    def __init__(self):
        self.calls = []

    def cursor_control(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "stubbed dispatch"


class CursorControlMonitorTests(unittest.TestCase):
    def setUp(self):
        self.backend = _RecordingBackend()
        _patch(self, "core.desktop.cursor._routed_backend",
               lambda: self.backend)  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        # B35 INTENTIONAL: cursor_control reads _routed_backend from
        # the control module globals, so fake both bindings (same
        # fake, both restored by addCleanup) — B16 pattern.
        _patch(self, "core.desktop.cursor_control._routed_backend",
               lambda: self.backend)
        self._orig_read_table = mon.read_table
        self.addCleanup(setattr, mon, "read_table", self._orig_read_table)

    def _set_table(self, table):
        mon.read_table = lambda backend=None: (  # noqa: ARG005
            mon.canonical_sort([dict(e) for e in table]), "")

    def test_norm1000_resolves_to_pixels_before_backend(self):
        self._set_table(_two_monitor_table())
        result = cursor.cursor_control(
            action="click", x=651, y=500, coords="norm1000",
            monitor="DISPLAY2")
        self.assertEqual(result, "stubbed dispatch")
        self.assertEqual(len(self.backend.calls), 1)
        kwargs = self.backend.calls[0][1]
        self.assertEqual((kwargs["x"], kwargs["y"]), (2499, 540))
        self.assertEqual(kwargs["coords"], "pixels")

    def test_pixels_validated_on_named_monitor(self):
        self._set_table(_two_monitor_table())
        result = cursor.cursor_control(
            action="move", x=2500, y=500, coords="pixels",
            monitor="DISPLAY2")
        self.assertEqual(result, "stubbed dispatch")
        kwargs = self.backend.calls[0][1]
        self.assertEqual((kwargs["x"], kwargs["y"]), (2500, 500))

    def test_no_monitor_passes_through_unchanged(self):
        self._set_table(_two_monitor_table())
        result = cursor.cursor_control(
            action="click", x=651, y=500, coords="norm1000")
        self.assertEqual(result, "stubbed dispatch")
        kwargs = self.backend.calls[0][1]
        self.assertEqual((kwargs["x"], kwargs["y"]), (651, 500))
        self.assertEqual(kwargs["coords"], "norm1000")

    def test_refusals_never_dispatch(self):
        self._set_table(_two_monitor_table())
        cases = [
            # (kwargs, expected substring)
            ({"action": "click", "x": 651, "y": 500,
              "coords": "norm1000", "monitor": "DISPLAY9"},
             "Unknown monitor reference"),
            ({"action": "click", "x": 100, "y": 500,
              "coords": "norm1000", "monitor": "DISPLAY2"},
             "not on monitor"),
            ({"action": "position", "monitor": "DISPLAY1"},
             "applies only to absolute-target"),
            ({"action": "click", "x": 651, "coords": "norm1000",
              "monitor": "DISPLAY2"},
             "needs both x and y"),
            ({"action": "click", "x": 651, "y": 500, "fx": 0.5,
              "fy": 0.5, "coords": "norm1000", "monitor": "DISPLAY2"},
             "Ambiguous target"),
            ({"action": "click", "x": "a", "y": 500,
              "coords": "norm1000", "monitor": "DISPLAY2"},
             "as numbers"),
            ({"action": "move_by", "x": 10, "y": 10,
              "monitor": "DISPLAY1"},
             "applies only to absolute-target"),
        ]
        for kwargs, substring in cases:
            with self.subTest(kwargs=kwargs):
                result = cursor.cursor_control(**kwargs)
                self.assertIn(substring, result)
        self.assertEqual(self.backend.calls, [])

    def test_unreadable_scale_refuses_without_dispatch(self):
        self._set_table(_unreadable_table())
        result = cursor.cursor_control(
            action="click", x=651, y=500, coords="norm1000",
            monitor="DISPLAY2")
        self.assertIn("unreadable scale", result)
        self.assertEqual(self.backend.calls, [])

    def test_pixels_on_other_monitor_refused(self):
        self._set_table(_two_monitor_table())
        result = cursor.cursor_control(
            action="move", x=100, y=500, coords="pixels",
            monitor="DISPLAY2")
        self.assertIn("not on monitor", result)
        self.assertEqual(self.backend.calls, [])


if __name__ == "__main__":
    unittest.main()
