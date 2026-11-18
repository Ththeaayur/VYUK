"""Command Deck state bus (Phase A): queue, isolation, non-blocking delivery.

Runs on BOTH interpreters. The pure queue/isolation logic runs
everywhere; the Qt wakeup path runs only where PySide6 + a
QApplication can be created (venv-qt + syslibs env) and skips
elsewhere. Lab honesty: offscreen delivery proves the mechanism,
not real-Windows thread behaviour — field proof stays owed.
"""

import os
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui import state_bus
from gui.state_bus import (
    AppState,
    CaptureFinished,
    CaptureStarted,
    FailsafeTripped,
    InputAcquired,
    MissionState,
    SensorState,
    StateBus,
    StepEvent,
    TargetRect,
    ToolFinished,
    ToolStarted,
    Verified,
    get_state_bus,
    reset_state_bus,
)

_QT_APP = None


def _qt_app():
    """Shared offscreen QApplication, or None when Qt cannot start."""
    global _QT_APP
    if not state_bus.QT_AVAILABLE:
        return None
    if _QT_APP is not None:
        return _QT_APP
    try:
        from PySide6.QtWidgets import QApplication

        _QT_APP = QApplication.instance() or QApplication([])
        return _QT_APP
    except Exception:
        return None


class TestModels(unittest.TestCase):
    def test_app_state_defaults(self):
        s = AppState(status="STANDING BY")
        self.assertEqual(s.colour, "cyan")
        self.assertEqual(s.glyph, "ring")
        self.assertEqual(s.mode, "LOCAL CORE")
        self.assertFalse(s.mic_muted)

    def test_models_frozen(self):
        s = AppState(status="LISTENING")
        with self.assertRaises(AttributeError):
            s.status = "NOPE"

    def test_sensor_source_empty_means_unmeasured(self):
        # Honesty rule: no source -> the consumer must render NOT
        # MEASURED. The bus carries the source; it never invents one.
        s = SensorState(name="LINK")
        self.assertEqual(s.source, "")

    def test_event_catalogue_complete(self):
        names = {t.__name__ for t in state_bus.EVENT_TYPES}
        for want in (
            "AppState",
            "SensorState",
            "MissionState",
            "StepEvent",
            "ToolStarted",
            "ToolFinished",
            "InputAcquired",
            "InputReleased",
            "TargetRect",
            "FailsafeTripped",
            "CaptureStarted",
            "CaptureFinished",
            "Verified",
        ):
            self.assertIn(want, names)


class TestBusPure(unittest.TestCase):
    def setUp(self):
        self.bus = StateBus()

    def test_emit_queues_without_delivering(self):
        seen = []
        self.bus.subscribe(ToolStarted, seen.append)
        self.bus.emit(ToolStarted(kind="click", target="Save"))
        self.assertEqual(seen, [])
        self.assertEqual(self.bus.queued_count(), 1)
        self.bus.drain()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].kind, "click")

    def test_delivery_order_preserved(self):
        seen = []
        self.bus.subscribe(StepEvent, lambda e: seen.append(e.n))
        for n in (1, 2, 3):
            self.bus.emit(StepEvent(n=n, m=3))
        self.bus.drain()
        self.assertEqual(seen, [1, 2, 3])

    def test_raising_subscriber_does_not_stop_others(self):
        seen = []

        def bad(_e):
            raise RuntimeError("boom")

        self.bus.subscribe(ToolFinished, bad)
        self.bus.subscribe(ToolFinished, seen.append)
        self.bus.emit(ToolFinished(status="success"))
        self.bus.drain()  # must not raise, and seen must still fill
        self.assertEqual(len(seen), 1)

    def test_unsubscribe(self):
        seen = []
        tok = self.bus.subscribe(CaptureStarted, seen.append)
        self.assertTrue(self.bus.unsubscribe(tok))
        self.assertFalse(self.bus.unsubscribe(tok))
        self.bus.emit(CaptureStarted(scope="screen"))
        self.bus.drain()
        self.assertEqual(seen, [])

    def test_latest_state_snapshot(self):
        self.assertIsNone(self.bus.latest_state(AppState))
        self.bus.emit(AppState(status="LISTENING", colour="cyan"))
        self.bus.emit(AppState(status="PROCESSING", colour="ice"))
        self.assertEqual(self.bus.latest_state(AppState).status, "PROCESSING")
        # Events are not state snapshots.
        self.bus.emit(StepEvent(n=1, m=2))
        self.assertIsNone(self.bus.latest_state(StepEvent))

    def test_emit_from_thread_then_drain(self):
        seen = []
        self.bus.subscribe(TargetRect, seen.append)

        def worker():
            self.bus.emit(TargetRect(x=1, y=2, w=3, h=4, role="BUTTON"))

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        self.assertEqual(seen, [])
        self.bus.drain()
        self.assertEqual(len(seen), 1)

    def test_singleton_reset_clears_subscriptions(self):
        bus = get_state_bus()
        seen = []
        bus.subscribe(FailsafeTripped, seen.append)
        reset_state_bus()
        fresh = get_state_bus()
        fresh.emit(FailsafeTripped(reason="mouse moved"))
        fresh.drain()
        self.assertEqual(seen, [])


@unittest.skipUnless(_qt_app() is not None, "Qt unavailable in this env")
class TestBusQtWakeup(unittest.TestCase):
    def setUp(self):
        self.app = _qt_app()
        self.bus = StateBus()

    def test_emit_returns_before_delivery(self):
        # The safety rule, without timing flakiness: delivery is
        # queued, so a subscriber has NOT run immediately after emit.
        seen = []
        self.bus.subscribe(Verified, seen.append)
        self.bus.emit(Verified(evidence="ok"))
        self.assertEqual(seen, [])
        self.app.processEvents()
        self.assertEqual(len(seen), 1)

    def test_qt_path_isolates_raising_subscriber(self):
        seen = []

        def bad(_e):
            raise RuntimeError("boom")

        self.bus.subscribe(ToolStarted, bad)
        self.bus.subscribe(ToolStarted, seen.append)
        self.bus.emit(ToolStarted(kind="type"))
        self.app.processEvents()
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
