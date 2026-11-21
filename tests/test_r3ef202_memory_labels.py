"""Round-3 section E, E-F202: Memory web-graph labels lose their
first/last characters ("Wake worc", "ister's birthday",
"roject deadline").

Section-E finding E-F202: Memory -> Web (2D) -> the node labels
lose their first or last character. Cause: labels are planned in
gui/memory_web_canvas2d.py with a rect exactly
QFontMetrics.horizontalAdvance(label) wide, and drawn by
_draw_label / _draw_label_text in gui/memory_web_graph.py with
drawText(rect, AlignCenter, text), which clips to the rect. The
drawn text's ink is wider than the advance — the ink overhangs
the advance by ~1 px on the right (plus antialiasing) — so the
edge ink is cut and the last glyph reads short.

The fix (E-F202, one product file, gui/memory_web_canvas2d.py):
the planner measures the DRAWN ink, not the advance —
QFontMetrics.boundingRect(label).width() + 4 px (2 each side) —
so the rect contains the ink however drawText centres the
advance. Nothing else changes: the label font, the halo
(still advance + 8 px, centred on the same centre), the
collision planning input shape, the node layout, the 3D view
and the list/search/filter/Forget controls are untouched.

The app has ONE dark chrome — there is no light/day theme in
v6.3.0, and the canvas paint uses fixed TEXT/TEXT_MUTED
colours — so the variable is width (908 / 1024 / 1280 / 1920).

GUI tests are Qt-only (the test_chat_ui WindowBase shape: the
real MainWindow offscreen), gated the same way as rethink Build
3's TestStripDerivation (skipped on the sys interpreter, which
has no PySide6). One source pin runs on both interpreters: it
reads the product file as text and pins the ink measurement in
place, so the rect cannot drift back to advance-wide silently.

Two seeds, because the dense graph's drawn set is not
reproducible: the force-directed layout relaxes on a timer and
its settled arrangement depends on the interleaving of data
reloads (a warm restart reuses mid-relax positions), so which
of the 14 facts collision planning keeps varies run to run —
the same 12 labels in one run, a different 12 in the next
(pre-fix and post-fix alike). The dense class therefore asserts
only per-label wholeness (metric + pixels, whichever labels
are drawn) and the forced/sticky labels that are never skipped
("You", the two hubs) plus a sane label count — never an exact
set. The sparse class seeds ONLY the three filed facts, so the
graph has no collisions and the filed labels are drawn
deterministically; it asserts they are drawn and whole.

Harness notes (also in repro_f202.py): the window is built
from the voice_assistant.gui package the shim synthesizes, not
the bare gui package — so the canvas module to patch is
voice_assistant.gui.memory_web_canvas2d, imported AFTER
WindowBase.setUp() installs the shim; patching the bare gui
module records nothing. The Memory page is pointed at a REAL
temp MemoryStore seeded with the section-E facts, by patching
both voice_assistant.gui.window_memory_web.memory_store and
voice_assistant.core.gui_facade.memory_store. The pixel check
reads the WINDOW grab (the true dark render): the canvas grab
alone cannot see the ink, because the canvas never fills its
own background and its grab has a light-grey backing. The
tests wait for canvas.is_settled() before reading labels:
before the layout settles the node positions depend on
wall-clock timing.
"""
import os
import pathlib
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    WindowBase = unittest.TestCase  # type: ignore[assignment]

# The exact section-E seed (r3e_screens_work/slice2/driver.py
# _seed_facts): same keys and categories, so the same labels are
# drawn in the same layout.
SEED_FACTS_DENSE = [
    ("general", "Favourite drink"),
    ("preferences", "Morning briefing"),
    ("general", "Gym days"),
    ("preferences", "Coffee order"),
    ("general", "Project deadline"),
    ("preferences", "Music while working"),
    ("general", "Sister's birthday"),
    ("preferences", "Wake word"),
    ("general", "Flight home"),
    ("preferences", "News digest"),
    ("general", "Dentist"),
    ("general", "Room temperature"),
    ("general", "Very long fact to test wrapping"),
    ("general", "IIT Bombay"),
]
# Sparse seed: only the filed facts, so the graph has no
# collisions and the filed labels are drawn deterministically.
SEED_FACTS_SPARSE = [
    ("preferences", "Wake word"),
    ("general", "Sister's birthday"),
    ("general", "Project deadline"),
]
FILED = ("Wake word", "Sister's birthday", "Project deadline")
# Forced (priority 0) and sticky-hub (priority 1) labels: the
# planner never skips these, in any run.
FORCED = ("You", "General", "Preferences")


def _pump(app, secs=0.4):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def _settle(app, canvas, timeout=30.0):
    """Pump until the force-directed layout settles. The canvas
    relaxes on a 16 ms timer until max displacement is below its
    epsilon (or MAX_TICKS); before it settles the node positions
    depend on wall-clock timing. (Note: __init__ sets _settled
    True, so this only means anything once data has been set and
    _restart_settle has run — which the page load does before
    this is called.)"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if canvas.is_settled():
            return True
        app.processEvents()
        time.sleep(0.02)
    return canvas.is_settled()


def _ink_span(text, font):
    """(ink_left, ink_right) relative to the advance origin, and
    the advance width — from the same font the painter uses."""
    from PySide6.QtGui import QFontMetrics

    m = QFontMetrics(font)
    adv = m.horizontalAdvance(text)
    br = m.boundingRect(text)
    return br.x(), br.x() + br.width(), adv


class TestLabelRectInSource(unittest.TestCase):
    """The planner measures the drawn ink — pinned at source
    level so it runs on both interpreters."""

    def test_planner_measures_ink_not_advance(self):
        src = (ROOT / "gui" / "memory_web_canvas2d.py").read_text(
            encoding="utf-8")
        self.assertIn("boundingRect(label)", src)
        self.assertNotIn(
            "text_w = float(QFontMetrics(font).horizontalAdvance(label))",
            src)

    def test_draw_still_clips_to_planned_rect(self):
        # The draw side is unchanged: _draw_label_text still
        # draws into the planned rect — the fix is the
        # measurement, not the draw.
        src = (ROOT / "gui" / "memory_web_graph.py").read_text(
            encoding="utf-8")
        self.assertIn("painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "
                      "text)", src)


@unittest.skipUnless(_QT, "Qt-only: the real MainWindow offscreen")
class _MemoryWebBase(unittest.TestCase):
    """Real MainWindow, Memory page seeded, Web view on.
    Subclasses set SEED_FACTS."""

    SEED_FACTS = SEED_FACTS_DENSE

    def setUp(self):
        self._base = WindowBase()
        self._base.setUp()
        self.window = self._base.window
        self.app = self._base.app
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="f202_suite_"))
        from core.memory.memory import MemoryStore

        store = MemoryStore(tmp / "assistant.db")
        for cat, key in self.SEED_FACTS:
            store.save_fact(cat, key, "probe value")
        import voice_assistant.gui.window_memory_web as _wmw
        import voice_assistant.core.gui_facade as _facade

        _wmw.memory_store = store
        _facade.memory_store = store
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — overlay best-effort
            pass
        _pump(self.app, 0.4)
        import voice_assistant.gui.memory_web_canvas2d as _canvas2d

        self._canvas2d = _canvas2d
        self._orig = _canvas2d._draw_label
        self._recorded = []

        def _rec(painter, rect, text, font, colour):
            self._recorded.append((rect, text, font))
            return self._orig(painter, rect, text, font, colour)

        _canvas2d._draw_label = _rec
        self.window._show_page(2)  # Memory page
        _pump(self.app, 0.5)
        self.window._set_memory_view("web")
        _pump(self.app, 0.8)
        self.canvas = self.window.memory_web.canvas2d
        self.assertTrue(_settle(self.app, self.canvas),
                        "canvas layout did not settle")

    def tearDown(self):
        try:
            self._canvas2d._draw_label = self._orig
        except Exception:  # noqa: BLE001 — teardown best-effort
            pass
        try:
            self._base.tearDown()
        except Exception:  # noqa: BLE001 — teardown best-effort
            pass

    def _paint_at(self, width):
        """Repaint at width; return (labels, window-grab image,
        canvas offset) from that exact paint."""
        from PySide6.QtCore import QPoint

        self.window.resize(width, 750)
        _pump(self.app, 0.6)
        self._recorded.clear()
        # repaint() is synchronous: it paints NOW. Grab the
        # window immediately, with NO pump, so the grab shows
        # exactly the paint whose rects were recorded.
        self.canvas.repaint()
        off = self.canvas.mapTo(self.window, QPoint(0, 0))
        img = self.window.grab().toImage()
        return list(self._recorded), img, off

    def _pixel_span(self, img, rect, off):
        """Light-ink span on the window grab in the label's band,
        in canvas coords (see repro_f202.py for the method)."""
        x0 = max(0, int(off.x() + rect.left()) - 6)
        x1 = min(img.width() - 1, int(off.x() + rect.right()) + 6)
        y0 = max(0, int(off.y() + rect.top()) + 1)
        y1 = min(img.height() - 1, int(off.y() + rect.bottom()) - 1)
        xs = []
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                c = img.pixelColor(x, y)
                r, g, b = c.red(), c.green(), c.blue()
                if (min(r, g, b) > 80
                        and max(r, g, b) - min(r, g, b) < 70):
                    xs.append(x - off.x())
        return (min(xs), max(xs)) if xs else None

    def _assert_ink_fits(self, labels, width):
        for rect, text, font in labels:
            ink_left, ink_right, adv = _ink_span(text, font)
            adv_start = rect.left() + (rect.width() - adv) / 2.0
            self.assertGreaterEqual(
                adv_start + ink_left, rect.left() - 0.5,
                f"{text!r} ink starts outside its rect "
                f"at width {width}")
            self.assertLessEqual(
                adv_start + ink_right, rect.right() + 0.5,
                f"{text!r} ink ends outside its rect at width {width}")

    def _assert_pixels_cover(self, labels, img, off, width):
        for rect, text, font in labels:
            span = self._pixel_span(img, rect, off)
            ink_left, ink_right, adv = _ink_span(text, font)
            adv_start = rect.left() + (rect.width() - adv) / 2.0
            exp_left = adv_start + ink_left
            exp_right = adv_start + ink_right
            self.assertIsNotNone(
                span, f"{text!r} no ink found at width {width}")
            # The right edge is the clip's edge (strict); the
            # left edge allows 4 px because a thin first stem
            # (the 'I' of "IIT Bombay") can render below the
            # measurement threshold at an unlucky subpixel
            # phase — the metric assertion above is the exact
            # left-edge proof, and a genuinely cut first glyph
            # loses a whole glyph width (~6 px).
            self.assertLessEqual(
                span[0], exp_left + 4,
                f"{text!r} first glyph cut at width {width}: "
                f"drawn starts {span[0]}, expected {exp_left:.1f}")
            self.assertGreaterEqual(
                span[1], exp_right - 2,
                f"{text!r} last glyph cut at width {width}: "
                f"drawn ends {span[1]}, expected {exp_right:.1f}")


@unittest.skipUnless(_QT, "Qt-only: the real MainWindow offscreen")
class TestMemoryWebLabels(_MemoryWebBase):
    """Dense section-E graph: every drawn label reads whole."""

    SEED_FACTS = SEED_FACTS_DENSE

    def test_label_ink_fits_rect_at_widths(self):
        for width in (908, 1024, 1280, 1920):
            with self.subTest(width=width):
                labels, _, _ = self._paint_at(width)
                self._assert_ink_fits(labels, width)

    def test_label_pixels_cover_ink_at_widths(self):
        for width in (908, 1024, 1280, 1920):
            with self.subTest(width=width):
                labels, img, off = self._paint_at(width)
                self._assert_pixels_cover(labels, img, off, width)

    def test_forced_labels_always_drawn(self):
        # "You" is forced and the hubs are sticky: the planner
        # never skips them, in any run. (The fact set varies
        # run to run — see the module docstring — so no exact
        # set is asserted here.)
        for width in (908, 1024, 1280, 1920):
            with self.subTest(width=width):
                labels, _, _ = self._paint_at(width)
                drawn = {t for _, t, _ in labels}
                for forced in FORCED:
                    self.assertIn(forced, drawn,
                                  f"{forced} not drawn at width {width}")

    def test_label_count_sane(self):
        # The dense graph draws 12 labels; the fix widens the
        # planned rects by ~5 px, which must not collapse the
        # graph through extra collisions.
        for width in (908, 1024, 1280, 1920):
            with self.subTest(width=width):
                labels, _, _ = self._paint_at(width)
                self.assertGreaterEqual(
                    len({t for _, t, _ in labels}), 10,
                    f"only {len({t for _, t, _ in labels})} labels "
                    f"drawn at width {width}")


@unittest.skipUnless(_QT, "Qt-only: the real MainWindow offscreen")
class TestFiledLabelsSparse(_MemoryWebBase):
    """Sparse graph (only the filed facts): the filed labels are
    drawn deterministically and read whole."""

    SEED_FACTS = SEED_FACTS_SPARSE

    def test_filed_labels_drawn(self):
        labels, _, _ = self._paint_at(1280)
        drawn = {t for _, t, _ in labels}
        for filed in FILED:
            self.assertIn(filed, drawn, f"{filed} is not drawn at 1280")

    def test_filed_labels_whole_at_widths(self):
        for width in (908, 1024, 1280, 1920):
            with self.subTest(width=width):
                labels, img, off = self._paint_at(width)
                drawn = {t for _, t, _ in labels}
                for filed in FILED:
                    self.assertIn(filed, drawn,
                                  f"{filed} not drawn at width {width}")
                self._assert_ink_fits(labels, width)
                self._assert_pixels_cover(labels, img, off, width)


if __name__ == "__main__":
    unittest.main()
