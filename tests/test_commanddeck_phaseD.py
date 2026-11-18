"""Command Deck Phase D tests: state-bus producers, §7 map, strip,
shell chrome, Verifying-hide.

Pure tests run on both interpreters. Qt tests (StatusStrip,
MissionRail) skip on system python3 — same pattern as Phase B/C:
a QApplication can only be created in the Qt venv.

Bus tests subscribe a collector to the producers' own bus instance
(core.state_bus_producers._bus()) so they hold in either import
context (voice_assistant.gui.* in production, top-level gui.* in
unit tests). The singleton is reset in setUp and tearDown so no
subscription leaks into another suite.
"""

import unittest

from core import capture_guard
from core import failsafe
from core import state_bus_producers as prod
from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from gui import state_bus
from gui.design_system.session_states import (
    ROLE_COLOURS,
    STATUS_MAP,
    resolve_status,
)
from gui.style import ACCENT, AMBER, DANGER, NEUTRAL, OK, TEXT
from gui.task_state import DISPLAY_STATES, render_task


class _BusCase(unittest.TestCase):
    def setUp(self):
        state_bus.reset_state_bus()
        self.bus = prod._bus()
        self.seen = []
        self._by_kind = {}

        def _mk(kind):
            def _cb(event):
                self.seen.append((kind, event))
                self._by_kind.setdefault(kind, []).append(event)

            return _cb

        for name in ("AppState", "SensorState", "MissionState", "StepEvent",
                     "ToolStarted", "ToolFinished", "InputAcquired",
                     "InputReleased", "TargetRect", "FailsafeTripped",
                     "CaptureStarted", "CaptureFinished", "Verified"):
            self.bus.subscribe(getattr(state_bus, name), _mk(name))

    def tearDown(self):
        state_bus.reset_state_bus()

    def drained(self, kind):
        """Drain, then return (and consume) this kind's new events."""
        self.bus.drain()
        return self._by_kind.pop(kind, [])


# ---------------------------------------------------------------- pure: §7 map
class SessionStatesTests(unittest.TestCase):
    def test_all_ten_labels_map(self):
        self.assertEqual(len(STATUS_MAP), 10)
        for label, (word, role, glyph) in STATUS_MAP.items():
            self.assertTrue(word and word == word.upper(), label)
            self.assertIn(role, ROLE_COLOURS, label)
            self.assertIn(glyph, ("ring", "diamond", "triangle", "square"),
                          label)

    def test_d2_listening_speaking_are_cyan_not_green(self):
        # D2: green is done/healthy only. Listening/Speaking move
        # to cyan — the old green (#3DD68C) must not appear.
        for label in ("Listening", "Speaking"):
            word, role, _glyph, colour = resolve_status(label)
            self.assertEqual(role, "cyan", label)
            self.assertEqual(colour, ACCENT, label)
            self.assertNotEqual(colour, OK, label)
        self.assertEqual(resolve_status("Listening")[0], "LISTENING")
        self.assertEqual(resolve_status("Speaking")[0], "RESPONDING")

    def test_role_colours_are_tokens(self):
        self.assertEqual(ROLE_COLOURS["cyan"], ACCENT)
        self.assertEqual(ROLE_COLOURS["ice"], TEXT)
        self.assertEqual(ROLE_COLOURS["amber"], AMBER)
        self.assertEqual(ROLE_COLOURS["red"], DANGER)
        self.assertEqual(ROLE_COLOURS["green"], OK)
        self.assertEqual(ROLE_COLOURS["neutral"], NEUTRAL)

    def test_fault_and_setup_required(self):
        self.assertEqual(resolve_status("Problem")[:3],
                         ("FAULT", "red", "square"))
        self.assertEqual(resolve_status("Setup needed")[:3],
                         ("SETUP REQUIRED", "amber", "triangle"))
        self.assertEqual(resolve_status("Offline")[:3],
                         ("LINK OFFLINE", "neutral", "ring"))

    def test_unknown_label_passes_through_neutral(self):
        word, role, glyph, colour = resolve_status("Something new")
        self.assertEqual(word, "Something new")
        self.assertEqual(role, "neutral")
        self.assertEqual(colour, NEUTRAL)


# ---------------------------------------------------------------- pure: glyphs
# NOTE: w9_wiring itself imports Qt (via chat_render), so the
# glyph tests import it lazily and skip where PySide6 is absent —
# the map is pure data, its module is not headless-importable.
class GlyphMapTests(unittest.TestCase):
    def test_section7_words_map(self):
        # T-2 (debug pass): status_glyph_for_text moved to
        # gui/w9_wiring_theme.py (GAPS-15 layering); core.w9_wiring
        # no longer defines it.
        try:
            from gui import w9_wiring_theme as w9
        except ImportError as exc:
            raise unittest.SkipTest(f"Qt unavailable: {exc}")
        self.assertEqual(w9.status_glyph_for_text("RESPONDING"), "speaking")
        self.assertEqual(w9.status_glyph_for_text("PROCESSING"), "thinking")
        self.assertEqual(w9.status_glyph_for_text("CONNECTING"), "thinking")
        self.assertEqual(w9.status_glyph_for_text("RECONNECTING"), "thinking")
        self.assertEqual(w9.status_glyph_for_text("CALIBRATING"), "thinking")
        self.assertEqual(w9.status_glyph_for_text("FAULT"), "error")
        self.assertEqual(w9.status_glyph_for_text("SETUP REQUIRED"), "idle")
        self.assertEqual(w9.status_glyph_for_text("LOCAL CORE"), "idle")
        self.assertEqual(w9.status_glyph_for_text("LINK OFFLINE"), "offline")
        self.assertEqual(w9.status_glyph_for_text("LISTENING"), "listening")


# ---------------------------------------------------------------- pure: producers
class ProducersTests(_BusCase):
    def test_helper_emits_and_latest_state(self):
        prod.emit_mission(goal="g", phase="act", step=1, steps_total=2)
        prod.emit_step(1, 2, "do it")
        prod.emit_tool_started("click")
        prod.emit_tool_finished("success", "click")
        missions = self.drained("MissionState")
        self.assertEqual(len(missions), 1)
        self.assertEqual(missions[0].goal, "g")
        self.assertEqual(self.bus.latest_state(state_bus.MissionState),
                         missions[0])
        steps = self.drained("StepEvent")
        self.assertEqual([(s.n, s.m, s.label) for s in steps],
                         [(1, 2, "do it")])
        started = self.drained("ToolStarted")
        finished = self.drained("ToolFinished")
        self.assertEqual([t.kind for t in started], ["click"])
        self.assertEqual([(t.status, t.kind) for t in finished],
                         [("success", "click")])

    def test_emit_never_raises_without_bus(self):
        # A producer helper must survive a missing bus entirely.
        original = prod._bus
        try:
            prod._bus = lambda: None
            prod.emit_step(1, 1, "x")  # no assertion beyond no-raise
            prod.emit_mission(goal="g")
        finally:
            prod._bus = original

    def test_leaked_shim_does_not_split_bus_identity(self):
        # Full-run incident (Phase D): a leaked voice_assistant
        # shim made the helper emit voice_assistant-flavoured
        # events that gui subscribers never saw — 5 tests failed
        # in the full run with zero events, green standalone.
        # The helper must resolve the gui identity first.
        import sys
        import types
        from pathlib import Path

        tree = str(Path(__file__).resolve().parent.parent)
        before = set(sys.modules)
        pkg = types.ModuleType("voice_assistant")
        pkg.__path__ = [tree]
        sys.modules["voice_assistant"] = pkg
        try:
            state_bus.reset_state_bus()
            self.assertIs(prod._bus(), state_bus.get_state_bus())
            self.assertIs(prod._cls("MissionState"),
                          state_bus.MissionState)
            seen = []
            prod._bus().subscribe(state_bus.MissionState, seen.append)
            prod.emit_mission(goal="shim test")
            prod._bus().drain()
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0].goal, "shim test")
        finally:
            for key in list(sys.modules):
                if (key == "voice_assistant"
                        or key.startswith("voice_assistant.")):
                    if key not in before:
                        del sys.modules[key]
            state_bus.reset_state_bus()


class CoordinatorEmitsTests(_BusCase):
    def _run(self, results):
        calls = {"n": 0}

        def _executor(tool, args):
            calls["n"] += 1
            return {"status": results.pop(0), "kind": "test",
                    "message": ""}

        coord = tc.TurnCoordinator(executor=_executor)
        task = coord.new_task("Morning briefing", "saved_plan",
                              [tc.StepDef(tool="read_calendar",
                                          label="Read calendar"),
                               tc.StepDef(tool="summarise",
                                          label="Summarise")])
        receipt = coord.run_task(task)
        return receipt, task, calls

    def test_success_run_emits_full_sequence(self):
        receipt, _task, _calls = self._run(["success", "success"])
        self.assertEqual(receipt.state, "completed")
        missions = self.drained("MissionState")
        self.assertEqual(
            [(m.phase, m.outcome) for m in missions],
            [("act", "running"), ("done", "success")])
        self.assertEqual(missions[0].steps_total, 2)
        steps = self.drained("StepEvent")
        self.assertEqual([(s.n, s.m, s.label) for s in steps],
                         [(1, 2, "Read calendar"), (2, 2, "Summarise")])
        started = self.drained("ToolStarted")
        finished = self.drained("ToolFinished")
        self.assertEqual([t.kind for t in started],
                         ["read_calendar", "summarise"])
        self.assertEqual([t.status for t in finished],
                         ["success", "success"])

    def test_failure_halt_emits_failed_terminal(self):
        _receipt, _task, _calls = self._run(["failure"])
        missions = self.drained("MissionState")
        self.assertEqual(
            [(m.phase, m.outcome) for m in missions],
            [("act", "running"), ("failed", "failed")])
        finished = self.drained("ToolFinished")
        self.assertEqual([t.status for t in finished], ["failure"])
        # The halted step's tool ran once; the second step never ran.
        started = self.drained("ToolStarted")
        self.assertEqual([t.kind for t in started], ["read_calendar"])

    def test_terminal_task_emits_nothing(self):
        coord = tc.TurnCoordinator(executor=lambda t, a: {"status": "success"})
        task = coord.new_task("Done already", "saved_plan",
                              [tc.StepDef(tool="x", label="x")])
        task.state = "completed"
        coord.run_task(task)
        self.bus.drain()
        self.assertEqual(self.seen, [])


class FailsafeEmitsTests(_BusCase):
    def setUp(self):
        super().setUp()
        if not failsafe.reset():
            self.skipTest("stop flag present — failsafe cannot re-arm")
        self.addCleanup(failsafe.reset)

    def test_trip_emits_once(self):
        failsafe.trip_now("phaseD test trip")
        trips = self.drained("FailsafeTripped")
        self.assertTrue(
            any(t.reason == "phaseD test trip" for t in trips),
            f"no matching trip in {trips!r}")
        # Idempotent: a second trip is a no-op, so no second event.
        failsafe.trip_now("phaseD test trip")
        trips2 = self.drained("FailsafeTripped")
        self.assertEqual(trips2, [])


class CaptureEmitsTests(_BusCase):
    KIND = "phaseD_test_kind"

    def test_transition_events_only(self):
        capture_guard.note_capture_started(self.KIND)
        capture_guard.note_capture_started(self.KIND)
        capture_guard.note_capture_ended(self.KIND)
        capture_guard.note_capture_ended(self.KIND)
        started = self.drained("CaptureStarted")
        finished = self.drained("CaptureFinished")
        self.assertEqual([s.scope for s in started], [self.KIND])
        self.assertEqual([s.scope for s in finished], [self.KIND])

    def test_unmatched_end_emits_nothing(self):
        capture_guard.note_capture_ended(self.KIND)
        started = self.drained("CaptureStarted")
        finished = self.drained("CaptureFinished")
        self.assertEqual(started, [])
        self.assertEqual(finished, [])


# ---------------------------------------------------------------- pure: Verifying real
# Wiring item 3 (v6.0.0) fulfils the D9 premise this class used
# to pin: the coordinator verification phase IS the real
# producer, so the stored "verifying" state renders Verifying.
# What stays pinned: the legacy boolean marker alone still
# never displays Verifying.
class VerifyingStateTests(unittest.TestCase):
    def test_marker_ignored(self):
        rec = {
            "state": "running",
            "title": "t",
            "steps": [{"tool": "a", "label": "a"}],
            "outcomes": [],
            "verifying": True,
        }
        self.assertEqual(render_task(rec)["state"], "Running")

    def test_stored_verifying_state_renders(self):
        rec = {
            "state": "verifying",
            "title": "t",
            "steps": [{"tool": "a", "label": "a"}],
            "outcomes": [{"tool": "a", "label": "a",
                          "status": "success", "kind": "",
                          "message": ""}],
        }
        rendered = render_task(rec)
        self.assertEqual(rendered["state"], "Verifying")
        self.assertEqual(rendered["action"], "Verifying")
        self.assertIn("Verifying", DISPLAY_STATES)


# ---------------------------------------------------------------- Qt: strip + rail
def _qt_available():
    try:
        from PySide6.QtWidgets import QApplication

        return QApplication.instance() is not None or True
    except Exception:  # noqa: BLE001
        return False


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"Qt unavailable: {exc}")
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.bus = state_bus.StateBus()


@unittest.skipUnless(_qt_available(), "Qt unavailable")
class StatusStripTests(_QtCase):
    def test_secondary_chips_follow_app_state(self):
        from gui.design_system.statusstrip import StatusStrip

        strip = StatusStrip(bus=self.bus)
        try:
            # NOTE: the strip is never shown here, so isVisible()
            # is False for every child regardless of its flag —
            # isHidden() is the flag that setVisible() controls.
            # UI rethink Build 1: there is NO mode chip — the
            # header is one state word, and the mode is carried by
            # the state word ("Local core"). AppState still carries
            # mode (Phase A pin); the strip ignores it.
            self.assertFalse(hasattr(strip, "mode_label"))
            self.assertTrue(strip.mic_label.isHidden())
            self.assertTrue(strip.wake_label.isHidden())
            self.bus.emit(state_bus.AppState(
                status="LISTENING", colour="cyan", glyph="ring",
                mic_muted=True, wake_on=True, mode="LOCAL CORE"))
            self.bus.drain()
            self.assertFalse(strip.mic_label.isHidden())
            self.assertFalse(strip.wake_label.isHidden())
            # NOTE: AppState.mode defaults to "LOCAL CORE" (Phase A
            # pin) — the strip ignores it either way.
            self.bus.emit(state_bus.AppState(
                status="LINK OFFLINE", colour="neutral", glyph="ring",
                mode=""))
            self.bus.drain()
            self.assertTrue(strip.mic_label.isHidden())
            self.assertTrue(strip.wake_label.isHidden())
        finally:
            strip.close()

    def test_close_unsubscribes(self):
        from gui.design_system.statusstrip import StatusStrip

        strip = StatusStrip(bus=self.bus)
        tokens = list(strip._tokens)
        self.assertTrue(tokens)
        strip.show()
        strip.close()
        for token in tokens:
            self.assertFalse(self.bus.unsubscribe(token))


@unittest.skipUnless(_qt_available(), "Qt unavailable")
class MissionRailTests(_QtCase):
    def test_permission_none_renders_empty(self):
        from gui.design_system.mission import MissionRail

        rail = MissionRail(bus=self.bus)
        try:
            self.bus.emit(state_bus.MissionState(
                goal="g", phase="act", permission="none"))
            self.bus.drain()
            self.assertEqual(rail._permission.text(), "")
            self.bus.emit(state_bus.MissionState(
                goal="g", phase="act", permission="granted"))
            self.bus.drain()
            self.assertEqual(rail._permission.text(),
                             "Permission: granted")
        finally:
            rail.close()


if __name__ == "__main__":
    unittest.main()
