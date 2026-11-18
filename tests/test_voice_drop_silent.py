"""Voice dropping, status-less worker death (R2, 2026-11-06) — tests.

Fix 4 (2026-11-05) left one hole open, stated in its own report:
"a status-less worker death would leave pending=True with no
timer; manual Connect still works as recovery." Every Fix 4 drop
path starts from a status the worker emits — but a worker that
dies WITHOUT emitting one leaves the GUI showing "Listening…"
with no timer and no drop. The shapes, proven from the tree
before this batch (see gui/drop_cause.py R2 note):

  S1  Wedged event loop — the thread is alive but the loop never
      processes another callback, so no status, no transcript,
      no tool result ever arrives. The RS-1 watchdog cannot
      fire (it is an asyncio task ON the same loop), and
      BackendWorker.run only emits ERROR on a Python exception
      from run_until_complete (a wedged loop raises nothing).
  S2  Thread dead with no status — the mic watch (2 s, the ONLY
      periodic worker check) sees is_alive() False and just
      stops itself, declaring nothing.
  S3  An attempt in flight that never answers — the reconnect
      timer already fired, _reconnect_pending stays True, no
      timer runs, no status ever arrives.

The fix under test — one GUI-thread liveness watch
(gui/window_session_status.py _liveness_tick, 5 s interval,
30 s grace): a trivial callback scheduled on the worker loop
with call_soon_threadsafe, whose ack proves the loop is still
processing work. Detection feeds the SAME Fix 4 machinery
(one drop record, one budget, one terminal give-up) with a
new cause, worker_died_silent. The session epoch is bumped
on declaration, so Fix 9's pause check engages at the next
step boundary even before any restart.

Scope pins (INTENTIONAL — do not "fix" by widening):
- The declaration NEVER calls worker.stop(): the live
  session's stop() clears the saved resumption handle, and
  an unexpected drop must keep its handle for the
  resume-first reconnect (the watchdog death's own rule).
  A wedged-alive worker frees its slot through the
  existing refused-start accounting and the give-up path.
- Fix 4's budgets (3 episodes / 600 s / 2-5-15-30 s) and the
  terminal give-up latch are NOT retuned here.
- A death on a fresh start that never lived presents an
  honest failure with NO episode and NO budget burn.
- A busy worker is never declared dead: any probe ack, or
  any status at all, proves the loop is alive.

Harness: the classifier tests are stdlib-only (run on both
interpreters). The mixin tests need PySide6 and skip without
it, following the test_voice_drop precedent. The GUI is a
fake window built from the two real mixins with stubbed
labels/timers/worker; DATA_DIR is pointed at a temp dir per
test so the real data/ is never touched. No real network,
no real audio, no real-time sleeps (staleness is simulated
by back-dating the probe timestamps).
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import types as _types_mod
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001 — lab has no PortAudio
    _fake_sd = _types_mod.ModuleType("sounddevice")
    sys.modules["sounddevice"] = _fake_sd

# Synthetic voice_assistant package (the test_livefix precedent):
# the tree layout is top-level core/ + gui/, so the alias maps
# voice_assistant -> tree root.
if "voice_assistant" not in sys.modules:
    _pkg = _types_mod.ModuleType("voice_assistant")
    _pkg.__path__ = [str(ROOT)]
    sys.modules["voice_assistant"] = _pkg
if "voice_assistant.core" not in sys.modules:
    _core = _types_mod.ModuleType("voice_assistant.core")
    _core.__path__ = [str(ROOT / "core")]
    sys.modules["voice_assistant.core"] = _core
if "voice_assistant.gui" not in sys.modules:
    _gui = _types_mod.ModuleType("voice_assistant.gui")
    _gui.__path__ = [str(ROOT / "gui")]
    sys.modules["voice_assistant.gui"] = _gui

from voice_assistant.gui.drop_cause import (  # noqa: E402
    DROP_CAUSES,
    classify_drop,
    give_up_caption,
    reconnect_caption,
)

try:
    from voice_assistant.core import session_epoch as _epoch_mod
    _EPOCH = True
except Exception:  # noqa: BLE001
    _epoch_mod = None
    _EPOCH = False

try:
    from voice_assistant.core.brain.task_coordinator_execution import (
        TurnCoordinatorExecutionMixin as _ExecMixin,
    )
    _COORD = True
except Exception:  # noqa: BLE001
    _ExecMixin = None
    _COORD = False

try:
    from PySide6.QtCore import Qt as _Qt  # noqa: F401
    from PySide6.QtWidgets import QApplication as _QA
    from PySide6.QtWidgets import QLabel as _QLabel
    _QT = True
except Exception:  # noqa: BLE001 — PySide6 absent on sys
    _QA = None
    _QLabel = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None


def setUpModule():
    """QApplication for the mixin tests (Qt only)."""
    global _APP, _VA_SNAPSHOT
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    if not _QT:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if _QA.instance() is None:
        _APP = _QA(sys.argv)


def tearDownModule():
    global _VA_SNAPSHOT
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        del sys.modules[key]
    if _VA_SNAPSHOT is not None:
        sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None


# ----------------------------------------------------------------------
# Part A: the new cause (both interpreters)
# ----------------------------------------------------------------------

class TestWorkerDiedSilentCause(unittest.TestCase):
    def test_silent_cause_exists_and_is_distinct(self):
        self.assertIn("worker_died_silent", DROP_CAUSES)
        self.assertEqual(
            classify_drop("worker thread ended", silent=True),
            "worker_died_silent")

    def test_silent_precedence(self):
        # Fatal and watchdog are more specific; silent beats the
        # text-matched causes (it comes from the drop point, not
        # from a status string).
        self.assertEqual(
            classify_drop("1008 already connected", fatal=True), "fatal")
        self.assertEqual(
            classify_drop("LIVE_WATCHDOG", watchdog=True, silent=True),
            "watchdog_silence")
        self.assertEqual(
            classify_drop("1008 already connected", silent=True),
            "worker_died_silent")
        self.assertEqual(
            classify_drop("ERROR: Timed out", silent=True),
            "worker_died_silent")
        self.assertEqual(classify_drop("DISCONNECTED"), "unknown")

    def test_captions_are_plain_words(self):
        caption = reconnect_caption("worker_died_silent")
        self.assertIn("voice worker stopped responding", caption)
        self.assertNotIn("ERROR", caption)
        self.assertNotIn("1008", caption)

    def test_give_up_caption_keeps_the_pinned_shape(self):
        caption = give_up_caption("worker_died_silent")
        self.assertIn("Couldn't reconnect", caption)
        self.assertIn("press Connect", caption)
        self.assertIn("voice worker", caption)

    def test_all_causes_still_have_captions(self):
        for cause in DROP_CAUSES:
            self.assertTrue(reconnect_caption(cause), cause)
            self.assertTrue(give_up_caption(cause), cause)


# ----------------------------------------------------------------------
# Part B: the liveness watch (Qt only)
# ----------------------------------------------------------------------

class _FakeTimer:
    def __init__(self):
        self.active = False
        self.starts = []

    def isActive(self):
        return self.active

    def start(self, ms=0):
        self.active = True
        self.starts.append(int(ms))

    def stop(self):
        self.active = False


class _FakeEarcons:
    def __init__(self):
        self.calls = []

    def connect(self):
        self.calls.append("connect")

    def disconnect(self):
        self.calls.append("disconnect")

    def error(self):
        self.calls.append("error")

    def done(self):
        self.calls.append("done")


class _FakeLoop:
    """A worker event loop: callbacks run only when the test
    drives them (a wedged loop is a loop whose callbacks are
    never driven)."""

    def __init__(self, closed=False, raise_on_schedule=False):
        self.closed = closed
        self.raise_on_schedule = raise_on_schedule
        self.queued = []

    def is_closed(self):
        return self.closed

    def call_soon_threadsafe(self, callback):
        if self.closed or self.raise_on_schedule:
            raise RuntimeError("Event loop is closed")
        self.queued.append(callback)
        return SimpleNamespace()

    def drive(self):
        """Run every queued callback (a live loop's tick)."""
        queued, self.queued = self.queued, []
        for callback in queued:
            callback()


class _FakeWorker:
    def __init__(self, loop=None):
        self.alive = True
        self.loop = loop
        self.stops = 0

    def is_alive(self):
        return self.alive

    def stop(self):
        self.alive = False
        self.stops += 1


class _FakeButton:
    def __init__(self):
        self.text = ""
        self.object_name = ""

    def setText(self, text):
        self.text = text

    def setObjectName(self, name):
        self.object_name = name

    def style(self):
        return None

    def setStyle(self, _style):
        pass


class _FakeSettings:
    def value(self, _key, default=None, type=None):  # noqa: A002
        return default


@unittest.skipUnless(_QT, "PySide6 needed for the liveness tests")
class TestVoiceDropSilentMixin(unittest.TestCase):
    def setUp(self):
        from voice_assistant.gui.window_session_status import (
            SessionStatusMixin,
        )
        from voice_assistant.gui.window_session_connection import (
            SessionConnectionMixin,
        )

        class _FakeWindow(SessionStatusMixin, SessionConnectionMixin):
            def _brain_engine(self):
                return "cloud"

            def _disable_watch_mode(self, _reason):
                pass

            def _update_tray_state(self, _state):
                pass

            def _show_thinking_bubble(self):
                pass

            def _hide_thinking_bubble(self):
                pass

            def _present_idle_state_text(self):
                self._set_caption("idle")

            def _sync_muted_label(self):
                pass

            def _show_page(self, _index):
                pass

            def _mark_session_provenance(self, _provenance):
                pass

            def _clear_deaf_hint(self):
                self._deaf_hint_active = False

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        import voice_assistant.core.config as _cfgmod
        self._data_patch = mock.patch.object(
            _cfgmod, "DATA_DIR", Path(self._tmp.name))
        self._data_patch.start()
        self.addCleanup(self._data_patch.stop)

        win = _FakeWindow.__new__(_FakeWindow)
        win.state_label = _QLabel("x")
        win.caption_label = _QLabel("")
        win.settings = _FakeSettings()
        win.earcons = _FakeEarcons()
        win.visualizer = SimpleNamespace(set_state=lambda _s: None)
        win.mini_orb = None
        win.btn_connect = _FakeButton()
        win.btn_mute = SimpleNamespace(isChecked=lambda: False)
        win.worker = None
        win.signals = SimpleNamespace()
        win._connected = False
        win._was_connected = False
        win._intentional_disconnect = False
        win._reconnect_attempts = 0
        win._reconnect_pending = False
        win._last_status_fatal = False
        win._watchdog_streak = 0
        win._watchdog_diagnostic_posted = False
        win._last_drop = None
        win._reconnect_exhausted = False
        win._reconnect_episodes_failed = 0
        win._last_episode_end_at = None
        win._go_away_seen = False
        win._mic_stall_noted = False
        win._status_raw = ""
        win._deaf_since = None
        win._deaf_hint_active = False
        win._deaf_hint_text = ""
        win._deaf_saved_caption = None
        win._last_mirrored_error = None
        win._last_error_sound = 0.0
        win._brain_fallback_active = False
        win._mic_watch_timer = _FakeTimer()
        win._reconnect_timer = _FakeTimer()
        win._session_liveness_timer = _FakeTimer()
        win._liveness_probe_sent_at = None
        win._liveness_last_ack = None
        win._silent_death_declared = False
        win._liveness_awaiting_first_status = False
        win._systems = []
        win._captions = []
        win._pills = []
        win._add_system = lambda text: win._systems.append(text)
        win._set_caption = lambda text: win._captions.append(text)
        win._set_pill = lambda text: win._pills.append(text)
        self.win = win
        self.timer = win._reconnect_timer
        self.liveness = win._session_liveness_timer

    def _log_text(self):
        log = Path(self._tmp.name) / "activity.log"
        return log.read_text(encoding="utf-8") if log.exists() else ""

    def _connect_live(self):
        """A live session, as the GUI sees it after Connect."""
        self.win._was_connected = True
        self.win._connected = True
        self.win._on_status("LISTENING")

    def _live_worker(self):
        loop = _FakeLoop()
        worker = _FakeWorker(loop=loop)
        self.win.worker = worker
        return loop, worker

    def _epoch(self):
        if not _EPOCH:
            self.skipTest("session_epoch unavailable")
        return _epoch_mod.current_epoch()

    # -- S2: thread dead with no status --------------------------------

    def test_thread_dead_while_connected_declares_silent_death(self):
        self._connect_live()
        _loop, worker = self._live_worker()
        epoch_before = self._epoch()
        worker.alive = False
        self.win._liveness_tick()
        record = self.win._last_drop
        self.assertEqual(record["cause"], "worker_died_silent")
        self.assertIn("voice worker stopped responding",
                      self.win._captions[-1])
        self.assertIn(
            "[GUI] DROP worker_died_silent: worker thread ended",
            self._log_text())
        # The Fix 4 path runs: a reconnect attempt is scheduled…
        self.assertEqual(len(self.timer.starts), 1)
        self.assertEqual(self.timer.starts[0], 2000)
        # …the epoch moved (Fix 9's pause hook engages)…
        self.assertEqual(self._epoch(), epoch_before + 1)
        # …and the worker was NOT stopped: stop() clears the
        # resumption handle, and an unexpected drop must keep
        # it for the resume-first reconnect.
        self.assertEqual(worker.stops, 0)

    def test_thread_dead_inside_episode_keeps_episode_cause(self):
        self._connect_live()
        self.win._on_status("ERROR: Timed out reaching Google")
        self.assertEqual(self.win._last_drop["cause"], "network")
        _loop, worker = self._live_worker()
        epoch_before = self._epoch()
        worker.alive = False
        self.win._liveness_tick()
        # First cause wins: the episode stays "network"…
        self.assertEqual(self.win._last_drop["cause"], "network")
        # …but the silent death is logged as its own line…
        self.assertIn("[GUI] DROP worker_died_silent:",
                      self._log_text())
        # …and the scheduled attempt stands (the duplicate-drop
        # branch holds the presentation — no second schedule).
        self.assertEqual(len(self.timer.starts), 1)
        self.assertEqual(self._epoch(), epoch_before + 1)

    # -- S1: wedged loop -----------------------------------------------

    def test_busy_worker_that_answers_probes_is_never_declared_dead(self):
        self._connect_live()
        loop, _worker = self._live_worker()
        epoch_before = self._epoch()
        for _ in range(3):
            self.win._liveness_tick()
            loop.drive()  # the loop answers every probe
        self.assertIsNone(self.win._last_drop)
        self.assertEqual(self.timer.starts, [])
        self.assertEqual(self._log_text(), "")
        self.assertEqual(self._epoch(), epoch_before)

    def test_slow_worker_under_grace_is_not_declared_dead(self):
        self._connect_live()
        _loop, _worker = self._live_worker()
        self.win._liveness_tick()  # schedules a probe
        # 20 s with no ack: inside the 30 s grace — a slow
        # worker, not a dead one.
        self.win._liveness_probe_sent_at = time.monotonic() - 20.0
        self.win._liveness_tick()
        self.assertIsNone(self.win._last_drop)
        self.assertEqual(self.timer.starts, [])

    def test_wedged_loop_past_grace_declares_silent_death(self):
        self._connect_live()
        _loop, worker = self._live_worker()
        epoch_before = self._epoch()
        self.win._liveness_tick()  # schedules a probe
        # 40 s with no ack: past the 30 s grace — wedged.
        self.win._liveness_probe_sent_at = time.monotonic() - 40.0
        self.win._liveness_tick()
        record = self.win._last_drop
        self.assertEqual(record["cause"], "worker_died_silent")
        self.assertIn("answered no probe for 40s",
                      self._log_text())
        self.assertEqual(len(self.timer.starts), 1)
        self.assertEqual(self._epoch(), epoch_before + 1)
        # Handle preservation again: no stop on a wedged worker.
        self.assertEqual(worker.stops, 0)

    def test_closed_loop_declares_silent_death(self):
        self._connect_live()
        loop, _worker = self._live_worker()
        loop.closed = True
        self.win._liveness_tick()
        self.assertEqual(
            self.win._last_drop["cause"], "worker_died_silent")
        self.assertIn("loop closed", self._log_text())

    def test_status_arrival_clears_an_outstanding_probe(self):
        self._connect_live()
        _loop, _worker = self._live_worker()
        self.win._liveness_tick()  # schedules a probe
        self.win._liveness_probe_sent_at = time.monotonic() - 40.0
        # A status proves the loop is alive and delivering —
        # the stale probe must not trip on the next tick.
        self.win._on_status("LISTENING")
        self.assertIsNone(self.win._liveness_probe_sent_at)
        self.win._liveness_tick()
        self.assertIsNone(self.win._last_drop)
        self.assertEqual(self.timer.starts, [])

    def test_probe_is_scheduled_once_per_tick(self):
        self._connect_live()
        loop, _worker = self._live_worker()
        self.win._liveness_tick()
        self.assertEqual(len(loop.queued), 1)
        # Outstanding probe, no ack yet: no second probe.
        self.win._liveness_tick()
        self.assertEqual(len(loop.queued), 1)
        # Acked: the next tick schedules a fresh probe.
        loop.drive()
        self.win._liveness_tick()
        self.assertEqual(len(loop.queued), 1)

    # -- guards: never twice, never early, never after the end ---------

    def test_declaration_happens_once_per_fall(self):
        self._connect_live()
        _loop, worker = self._live_worker()
        worker.alive = False
        self.win._liveness_tick()
        self.win._liveness_tick()
        self.win._liveness_tick()
        self.assertEqual(
            self._log_text().count("[GUI] DROP worker_died_silent"), 1)
        self.assertEqual(len(self.timer.starts), 1)

    def test_no_declaration_while_an_attempt_is_scheduled(self):
        self._connect_live()
        loop, _worker = self._live_worker()
        self.win._reconnect_pending = True
        self.win._reconnect_timer.start(2000)  # attempt scheduled
        # Even a wedged loop is not this tick's call: the
        # scheduled attempt has not had its chance yet.
        self.win._liveness_probe_sent_at = time.monotonic() - 40.0
        self.win._liveness_tick()
        self.assertIsNone(self.win._last_drop)
        self.assertEqual(loop.queued, [])

    def test_no_declaration_after_give_up_latch(self):
        self._connect_live()
        _loop, worker = self._live_worker()
        self.win._reconnect_exhausted = True
        worker.alive = False
        self.win._liveness_tick()
        self.assertIsNone(self.win._last_drop)
        self.assertFalse(self.liveness.active)

    def test_tick_stops_itself_when_no_session_is_expected(self):
        self.win.worker = None
        self.win._connected = False
        self.win._reconnect_pending = False
        self.liveness.start()
        self.win._liveness_tick()
        self.assertFalse(self.liveness.active)

    def test_tick_stops_itself_on_intentional_disconnect(self):
        _loop, worker = self._live_worker()
        self.win._intentional_disconnect = True
        worker.alive = False
        self.liveness.start()
        self.win._liveness_tick()
        self.assertFalse(self.liveness.active)
        self.assertIsNone(self.win._last_drop)

    # -- S3: fresh start that never lived -------------------------------

    def test_fresh_stall_fails_honestly_without_episode_or_budget(self):
        # Never connected, never pending: a worker that dies
        # before the first LISTENING.
        loop, worker = self._live_worker()
        self.win._liveness_awaiting_first_status = True
        epoch_before = self._epoch()
        worker.alive = False
        self.win._liveness_tick()
        self.assertEqual(
            self.win._last_drop["cause"], "worker_died_silent")
        self.assertIn("didn't respond", self.win.state_label.text())
        self.assertIn("Press Connect to try again.",
                      self.win._captions[-1])
        # No episode, no budget burn, no latch.
        self.assertEqual(self.timer.starts, [])
        self.assertEqual(self.win._reconnect_episodes_failed, 0)
        self.assertFalse(self.win._reconnect_exhausted)
        self.assertFalse(self.win._reconnect_pending)
        self.assertFalse(self.win._last_drop["pending"])
        self.assertEqual(self._epoch(), epoch_before + 1)
        self.assertEqual(self.win.btn_connect.text, "Connect")

    def test_manual_connect_recovers_after_fresh_stall(self):
        loop, worker = self._live_worker()
        self.win._liveness_awaiting_first_status = True
        worker.alive = False
        self.win._liveness_tick()
        self.assertEqual(self.win.btn_connect.text, "Connect")
        # A human Connect now starts a fresh worker (the dead
        # one no longer blocks the slot).
        import voice_assistant.gui.window_session_connection as _wsc
        from voice_assistant.core import config as _cfgmod
        started = []

        class _FakeBackendWorker:
            def __init__(self, signals):
                self.signals = signals
                self.alive = False

            def start(self):
                self.alive = True
                started.append(self)

            def is_alive(self):
                return self.alive

            def stop(self):
                self.alive = False

        self.win.worker = None
        self.win._maybe_mic_first_use_ask = lambda user_started: None
        fake_audio = SimpleNamespace(start=lambda: None)
        with mock.patch.object(_wsc, "audio_manager", fake_audio), \
                mock.patch.object(
                    _wsc, "BackendWorker", _FakeBackendWorker), \
                mock.patch.object(
                    _cfgmod.config, "selected_provider_key_present",
                    return_value=True):
            self.assertTrue(
                self.win._start_connection(user_started=True))
        self.assertEqual(len(started), 1)
        self.assertTrue(self.liveness.active)

    # -- death during a task pauses it (Fix 9 path) --------------------

    @unittest.skipUnless(_EPOCH and _COORD,
                         "session_epoch + coordinator needed")
    def test_silent_death_bumps_epoch_so_a_task_pauses(self):
        # Fix 9's _pause_reason reads exactly one thing: the
        # session epoch moved since run_task entry. A silent
        # death must move it, so a marked task pauses at its
        # next step boundary instead of running orphaned.
        self._connect_live()
        _loop, worker = self._live_worker()
        epoch_before = self._epoch()
        task = SimpleNamespace(
            _honor_stop_signals=True, _epoch_at_start=epoch_before)
        mixin = _ExecMixin.__new__(_ExecMixin)
        self.assertIsNone(mixin._pause_reason(task))
        worker.alive = False
        self.win._liveness_tick()
        self.assertEqual(self._epoch(), epoch_before + 1)
        reason = mixin._pause_reason(task)
        self.assertIsNotNone(reason)
        self.assertIn("paused", reason)

    # -- timer lifecycle -------------------------------------------------

    def test_liveness_timer_starts_with_the_session(self):
        import voice_assistant.gui.window_session_connection as _wsc
        from voice_assistant.core import config as _cfgmod

        class _FakeBackendWorker:
            def __init__(self, signals):
                self.signals = signals
                self.alive = False

            def start(self):
                self.alive = True

            def is_alive(self):
                return self.alive

            def stop(self):
                self.alive = False

        self.win._maybe_mic_first_use_ask = lambda user_started: None
        fake_audio = SimpleNamespace(start=lambda: None)
        with mock.patch.object(_wsc, "audio_manager", fake_audio), \
                mock.patch.object(
                    _wsc, "BackendWorker", _FakeBackendWorker), \
                mock.patch.object(
                    _cfgmod.config, "selected_provider_key_present",
                    return_value=True):
            self.assertTrue(
                self.win._start_connection(user_started=True))
        self.assertTrue(self.liveness.active)
        self.assertTrue(self.win._mic_watch_timer.active)

    def test_give_up_stops_the_liveness_timer(self):
        self._connect_live()
        self._live_worker()
        self.liveness.start()
        self.win._reconnect_attempts = 4
        self.win._on_status("DISCONNECTED")
        self.assertTrue(self.win._reconnect_exhausted)
        self.assertFalse(self.liveness.active)

    def test_live_status_clears_the_declared_flag(self):
        self._connect_live()
        _loop, worker = self._live_worker()
        worker.alive = False
        self.win._liveness_tick()
        self.assertTrue(self.win._silent_death_declared)
        # The session comes back: a new fall may be declared.
        worker.alive = True
        self.win._on_status("LISTENING")
        self.assertFalse(self.win._silent_death_declared)


if __name__ == "__main__":
    unittest.main()
