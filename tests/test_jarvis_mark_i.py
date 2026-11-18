"""R7-2 tests: the jarvis-mark-i pattern as VYUK's own code.

* The voice-reactive particle sphere (gui/orb_sphere.py): pure
  math (unit sphere, determinism, swell with voice energy, depth
  order, frozen rotation) + the QPainter entry (Aura-blue-only
  pixels, swell visible in the paint, both orb paint paths).
* The cinematic boot screen (gui/boot_screen.py): pure boot_lines
  (order, beat headers, verdict tags, greeting last) + the
  BootScreen widget (show/hide, transparent for mouse input,
  paints without error).
* The mini-orb prerequisite: _paint_aura no longer raises the
  UnboundLocalError (body_r used before assignment) and paints.
* Guardrails: no Marvel strings, no new colour literals in the
  new gui files.

Qt tests skip without PySide6 (the test_journey_gui_path
pattern); the pure-math tests run on both interpreters.
"""

import ast
import math
import os
import pathlib
import re
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TREE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TREE))


# T-3 (debug pass): gui modules import the frozen voice_assistant.*
# package names (gui/mini_orb.py does `from voice_assistant.core.
# gui_facade import ...`) — the alias is registered at runtime by
# doctor._register_package(), so tests register it themselves.
# Snapshot/restore discipline (L3): register only keys that are
# absent, and tearDownModule removes exactly the keys this module
# added — never delete-all.
import types as _types

_ADDED_VA_MODULES = []


def setUpModule():
    for key, path in (
        ("voice_assistant", str(_TREE)),
        ("voice_assistant.core", str(_TREE / "core")),
        ("voice_assistant.gui", str(_TREE / "gui")),
    ):
        if key not in sys.modules:
            pkg = _types.ModuleType(key)
            pkg.__path__ = [path]
            sys.modules[key] = pkg
            _ADDED_VA_MODULES.append(key)


def tearDownModule():
    for key in _ADDED_VA_MODULES:
        sys.modules.pop(key, None)
    _ADDED_VA_MODULES.clear()

try:
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    _QT_AVAILABLE = True
except Exception:  # noqa: BLE001 — Qt optional, skip behavioural
    _QT_AVAILABLE = False

from gui.orb_sphere import (
    SPHERE_PARTICLE_COUNTS,
    SPHERE_SWELL_MAX,
    SphereState,
    fibonacci_points,
    paint_particle_sphere,
    project_points,
    sphere_swell_radius,
)

if _QT_AVAILABLE:
    _HI = QColor(230, 251, 255)
    _MID = QColor(53, 214, 255)
    _EDGE = QColor(61, 139, 255)
else:
    _HI = _MID = _EDGE = None


def _app():
    return QApplication.instance() or QApplication([])


class TestSphereMath(unittest.TestCase):
    """The lattice + projection: pure, no Qt needed."""

    def test_points_on_unit_sphere(self):
        for count in (160, 320, 480):
            for (x, y, z) in fibonacci_points(count):
                self.assertAlmostEqual(x * x + y * y + z * z, 1.0,
                                       places=9)

    def test_points_deterministic(self):
        self.assertEqual(fibonacci_points(480), fibonacci_points(480))

    def test_swell_radius_grows_with_energy(self):
        self.assertEqual(sphere_swell_radius(0.0), 1.0)
        self.assertEqual(sphere_swell_radius(1.0),
                         1.0 + SPHERE_SWELL_MAX)
        self.assertGreater(sphere_swell_radius(0.8),
                           sphere_swell_radius(0.2))

    def test_projection_sorted_far_to_near(self):
        pts = fibonacci_points(480)
        projected = project_points(pts, 0.7, 0.35, 0.5)
        depths = [d for (_, _, d) in projected]
        self.assertEqual(depths, sorted(depths))
        self.assertTrue(all(0.0 <= d <= 1.0 for d in depths))

    def test_projection_swell_enlarges_radius(self):
        pts = fibonacci_points(480)
        r0 = max((x * x + y * y) ** 0.5
                 for (x, y, _) in project_points(pts, 0.0, 0.35, 0.0))
        r1 = max((x * x + y * y) ** 0.5
                 for (x, y, _) in project_points(pts, 0.0, 0.35, 1.0))
        self.assertGreater(r1, r0)
        self.assertAlmostEqual(r1 / r0, 1.0 + SPHERE_SWELL_MAX, places=2)

    def test_state_advance_turns_and_eases_swell(self):
        state = SphereState()
        state.advance(1.0, 1.0)
        self.assertGreater(state.yaw, 0.0)
        self.assertGreater(state.swell, 0.0)
        self.assertLessEqual(state.swell, 1.0)
        # Silence settles the swell back down.
        for _ in range(600):
            state.advance(1.0 / 60.0, 0.0)
        self.assertLess(state.swell, 0.01)

    def test_state_frozen_stills_the_turn(self):
        state = SphereState()
        state.advance(1.0, 0.0, frozen=True)
        self.assertEqual(state.yaw, 0.0)


@unittest.skipUnless(_QT_AVAILABLE, "Qt not available")
class TestSpherePaint(unittest.TestCase):
    """The QPainter entry: Aura blue only, swell visible."""

    def test_paint_aura_blue_only(self):
        _app()
        state = SphereState()
        state.swell = 0.8
        img = QImage(400, 400, QImage.Format_ARGB32)
        img.fill(0)
        painter = QPainter(img)
        painted = paint_particle_sphere(painter, 200.0, 200.0, 136.0,
                                        state, 0.8, _HI, _MID, _EDGE,
                                        1.0, 0)
        painter.end()
        self.assertEqual(painted, SPHERE_PARTICLE_COUNTS[0])
        # Every painted pixel is Aura blue: blue dominates red/green.
        seen = 0
        for x in range(0, 400, 2):
            for y in range(0, 400, 2):
                col = img.pixelColor(x, y)
                if col.alpha() > 0:
                    seen += 1
                    self.assertGreater(col.blue(), col.red())
                    self.assertGreater(col.blue(), col.green())
        self.assertGreater(seen, 100)

    def test_paint_swell_visible(self):
        _app()
        img0 = QImage(400, 400, QImage.Format_ARGB32)
        img0.fill(0)
        img1 = QImage(400, 400, QImage.Format_ARGB32)
        img1.fill(0)
        for img, swell in ((img0, 0.0), (img1, 1.0)):
            state = SphereState()
            state.swell = swell
            painter = QPainter(img)
            paint_particle_sphere(painter, 200.0, 200.0, 136.0,
                                  state, swell, _HI, _MID, _EDGE,
                                  1.0, 0)
            painter.end()
        # The swollen sphere's painted extent is wider.
        def extent(image):
            xs = [x for x in range(400)
                  for y in range(400)
                  if image.pixelColor(x, y).alpha() > 0]
            return (max(xs) - min(xs)) if xs else 0
        self.assertGreater(extent(img1), extent(img0))


@unittest.skipUnless(_QT_AVAILABLE, "Qt not available")
class TestOrbWiring(unittest.TestCase):
    """Both orb paint paths carry the sphere, live."""

    def test_home_orb_has_sphere_state(self):
        _app()
        from gui.visualizer import OrbVisualizer
        orb = OrbVisualizer()
        self.assertIsNotNone(getattr(orb, "_sphere", None))
        orb.update_audio_data(0.8)
        before = orb._sphere.swell
        orb._animate_frame(1.0 / 60.0)
        self.assertGreaterEqual(orb._sphere.swell, before)

    def test_mini_orb_paints_and_has_sphere(self):
        # R7-2 prerequisite pin: mini _paint_aura used body_r
        # before its assignment (UnboundLocalError on every paint
        # — the mini orb painted nothing). It must paint now.
        _app()
        from gui.mini_orb import _AuraMiniCanvas
        canvas = _AuraMiniCanvas()
        canvas.resize(92, 92)
        canvas.set_state("IDLE")
        canvas.update_audio_data(0.5)
        self.assertIsNotNone(getattr(canvas, "_sphere", None))
        img = QImage(92, 92, QImage.Format_ARGB32)
        img.fill(0)
        painter = QPainter(img)
        hi, mid, edge = canvas._palette()
        canvas._paint_aura(painter, 46.0, 46.0, 46.0, hi, mid, edge,
                           0.5, 1.0)  # must not raise
        painter.end()
        painted = sum(
            1 for x in range(92) for y in range(92)
            if img.pixelColor(x, y).alpha() > 0)
        self.assertGreater(painted, 1000)


class TestBootLines(unittest.TestCase):
    """boot_lines: order, beat headers, verdict tags, greeting last."""

    def _report(self):
        return {
            "version": "6.2.0",
            "beats": ["bar", "rings", "suit", "reactor"],
            "stages": [
                {"id": "identity", "label": "Identity",
                 "beat": "bar", "verdict": "ready", "reason": "",
                 "checked": True},
                {"id": "mic", "label": "Microphone",
                 "beat": "rings", "verdict": "degraded",
                 "reason": "no mic found", "checked": True},
                {"id": "voice", "label": "Voice engine",
                 "beat": "suit", "verdict": "unchecked", "reason": "",
                 "checked": False},
            ],
            "greeting": "Systems ready.",
        }

    def test_order_and_greeting_last(self):
        from gui.boot_lines import boot_lines
        lines = boot_lines(self._report())
        kinds = [k for (_, k) in lines]
        self.assertEqual(kinds[0], "header")
        self.assertEqual(kinds[-1], "greeting")
        self.assertIn("beat", kinds)
        self.assertIn("stage", kinds)
        # Beat headers precede their stages, in beat order.
        texts = [t for (t, _) in lines]
        self.assertLess(texts.index("── BAR ──"),
                        texts.index("── RINGS ──"))
        self.assertLess(texts.index("── RINGS ──"),
                        texts.index("── SUIT ──"))

    def test_verdict_tags(self):
        from gui.boot_lines import boot_lines
        texts = [t for (t, _) in boot_lines(self._report())]
        self.assertTrue(any("[ OK ] Identity" in t for t in texts))
        self.assertTrue(any("[WARN] Microphone" in t for t in texts))
        self.assertTrue(any("no mic found" in t for t in texts))
        self.assertTrue(any("[    ] Voice engine" in t for t in texts))

    def test_progress_counts_checked(self):
        from gui.boot_lines import boot_progress
        self.assertEqual(boot_progress(self._report()), (2, 3))
        self.assertEqual(boot_progress({}), (0, 0))
        self.assertEqual(boot_progress(None), (0, 0))


@unittest.skipUnless(_QT_AVAILABLE, "Qt not available")
class TestBootScreenWidget(unittest.TestCase):
    """The overlay: show/hide, transparent for input, paints."""

    def test_show_hide_and_transparent(self):
        _app()
        from PySide6.QtCore import QPoint, Qt
        from gui.boot_screen import BootScreen
        from core.desktop.boot_sequence import run_boot
        screen = BootScreen()
        screen.resize(600, 400)
        screen.set_palette(_HI, _MID, _EDGE)
        self.assertFalse(screen.isVisible())
        self.assertTrue(screen.testAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        screen.show_report(run_boot())
        self.assertTrue(screen.isVisible())
        img = QImage(600, 400, QImage.Format_ARGB32)
        img.fill(0)
        painter = QPainter(img)
        screen.render(painter, QPoint())
        painter.end()
        painted = sum(
            1 for x in range(0, 600, 3) for y in range(0, 400, 3)
            if img.pixelColor(x, y).alpha() > 0)
        self.assertGreater(painted, 100)
        screen.hide_screen()
        self.assertFalse(screen.isVisible())


class TestGuardrails(unittest.TestCase):
    """No Marvel marks, no new colour literals in the new files."""

    def test_no_marvel_strings(self):
        # The disclaimer docstrings NAME the marks to forbid them;
        # the check is that no branding use survives outside the
        # docstring (no arc-reactor imagery, no INITIALIZE copy).
        for name in ("gui/orb_sphere.py", "gui/boot_screen.py"):
            src = (_TREE / name).read_text(encoding="utf-8")
            tree = ast.parse(src)
            doc = ast.get_docstring(tree) or ""
            body = src.replace(doc, "")
            lowered = body.lower()
            for banned in ("arc reactor", "iron man", "stark",
                           "initialize", "marvel"):
                self.assertNotIn(banned, lowered,
                                 f"{name} must not carry {banned!r}")

    def test_no_new_colour_literals(self):
        hex_re = re.compile(r"#(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{6}"
                            r"|[0-9A-Fa-f]{3})\b")
        rgb_re = re.compile(r"rgba?\s*\([^)]*\)")
        for name in ("gui/orb_sphere.py", "gui/boot_screen.py"):
            src = (_TREE / name).read_text(encoding="utf-8")
            self.assertEqual(hex_re.findall(src), [],
                             f"{name} must not carry hex literals")
            self.assertEqual(rgb_re.findall(src), [],
                             f"{name} must not carry rgb() literals")


if __name__ == "__main__":
    unittest.main()
