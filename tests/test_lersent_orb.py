"""B-R1-5 LerSent liquid-glass orb — the glass shows VYUK's real
state, honestly, on a timer, and the state reads at a glance.

Gap (proved first): today's Aura (gui/visualizer_paint.py::_paint_aura)
is a luminous sphere WITHOUT glass — a matte radial-gradient body in
a perfect ellipse, a bright core, a single-colour fresnel rim; no
glass shell, no contour deform, no chromatic fringe, no single
parameter snapshot. The LerSent pattern (LerSent001/orb, MIT) is
re-implemented in gui/visualizer_glass.py and wired into the two Aura
paint sites (the home orb + the mini orb).

Qt tests skip on system python3 (no PySide6 there); AST checks run
on both interpreters. No real mic, no real audio — fake session
state + fake energy only.
"""

import ast
import math
import sys
import time
import types
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent


# T-3 (debug pass): gui modules import the frozen voice_assistant.*
# package names (gui/mini_orb.py does `from voice_assistant.core.
# gui_facade import ...`) — the alias is registered at runtime by
# doctor._register_package(), so tests register it themselves.
# Snapshot/restore discipline (L3): register only keys that are
# absent, and tearDownModule removes exactly the keys this module
# added — never delete-all.
_ADDED_VA_MODULES = []


def setUpModule():
    for key, path in (
        ("voice_assistant", str(TREE)),
        ("voice_assistant.core", str(TREE / "core")),
        ("voice_assistant.gui", str(TREE / "gui")),
    ):
        if key not in sys.modules:
            pkg = types.ModuleType(key)
            pkg.__path__ = [path]
            sys.modules[key] = pkg
            _ADDED_VA_MODULES.append(key)


def tearDownModule():
    for key in _ADDED_VA_MODULES:
        sys.modules.pop(key, None)
    _ADDED_VA_MODULES.clear()

try:
    from PySide6.QtWidgets import QApplication

    _QT_IMPORTABLE = True
except Exception:  # noqa: BLE001 — system python3 has no PySide6
    _QT_IMPORTABLE = False


def _qt_available():
    if not _QT_IMPORTABLE:
        return False
    try:
        if QApplication.instance() is None:
            QApplication([])
        return True
    except Exception:  # noqa: BLE001
        return False


QT_AVAILABLE = _qt_available()

LIVE_STATES = ("IDLE", "LISTENING", "THINKING", "SPEAKING")


def _src(rel):
    return (TREE / rel).read_text(encoding="utf-8")


def _close(a, b, tol=0.05):
    return abs(a - b) <= tol


class GlassAstTests(unittest.TestCase):
    """Run on both interpreters — no Qt needed."""

    def test_no_blocking_calls_in_glass_module(self):
        src = _src("gui/visualizer_glass.py")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                self.assertNotIn(
                    name, ("sleep", "msleep", "usleep", "wait"),
                    "the glass must never block the event loop")

    def test_home_paint_calls_the_glass(self):
        src = _src("gui/visualizer_paint.py")
        self.assertIn("paint_glass_layers(", src)
        self.assertIn("glass_contour_path(", src)
        self.assertIn("glass_contour_arc(", src)

    def test_mini_paint_calls_the_glass(self):
        src = _src("gui/mini_orb.py")
        self.assertIn("paint_glass_layers(", src)
        self.assertIn("glass_contour_path(", src)
        self.assertIn("self._glass = GlassState()", src)

    def test_home_orb_call_site_exists(self):
        # The glass rides the existing home orb — window_shell_assistant
        # already instantiates OrbVisualizer on Home (B-R1-5 wires the
        # treatment into it; it does not add a second call site).
        src = _src("gui/window_shell_assistant.py")
        self.assertIn("self.visualizer = OrbVisualizer()", src)

    def test_glass_module_registers_no_tools(self):
        src = _src("gui/visualizer_glass.py")
        self.assertNotIn("register_tool", src)
        self.assertNotIn("TOOLS =", src)


@unittest.skipUnless(QT_AVAILABLE, "PySide6 required")
class GlassStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gui.visualizer_glass import GlassParams, GlassState

        cls.GlassParams = GlassParams
        cls.GlassState = GlassState

    def test_fresh_orb_shows_its_real_state(self):
        # A fresh orb's state is IDLE (visualizer_state.py) — its
        # glass eases toward the IDLE targets, never listening.
        from gui.visualizer import OrbVisualizer

        orb = OrbVisualizer()
        orb.timer.stop()
        self.assertEqual(orb.state, "IDLE")
        for _ in range(120):
            orb._animate_frame()
        snap = orb._glass.snapshot()
        idle = self.GlassState.signature("IDLE")
        for got, want in zip(snap.as_tuple(), idle):
            self.assertTrue(_close(got, want),
                            f"fresh orb glass {got} != IDLE {want}")

    def test_unknown_state_falls_back_to_idle(self):
        params = self.GlassState.signature("BOGUS")
        self.assertEqual(params, self.GlassState.signature("IDLE"))
        glass = self.GlassState()
        for _ in range(120):
            glass.advance(None, 0.0, 1.0)
        snap = glass.snapshot()
        for got, want in zip(snap.as_tuple(),
                             self.GlassState.signature("IDLE")):
            self.assertTrue(_close(got, want, tol=0.08),
                            f"unknown state glass {got} != IDLE {want}")

    def test_state_transition_is_honest(self):
        # The glass follows VYUK's state: SPEAKING then IDLE, each
        # eased to its own targets — it changes when VYUK changes.
        glass = self.GlassState()
        for _ in range(120):
            glass.advance("SPEAKING", 0.0, 1.0)
        snap = glass.snapshot()
        for got, want in zip(snap.as_tuple(),
                             self.GlassState.signature("SPEAKING")):
            self.assertTrue(_close(got, want),
                            f"speaking glass {got} != {want}")
        for _ in range(120):
            glass.advance("IDLE", 0.0, 1.0)
        snap = glass.snapshot()
        for got, want in zip(snap.as_tuple(),
                             self.GlassState.signature("IDLE")):
            self.assertTrue(_close(got, want),
                            f"idle glass {got} != {want}")

    def test_audio_lift_eases_back_to_base(self):
        # LerSent's rule: audio lifts the targets after the state
        # targets; silence eases the glass back to its base.
        glass = self.GlassState()
        for _ in range(60):
            glass.advance("SPEAKING", 0.8, 1.0)
        lifted = glass.snapshot().sheen
        self.assertGreater(lifted, 0.85,
                           "voice energy must lift the sheen")
        for _ in range(120):
            glass.advance("SPEAKING", 0.0, 1.0)
        settled = glass.snapshot().sheen
        self.assertTrue(_close(settled, 0.70),
                        f"silence must restore base sheen, got {settled}")

    def test_signatures_are_pairwise_distinct(self):
        # The state reads at a glance: no two live states share a
        # glass signature.
        sigs = {s: self.GlassState.signature(s) for s in LIVE_STATES}
        seen = set()
        for state, sig in sigs.items():
            self.assertNotIn(sig, seen, f"{state} shares a signature")
            seen.add(sig)

    def test_contour_is_bounded(self):
        from gui.visualizer_glass import glass_contour_path

        params = self.GlassParams(contour_deform=0.0, warp=0.0)
        path = glass_contour_path(100.0, 100.0, 50.0, params, 0.0)
        for i in range(path.elementCount()):
            el = path.elementAt(i)
            r = math.hypot(el.x - 100.0, el.y - 100.0)
            self.assertTrue(_close(r, 50.0, tol=1e-6),
                            f"deform 0 must be a circle, r={r}")
        for state in LIVE_STATES:
            target = self.GlassState.signature(state)
            p = self.GlassParams(*target)
            path = glass_contour_path(100.0, 100.0, 50.0, p, 0.7)
            worst = 0.0
            for i in range(path.elementCount()):
                el = path.elementAt(i)
                r = math.hypot(el.x - 100.0, el.y - 100.0)
                worst = max(worst, abs(r - 50.0))
            self.assertLessEqual(
                worst, 0.07 * 50.0,
                f"{state} wobble {worst} breaks the silhouette")

    def test_arc_sits_on_the_contour(self):
        from gui.visualizer_glass import (
            _contour_radius,
            glass_contour_arc,
        )

        params = self.GlassParams(contour_deform=0.55, warp=0.35)
        arc = glass_contour_arc(100.0, 100.0, 50.0, params, 0.7,
                                -145.0, 110.0)
        self.assertGreater(arc.elementCount(), 10)
        for i in range(arc.elementCount()):
            el = arc.elementAt(i)
            theta = math.atan2(el.y - 100.0, el.x - 100.0)
            want = _contour_radius(50.0, theta, params, 0.7)
            got = math.hypot(el.x - 100.0, el.y - 100.0)
            self.assertTrue(_close(got, want, tol=1.0),
                            f"rim arc off the contour: {got} != {want}")

    def test_params_clamp(self):
        params = self.GlassParams(contour_deform=5.0, sheen=-1.0,
                                  gloss=float("nan"))
        self.assertEqual(params.contour_deform, 1.0)
        self.assertEqual(params.sheen, 0.0)
        self.assertEqual(params.gloss, 0.0)

    def test_animate_never_blocks(self):
        from gui.visualizer import OrbVisualizer

        orb = OrbVisualizer()
        orb.timer.stop()
        orb.set_state("SPEAKING")
        orb.update_audio_data(0.8, [0.8] * 16)
        start = time.monotonic()
        for _ in range(200):
            orb._animate_frame()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 2.0,
                        f"200 frames took {elapsed:.2f}s — a stutter")

    def test_paint_completes_in_every_live_state(self):
        from gui.visualizer import OrbVisualizer

        orb = OrbVisualizer()
        orb.timer.stop()
        orb.resize(360, 360)
        orb.show()
        for state, energy in (("IDLE", 0.0), ("LISTENING", 0.5),
                              ("THINKING", 0.0), ("SPEAKING", 0.8)):
            orb.set_state(state)
            orb.update_audio_data(energy, [energy] * 16)
            for _ in range(30):
                orb._animate_frame()
            pix = orb.grab()
            self.assertFalse(pix.isNull(), f"{state} painted nothing")

    def test_mini_orb_glass_follows_its_state(self):
        from gui.mini_orb import _AuraMiniCanvas

        canvas = _AuraMiniCanvas()
        canvas._timer.stop()
        canvas.set_state("SPEAKING")
        canvas._target_energy = 0.0
        for _ in range(120):
            canvas._tick()
        snap = canvas._glass.snapshot()
        for got, want in zip(snap.as_tuple(),
                             self.GlassState.signature("SPEAKING")):
            self.assertTrue(_close(got, want, tol=0.08),
                            f"mini speaking glass {got} != {want}")

    def test_orb_shows_the_session_state(self):
        # The glass reads the session state through the same
        # set_state() channel window_session_status.py uses — the
        # state shown is the state VYUK is in.
        from gui.visualizer import OrbVisualizer

        orb = OrbVisualizer()
        orb.timer.stop()
        orb.set_state("LISTENING")
        orb.update_audio_data(0.0, [0.0] * 16)
        for _ in range(120):
            orb._animate_frame()
        snap = orb._glass.snapshot()
        for got, want in zip(snap.as_tuple(),
                             self.GlassState.signature("LISTENING")):
            self.assertTrue(_close(got, want),
                            f"listening glass {got} != {want}")


if __name__ == "__main__":
    unittest.main()
