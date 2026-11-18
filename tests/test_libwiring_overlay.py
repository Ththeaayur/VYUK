"""Libwiring sweep — agent overlay product wiring.

Until this sweep, gui/agent_overlay.py existed only in tests: no
product module instantiated AgentOverlay. This suite proves the
wiring added in the sweep:

- `create_and_attach_overlay()` attaches a real overlay to the
  state bus (producer: bus events; consumer: overlay model +
  windows; guard: never raises, affinity-gated show).
- Bus events drive the model (target rect + action chip) via
  explicit attach — the same pattern as Phase F. The driving
  path is proven through attach_to_bus on a bus the test
  holds, because create_and_attach_overlay's bus is
  layout-dependent by design (dual-path, matching main.py):
  in a hybrid lab layout (a voice_assistant shim beside gui.*)
  its bus can live in the other namespace than the test's
  bus, and asserting cross-namespace delivery would test the
  test harness, not the product — production has one
  namespace, where create + producers share bus and classes.
- On Linux the honest state is attached-but-hidden BY DESIGN
  (guide D8: no display affinity, windows stay hidden) — hidden
  is not an error, and the status says so.
- Killing the overlay closes windows and changes no action state.
- main.py actually calls the wiring (presence test: it proves
  the call site exists, not that a full launch runs).

Qt tests skip on system python3 (same pattern as Phase F): a
QApplication only exists in the Qt venv. Pixel-level proof on
real Windows is field-owed, never claimed here.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

try:
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except Exception:  # pragma: no cover - system interpreter has no Qt
    QT_AVAILABLE = False

from gui import agent_overlay as ov
from gui import state_bus


class TestOverlayWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QT_AVAILABLE:
            raise unittest.SkipTest("Qt unavailable on this interpreter")
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        state_bus.reset_state_bus()
        self._overlay = None

    def tearDown(self):
        if self._overlay is not None:
            try:
                self._overlay.kill()
            except Exception:  # noqa: BLE001 — teardown never raises
                pass
        state_bus.reset_state_bus()

    def test_create_attaches_with_honest_status(self):
        overlay, status = ov.create_and_attach_overlay()
        self._overlay = overlay
        self.assertIsNotNone(overlay)
        self.assertTrue(status["attached"])
        self.assertEqual(overlay.window_count(), status["windows_built"])
        if not sys.platform.startswith("win"):
            # No display affinity on Linux: windows stay hidden BY
            # DESIGN (guide D8). Shown 0 with that reason is the
            # correct state, not a failure.
            self.assertEqual(status["windows_shown"], 0)
            self.assertIn("hidden by design", status["reason"])
            self.assertEqual(overlay.shown_count(), 0)

    def test_attach_drives_model(self):
        # Event-driving proof via explicit attach on a bus this
        # test holds (Phase F pattern): create's bus is
        # layout-dependent (see module docstring), so the
        # driving proof lives here, not on create's bus.
        overlay = ov.AgentOverlay()
        self._overlay = overlay
        bus = state_bus.get_state_bus()
        overlay.attach_to_bus(bus)
        bus.emit(state_bus.TargetRect(
            x=10, y=20, w=100, h=40, role="Button", name="Save"))
        bus.emit(state_bus.AgentAction(kind="click", label="Save"))
        bus.emit(state_bus.InputAcquired(source="test"))
        bus.drain()
        self.assertEqual(
            overlay.model.target_rect, (10, 20, 100, 40, "Button", "Save"))
        self.assertEqual(overlay.model.chip, "CLICK \u203a Save")

    def test_kill_closes_windows_not_action_state(self):
        overlay = ov.AgentOverlay()
        self._overlay = overlay
        bus = state_bus.get_state_bus()
        overlay.attach_to_bus(bus)
        overlay.build_windows()
        self.assertGreater(overlay.window_count(), 0)
        bus.emit(state_bus.AgentAction(kind="click", label="Save"))
        bus.drain()
        self.assertEqual(overlay.model.chip, "CLICK \u203a Save")
        overlay.kill()
        self.assertEqual(overlay.window_count(), 0)
        # Killing the overlay changes nothing about the action.
        self.assertEqual(overlay.model.chip, "CLICK \u203a Save")
        self._overlay = None  # already killed

    def test_main_wiring_present(self):
        main_py = pathlib.Path(__file__).resolve().parent.parent / "main.py"
        text = main_py.read_text(encoding="utf-8")
        self.assertIn("create_and_attach_overlay", text)
        self.assertIn("window.agent_overlay", text)


if __name__ == "__main__":
    unittest.main()
