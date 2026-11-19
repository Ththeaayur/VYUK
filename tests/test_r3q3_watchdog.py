"""Round-3 fix Q3 — the RS-1 watchdog killed long spoken turns.

Section B: a 7.3 s utterance was killed twice, a 4.9 s turn works.
Root cause: the watchdog's first rule tripped on response-plane
silence over 7.0 s while the user was still speaking — but the
server yields nothing until the turn ends, so the silence clock
measured TURN LENGTH, not server silence after the user finished
speaking. The fix (at the watchdog, per the parent ruling) adds
voice quiescence as a second trip condition: the user must have
stopped sending voice-level audio for _WATCHDOG_VOICE_QUIET_S
(5.0 s) before the silence rules may trip. The 7.0 s / 30.0 s
budgets do NOT move — a dead session still dies (after a short
turn ~10 s; after a long monologue, turn end + 5 s), and a
genuinely stuck session can never run forever.

Every test drives the REAL `_live_watchdog_loop` on a real
`GeminiLiveSession` (the test_voicecontrol B1 pattern) — no
stubbed timer, only the session object is a test double with
stamped state. Pre-aged stamps are the standard seam for the
time-based guards (B1 pre-ages `_last_send_failure_at` the
same way); the headline long-turn case streams voice in real
time for 9 s, like the field case.
"""
import asyncio
import pathlib
import shutil
import tempfile
import time
import unittest
from unittest import mock

from core.voice import live_client
from core.voice import live_client_helpers as _lh
from core.voice import live_client_lifecycle as _lc
from core.voice import live_client_resume as _lr


class _WatchdogBase(unittest.TestCase):
    """Temp resume file + log capture, mirroring
    tests/test_voicecontrol.py's _LiveFixBase."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="r3q3_watchdog_")
        self.addCleanup(shutil.rmtree, self._tmpdir, True)
        self._resume_file = pathlib.Path(self._tmpdir) / "live_resume.json"
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
        session._last_send_failure_at = None
        session._last_send_success_at = None
        return session

    def _assert_no_trip(self, session, notes, wait_s=2.5):
        """Run the real watchdog loop for wait_s and prove it is
        still running with no LIVE_WATCHDOG (the B1 bare-task
        pattern — wait_for cannot prove "still running")."""
        async def _run():
            task = asyncio.create_task(session._live_watchdog_loop())
            await asyncio.sleep(wait_s)
            try:
                self.assertFalse(task.done())
                self.assertNotIn("LIVE_WATCHDOG", notes)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(_run())

    def _assert_trips(self, session, notes, timeout_s=5.0):
        """Run the real watchdog loop and prove it returns with a
        LIVE_WATCHDOG notice inside timeout_s."""
        async def _run():
            await asyncio.wait_for(
                session._live_watchdog_loop(), timeout=timeout_s)
            self.assertIn("LIVE_WATCHDOG", notes)

        asyncio.run(_run())


class TestLongSpokenTurnSurvives(_WatchdogBase):
    """The field case: 9 s of continuous voice, server silent
    throughout (section B's 7.3 s turn was killed twice)."""

    def test_long_spoken_turn_survives(self):
        async def _run():
            session = self._new_session()
            notes = []
            session.on_status_change = notes.append
            stop = asyncio.Event()
            t0 = time.monotonic()

            async def _voice_stream():
                while time.monotonic() - t0 < 9.0 and not stop.is_set():
                    session._last_voice_at = time.monotonic()
                    await asyncio.sleep(0.4)

            watchdog = asyncio.create_task(session._live_watchdog_loop())
            streamer = asyncio.create_task(_voice_stream())
            await streamer
            # The turn ended and the server answered it.
            session._last_response_at = time.monotonic()
            await asyncio.sleep(2.0)
            try:
                self.assertFalse(watchdog.done())
                self.assertNotIn("LIVE_WATCHDOG", notes)
            finally:
                stop.set()
                session._running = False
                watchdog.cancel()
                await asyncio.gather(watchdog, return_exceptions=True)

        asyncio.run(_run())


class TestQuiescenceBoundary(_WatchdogBase):
    """Pre-aged stamps pin the quiescence condition itself."""

    def test_short_turn_survives(self):
        # 4.9 s field case, pre-aged: response 5 s ago, voice now.
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        session._last_response_at = time.monotonic() - 5.0
        session._last_voice_at = time.monotonic()
        self._assert_no_trip(session, notes)

    def test_long_silence_while_user_still_speaking_does_not_trip(self):
        # Server silent 20 s, but the user is speaking RIGHT NOW
        # (a 20 s monologue): no trip — the server's silence is
        # correct while voice is still arriving.
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        session._last_response_at = time.monotonic() - 20.0
        session._last_voice_at = time.monotonic()
        self._assert_no_trip(session, notes)

    def test_dead_session_still_killed(self):
        # Voice 6 s ago, response 8 s ago: the user stopped and
        # the server never answered — the failsafe must fire.
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        session._last_response_at = time.monotonic() - 8.0
        session._last_voice_at = time.monotonic() - 6.0
        self._assert_trips(session, notes)

    def test_idle_session_never_trips(self):
        # No voice since the last response means nothing is owed
        # (the RS-1 invariant, unchanged by Q3).
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        session._last_response_at = time.monotonic() - 60.0
        session._last_voice_at = None
        session._last_audible_at = None
        self._assert_no_trip(session, notes)


class TestAudibleRuleQuiescence(_WatchdogBase):
    """The same guard on the second (audible) rule: a quiet
    monologue over 30 s is the same bug at the audible floor."""

    def test_quiet_voice_monologue_survives(self):
        # Server silent 35 s, audible audio arriving right now.
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        session._last_response_at = time.monotonic() - 35.0
        session._last_audible_at = time.monotonic()
        self._assert_no_trip(session, notes)

    def test_quiet_voice_dead_session_still_killed(self):
        # Audible audio 6 s ago, response 35 s ago: the user
        # stopped quietly and the server never answered.
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        session._last_response_at = time.monotonic() - 35.0
        session._last_audible_at = time.monotonic() - 6.0
        self._assert_trips(session, notes)


class TestWatchdogBudgetsUnmoved(_WatchdogBase):
    """Q3 adds a condition, it moves no threshold: the 7 s / 30 s
    budgets, the 15 s send-failure bound and the 1 s tick are
    exactly what RS-1 set."""

    def test_budgets_unmoved(self):
        self.assertEqual(_lh._WATCHDOG_SILENCE_S, 7.0)
        self.assertEqual(_lh._WATCHDOG_AUDIBLE_SILENCE_S, 30.0)
        self.assertEqual(_lh._WATCHDOG_SEND_FAIL_S, 15.0)
        self.assertEqual(_lh._WATCHDOG_TICK_S, 1.0)
        self.assertEqual(_lh._WATCHDOG_VOICE_QUIET_S, 5.0)


if __name__ == "__main__":
    unittest.main()
