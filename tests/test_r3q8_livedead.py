"""Round-3 fix Q8 — dead-on-arrival Live sessions.

Section B F2: 2 of 12 Live sessions connected (CONNECTING ->
LISTENING) but the model never responded to any turn — no
transcript, no audio, no tool call, no error status. The driver
detected it (session_dead) and aborted; the product did not.

The gap was typed turns. The RS-1 watchdog only watched
voice/audible turns: a voice turn into a dead session trips
rule 1 (~10 s, LIVE_WATCHDOG notice, GUI auto-reconnect — the
Q3 repro proves it). But send_text stamped NOTHING on the
watchdog's clocks, so a typed turn into a dead session sat
forever with no detection, no notice, no retry.

The fix (three seams, no new machinery):
- send_text stamps _last_text_at for genuine user text only
  (bracketed VYUK-internal injections expect no reply and
  must never arm the clock).
- The watchdog gains a fourth rule: a typed turn unanswered
  for _WATCHDOG_SILENCE_S (7.0 s, the same budget as rule 1)
  ends the session with a LIVE_DEAD notice. A typed turn is
  complete at send time, so it carries no quiescence
  condition; any yielded response after the text disarms it.
- The GUI renders LIVE_DEAD as its own plain-words notice and
  records the no_reply drop cause, so the Fix 4 episode budget
  counts dead episodes (3 strikes, then the give-up posts its
  own line). The auto-retry IS the existing reconnect loop —
  no second path: LIVE_DEAD ends the session exactly like a
  watchdog death, DISCONNECTED follows, and _handle_session_drop
  schedules the next attempt.

Deliberate non-goal: a session that has had NO turn sent owes
no response yet — an idle session at LISTENING is not provably
dead, so nothing arms until the first turn. (That is also why
the idle test below must never trip.)

Harness notes:
- Part A drives the REAL `_live_watchdog_loop` on a real
  `GeminiLiveSession` (the test_voicecontrol B1 pattern), and
  the REAL `send_text` against a fake server session.
- google-genai is absent from the sys interpreter, so the
  send_text test installs a minimal types shim there (Content /
  Part.from_text returning dummies) — the shim never touches
  the stamp logic, and the Qt leg runs the same test against
  real genai.
- Part C reuses the test_voice_drop_silent fake-window fixture
  (the two real mixins, stubbed labels/timers, DATA_DIR in a
  temp dir) and needs PySide6.
"""
import asyncio
import os
import shutil
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
from core.voice import live_client  # noqa: E402
from core.voice import live_client_helpers as _lh  # noqa: E402
from core.voice import live_client_lifecycle as _lc  # noqa: E402
from core.voice import live_client_resume as _lr  # noqa: E402
from core.voice import live_client_send as _lcs  # noqa: E402

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
# Part A: the typed-turn watchdog rule (both interpreters)
# ----------------------------------------------------------------------

class _WatchdogBase(unittest.TestCase):
    """Temp resume file + log capture, mirroring
    tests/test_voicecontrol.py's _LiveFixBase."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="r3q8_livedead_")
        self.addCleanup(shutil.rmtree, self._tmpdir, True)
        self._resume_file = Path(self._tmpdir) / "live_resume.json"
        self._orig_override = live_client._resume_path_override
        live_client._resume_path_override = self._resume_file
        self._orig_override_recv = _lr._resume_path_override
        _lr._resume_path_override = self._resume_file
        self.log_lines = []
        self._log_patcher = mock.patch.object(
            live_client, "_log_line", new=self.log_lines.append)
        self._log_patcher.start()
        self.addCleanup(self._log_patcher.stop)
        self._log_patcher_mixin = mock.patch.object(
            _lc, "_log_line", new=self.log_lines.append)
        self._log_patcher_mixin.start()
        self.addCleanup(self._log_patcher_mixin.stop)
        self.addCleanup(self._restore_override)

    def _restore_override(self):
        live_client._resume_path_override = self._orig_override
        _lr._resume_path_override = self._orig_override_recv
        if self._resume_file.exists():
            self._resume_file.unlink()

    def _new_session(self):
        session = live_client.GeminiLiveSession()
        session._running = True
        session.is_connected = True
        session._last_response_at = time.monotonic()
        session._last_voice_at = None
        session._last_audible_at = None
        session._last_text_at = None
        session._last_send_failure_at = None
        session._last_send_success_at = None
        return session

    def _assert_no_trip(self, session, notes, wait_s=2.5):
        """Run the real watchdog loop for wait_s and prove it is
        still running with no LIVE_DEAD / LIVE_WATCHDOG (the B1
        bare-task pattern — wait_for cannot prove 'still
        running')."""
        async def _run():
            task = asyncio.create_task(session._live_watchdog_loop())
            await asyncio.sleep(wait_s)
            try:
                self.assertFalse(task.done())
                self.assertNotIn("LIVE_DEAD", notes)
                self.assertNotIn("LIVE_WATCHDOG", notes)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(_run())

    def _assert_trips(self, session, notes, expect, timeout_s=5.0):
        """Run the real watchdog loop and prove it returns with
        the expected notice inside timeout_s."""
        async def _run():
            await asyncio.wait_for(
                session._live_watchdog_loop(), timeout=timeout_s)
            self.assertIn(expect, notes)

        asyncio.run(_run())


class _FakeServerSession:
    """A server session whose send_client_content succeeds and
    whose receive() yields nothing — the dead-server shape."""

    def __init__(self):
        self.sent = []

    async def send_client_content(self, turns=None, turn_complete=None):
        self.sent.append((turns, turn_complete))

    async def receive(self):
        if False:
            yield


class TestTypedTurnWatchdog(_WatchdogBase):
    """The Q8 gap, closed: a typed turn into a dead session."""

    def test_typed_turn_dead_trips_live_dead(self):
        # Typed turn 8 s ago, no server response ever: the
        # session is dead on arrival and must end with LIVE_DEAD.
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        session._last_response_at = time.monotonic() - 8.0
        session._last_text_at = time.monotonic() - 8.0
        self._assert_trips(session, notes, "LIVE_DEAD")

    def test_typed_turn_answered_does_not_trip(self):
        # The server answered 1 s ago, after the text: the turn
        # is answered, so nothing is owed and nothing trips.
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        session._last_response_at = time.monotonic() - 1.0
        session._last_text_at = time.monotonic() - 8.0
        self._assert_no_trip(session, notes)

    def test_typed_turn_fresh_does_not_trip(self):
        # The text was just sent: the model has its 7 s to
        # answer, so a fresh turn never trips.
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        session._last_response_at = time.monotonic() - 8.0
        session._last_text_at = time.monotonic()
        self._assert_no_trip(session, notes)

    def test_voice_turn_dead_still_trips_watchdog(self):
        # The voice rule is unmoved by Q8: a voice turn into a
        # dead session still ends with LIVE_WATCHDOG, not
        # LIVE_DEAD (the notice must name the right channel).
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        session._last_response_at = time.monotonic() - 8.0
        session._last_voice_at = time.monotonic() - 6.0
        self._assert_trips(session, notes, "LIVE_WATCHDOG")

    def test_idle_session_never_trips(self):
        # No turn sent at all: the session owes no response yet,
        # so an idle session at LISTENING never trips (the
        # deliberate non-goal — an idle session is not provably
        # dead).
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        session._last_response_at = time.monotonic() - 60.0
        self._assert_no_trip(session, notes)


class TestSendTextStamps(_WatchdogBase):
    """The stamp itself: real send_text, fake server session."""

    def _install_types_shim_if_needed(self):
        """google-genai is absent on sys: stand in a minimal
        types shim (Content/Part.from_text returning dummies)
        so the real send_text body still runs. On Qt genai is
        real and no shim is installed. Restored afterwards."""
        if getattr(_lcs, "types", None) is not None:
            return

        class _StubTypes:
            class Part:
                @staticmethod
                def from_text(text=None):
                    return ("part", text)

            class Content:
                def __init__(self, parts=None, role=None):
                    self.parts = parts
                    self.role = role

        self._orig_types = _lcs.types
        _lcs.types = _StubTypes
        self.addCleanup(setattr, _lcs, "types", self._orig_types)

    def test_send_text_stamps_genuine_text_only(self):
        self._install_types_shim_if_needed()
        from core.memory.memory import memory_store as _ms

        async def _run():
            session = self._new_session()
            session._session = _FakeServerSession()
            session.on_transcript = None
            with mock.patch.object(_ms, "log_message"):
                await session.send_text("what time is it")
                stamped = getattr(session, "_last_text_at", None)
                self.assertIsNotNone(stamped)
                await session.send_text(
                    "[Ambient context update — do not reply]")
                self.assertEqual(
                    getattr(session, "_last_text_at", None), stamped)

        asyncio.run(_run())

    def test_send_text_without_session_does_not_stamp(self):
        # No live session: the text was never sent, so nothing
        # stamps (a dropped session must not arm the clock).
        self._install_types_shim_if_needed()

        async def _run():
            session = self._new_session()
            session._session = None
            session.is_connected = False
            await session.send_text("what time is it")
            self.assertIsNone(getattr(session, "_last_text_at", None))

        asyncio.run(_run())


class TestWatchdogBudgetsUnmoved(_WatchdogBase):
    """Q8 adds a rule, it moves no threshold: the typed-turn
    rule reuses the 7 s silence budget (rule 1's), and every
    other budget is exactly what RS-1 / Q3 set."""

    def test_budgets_unmoved(self):
        self.assertEqual(_lh._WATCHDOG_SILENCE_S, 7.0)
        self.assertEqual(_lh._WATCHDOG_AUDIBLE_SILENCE_S, 30.0)
        self.assertEqual(_lh._WATCHDOG_SEND_FAIL_S, 15.0)
        self.assertEqual(_lh._WATCHDOG_TICK_S, 1.0)
        self.assertEqual(_lh._WATCHDOG_VOICE_QUIET_S, 5.0)


# ----------------------------------------------------------------------
# Part B: the no_reply drop cause (both interpreters)
# ----------------------------------------------------------------------

class TestNoReplyCause(unittest.TestCase):
    def test_no_reply_cause_exists_and_is_distinct(self):
        self.assertIn("no_reply", DROP_CAUSES)
        self.assertEqual(
            classify_drop("LIVE_DEAD", no_reply=True), "no_reply")

    def test_no_reply_precedence(self):
        # Fatal and watchdog are more specific; no_reply beats
        # the text-matched causes (it comes from the drop point,
        # not from a status string).
        self.assertEqual(
            classify_drop("1008 already connected", fatal=True), "fatal")
        self.assertEqual(
            classify_drop("LIVE_WATCHDOG", watchdog=True, no_reply=True),
            "watchdog_silence")
        self.assertEqual(
            classify_drop("ERROR: Timed out", no_reply=True), "no_reply")
        self.assertEqual(classify_drop("DISCONNECTED"), "unknown")

    def test_captions_are_plain_words(self):
        caption = reconnect_caption("no_reply")
        self.assertIn("never answered", caption)
        self.assertNotIn("ERROR", caption)
        self.assertNotIn("LIVE_DEAD", caption)

    def test_give_up_caption_keeps_the_pinned_shape(self):
        caption = give_up_caption("no_reply")
        self.assertIn("Couldn't reconnect", caption)
        self.assertIn("press Connect", caption)
        self.assertIn("never answering", caption)

    def test_all_causes_still_have_captions(self):
        for cause in DROP_CAUSES:
            self.assertTrue(reconnect_caption(cause), cause)
            self.assertTrue(give_up_caption(cause), cause)


# ----------------------------------------------------------------------
# Part C: the LIVE_DEAD notice + bounded retry (Qt only)
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
    def __init__(self, values=None):
        self._values = dict(values or {})

    def value(self, _key, default=None, type=None):  # noqa: A002
        return self._values.get(_key, default)


@unittest.skipUnless(_QT, "PySide6 needed for the LIVE_DEAD tests")
class TestLiveDeadMixin(unittest.TestCase):
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
        win.settings = _FakeSettings(
            {"presence/auto_reconnect": True})
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

    def _log_text(self):
        log = Path(self._tmp.name) / "activity.log"
        return log.read_text(encoding="utf-8") if log.exists() else ""

    def _connect_live(self):
        """A live session, as the GUI sees it after Connect."""
        self.win._was_connected = True
        self.win._connected = True
        self.win._on_status("LISTENING")

    def _dead_episode(self):
        """One dead-on-arrival episode: the session reports
        LIVE_DEAD, ends, and — while the budget holds — the
        fresh session reports LISTENING."""
        self.win._on_status("LIVE_DEAD")
        self.win._on_status("DISCONNECTED")
        if self.win._reconnect_pending:
            self.win._on_status("LISTENING")

    def test_live_dead_posts_plain_notice_and_records_no_reply(self):
        self._connect_live()
        self.win._on_status("LIVE_DEAD")
        # Plain words, no technical dump — and the words name
        # the typed channel ("message", not "spoke").
        self.assertTrue(any(
            "never answered" in s and "fresh one" in s
            for s in self.win._systems))
        self.assertFalse(any("LIVE_DEAD" in s for s in self.win._systems))
        record = self.win._last_drop
        self.assertEqual(record["cause"], "no_reply")
        self.assertEqual(
            self.win.state_label.text(), "Live session never answered")
        self.assertEqual(self.win._pills[-1], "Reconnecting")
        self.assertIn("never answered", self.win._captions[-1])
        self.assertIn("[GUI] DROP no_reply: LIVE_DEAD", self._log_text())
        # The watchdog streak is untouched: a dead session is
        # not an unanswered SPOKEN turn, so the mic diagnostic
        # must never fire for it.
        self.assertEqual(self.win._watchdog_streak, 0)

    def test_live_dead_drop_auto_reconnects(self):
        self._connect_live()
        self.win._on_status("LIVE_DEAD")
        self.win._on_status("DISCONNECTED")
        # The existing reconnect loop is the auto-retry: an
        # attempt is scheduled at the first backoff delay, and
        # nothing has given up.
        self.assertTrue(self.win._reconnect_pending)
        self.assertEqual(self.timer.starts, [2000])
        self.assertFalse(self.win._reconnect_exhausted)

    def test_three_dead_episodes_give_up(self):
        # The Fix 4 episode budget is the bound: three dead
        # episodes strike out, and the fourth dead drop gives
        # up instead of starting another episode.
        self._connect_live()
        for _ in range(3):
            self._dead_episode()
        self.assertEqual(self.win._reconnect_episodes_failed, 3)
        # Each dead episode ended on LISTENING alone, so the
        # reconnect timer is stopped — the next dead drop, not
        # a pending timer, is what gives up.
        self.assertFalse(self.win._reconnect_pending)
        self.win._on_status("LIVE_DEAD")
        self.win._on_status("DISCONNECTED")
        self.assertTrue(self.win._reconnect_exhausted)
        self.assertFalse(self.win._reconnect_pending)
        self.assertEqual(self.win._pills[-1], "Offline")
        self.assertTrue(any(
            "Couldn't reconnect" in s and "never answering" in s
            and "press Connect" in s for s in self.win._systems))
        self.assertIn("[GUI] DROP give-up no_reply", self._log_text())

    def test_live_session_answering_resets_the_budget(self):
        # One real server response (SPEAKING) clears the dead
        # strikes — a service that finally answers is alive,
        # and unrelated dead episodes must never accumulate
        # into a false give-up.
        self._connect_live()
        self._dead_episode()
        self.assertEqual(self.win._reconnect_episodes_failed, 1)
        self.win._on_status("SPEAKING")
        self.assertEqual(self.win._reconnect_episodes_failed, 0)
        self._dead_episode()
        self.assertEqual(self.win._reconnect_episodes_failed, 1)
        self.assertFalse(self.win._reconnect_exhausted)


if __name__ == "__main__":
    unittest.main()
