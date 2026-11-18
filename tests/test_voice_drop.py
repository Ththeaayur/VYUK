"""Voice dropping (Fix 4, 2026-11-05) — tests.

Owner field report: "voice dropping off here and there". The gap,
proven from the tree before this batch (see gui/drop_cause.py for
the full loophole family):

  L1  No drop cause existed anywhere — a socket close, a server
      end, a refused resumption handle, a watchdog silence and
      a mic stall all presented identically, and a bare
      DISCONNECTED logged nothing.
  L2  Resume-handle refusal was invisible above the client (the
      client already goes cold cleanly inside one connect
      attempt; the GUI could not name it).
  L3  Zombie cross-episode loop: an episode ending on LISTENING
      reset the attempt counter, so a session that connects,
      claims to listen, then goes quiet again cycled forever
      across episodes without ever exhausting.
  L4  Give-up was not terminal: _was_connected stayed True, so
      a late status started a NEW episode after give-up; and
      give-up was caption-only (no chat line, no log line).
  L5  Give-up was not one-tap recoverable when the worker was
      wedged: the button showed "Cancel", not "Connect".
  L6  Mic stall never surfaced: the mic watchdog reopens the
      stream locally and tells nobody.

The fix under test — "one drop record, one budget, one terminal
give-up" (gui/drop_cause.py + gui/window_session_status.py):

  * one pure classifier; one DropRecord per fall (first, most
    specific cause wins); the cause rides to the caption and
    to data/activity.log;
  * one cross-episode budget (3 episodes ending without a real
    SPEAKING/THINKING response, inside one 600 s handle
    lifetime); at the budget the next drop gives up;
  * one terminal give-up (latch; wedged worker stopped; chat
    line + log line once); only a human Connect clears it.

Scope pins (INTENTIONAL — do not "fix" by widening):
- The resume-vs-cold DECISION stays in the client
  (live_client_lifecycle._try_candidates already handles
  stale-handle -> cold, already-connected -> wait then cold).
  This batch only NAMES the cause in the GUI; it never
  re-decides resumption.
- The backoff shape (2/5/15/30 s, 4 attempts) is unchanged.
- Fatal / intentional-disconnect behaviour is unchanged.
- A drop mid-task does NOT pause the task here (Fix 9): no
  caller of coordinator pause_task() exists outside tests,
  and no DISCONNECTED hook exists in the coordinator. See
  test_task_pause_is_out_of_scope.
- The mic-stall note changes no session state and ends no
  session; the mic watchdog's local reopen is the recovery.

Harness: the classifier tests are stdlib-only (run on both
interpreters). The mixin tests need PySide6 and skip without
it, following the programme's standing optional-dependency
skips (the test_mic_first_use_ask precedent). The GUI is a
fake window built from the two real mixins with stubbed
labels/timers/worker; DATA_DIR is pointed at a temp dir per
test so the real data/ is never touched. No real network,
no real audio, no real-time sleeps.
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
    drop_log_line,
    give_up_caption,
    reconnect_caption,
)

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
# Part A: the pure classifier (both interpreters)
# ----------------------------------------------------------------------

class TestClassifyDrop(unittest.TestCase):
    def test_each_cause_is_distinct(self):
        cases = {
            "network": "ERROR: Timed out reaching Google's voice service",
            "server_ended": "LIVE_GOAWAY",
            "handle_refused": "1008 policy violation: resumption handle expired",
            "already_connected": (
                "1008 session is already connected to an existing client"),
            "watchdog_silence": "LIVE_WATCHDOG",
            "mic_stall": "mic callbacks stalled",
            "fatal": "ERROR: Missing API key",
        }
        seen = set()
        for cause, text in cases.items():
            if cause == "server_ended":
                got = classify_drop(text, go_away=True)
            elif cause == "watchdog_silence":
                got = classify_drop(text, watchdog=True)
            elif cause == "mic_stall":
                got = classify_drop(text, mic_stalled=True)
            elif cause == "fatal":
                got = classify_drop(text, fatal=True)
            else:
                got = classify_drop(text)
            self.assertEqual(got, cause, text)
            seen.add(got)
        self.assertEqual(len(seen), len(cases))

    def test_already_connected_beats_handle_markers(self):
        # The field error carries BOTH the 1008 code and the phrase;
        # a stale-first check would misname it (the LIVEFIX lesson).
        self.assertEqual(
            classify_drop("1008: session already connected"),
            "already_connected")
        self.assertEqual(
            classify_drop("1008: handle invalid"),
            "handle_refused")

    def test_fatal_wins_over_everything(self):
        self.assertEqual(
            classify_drop("1008 already connected", fatal=True), "fatal")

    def test_bare_disconnect_is_unknown_not_a_guess(self):
        self.assertEqual(classify_drop("DISCONNECTED"), "unknown")
        self.assertEqual(classify_drop(""), "unknown")

    def test_network_markers(self):
        for text in ("timed out", "connection reset", "network unreachable",
                     "getaddrinfo failed", "1006 abnormal closure"):
            self.assertEqual(classify_drop(text), "network", text)

    def test_captions_are_plain_words_per_cause(self):
        for cause in DROP_CAUSES:
            caption = reconnect_caption(cause)
            self.assertTrue(caption, cause)
            self.assertNotIn("1008", caption, cause)
            self.assertNotIn("ERROR", caption, cause)

    def test_give_up_captions_keep_the_pinned_shape(self):
        for cause in DROP_CAUSES:
            caption = give_up_caption(cause)
            self.assertIn("Couldn't reconnect", caption, cause)
            self.assertIn("press Connect", caption, cause)

    def test_give_up_captions_differ_by_cause(self):
        self.assertNotEqual(
            give_up_caption("network"), give_up_caption("unknown"))
        self.assertIn("kept dropping", give_up_caption("network"))
        self.assertIn("going quiet", give_up_caption("watchdog_silence"))

    def test_drop_log_line_carries_cause_and_raw(self):
        line = drop_log_line("network", "ERROR: Timed out")
        self.assertEqual(line, "[GUI] DROP network: ERROR: Timed out")
        self.assertEqual(drop_log_line("unknown"), "[GUI] DROP unknown")


# ----------------------------------------------------------------------
# Part B: the session mixin (Qt only)
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


class _FakeWorker:
    def __init__(self):
        self.alive = True
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


@unittest.skipUnless(_QT, "PySide6 needed for the session-mixin tests")
class TestVoiceDropMixin(unittest.TestCase):
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
        win._systems = []
        win._captions = []
        win._pills = []
        win._add_system = lambda text: win._systems.append(text)
        win._set_caption = lambda text: win._captions.append(text)
        win._set_pill = lambda text: win._pills.append(text)
        self.win = win
        self.timer = win._reconnect_timer

    def _log_text(self):
        log = Path(self._tmp.name) / "activity.log"
        return log.read_text(encoding="utf-8") if log.exists() else ""

    def _connect_live(self):
        """A live session, as the GUI sees it after Connect."""
        self.win._was_connected = True
        self.win._connected = True
        self.win._on_status("LISTENING")

    # -- classification + log (L1, L2) ----------------------------------

    def test_network_drop_names_cause_and_logs_it(self):
        self._connect_live()
        self.win._on_status(
            "ERROR: Timed out reaching Google's voice service")
        record = self.win._last_drop
        self.assertEqual(record["cause"], "network")
        self.assertIn("connection to Google dropped",
                      self.win._captions[-1])
        self.assertEqual(len(self.timer.starts), 1)
        self.assertEqual(self.timer.starts[0], 2000)
        log = self._log_text()
        self.assertIn("[GUI] DROP network: ERROR: Timed out", log)
        # The v5.29 ERROR-status line is preserved alongside it.
        self.assertIn("[GUI] ERROR status: ERROR: Timed out", log)

    def test_bare_disconnect_logs_a_drop_line(self):
        self._connect_live()
        self.win._on_status("DISCONNECTED")
        self.assertEqual(self.win._last_drop["cause"], "unknown")
        self.assertIn("[GUI] DROP unknown: DISCONNECTED", self._log_text())

    def test_goaway_classifies_server_ended(self):
        self._connect_live()
        self.win._on_status("LIVE_GOAWAY")
        self.assertTrue(self.win._go_away_seen)
        self.win._on_status("DISCONNECTED")
        self.assertEqual(self.win._last_drop["cause"], "server_ended")
        self.assertIn("Google ended the session",
                      self.win._captions[-1])

    def test_handle_refused_and_already_connected_distinct(self):
        self._connect_live()
        self.win._on_status(
            "ERROR: 1008 resumption handle expired")
        self.assertEqual(self.win._last_drop["cause"], "handle_refused")
        self.win._reconnect_timer.stop()
        self.win._reconnect_pending = False
        self.win._reconnect_attempts = 0
        self.win._last_drop = None
        self.win._on_status(
            "ERROR: 1008 session already connected to an existing client")
        self.assertEqual(
            self.win._last_drop["cause"], "already_connected")

    def test_first_specific_cause_wins_over_the_pair(self):
        # LIVE_WATCHDOG records watchdog_silence; the DISCONNECTED
        # that follows the same fall must not overwrite it.
        self._connect_live()
        self.win._on_status("LIVE_WATCHDOG")
        self.assertEqual(
            self.win._last_drop["cause"], "watchdog_silence")
        self.win._on_status("DISCONNECTED")
        self.assertEqual(
            self.win._last_drop["cause"], "watchdog_silence")
        self.assertIn("went quiet after you spoke",
                      self.win._captions[-1])

    # -- fatal / duplicate handling (already hardened, pinned) -----------

    def test_fatal_error_does_not_retry(self):
        self._connect_live()
        self.win._on_status("ERROR: Missing API key")
        self.assertEqual(self.win._last_drop["cause"], "fatal")
        self.assertEqual(self.timer.starts, [])
        self.assertFalse(self.win._reconnect_pending)
        self.assertIn("key", self.win.state_label.text().lower())

    def test_duplicate_drop_statuses_schedule_one_attempt(self):
        self._connect_live()
        self.win._on_status("ERROR: Timed out reaching Google")
        self.win._on_status("DISCONNECTED")
        self.assertEqual(len(self.timer.starts), 1)

    # -- cross-episode budget / zombie loop (L3) ------------------------

    def _zombie_episode(self):
        """One zombie cycle: an episode ends on LISTENING, then
        the session drops again. Returns the drop handling."""
        self.win._reconnect_pending = True
        self.win._reconnect_attempts = 1
        self.win._on_status("LISTENING")
        return self.win._on_status("DISCONNECTED")

    def test_transient_drop_resumes_and_real_response_resets_budget(self):
        self._connect_live()
        self.win._reconnect_pending = True
        self.win._reconnect_attempts = 1
        self.win._on_status("LISTENING")
        self.assertEqual(self.win._reconnect_episodes_failed, 1)
        # A real server response proves the service alive again.
        self.win._on_status("SPEAKING")
        self.assertEqual(self.win._reconnect_episodes_failed, 0)

    def test_zombie_episodes_exhaust_instead_of_looping_forever(self):
        self._connect_live()
        self._zombie_episode()
        self.assertEqual(self.win._reconnect_episodes_failed, 1)
        self.assertEqual(len(self.timer.starts), 1)
        self._zombie_episode()
        self.assertEqual(self.win._reconnect_episodes_failed, 2)
        self.assertEqual(len(self.timer.starts), 2)
        # Third zombie cycle: LISTENING strikes a third time, then
        # the watchdog death that follows it must give up.
        self.win._reconnect_pending = True
        self.win._reconnect_attempts = 1
        self.win._on_status("LISTENING")
        self.assertEqual(self.win._reconnect_episodes_failed, 3)
        self.win._on_status("LIVE_WATCHDOG")
        self.win._on_status("DISCONNECTED")
        self.assertTrue(self.win._reconnect_exhausted)
        self.assertEqual(len(self.timer.starts), 2)
        self.assertIn("kept going quiet", self.win._captions[-1])
        self.assertIn("Couldn't reconnect", self.win._systems[-1])

    def test_episode_window_resets_stale_strikes(self):
        self.win._reconnect_episodes_failed = 2
        self.win._last_episode_end_at = time.monotonic() - 700.0
        self.win._begin_reconnect()
        self.assertEqual(self.win._reconnect_episodes_failed, 0)
        self.assertEqual(len(self.timer.starts), 1)

    # -- terminal give-up (L4, L5) --------------------------------------

    def test_give_up_is_terminal_and_one_tap_recoverable(self):
        self._connect_live()
        worker = _FakeWorker()
        self.win.worker = worker
        self.win._reconnect_attempts = 4
        self.win._on_status("DISCONNECTED")
        self.assertTrue(self.win._reconnect_exhausted)
        self.assertFalse(self.win._was_connected)
        # The wedged worker is stopped, so Connect is one tap:
        # the button offers a fresh start, not Cancel.
        self.assertEqual(worker.stops, 1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.win.btn_connect.text, "Connect")
        # The give-up is posted once in chat and once in the log.
        give_ups = [s for s in self.win._systems
                    if s.startswith("Couldn't reconnect")]
        self.assertEqual(len(give_ups), 1)
        self.assertIn("[GUI] DROP give-up", self._log_text())
        # A late status after give-up starts NO new episode.
        self.timer.starts.clear()
        self.win._on_status("DISCONNECTED")
        self.assertEqual(self.timer.starts, [])
        self.assertTrue(self.win._reconnect_exhausted)
        give_ups = [s for s in self.win._systems
                    if s.startswith("Couldn't reconnect")]
        self.assertEqual(len(give_ups), 1)

    def test_attempt_refused_gives_up_without_spinning(self):
        self.win._was_connected = True
        self.win._reconnect_pending = True
        self.win._reconnect_attempts = 4
        self.win._start_connection = lambda: False
        self.win._attempt_reconnect()
        self.assertTrue(self.win._reconnect_exhausted)
        self.assertEqual(self.timer.starts, [])
        # A second timer fire while latched is a no-op.
        self.win._attempt_reconnect()
        give_ups = [s for s in self.win._systems
                    if s.startswith("Couldn't reconnect")]
        self.assertEqual(len(give_ups), 1)

    def test_human_connect_clears_give_up_latch(self):
        import voice_assistant.gui.window_session_connection as _wsc
        from voice_assistant.core import config as _cfgmod
        self.win._reconnect_exhausted = True
        self.win._reconnect_episodes_failed = 2
        fake_audio = SimpleNamespace(start=lambda: None)
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

        self.win._maybe_mic_first_use_ask = lambda user_started: None
        with mock.patch.object(_wsc, "audio_manager", fake_audio), \
                mock.patch.object(
                    _wsc, "BackendWorker", _FakeBackendWorker), \
                mock.patch.object(
                    _cfgmod.config, "selected_provider_key_present",
                    return_value=True):
            self.assertTrue(
                self.win._start_connection(user_started=True))
        self.assertFalse(self.win._reconnect_exhausted)
        self.assertEqual(self.win._reconnect_episodes_failed, 0)
        self.assertEqual(len(started), 1)
        # ... but an auto-reconnect start must NOT clear it.
        self.win.worker = None
        self.win._reconnect_exhausted = True
        self.win._reconnect_episodes_failed = 2
        with mock.patch.object(_wsc, "audio_manager", fake_audio), \
                mock.patch.object(
                    _wsc, "BackendWorker", _FakeBackendWorker), \
                mock.patch.object(
                    _cfgmod.config, "selected_provider_key_present",
                    return_value=True):
            self.assertTrue(
                self.win._start_connection(user_started=False))
        self.assertTrue(self.win._reconnect_exhausted)
        self.assertEqual(self.win._reconnect_episodes_failed, 2)

    # -- mic stall surfacing (L6) ---------------------------------------

    def test_mic_stall_noted_once_per_episode(self):
        self.win._note_mic_stall({"device": "USB Mic"})
        self.win._note_mic_stall({"device": "USB Mic"})
        log = self._log_text()
        self.assertEqual(log.count("[GUI] DROP mic_stall"), 1)
        self.assertIn("USB Mic", log)
        self.win._mic_stall_noted = False
        self.win._note_mic_stall({"device": "USB Mic"})
        self.assertEqual(
            self._log_text().count("[GUI] DROP mic_stall"), 2)

    # -- scope pin -------------------------------------------------------

    def test_task_pause_is_out_of_scope(self):
        # Fix 9 owns pausing a task on a drop. This batch proves
        # the absence it must build on: no session-drop hook
        # exists in the coordinator, and this module never calls
        # pause_task — so a drop today neither pauses nor loses
        # a task by design here; Fix 9 decides the behaviour.
        coord_src = Path(
            __file__).resolve().parent.parent.joinpath(
                "core/brain/task_coordinator.py").read_text(
                    encoding="utf-8")
        self.assertNotIn("DISCONNECTED", coord_src)
        status_src = Path(
            __file__).resolve().parent.parent.joinpath(
                "gui/window_session_status.py").read_text(
                    encoding="utf-8")
        self.assertNotIn("pause_task", status_src)


if __name__ == "__main__":
    unittest.main()
