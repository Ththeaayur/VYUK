"""B-D1 (2026-11-09) PyWinCtl + PyMonCtl window control.

Runs against an INJECTED FAKE backend — the Linux lab has no X
display, so the real PyWinCtl backend cannot load here (import
fails with Xlib DisplayNameError; see test_real_backend_unavailable
below, which asserts that honest degradation rather than
skipping). The real-Windows leg — real windows moving, snapping
and focusing on his machine — is a field leg, owed, not this
batch.

The fake models Windows honestly where it matters: moveTo /
resizeTo on a maximised or minimised window are no-ops (Windows
refuses them), activate() can be refused (Windows foreground
rules), and a stubborn window ignores moves entirely — so the
read-back contract is genuinely exercised, not just the call.
"""

import unittest

from core.desktop import pywinctl_wrapper as pw


class _FakeRect:
    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _FakeMonitor:
    def __init__(self, name, primary, box, workarea):
        self.name = name
        self.isPrimary = primary
        self.box = box
        self.workarea = workarea


class _FakeWindow:
    """PyWinCtl Window duck-type."""

    def __init__(self, backend, handle, title, left, top, width,
                 height, refuse_focus=False, stubborn=False):
        self._backend = backend
        self._handle = handle
        self._title = title
        self._left = left
        self._top = top
        self._width = width
        self._height = height
        self._minimized = False
        self._maximized = False
        self._refuse_focus = refuse_focus
        self._stubborn = stubborn

    def getHandle(self):
        return self._handle

    @property
    def title(self):
        return self._title

    @property
    def left(self):
        return self._left

    @property
    def top(self):
        return self._top

    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    @property
    def box(self):
        return (self._left, self._top, self._width, self._height)

    def moveTo(self, x, y):
        if self._stubborn or self._maximized or self._minimized:
            return False
        self._left = int(x)
        self._top = int(y)
        return True

    def resizeTo(self, w, h):
        if self._stubborn or self._maximized or self._minimized:
            return False
        self._width = int(w)
        self._height = int(h)
        return True

    def minimize(self):
        self._minimized = True
        self._maximized = False
        return True

    def maximize(self):
        self._maximized = True
        self._minimized = False
        return True

    def restore(self):
        self._minimized = False
        self._maximized = False
        return True

    def activate(self):
        if self._refuse_focus:
            return False
        self._backend._set_active(self._handle)
        return True

    def isMinimized(self):
        return self._minimized

    def isMaximized(self):
        return self._maximized

    def isActive(self):
        return self._backend._active == self._handle


class _FakeBackend:
    """Two monitors (primary 1920x1080, second 1920x1080 to its
    right) and a small window set. The second monitor is what
    catches monitor-0 geometry: its work area starts at x=1920."""

    def __init__(self):
        self._active = None
        self.monitors_list = [
            _FakeMonitor("Primary", True,
                         _FakeRect(0, 0, 1920, 1080),
                         _FakeRect(0, 0, 1920, 1040)),
            _FakeMonitor("Second", False,
                         _FakeRect(1920, 0, 3840, 1080),
                         _FakeRect(1920, 0, 3840, 1040)),
        ]
        self.windows = [
            _FakeWindow(self, 101, "Notepad", 100, 100, 800, 600),
            _FakeWindow(self, 102, "Notepad", 2000, 100, 800, 600),
            _FakeWindow(self, 103, "Calculator", 300, 300, 400, 300),
            _FakeWindow(self, 105, "Calculator Pro", 400, 400, 500, 400),
            _FakeWindow(self, 106, "Shy Window", 100, 100, 300, 200,
                        refuse_focus=True),
            _FakeWindow(self, 107, "Stubborn Window", 100, 100, 300, 200,
                        stubborn=True),
        ]

    def _set_active(self, handle):
        self._active = handle

    def list_windows(self):
        return list(self.windows)

    def active_window(self):
        for win in self.windows:
            if win.getHandle() == self._active:
                return win
        return None

    def monitors(self):
        return list(self.monitors_list)

    def window(self, handle):
        for win in self.windows:
            if win.getHandle() == handle:
                return win
        return None


class TestWindowControl(unittest.TestCase):
    def setUp(self):
        self.backend = _FakeBackend()

    # -- engine availability (real backend, this interpreter) --

    def test_real_backend_unavailable_on_headless_lab(self):
        """The suite states which interpreter it ran on and what
        the backend availability was: on this headless Linux lab
        the real backend cannot load (no X display). The wrapper
        must say so honestly — never silently skip the checks."""
        self.assertFalse(pw.engine_available())
        status = pw.engine_status()
        self.assertFalse(status["available"])
        self.assertIn("PyWinCtl", status["error"])
        listed = pw.list_windows()
        self.assertFalse(listed["ok"])
        self.assertTrue(listed["reason"].startswith(
            "engine_unavailable:"))
        self.assertEqual(listed["windows"], [])
        self.assertIn("Could not place", pw.window_place(
            "Notepad", position="left"))
        self.assertIn("engine_unavailable", pw.window_place(
            "Notepad", position="left"))

    # -- list / monitors --

    def test_list_returns_windows_with_handles(self):
        listed = pw.list_windows(backend=self.backend)
        self.assertTrue(listed["ok"])
        by_handle = {w["handle"]: w for w in listed["windows"]}
        self.assertEqual(
            sorted(by_handle), [101, 102, 103, 105, 106, 107])
        calc = by_handle[103]
        self.assertEqual(calc["title"], "Calculator")
        self.assertEqual(
            (calc["left"], calc["top"], calc["width"],
             calc["height"]), (300, 300, 400, 300))

    def test_list_assigns_each_windows_own_monitor(self):
        listed = pw.list_windows(backend=self.backend)
        by_handle = {w["handle"]: w for w in listed["windows"]}
        self.assertEqual(by_handle[101]["monitor"], 0)
        # Window 102 sits on the second monitor (x=2000).
        self.assertEqual(by_handle[102]["monitor"], 1)

    def test_monitors_reports_both_with_work_areas(self):
        got = pw.monitors(backend=self.backend)
        self.assertTrue(got["ok"])
        self.assertEqual(len(got["monitors"]), 2)
        primary, second = got["monitors"]
        self.assertTrue(primary["primary"])
        self.assertFalse(second["primary"])
        self.assertEqual(
            (second["work_left"], second["work_top"],
             second["work_right"], second["work_bottom"]),
            (1920, 0, 3840, 1040))

    # -- resolve: title is a hint, handle is the identity --

    def test_resolve_exact_beats_substring(self):
        # "Calculator" matches exactly one window AND is a
        # substring of "Calculator Pro" — exact wins.
        got = pw.resolve("Calculator", backend=self.backend)
        self.assertTrue(got["ok"])
        self.assertEqual(got["handle"], 103)

    def test_resolve_substring_when_unique(self):
        got = pw.resolve("ator Pro", backend=self.backend)
        self.assertTrue(got["ok"])
        self.assertEqual(got["handle"], 105)

    def test_resolve_duplicate_title_refuses_with_candidates(self):
        # Two windows titled exactly "Notepad": the action must
        # refuse, never move one of them at random.
        got = pw.resolve("Notepad", backend=self.backend)
        self.assertFalse(got["ok"])
        self.assertIn("ambiguous", got["reason"])
        self.assertEqual(
            sorted(c["handle"] for c in got["candidates"]),
            [101, 102])

    def test_resolve_no_match_fails_honestly(self):
        got = pw.resolve("No Such Window", backend=self.backend)
        self.assertFalse(got["ok"])
        self.assertIn("no_match", got["reason"])
        self.assertIsNone(got["handle"])

    def test_resolve_empty_title_fails_honestly(self):
        got = pw.resolve("   ", backend=self.backend)
        self.assertFalse(got["ok"])
        self.assertIn("no_match", got["reason"])

    # -- move / resize with read-back --

    def test_move_reads_back_actual_geometry(self):
        got = pw.move(103, 500, 400, backend=self.backend)
        self.assertTrue(got["ok"])
        self.assertEqual(
            (got["geometry"]["left"], got["geometry"]["top"],
             got["geometry"]["width"], got["geometry"]["height"]),
            (500, 400, 400, 300))

    def test_move_and_resize_reads_back(self):
        got = pw.move(103, 500, 400, 900, 700, backend=self.backend)
        self.assertTrue(got["ok"])
        self.assertEqual(
            (got["geometry"]["left"], got["geometry"]["top"],
             got["geometry"]["width"], got["geometry"]["height"]),
            (500, 400, 900, 700))

    def test_resize_reads_back(self):
        got = pw.resize(103, 1000, 800, backend=self.backend)
        self.assertTrue(got["ok"])
        self.assertEqual(
            (got["geometry"]["width"], got["geometry"]["height"]),
            (1000, 800))

    def test_move_that_does_not_land_fails_honestly(self):
        """The stubborn window ignores the move: the wrapper
        must report the mismatch, never claim success."""
        before = self.backend.window(107)
        got = pw.move(107, 500, 400, backend=self.backend)
        self.assertFalse(got["ok"])
        self.assertIn("move_did_not_land", got["reason"])
        self.assertEqual(
            (got["geometry"]["left"], got["geometry"]["top"]),
            (before.left, before.top))

    def test_move_disappeared_window_fails_honestly(self):
        resolved = pw.resolve("Calculator", backend=self.backend)
        self.assertTrue(resolved["ok"])
        # The window closes between resolve and action.
        self.backend.windows = [
            w for w in self.backend.windows
            if w.getHandle() != resolved["handle"]]
        got = pw.move(resolved["handle"], 500, 400,
                      backend=self.backend)
        self.assertFalse(got["ok"])
        self.assertIn("window_disappeared", got["reason"])

    def test_move_maximised_window_restores_first(self):
        pw.maximise(103, backend=self.backend)
        self.assertTrue(self.backend.window(103).isMaximized())
        got = pw.move(103, 50, 60, backend=self.backend)
        self.assertTrue(got["ok"])
        self.assertEqual(
            (got["geometry"]["left"], got["geometry"]["top"]),
            (50, 60))
        self.assertFalse(self.backend.window(103).isMaximized())

    # -- snap: geometry from the window's OWN monitor --

    def test_snap_positions_table_has_all_twelve(self):
        self.assertEqual(len(pw.SNAP_POSITIONS), 12)
        for name in ("left", "right", "top", "bottom",
                     "left-third", "center-third", "right-third",
                     "top-left", "top-right", "bottom-left",
                     "bottom-right", "maximise"):
            self.assertIn(name, pw.SNAP_POSITIONS)

    def test_snap_left_half_from_primary_work_area(self):
        got = pw.snap(103, "left", backend=self.backend)
        self.assertTrue(got["ok"])
        self.assertEqual(got["monitor"], 0)
        self.assertEqual(
            (got["geometry"]["left"], got["geometry"]["top"],
             got["geometry"]["width"], got["geometry"]["height"]),
            (0, 0, 960, 1040))

    def test_snap_right_third_uses_second_monitor_not_monitor_0(self):
        """The trap this batch exists to close: window 102 is
        on the second monitor, so its right-third is at x=3200,
        not monitor 0's x=1280."""
        got = pw.snap(102, "right-third", backend=self.backend)
        self.assertTrue(got["ok"])
        self.assertEqual(got["monitor"], 1)
        self.assertEqual(
            (got["geometry"]["left"], got["geometry"]["top"],
             got["geometry"]["width"], got["geometry"]["height"]),
            (3200, 0, 640, 1040))

    def test_snap_top_and_bottom_halves(self):
        top = pw.snap(103, "top", backend=self.backend)
        self.assertTrue(top["ok"])
        self.assertEqual(
            (top["geometry"]["left"], top["geometry"]["top"],
             top["geometry"]["width"], top["geometry"]["height"]),
            (0, 0, 1920, 520))
        bottom = pw.snap(103, "bottom", backend=self.backend)
        self.assertTrue(bottom["ok"])
        self.assertEqual(
            (bottom["geometry"]["left"], bottom["geometry"]["top"],
             bottom["geometry"]["width"],
             bottom["geometry"]["height"]),
            (0, 520, 1920, 520))

    def test_snap_quarter(self):
        got = pw.snap(103, "top-right", backend=self.backend)
        self.assertTrue(got["ok"])
        self.assertEqual(
            (got["geometry"]["left"], got["geometry"]["top"],
             got["geometry"]["width"], got["geometry"]["height"]),
            (960, 0, 960, 520))

    def test_snap_thirds_sum_to_work_area_width(self):
        widths = []
        for pos in ("left-third", "center-third", "right-third"):
            got = pw.snap(103, pos, backend=self.backend)
            self.assertTrue(got["ok"])
            widths.append(got["geometry"]["width"])
        self.assertEqual(sum(widths), 1920)
        self.assertEqual(widths, [640, 640, 640])

    def test_snap_maximise(self):
        got = pw.snap(103, "maximise", backend=self.backend)
        self.assertTrue(got["ok"])
        self.assertEqual(got["state"], "maximised")
        self.assertEqual(got["monitor"], 0)

    def test_snap_bad_position_fails_honestly(self):
        got = pw.snap(103, "diagonal", backend=self.backend)
        self.assertFalse(got["ok"])
        self.assertIn("bad_position", got["reason"])

    # -- focus / minimise / maximise / restore --

    def test_focus_verifies_active_handle(self):
        got = pw.focus(103, backend=self.backend)
        self.assertTrue(got["ok"])
        self.assertEqual(self.backend.active_window().getHandle(), 103)

    def test_focus_refused_fails_honestly(self):
        """activate() refused by the foreground rules: the
        wrapper must say so, never claim the focus landed."""
        got = pw.focus(106, backend=self.backend)
        self.assertFalse(got["ok"])
        self.assertIn("focus_failed", got["reason"])

    def test_minimise_maximise_restore_read_back_state(self):
        minimised = pw.minimise(103, backend=self.backend)
        self.assertTrue(minimised["ok"])
        self.assertEqual(minimised["state"], "minimised")
        restored = pw.restore(103, backend=self.backend)
        self.assertTrue(restored["ok"])
        self.assertEqual(restored["state"], "normal")
        maximised = pw.maximise(103, backend=self.backend)
        self.assertTrue(maximised["ok"])
        self.assertEqual(maximised["state"], "maximised")

    # -- window_place tool handler --

    def test_window_place_snap_reports_actual_geometry(self):
        text = pw.window_place("Calculator", position="left",
                               backend=self.backend)
        self.assertIn("Snapped 'Calculator' to left on monitor 0",
                      text)
        self.assertIn("(0, 0, 960x1040)", text)

    def test_window_place_explicit_geometry(self):
        text = pw.window_place("Calculator", left=10, top=20,
                               width=300, height=200,
                               backend=self.backend)
        self.assertIn("Moved 'Calculator' to (10, 20, 300x200)", text)

    def test_window_place_resize_only_keeps_position(self):
        text = pw.window_place("Calculator", width=500, height=400,
                               backend=self.backend)
        self.assertIn("Moved 'Calculator' to (300, 300, 500x400)",
                      text)

    def test_window_place_ambiguous_title_moves_nothing(self):
        before = {w.getHandle(): (w.left, w.top)
                  for w in self.backend.windows}
        text = pw.window_place("Notepad", position="left",
                               backend=self.backend)
        self.assertIn("ambiguous", text)
        after = {w.getHandle(): (w.left, w.top)
                 for w in self.backend.windows}
        self.assertEqual(before, after)

    def test_window_place_no_match_fails_honestly(self):
        text = pw.window_place("No Such Window", position="left",
                               backend=self.backend)
        self.assertIn("Could not place", text)
        self.assertIn("no_match", text)

    def test_window_place_rejects_both_position_and_geometry(self):
        text = pw.window_place("Calculator", position="left",
                               left=10, top=10,
                               backend=self.backend)
        self.assertIn("either a snap position or explicit geometry",
                      text)

    def test_window_place_rejects_position_only(self):
        text = pw.window_place("Calculator",
                               backend=self.backend)
        self.assertIn("give a snap position or a geometry", text)

    def test_window_place_rejects_half_geometry(self):
        text = pw.window_place("Calculator", left=10,
                               backend=self.backend)
        self.assertIn("needs both left and top", text)

    def test_window_place_bad_position(self):
        text = pw.window_place("Calculator", position="diagonal",
                               backend=self.backend)
        self.assertIn("not a snap position", text)


if __name__ == "__main__":
    unittest.main()
