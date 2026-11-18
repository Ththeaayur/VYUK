"""Sleeptime idle trigger (2026-11-03).

Decision under test: the watcher away episode IS the sleeptime
trigger. WatcherEngine takes an optional sleeptime_hook (default
None = off); _watch_idle calls it ONCE, at the False→True
transition when idle first reaches IDLE_AWAY_SECONDS (20 min),
and never again in the same episode. A hook report with
writes > 0 surfaces as a watcher event (kind "sleeptime");
a zero-write pass emits nothing, because no effectful work
happened. The shipped hook is w8_wiring.maybe_sleeptime_on_idle
(the existing 600 s gate, subsumed by the 1200 s episode
threshold), wired in gui/window_memory._init_watchers.

Technique: self-contained fakes mirroring the documented
watcher pattern (fake probes + fake clock, no Qt, no real
probes, no real store). The GUI wiring itself is pinned by a
source scan, not a behaviour test: gui/window_memory is
unimportable in this lab (it pulls the full window, PortAudio
absent) — the same limitation the journal-compensate batch
recorded for MainWindow mixin methods. The scan asserts the
hook line sits inside _init_watchers, not merely somewhere in
the file.
"""

import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import watchers as watchers_mod
from core import w8_wiring as w8

AWAY = watchers_mod.IDLE_AWAY_SECONDS  # 1200
BACK = watchers_mod.IDLE_BACK_SECONDS  # 10


class _Clock:
    """Fake clock: daytime (outside quiet hours) + advance."""

    def __init__(self):
        self.t = datetime(2026, 11, 3, 14, 0, 0)

    def now(self):
        return self.t

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


class _Hook:
    """Recording sleeptime hook; report writes configurable."""

    def __init__(self, writes=0):
        self.calls = []
        self.writes = writes

    def __call__(self, idle_seconds):
        self.calls.append(float(idle_seconds))
        return {"writes": self.writes, "refused": False,
                "detail": "fake sleeptime pass"}


def _engine(idle_value, hook=None, clock=None):
    """Engine with every probe faked (None) except idle_seconds."""
    probes = {
        "battery": lambda: None,
        "downloads": lambda: None,
        "network": lambda: None,
        "disk": lambda: None,
        "drives": lambda: None,
        "clipboard": lambda: None,
        "privacy": lambda: None,
        "fullscreen": lambda: None,
        "load": lambda: None,
        "top_cpu_process": lambda: None,
        "top_memory_process": lambda: None,
        "disk_top_folders": lambda: None,
        "network_ssid": lambda: None,
        "network_diag": lambda: None,
        "foreground_app": lambda: None,
        "foreground_enabled": lambda: False,
        "ambient_light_enabled": lambda: False,
        "ambient_frame": lambda: None,
        "file_sniffer": lambda: None,
        "idle_seconds": lambda: idle_value,
    }
    events = []
    clock = clock or _Clock()
    engine = watchers_mod.WatcherEngine(
        on_event=lambda kind, message: events.append((kind, message)),
        probes=probes,
        now_fn=clock.now,
        timeline_reader=lambda: [],
        event_writer=lambda *args, **kwargs: None,
        sleeptime_hook=hook)
    engine._events = events
    engine._clock = clock
    return engine


def _idle_only(engine, idle_value):
    """Drive just _watch_idle (hermetic; no other watcher runs)."""
    engine._probes["idle_seconds"] = lambda: idle_value
    out = []
    engine._watch_idle(out)
    return out


class TestSleeptimeTrigger(unittest.TestCase):
    def test_hook_fires_once_on_transition(self):
        hook = _Hook(writes=0)
        engine = _engine(AWAY, hook=hook)
        _idle_only(engine, AWAY)
        self.assertEqual(hook.calls, [float(AWAY)])
        self.assertTrue(engine._away)

    def test_hook_not_refired_within_episode(self):
        hook = _Hook(writes=0)
        engine = _engine(AWAY, hook=hook)
        _idle_only(engine, AWAY)
        _idle_only(engine, AWAY + 600)
        _idle_only(engine, AWAY + 3600)
        self.assertEqual(len(hook.calls), 1)

    def test_hook_not_fired_below_threshold(self):
        hook = _Hook(writes=0)
        engine = _engine(AWAY - 1, hook=hook)
        _idle_only(engine, AWAY - 1)
        self.assertEqual(hook.calls, [])
        self.assertFalse(engine._away)

    def test_hook_not_fired_when_probe_down(self):
        hook = _Hook(writes=0)
        engine = _engine(None, hook=hook)
        _idle_only(engine, None)
        self.assertEqual(hook.calls, [])
        self.assertFalse(engine._away)

    def test_default_engine_has_no_hook(self):
        engine = _engine(AWAY, hook=None)
        _idle_only(engine, AWAY)
        self.assertTrue(engine._away)
        self.assertEqual(
            [k for k, _ in engine._events], [])

    def test_hook_exception_never_breaks_watcher(self):
        def _boom(idle_seconds):
            raise RuntimeError("sleeptime down")

        engine = _engine(AWAY, hook=_boom)
        _idle_only(engine, AWAY)  # must not raise
        self.assertTrue(engine._away)
        _idle_only(engine, AWAY + 60)  # engine still ticks
        self.assertTrue(engine._away)

    def test_writes_reported_as_event(self):
        hook = _Hook(writes=2)
        engine = _engine(AWAY, hook=hook)
        out = _idle_only(engine, AWAY)
        kinds = [k for k, _ in out]
        self.assertIn("sleeptime", kinds)
        message = [m for k, m in out if k == "sleeptime"][0]
        self.assertIn("2", message)
        self.assertIn("sleeptime", [k for k, _ in engine._events])

    def test_zero_write_pass_emits_nothing(self):
        hook = _Hook(writes=0)
        engine = _engine(AWAY, hook=hook)
        out = _idle_only(engine, AWAY)
        self.assertEqual(hook.calls, [float(AWAY)])
        self.assertNotIn("sleeptime", [k for k, _ in out])
        self.assertNotIn("sleeptime", [k for k, _ in engine._events])

    def test_non_dict_and_bad_writes_reports_ignored(self):
        engine = _engine(AWAY, hook=lambda idle: "ok")
        out = _idle_only(engine, AWAY)
        self.assertNotIn("sleeptime", [k for k, _ in out])
        engine2 = _engine(AWAY, hook=lambda idle: {"writes": "many"})
        out2 = _idle_only(engine2, AWAY)
        self.assertNotIn("sleeptime", [k for k, _ in out2])

    def test_refires_on_next_episode_after_welcome_back(self):
        hook = _Hook(writes=1)
        engine = _engine(AWAY, hook=hook)
        _idle_only(engine, AWAY)
        self.assertEqual(len(hook.calls), 1)
        _idle_only(engine, BACK - 1)  # welcome back clears away
        self.assertFalse(engine._away)
        engine._clock.advance(3600)  # past the 60 s dedup window
        _idle_only(engine, AWAY)
        self.assertEqual(len(hook.calls), 2)

    def test_full_tick_lifecycle(self):
        hook = _Hook(writes=3)
        engine = _engine(AWAY, hook=hook)
        engine.tick()
        self.assertEqual(len(hook.calls), 1)
        engine.tick()  # same episode: no re-fire
        self.assertEqual(len(hook.calls), 1)
        engine._probes["idle_seconds"] = lambda: BACK - 1
        engine.tick()  # welcome back
        self.assertFalse(engine._away)
        engine._clock.advance(3600)
        engine._probes["idle_seconds"] = lambda: AWAY
        engine.tick()  # next episode fires again
        self.assertEqual(len(hook.calls), 2)


class TestSleeptimeGate(unittest.TestCase):
    def test_below_gate_threshold_is_noop(self):
        report = w8.maybe_sleeptime_on_idle(599.0)
        self.assertEqual(report["writes"], 0)
        self.assertFalse(report["refused"])

    def test_unusable_reading_refused(self):
        report = w8.maybe_sleeptime_on_idle(None)
        self.assertEqual(report["writes"], 0)
        self.assertTrue(report["refused"])

    def test_custom_threshold_respected(self):
        report = w8.maybe_sleeptime_on_idle(700.0,
                                            threshold_seconds=900.0)
        self.assertEqual(report["writes"], 0)

    def test_gate_runs_real_pass_with_candidates(self):
        written = []

        def _writer(record, base=None):
            written.append(record)

        candidate = {"category": "preference", "key": "theme",
                     "value": "dark", "kind": "fact", "heat": 3,
                     "updated_at": "2026-11-03"}
        with tempfile.TemporaryDirectory() as tmp:
            report = w8.maybe_sleeptime_on_idle(
                1300.0, candidates=[candidate], base=tmp,
                writer=_writer)
        self.assertEqual(report["writes"], 1)
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0]["key"], "theme")

    def test_voice_path_still_refused(self):
        report = w8.run_sleeptime_pass(from_voice_path=True)
        self.assertEqual(report["writes"], 0)
        self.assertTrue(report["refused"])

    def test_owed_text_states_trigger_built(self):
        self.assertIn("BUILT", w8.SLEEPTIME_IDLE_TRIGGER_OWED)
        self.assertNotIn("no live idle trigger",
                         w8.SLEEPTIME_IDLE_TRIGGER_OWED)

    def test_candidate_gate_names_missing_piece(self):
        gate = w8.SLEEPTIME_CANDIDATE_GATE
        self.assertIn("heat", gate)
        self.assertIn("candidates", gate)
        self.assertIn("Unblocker", gate)

    def test_gui_wiring_pins_hook_in_init_watchers(self):
        # GUI split #12 batch 2 INTENTIONAL: _init_watchers moved
        # verbatim to window_memory_watchers.py. Assertion unchanged.
        text = (ROOT / "gui" / "window_memory_watchers.py").read_text(
            encoding="utf-8")
        start = text.index("def _init_watchers")
        end = text.index("def _on_watcher_event", start)
        span = text[start:end]
        self.assertIn("sleeptime_hook=", span)
        self.assertIn("maybe_sleeptime_on_idle", span)


if __name__ == "__main__":
    unittest.main()
