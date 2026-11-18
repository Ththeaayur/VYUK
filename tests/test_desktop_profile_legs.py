"""Shipped unit tests (desktop programme, desktop-profile legs): test
legs for get_window_controls, window_snap, and tile_windows — the
three desktop functions the desktop programme left untested.

Contracts, as read from the engine code (not invented):

- get_window_controls (core/desktop/control_plus.py): plain str
  result. Non-Windows and missing-pywinauto fallbacks; connect
  failures name the window (or "the window" when the connect
  helper raises before any description exists); descendants
  failures are reported; the walk counts EVERY named control
  before the 60-line display cap, stops examining at a hard
  budget of 2000 elements and says so, skips unnamed and dead
  elements, and truncates the whole report at 1500 chars.
- window_snap (core/skills_window_control.py): position is stripped,
  lowercased, and "maximize" is accepted for "maximise"; unknown
  positions are refused with the position list BEFORE any
  Windows check. On Windows it restores a maximised/minimised
  window before moving, verifies the read-back rect within 2px,
  and reports refusal or drift honestly instead of claiming
  success.
- tile_windows (core/skills_desktop_tiling.py): layout is stripped and
  lowercased (None means "grid", unlike snap where None is
  unknown); unknown layouts are refused with the layout list
  BEFORE any Windows check. It tiles at most the first 12
  windows, reports placed-vs-total honestly, lists every refused
  or drifted window, and notes restored-minimised windows and
  the over-cap remainder. Cell geometry is pure (_cells /
  _grid_dims) with a 4px gap inset and an 8px read-back
  tolerance.

What is faked here: Windows itself. The lab has no Windows, no
UIA, and no window manager, so every Windows-path leg patches
the module's own seams (patched _is_windows, _uia_window,
_user32, _foreground_hwnd, _title_of, _monitors,
_monitor_for_window, _window_rect, _open_windows, _work_area,
_place_window, plus a sys.modules pywinauto stand-in) — the
same seam-patching discipline as the other desktop suites.
What these legs prove is the functions' decision logic,
geometry, counting, caps, and honest-failure wording against
contract-faithful fakes. What they do NOT prove is real
Windows behaviour: real UIA tree walks, real SetWindowPos /
GetWindowRect round-trips, real monitor enumeration, and real
DPI/taskbar geometry stay field work on real Windows (the
D-10 matrix is the programme's release gate).

Stdlib unittest only. Run via tests/run_tests.py.
"""

from __future__ import annotations

import sys
import types
import unittest

try:
    from core.desktop import control_plus
    from core import skills_desktop_tiling  # Phase C C8 INTENTIONAL: desktop4 renamed to desktop_tiling
    from core import skills_window_control  # Phase C C6 INTENTIONAL: hands3 renamed to window_control
    # SKILLS SPLIT PHASE A BATCH 1 INTENTIONAL: window_snap reads its
    # seams from the window-manip module's globals now, so the snap
    # fakes below patch that module, not the facade. Imported after
    # the facade by convention; batch 2 removed the order constraint
    # (impl modules import each other at the bottom, after all
    # definitions, so any import order works).
    from core import skills_hands3_window_manip as _hands3_manip
except Exception as exc:  # noqa: BLE001
    raise unittest.SkipTest(f"desktop modules unavailable: {exc}")


# ---------------------------------------------------------------------------
# Patch helpers (restore via addCleanup, so a failing test cannot leak
# a fake into the next test in the same process)
# ---------------------------------------------------------------------------


def _patch_attr(testcase, obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    testcase.addCleanup(setattr, obj, name, old)


def _patch_sys_module(testcase, name, value):
    had = name in sys.modules
    old = sys.modules.get(name)
    sys.modules[name] = value
    def _restore():  # noqa: E306
        if had:
            sys.modules[name] = old
        else:
            del sys.modules[name]
    testcase.addCleanup(_restore)


def _patch_windows_on(testcase, module):
    """Pretend the module runs on Windows; failsafe free to act."""
    _patch_attr(testcase, module, "_is_windows", lambda: True)
    if hasattr(module, "_input_blocked"):
        _patch_attr(testcase, module, "_input_blocked", lambda: None)


# ---------------------------------------------------------------------------
# Fakes for the UIA walk (get_window_controls)
# ---------------------------------------------------------------------------


class _FakeInfo:
    def __init__(self, name, control_type="Button"):
        self.name = name
        self.control_type = control_type


class _FakeElement:
    def __init__(self, name, control_type="Button", dead=False):
        self._name = name
        self._control_type = control_type
        self._dead = dead

    @property
    def element_info(self):
        if self._dead:
            raise RuntimeError("dead element")
        return _FakeInfo(self._name, self._control_type)


class _FakeWindow:
    def __init__(self, elements=None, descendants_exc=None):
        self._elements = list(elements or [])
        self._exc = descendants_exc

    def descendants(self):
        if self._exc is not None:
            raise self._exc
        return list(self._elements)


def _named(n, prefix="Control"):
    return [_FakeElement(f"{prefix} {i}", "Button") for i in range(n)]


# ---------------------------------------------------------------------------
# Fakes for the win32 seams (window_snap)
# ---------------------------------------------------------------------------


class _FakeUser32:
    """Records ShowWindow / SetWindowPos calls; answers IsZoomed /
    IsIconic from construction flags."""

    def __init__(self, zoomed=False, iconic=False, setpos_ok=True,
                 zoomed_exc=False):
        self.zoomed = zoomed
        self.iconic = iconic
        self.setpos_ok = setpos_ok
        self.zoomed_exc = zoomed_exc
        self.show_calls = []
        self.setpos_calls = []

    def ShowWindow(self, hwnd, cmd):
        self.show_calls.append((hwnd, cmd))
        return True

    def IsZoomed(self, hwnd):
        if self.zoomed_exc:
            raise RuntimeError("zoom probe failed")
        return self.zoomed

    def IsIconic(self, hwnd):
        return self.iconic

    def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):
        self.setpos_calls.append((hwnd, after, x, y, cx, cy, flags))
        return self.setpos_ok


# One monitor, 1920x1080 full, 40px taskbar at the bottom.
MON = {"index": 1, "device": "\\\\.\\DISPLAY1", "primary": True,
       "left": 0, "top": 0, "right": 1920, "bottom": 1080,
       "width": 1920, "height": 1080,
       "work_left": 0, "work_top": 0, "work_right": 1920,
       "work_bottom": 1040}

# _snap_rect expectations on MON (mid_x 960, mid_y 520).
SNAP_EXPECTED = {
    "left": (0, 0, 960, 1040),
    "right": (960, 0, 1920, 1040),
    "top-left": (0, 0, 960, 520),
    "top-right": (960, 0, 1920, 520),
    "bottom-left": (0, 520, 960, 1040),
    "bottom-right": (960, 520, 1920, 1040),
}


def _snap_fakes(testcase, user32=None, hwnd=1234, title="Notepad",
                monitors=None, monitor=None, rect=None):
    """Patch every window_snap seam; returns (user32, calls dict)."""
    # SKILLS SPLIT PHASE A BATCH 1 INTENTIONAL (patch where bound):
    # window_snap now lives in the window-manip module and reads these
    # seams from that module's globals; patching the facade would no
    # longer reach it. No fake value or assertion changed.
    _patch_windows_on(testcase, _hands3_manip)
    fake_u32 = user32 if user32 is not None else _FakeUser32()
    _patch_attr(testcase, _hands3_manip, "_user32", lambda: fake_u32)
    _patch_attr(testcase, _hands3_manip, "_foreground_hwnd",
                lambda: hwnd)
    _patch_attr(testcase, _hands3_manip, "_title_of", lambda h: title)
    _patch_attr(testcase, _hands3_manip, "_monitors",
                lambda: list(monitors) if monitors is not None else [dict(MON)])
    _patch_attr(testcase, _hands3_manip, "_monitor_for_window",
                lambda h, ms: dict(monitor) if monitor is not None else dict(MON))
    _patch_attr(testcase, _hands3_manip, "_window_rect",
                lambda h: rect)
    return fake_u32


# ---------------------------------------------------------------------------
# Fakes for tiling (tile_windows)
# ---------------------------------------------------------------------------

AREA = {"left": 0, "top": 0, "width": 1920, "height": 1040}


def _tile_fakes(testcase, windows, area=None, place=None):
    """Patch every tile_windows seam; returns the place-call log."""
    _patch_windows_on(testcase, skills_desktop_tiling)
    _patch_attr(testcase, skills_desktop_tiling, "_open_windows",
                lambda: list(windows))
    _patch_attr(testcase, skills_desktop_tiling, "_work_area",
                lambda: dict(area) if area is not None else dict(AREA))
    calls = []

    def _place(win, cell):
        calls.append((win, dict(cell)))
        if place is not None:
            return place(win, cell)
        return dict(cell)  # exact read-back: counts as placed

    _patch_attr(testcase, skills_desktop_tiling, "_place_window", _place)
    return calls


def _wins(n, minimized=()):
    return [{"hwnd": 100 + i, "title": f"App {i}",
             "minimized": i in minimized} for i in range(n)]


# ===========================================================================
# get_window_controls
# ===========================================================================


class TestGetWindowControls(unittest.TestCase):

    def _win_path(self, testcase_elements, desc="the foreground window",
                  title=""):
        """Windows path: pywinauto present, _uia_window faked."""
        _patch_windows_on(self, control_plus)
        _patch_sys_module(self, "pywinauto", types.ModuleType("pywinauto"))
        seen = {}

        def _uia_window(t):
            seen["title"] = t
            return _FakeWindow(testcase_elements), desc

        _patch_attr(self, control_plus, "_uia_window", _uia_window)
        return seen

    def test_non_windows_fallback(self):
        # No patching: the lab is Linux, so the real guard fires.
        self.assertEqual(
            control_plus.get_window_controls(),
            "UI Automation inspection is only available on Windows "
            "— this system is not Windows.")

    def test_pywinauto_missing_fallback(self):
        _patch_windows_on(self, control_plus)
        # None in sys.modules makes `import pywinauto` raise ImportError.
        _patch_sys_module(self, "pywinauto", None)
        self.assertEqual(
            control_plus.get_window_controls(),
            "UI Automation isn't available in this build of VYUK "
            "— the UI Automation part didn't make it into the package.")

    def test_connect_failure_names_generic_window(self):
        _patch_windows_on(self, control_plus)
        _patch_sys_module(self, "pywinauto", types.ModuleType("pywinauto"))

        def _boom(t):
            raise RuntimeError("no such window")

        _patch_attr(self, control_plus, "_uia_window", _boom)
        # desc is never bound when _uia_window raises, so the message
        # must fall back to "the window" rather than crash on NameError.
        self.assertEqual(control_plus.get_window_controls("Note"),
                         "Could not connect to the window: no such window")

    def test_title_forwarded_and_desc_used(self):
        seen = self._win_path(_named(1), desc="window matching 'Note'")
        out = control_plus.get_window_controls("Note")
        self.assertEqual(seen["title"], "Note")
        self.assertIn("Controls in window matching 'Note' (1 named "
                      "controls in total; 1 shown, cap 60):", out)
        self.assertIn("• Control 0 (Button)", out)

    def test_descendants_failure(self):
        _patch_windows_on(self, control_plus)
        _patch_sys_module(self, "pywinauto", types.ModuleType("pywinauto"))
        _patch_attr(self, control_plus, "_uia_window",
                    lambda t: (_FakeWindow(
                        descendants_exc=RuntimeError("tree unreadable")),
                        "the foreground window"))
        self.assertEqual(control_plus.get_window_controls(),
                         "Could not read controls from the foreground "
                         "window: tree unreadable")

    def test_empty_window(self):
        self._win_path([])
        self.assertEqual(control_plus.get_window_controls(),
                         "No named controls found in the foreground window.")

    def test_unnamed_only_window(self):
        # Unnamed elements are skipped AND not counted: still "No named".
        self._win_path([_FakeElement(""), _FakeElement("   "),
                        _FakeElement(None)])
        self.assertEqual(control_plus.get_window_controls(),
                         "No named controls found in the foreground window.")

    def test_named_unnamed_mixed_counts_only_named(self):
        self._win_path([_FakeElement("Save", "Button"),
                        _FakeElement(""),
                        _FakeElement("File", "MenuItem"),
                        _FakeElement("  "),
                        _FakeElement("Edit", "MenuItem")])
        out = control_plus.get_window_controls()
        self.assertIn("(3 named controls in total; 3 shown, cap 60):", out)
        self.assertIn("• Save (Button)", out)
        self.assertIn("• File (MenuItem)", out)
        self.assertIn("• Edit (MenuItem)", out)
        self.assertNotIn("[truncated", out)

    def test_dead_element_skipped_mid_walk(self):
        self._win_path([_FakeElement("First", "Button"),
                        _FakeElement("Dead", dead=True),
                        _FakeElement("Last", "Button")])
        out = control_plus.get_window_controls()
        self.assertIn("(2 named controls in total; 2 shown, cap 60):", out)
        self.assertIn("• First (Button)", out)
        self.assertIn("• Last (Button)", out)
        self.assertNotIn("Dead", out)

    def test_display_cap_60(self):
        self._win_path(_named(65))
        out = control_plus.get_window_controls()
        self.assertIn("(65 named controls in total; 60 shown, cap 60):", out)
        bullets = [ln for ln in out.split("\n") if ln.startswith("• ")]
        self.assertEqual(len(bullets), 60)
        self.assertEqual(bullets[-1], "• Control 59 (Button)")

    def test_walk_budget_stops_early_with_named(self):
        elements = _named(10) + [_FakeElement("") for _ in range(1995)]
        self._win_path(elements)
        out = control_plus.get_window_controls()
        self.assertIn("(10 named controls in total; 10 shown, cap 60):", out)
        self.assertIn("the walk stopped after examining 2000 elements",
                      out)
        self.assertIn("may hold more named controls than the total shown",
                      out)

    def test_walk_budget_all_unnamed_takes_header_branch(self):
        # Edge the code distinguishes: total 0 BUT stopped early means
        # the header branch, not the "No named controls" branch.
        self._win_path([_FakeElement("") for _ in range(2005)])
        out = control_plus.get_window_controls()
        self.assertIn("(0 named controls in total; 0 shown, cap 60):", out)
        self.assertIn("the walk stopped after examining 2000 elements",
                      out)
        self.assertNotIn("No named controls found", out)

    def test_report_truncated_at_1500(self):
        self._win_path([_FakeElement("N" * 400, "Button")
                        for _ in range(5)])
        out = control_plus.get_window_controls()
        marker = "\n... [truncated, "
        self.assertIn(marker, out)
        self.assertTrue(out.endswith(" more chars]"))
        head, _, tail = out.partition(marker)
        self.assertEqual(len(head), 1500)
        more = int(tail.split(" more chars]")[0])
        self.assertGreater(more, 0)


# ===========================================================================
# window_snap
# ===========================================================================


class TestWindowSnap(unittest.TestCase):

    def test_unknown_position_refused_with_list(self):
        for pos in ("banana", "", None):
            with self.subTest(pos=pos):
                out = skills_window_control.window_snap(pos)
                self.assertTrue(
                    out.startswith(f"Unknown snap position: '{pos}'. "
                                   "Use one of: "))
                self.assertIn("left, right, top-left, top-right, "
                              "bottom-left, bottom-right, maximise", out)

    def test_non_windows_fallback(self):
        # No patching: the lab is Linux, so the real guard fires, and
        # it fires AFTER position validation (a valid position here).
        self.assertEqual(
            skills_window_control.window_snap("left"),
            "Snapping windows uses Windows window APIs — this system "
            "is not Windows, so nothing was changed.")

    def test_case_whitespace_and_us_spelling_normalized(self):
        u32 = _snap_fakes(self, rect=(0, 0, 960, 1040))
        out = skills_window_control.window_snap("  LEFT  ")
        self.assertIn("to the left half of monitor 1", out)
        self.assertEqual(u32.setpos_calls[0][2:6], (0, 0, 960, 1040))
        u32b = _snap_fakes(self, user32=_FakeUser32(zoomed=True))
        out = skills_window_control.window_snap("maximize")
        self.assertEqual(out, "Maximised 'Notepad' on monitor 1 "
                              "(1920×1080).")
        self.assertEqual(u32b.show_calls, [(1234, 3)])

    def test_blocked_by_failsafe_short_circuits(self):
        # SKILLS SPLIT PHASE A BATCH 1 INTENTIONAL (patch where bound):
        # window_snap reads these seams from the window-manip module.
        _patch_windows_on(self, _hands3_manip)
        _patch_attr(self, _hands3_manip, "_input_blocked",
                    lambda: "Input is blocked by the failsafe.")
        u32 = _FakeUser32()
        _patch_attr(self, _hands3_manip, "_user32", lambda: u32)
        self.assertEqual(skills_window_control.window_snap("left"),
                         "Input is blocked by the failsafe.")
        self.assertEqual(u32.show_calls, [])
        self.assertEqual(u32.setpos_calls, [])

    def test_no_foreground_window(self):
        _snap_fakes(self, hwnd=None)
        self.assertEqual(
            skills_window_control.window_snap("left"),
            "No window is currently in focus, so there's nothing to snap.")

    def test_no_monitors(self):
        _snap_fakes(self, monitors=[])
        self.assertEqual(
            skills_window_control.window_snap("left"),
            "Could not read the monitor list, so I can't place the "
            "window exactly.")

    def test_untitled_window(self):
        _snap_fakes(self, title="", rect=(0, 0, 960, 1040))
        out = skills_window_control.window_snap("left")
        self.assertIn("Snapped '(untitled window)' to the left half", out)

    def test_each_position_geometry_and_phrasing(self):
        for pos, (l, t, r, b) in SNAP_EXPECTED.items():
            with self.subTest(pos=pos):
                u32 = _snap_fakes(self, rect=(l, t, r, b))
                out = skills_window_control.window_snap(pos)
                self.assertEqual(u32.setpos_calls[0][2:6],
                                 (l, t, r - l, b - t))
                self.assertEqual(u32.setpos_calls[0][6], 0x0004)  # NOZORDER
                where = ("left half" if pos == "left" else
                         "right half" if pos == "right" else
                         f"{pos} quarter")
                self.assertIn(
                    f"Snapped 'Notepad' to the {where} of monitor 1 — "
                    f"it now covers ({l}, {t}) to ({r}, {b}) on the "
                    "1920×1080 screen.", out)

    def test_odd_monitor_midpoints_use_integer_division(self):
        odd = dict(MON, work_right=1921, work_bottom=1041, width=1921,
                   height=1081)
        u32 = _snap_fakes(self, monitor=odd, rect=(0, 0, 960, 1041))
        out = skills_window_control.window_snap("left")
        # mid_x = (0 + 1921) // 2 = 960; mid_y = (0 + 1041) // 2 = 520.
        self.assertEqual(u32.setpos_calls[0][2:6], (0, 0, 960, 1041))
        self.assertIn("it now covers (0, 0) to (960, 1041)", out)

    def test_snap_rect_direct_contract(self):
        # Pure helper: unknown positions are a KeyError — callers
        # (window_snap) must validate first, which it does.
        with self.assertRaises(KeyError):
            skills_window_control._snap_rect("banana", MON)
        for pos, expected in SNAP_EXPECTED.items():
            self.assertEqual(skills_window_control._snap_rect(pos, MON), expected)

    def test_maximise_success_and_refusal(self):
        u32 = _snap_fakes(self, user32=_FakeUser32(zoomed=True))
        self.assertEqual(skills_window_control.window_snap("maximise"),
                         "Maximised 'Notepad' on monitor 1 (1920×1080).")
        self.assertEqual(u32.show_calls, [(1234, 3)])
        _snap_fakes(self, user32=_FakeUser32(zoomed=False))
        self.assertEqual(
            skills_window_control.window_snap("maximise"),
            "Sent maximise to 'Notepad', but it does not appear "
            "maximised — the app may refuse resizing.")

    def test_maximise_zoom_probe_failure_counts_as_success(self):
        # IsZoomed raising leaves zoomed None, and None is not False,
        # so the maximise report stands (exception swallowed by design).
        _snap_fakes(self, user32=_FakeUser32(zoomed_exc=True))
        self.assertEqual(skills_window_control.window_snap("maximise"),
                         "Maximised 'Notepad' on monitor 1 (1920×1080).")

    def test_maximised_window_restored_before_snap(self):
        u32 = _snap_fakes(self, user32=_FakeUser32(zoomed=True),
                          rect=(0, 0, 960, 1040))
        skills_window_control.window_snap("left")
        # SW_RESTORE (9) must precede the SetWindowPos call.
        self.assertEqual(u32.show_calls, [(1234, 9)])
        self.assertEqual(len(u32.setpos_calls), 1)

    def test_minimised_window_restored_before_snap(self):
        u32 = _snap_fakes(self, user32=_FakeUser32(iconic=True),
                          rect=(0, 0, 960, 1040))
        skills_window_control.window_snap("right")
        self.assertEqual(u32.show_calls, [(1234, 9)])

    def test_setwindowpos_refused(self):
        _snap_fakes(self, user32=_FakeUser32(setpos_ok=False))
        self.assertEqual(skills_window_control.window_snap("left"),
                         "Windows refused to move 'Notepad'.")

    def test_readback_mismatch_reported(self):
        _snap_fakes(self, rect=(10, 0, 970, 1040))
        out = skills_window_control.window_snap("left")
        self.assertIn("I asked Windows to snap 'Notepad' to the left "
                      "of monitor 1, but it actually sits at "
                      "(10, 0)–(970, 1040) instead of (0, 0)–(960, 1040)",
                      out)
        self.assertIn("may enforce its own size or position", out)

    def test_readback_tolerance_boundary(self):
        # Off by exactly 2px: within tolerance, success. Off by 3px:
        # outside, mismatch. The code tests `> 2`, not `>= 2`.
        _snap_fakes(self, rect=(2, 0, 962, 1040))
        self.assertIn("Snapped 'Notepad' to the left half",
                      skills_window_control.window_snap("left"))
        _snap_fakes(self, rect=(3, 0, 963, 1040))
        self.assertIn("actually sits at (3, 0)–(963, 1040)",
                      skills_window_control.window_snap("left"))

    def test_readback_none_still_success(self):
        _snap_fakes(self, rect=None)
        self.assertIn("Snapped 'Notepad' to the left half",
                      skills_window_control.window_snap("left"))

    def test_unexpected_exception_reported(self):
        # SKILLS SPLIT PHASE A BATCH 1 INTENTIONAL (patch where bound):
        # window_snap reads these seams from the window-manip module.
        _patch_windows_on(self, _hands3_manip)
        # _user32() runs before _foreground_hwnd(), so it must be
        # faked too — otherwise the real ctypes.windll lookup fails
        # first on Linux and masks the intended exception.
        _patch_attr(self, _hands3_manip, "_user32",
                    lambda: _FakeUser32())

        def _boom():
            raise RuntimeError("win32 exploded")

        _patch_attr(self, _hands3_manip, "_foreground_hwnd", _boom)
        self.assertEqual(skills_window_control.window_snap("left"),
                         "Could not snap the window: win32 exploded")


# ===========================================================================
# tile_windows
# ===========================================================================


class TestTileWindows(unittest.TestCase):

    def test_unknown_layout_refused_with_list(self):
        out = skills_desktop_tiling.tile_windows("diagonal")
        self.assertEqual(out, "Unknown tiling layout: 'diagonal'. "
                              "Use one of: grid, columns, rows.")

    def test_none_layout_means_grid(self):
        # Contrast with snap: tile_windows treats None as the default.
        calls = _tile_fakes(self, _wins(1))
        out = skills_desktop_tiling.tile_windows(None)
        self.assertEqual(out, "Tiled 1 window in a grid (1×1).")
        self.assertEqual(calls[0][1],
                         {"left": 4, "top": 4, "width": 1912,
                          "height": 1032})

    def test_layout_normalization(self):
        _tile_fakes(self, _wins(1))
        self.assertEqual(skills_desktop_tiling.tile_windows("  GRID "),
                         "Tiled 1 window in a grid (1×1).")

    def test_non_windows_fallback(self):
        # No patching: the lab is Linux, so the real guard fires, and
        # it fires AFTER layout validation (a valid layout here).
        self.assertEqual(
            skills_desktop_tiling.tile_windows("grid"),
            "Tiling windows needs Windows — this system is not "
            "Windows, so nothing was changed.")

    def test_no_open_windows(self):
        _tile_fakes(self, [])
        self.assertEqual(
            skills_desktop_tiling.tile_windows("grid"),
            "No open windows to tile — nothing visible with a title "
            "is open right now.")

    def test_no_work_area(self):
        _tile_fakes(self, _wins(1))
        # The seam itself returns None (area unreadable).
        _patch_attr(self, skills_desktop_tiling, "_work_area", lambda: None)
        self.assertEqual(
            skills_desktop_tiling.tile_windows("grid"),
            "I couldn't read the primary monitor's work area, so no "
            "windows were moved.")

    def test_single_window_grid_cell(self):
        calls = _tile_fakes(self, _wins(1))
        out = skills_desktop_tiling.tile_windows("grid")
        self.assertEqual(out, "Tiled 1 window in a grid (1×1).")
        self.assertEqual(len(calls), 1)
        # 4px gap inset on every side of the full area.
        self.assertEqual(calls[0][1],
                         {"left": 4, "top": 4, "width": 1912,
                          "height": 1032})

    def test_two_windows_grid_cells_row_major(self):
        calls = _tile_fakes(self, _wins(2))
        out = skills_desktop_tiling.tile_windows("grid")
        self.assertEqual(out, "Tiled 2 windows in a grid (2×1).")
        self.assertEqual(calls[0][1],
                         {"left": 4, "top": 4, "width": 952,
                          "height": 1032})
        self.assertEqual(calls[1][1],
                         {"left": 964, "top": 4, "width": 952,
                          "height": 1032})

    def test_four_windows_grid(self):
        calls = _tile_fakes(self, _wins(4))
        out = skills_desktop_tiling.tile_windows("grid")
        self.assertEqual(out, "Tiled 4 windows in a grid (2×2).")
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[3][1],
                         {"left": 964, "top": 524, "width": 952,
                          "height": 512})

    def test_columns_layout(self):
        calls = _tile_fakes(self, _wins(3))
        out = skills_desktop_tiling.tile_windows("columns")
        self.assertEqual(out, "Tiled 3 windows in 3 columns.")
        self.assertEqual([c[1]["left"] for c in calls], [4, 644, 1284])
        self.assertTrue(all(c[1]["width"] == 632 for c in calls))
        self.assertTrue(all(c[1]["height"] == 1032 for c in calls))

    def test_rows_layout(self):
        calls = _tile_fakes(self, _wins(3))
        out = skills_desktop_tiling.tile_windows("rows")
        self.assertEqual(out, "Tiled 3 windows in 3 rows.")
        # Row boundaries: round(1040/3)=347, round(2080/3)=693.
        self.assertEqual([c[1]["top"] for c in calls], [4, 351, 697])
        self.assertEqual([c[1]["height"] for c in calls], [339, 338, 339])

    def test_minimized_window_noted_as_restored(self):
        _tile_fakes(self, _wins(1, minimized={0}))
        out = skills_desktop_tiling.tile_windows("grid")
        self.assertEqual(out, "Tiled 1 window in a grid (1×1).\n"
                              "1 of them were minimized and had to be "
                              "restored to be tiled.")

    def test_refused_window_listed(self):
        _tile_fakes(self, _wins(1),
                    place=lambda w, c: None)
        out = skills_desktop_tiling.tile_windows("grid")
        self.assertEqual(out, "Tiled 0 of 1 windows in a grid (1×1).\n"
                              "• 'App 0' couldn't be moved — Windows "
                              "refused it or its new position couldn't "
                              "be read back.")

    def test_moved_but_drifted_window_listed(self):
        def _drift(win, cell):
            bad = dict(cell)
            bad["left"] = cell["left"] + 9  # beyond the 8px tolerance
            return bad

        _tile_fakes(self, _wins(1), place=_drift)
        out = skills_desktop_tiling.tile_windows("grid")
        self.assertIn("Tiled 0 of 1 windows in a grid (1×1).", out)
        self.assertIn("• 'App 0' was moved, but it now sits at "
                      "(13, 4) 1912x1032 — not exactly its tile "
                      "((4, 4) 1912x1032).", out)

    def test_readback_within_tolerance_counts_as_placed(self):
        def _close(win, cell):
            ok = dict(cell)
            ok["left"] = cell["left"] + 8  # exactly the tolerance
            return ok

        _tile_fakes(self, _wins(1), place=_close)
        self.assertEqual(skills_desktop_tiling.tile_windows("grid"),
                         "Tiled 1 window in a grid (1×1).")

    def test_mixed_results(self):
        # 3 windows tile into a grid (2x2); cell0 is (4, 4, 952x512).
        outcomes = [{"left": 4, "top": 4, "width": 952, "height": 512},
                    None,
                    {"left": 1000, "top": 524, "width": 952,
                     "height": 512}]

        def _scripted(win, cell):
            return outcomes.pop(0)

        _tile_fakes(self, _wins(3), place=_scripted)
        out = skills_desktop_tiling.tile_windows("grid")
        self.assertIn("Tiled 1 of 3 windows in a grid (2×2).", out)
        self.assertIn("• 'App 1' couldn't be moved", out)
        self.assertIn("• 'App 2' was moved, but it now sits at", out)

    def test_max_tile_cap_12(self):
        calls = _tile_fakes(self, _wins(14))
        out = skills_desktop_tiling.tile_windows("grid")
        self.assertEqual(len(calls), 12)
        self.assertIn("Tiled 12 windows in a grid (4×3).", out)
        self.assertIn("14 windows are open; only the first 12 were "
                      "tiled.", out)

    def test_place_window_non_windows_returns_none(self):
        # No patching: the real _place_window guard fires on Linux.
        win = {"hwnd": 1234, "title": "App", "minimized": False}
        cell = {"left": 4, "top": 4, "width": 100, "height": 100}
        self.assertIsNone(skills_desktop_tiling._place_window(win, cell))

    def test_cells_and_grid_dims_pure_geometry(self):
        self.assertEqual(skills_desktop_tiling._cells(AREA, 0, "grid"), [])
        self.assertEqual(skills_desktop_tiling._cells(AREA, -3, "rows"), [])
        # Unknown layout falls through to the grid branch (else).
        self.assertEqual(len(skills_desktop_tiling._cells(AREA, 4, "weird")), 4)
        for n, expected in ((1, (1, 1)), (2, (2, 1)), (3, (2, 2)),
                            (5, (3, 2)), (12, (4, 3))):
            self.assertEqual(skills_desktop_tiling._grid_dims(n), expected)

    def test_rect_close_tolerance_boundary(self):
        cell = {"left": 4, "top": 4, "width": 100, "height": 100}
        same = dict(cell)
        edge = dict(cell, left=12)   # exactly 8px: within tolerance
        over = dict(cell, left=13)   # 9px: outside tolerance
        self.assertTrue(skills_desktop_tiling._rect_close(same, cell))
        self.assertTrue(skills_desktop_tiling._rect_close(edge, cell))
        self.assertFalse(skills_desktop_tiling._rect_close(over, cell))
        self.assertEqual(skills_desktop_tiling._rect_text(cell),
                         "(4, 4) 100x100")


if __name__ == "__main__":
    unittest.main()
