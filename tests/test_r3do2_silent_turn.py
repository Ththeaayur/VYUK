"""Round-3 section D fix D-O2 — the silent turn.

Section D soak: 15 of 446 non-quiet turns went unanswered
(3.4%; chatty 11/71) — no assistant transcript after the
turn's anchor, session stayed alive, watchdog recorded no
trip. The user spoke or typed and got NOTHING.

Root cause (proven by the repro, r3do2_repro.py): EVERY
yielded response stamps _last_response_at, tool calls
included — so a turn where the model calls a tool and then
never speaks disarms every watchdog rule (voice, audible,
Q8 typed): the tool call is newer than the turn, so the
"turn after last response" condition fails forever. The tool
runs, its result goes back, the model stays silent, the user
hears nothing, the session stays "alive".

The fix (three seams, no new machinery, no moved budget):
- A reply clock, _last_reply_at — stamped only by model
  audio the user hears and by an assistant transcript
  actually reaching the user. Tool calls, resumption
  updates and streamed chunks do NOT stamp it (a reply
  stalling mid-stream must still trip).
- Tool-batch bookkeeping in _tool_worker_loop: _tool_in_flight
  while a batch runs, _last_tool_done_at when it finishes.
- Watchdog rule 5, the answered-turn rule: a turn sent after
  the last reply, itself 7 s old, with no reply for the 7 s
  silence budget after the last server activity or tool
  completion, and no tool batch running or queued, ends the
  session with the channel's notice (voice -> LIVE_WATCHDOG,
  typed -> LIVE_DEAD) — the Q8 precedent: the user is told
  and auto-reconnect retries. Long tools never trip (a batch
  still running holds the rule off; the 7 s runs from its
  completion, not the call); a confirm gate holds the same
  way. The GUI renders both notices already (Q8 Part C, Q3
  suite) — no new GUI surface, so no GUI tests here.

Harness notes: Part A drives the REAL _live_watchdog_loop
on a real GeminiLiveSession (the test_voicecontrol B1
pattern). Part B drives the REAL _receive_loop / tool
worker / flush against a fake server session. Part C is
the end-to-end repro shape, shortened. google-genai is
absent from the sys interpreter, so the send/receive tests
install minimal types shims there (the Q8 precedent) — the
shims never touch the stamp logic, and the Qt leg runs the
same tests against real genai.
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

from core.voice import live_client  # noqa: E402
from core.voice import live_client_helpers as _lh  # noqa: E402
from core.voice import live_client_lifecycle as _lc  # noqa: E402
from core.voice import live_client_receive as _lcr  # noqa: E402
from core.voice import live_client_resume as _lr  # noqa: E402
from core.voice import live_client_send as _lcs  # noqa: E402
from core.memory.memory import memory_store as _ms  # noqa: E402


def _install_types_shims(testcase):
    """google-genai is absent on sys: stand in minimal types
    shims (Content / Part.from_text for send_text,
    FunctionResponse for tool-result delivery) so the real
    send / receive bodies still run. On Qt genai is real and
    no shim is installed. Restored afterwards."""
    if getattr(_lcs, "types", None) is None:
        class _StubTypes:
            class Part:
                @staticmethod
                def from_text(text=None):
                    return ("part", text)

            class Content:
                def __init__(self, parts=None, role=None):
                    self.parts = parts
                    self.role = role

        _orig = _lcs.types
        _lcs.types = _StubTypes
        testcase.addCleanup(setattr, _lcs, "types", _orig)
    if getattr(_lcr, "types", None) is None:
        class _StubRecvTypes:
            class FunctionResponse:
                def __init__(self, name=None, id=None, response=None):
                    self.name = name
                    self.id = id
                    self.response = response

        _orig_recv = _lcr.types
        _lcr.types = _StubRecvTypes
        testcase.addCleanup(setattr, _lcr, "types", _orig_recv)


class _WatchdogBase(unittest.TestCase):
    """Temp resume file + log capture, mirroring
    tests/test_voicecontrol.py's _LiveFixBase."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="r3do2_silent_")
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
        session._last_reply_at = None
        session._last_tool_done_at = None
        session._tool_in_flight = 0
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


# ----------------------------------------------------------------------
# Part A: the answered-turn rule (both interpreters)
# ----------------------------------------------------------------------

class TestAnsweredTurnRule(_WatchdogBase):
    """A tool call after the turn disarms rules 1/2/Q8 but must
    NOT disarm rule 5: the turn is judged against the reply
    clock, not the response clock."""

    def _tool_then_silence(self, session, channel):
        # Turn 12 s ago; the server yielded a tool call 8 s
        # ago (newer than the turn, so rules 1/Q8 are
        # disarmed); the tool finished 8 s ago; no reply ever.
        now = time.monotonic()
        if channel == "voice":
            session._last_voice_at = now - 12.0
        else:
            session._last_text_at = now - 12.0
        session._last_response_at = now - 8.0
        session._last_tool_done_at = now - 8.0
        session._last_reply_at = None

    def test_voice_tool_silence_trips_watchdog(self):
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        self._tool_then_silence(session, "voice")
        self._assert_trips(session, notes, "LIVE_WATCHDOG")

    def test_typed_tool_silence_trips_live_dead(self):
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        self._tool_then_silence(session, "typed")
        self._assert_trips(session, notes, "LIVE_DEAD")

    def test_tool_still_running_holds_the_rule(self):
        # Same shape, but the batch is still executing: the
        # turn is in progress, not skipped.
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        self._tool_then_silence(session, "voice")
        session._tool_in_flight = 1
        self._assert_no_trip(session, notes)

    def test_queued_batch_holds_the_rule(self):
        # Nothing executing, but a batch is queued behind the
        # finished one: the turn is still in progress.
        async def _run():
            session = self._new_session()
            notes = []
            session.on_status_change = notes.append
            self._tool_then_silence(session, "voice")
            session._tool_queue = asyncio.Queue()
            session._tool_queue.put_nowait(object())
            task = asyncio.create_task(session._live_watchdog_loop())
            await asyncio.sleep(2.5)
            try:
                self.assertFalse(task.done())
                self.assertNotIn("LIVE_DEAD", notes)
                self.assertNotIn("LIVE_WATCHDOG", notes)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(_run())

    def test_fresh_tool_result_does_not_trip(self):
        # The batch finished 1 s ago: the model has its 7 s
        # from the completion, not from the call.
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        self._tool_then_silence(session, "voice")
        session._last_tool_done_at = time.monotonic() - 1.0
        self._assert_no_trip(session, notes)

    def test_answered_turn_never_trips(self):
        # The model replied 2 s ago: the turn is answered,
        # whatever the response clock says.
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        self._tool_then_silence(session, "voice")
        session._last_reply_at = time.monotonic() - 2.0
        self._assert_no_trip(session, notes)

    def test_fresh_turn_never_trips(self):
        # The text was just sent and the response clock is
        # stale from the previous turn: the model still has
        # its 7 s, so the rule must not trip on the stale
        # clock (the premature-trip regression).
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        session._last_response_at = time.monotonic() - 10.0
        session._last_text_at = time.monotonic()
        session._last_reply_at = None
        self._assert_no_trip(session, notes)

    def test_idle_session_never_trips(self):
        # No turn sent at all: nothing is owed.
        session = self._new_session()
        notes = []
        session.on_status_change = notes.append
        session._last_response_at = time.monotonic() - 60.0
        self._assert_no_trip(session, notes)


class TestRule5BudgetsUnmoved(_WatchdogBase):
    """Rule 5 adds a rule, it moves no threshold: the 7 s
    silence budget and the 5 s quiescence are rule 1's, and
    every other budget is exactly what RS-1 / Q3 / Q8 set."""

    def test_budgets_unmoved(self):
        self.assertEqual(_lh._WATCHDOG_SILENCE_S, 7.0)
        self.assertEqual(_lh._WATCHDOG_AUDIBLE_SILENCE_S, 30.0)
        self.assertEqual(_lh._WATCHDOG_SEND_FAIL_S, 15.0)
        self.assertEqual(_lh._WATCHDOG_TICK_S, 1.0)
        self.assertEqual(_lh._WATCHDOG_VOICE_QUIET_S, 5.0)


# ----------------------------------------------------------------------
# Part B: the reply clock + tool bookkeeping (both interpreters)
# ----------------------------------------------------------------------

def _resp(tool_calls=None, audio=False, text=None, turn_complete=False):
    sc = SimpleNamespace(
        model_turn=None, turn_complete=turn_complete,
        interrupted=False, output_transcription=None,
        input_transcription=None, interaction_status=None)
    if audio or text:
        parts = []
        if audio:
            parts.append(SimpleNamespace(
                inline_data=SimpleNamespace(data=b"\x00\x01" * 800),
                text=None))
        if text:
            parts.append(SimpleNamespace(inline_data=None, text=text))
        sc.model_turn = SimpleNamespace(parts=parts)
    if text is not None:
        sc.output_transcription = SimpleNamespace(text=text, finished=True)
    return SimpleNamespace(
        session_resumption_update=None, go_away=None,
        server_content=sc, tool_call_cancellation=None,
        tool_call=(SimpleNamespace(function_calls=tool_calls)
                   if tool_calls else None))


class _FakeServer:
    """Scripted receive(): yields (delay, response) pairs, then
    parks forever (a live server waiting for the next turn)."""

    def __init__(self, script):
        self.script = script
        self.tool_responses = []

    async def send_client_content(self, turns=None, turn_complete=None):
        pass

    async def send_tool_response(self, function_responses=None):
        self.tool_responses.append(function_responses)

    async def send_realtime_input(self, audio=None):
        pass

    async def receive(self):
        for delay, resp in self.script:
            await asyncio.sleep(delay)
            if resp is not None:
                yield resp
        await asyncio.Event().wait()


class _FakeAM:
    def __init__(self):
        self.played = []

    def enqueue_playback(self, data):
        self.played.append(len(data))

    def interrupt_playback(self):
        pass


class TestReplyClock(_WatchdogBase):
    """The stamp sites: model audio and a delivered transcript
    stamp the reply clock; a tool call does not."""

    def _drive_receive(self, session, server, wait_s=1.5):
        async def _run():
            am = _FakeAM()
            recv_task = asyncio.create_task(
                session._receive_loop(am))
            await asyncio.sleep(wait_s)
            recv_task.cancel()
            await asyncio.gather(recv_task, return_exceptions=True)
            worker = getattr(session, "_tool_worker_task", None)
            if worker is not None:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

        asyncio.run(_run())

    def test_audio_stamps_the_reply_clock(self):
        _install_types_shims(self)
        session = self._new_session()
        session._session = _FakeServer(
            [(0.2, _resp(audio=True, text="The answer is 51.",
                         turn_complete=True))])
        self._drive_receive(session, session._session)
        self.assertIsNotNone(getattr(session, "_last_reply_at", None))

    def test_tool_call_does_not_stamp_the_reply_clock(self):
        _install_types_shims(self)
        session = self._new_session()
        fc = SimpleNamespace(name="quick_math",
                             args={"expression": "17*3"}, id="call-1")
        session._session = _FakeServer(
            [(0.2, _resp(tool_calls=[fc]))])
        with mock.patch.object(_ms, "log_message"):
            self._drive_receive(session, session._session)
        self.assertIsNone(getattr(session, "_last_reply_at", None))

    def test_flush_stamps_the_reply_clock(self):
        # The transcript flush is the text emission point:
        # a delivered bubble stamps, an empty buffer does not.
        session = self._new_session()
        session.on_transcript = lambda r, t: None
        session._output_transcript_buf = "The answer is 51."
        with mock.patch.object(_ms, "log_message"):
            session._flush_output_transcript()
        self.assertIsNotNone(getattr(session, "_last_reply_at", None))
        session._last_reply_at = None
        session._output_transcript_buf = "   "
        with mock.patch.object(_ms, "log_message"):
            session._flush_output_transcript()
        self.assertIsNone(getattr(session, "_last_reply_at", None))

    def test_tool_worker_tracks_in_flight_and_done(self):
        # The worker seam: while a batch runs the in-flight
        # count is 1; after it the count is 0 and the done
        # stamp is set.
        async def _run():
            session = self._new_session()
            seen = []

            async def _fake_batch(batch):
                seen.append(getattr(session, "_tool_in_flight", None))
                await asyncio.sleep(0.3)

            session._run_tool_batch = _fake_batch
            session._tool_queue = asyncio.Queue()
            session._tool_queue.put_nowait([object()])
            worker = asyncio.create_task(session._tool_worker_loop())
            await asyncio.sleep(1.0)
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            self.assertEqual(seen, [1])
            self.assertEqual(getattr(session, "_tool_in_flight", None), 0)
            done = getattr(session, "_last_tool_done_at", None)
            self.assertIsNotNone(done)
            self.assertLess(time.monotonic() - done, 5.0)

        asyncio.run(_run())


# ----------------------------------------------------------------------
# Part C: the end-to-end shape (both interpreters)
# ----------------------------------------------------------------------

class TestSilentTurnEndToEnd(_WatchdogBase):
    """The repro shape, shortened: a real session, a real
    receive loop, a real watchdog — a tool call and then
    silence must end with the channel's notice."""

    def _run_shape(self, kind, script, typed_text=None,
                   watch_s=11.0):
        async def _run():
            session = self._new_session()
            notes = []
            transcripts = []
            session.on_status_change = notes.append
            session.on_transcript = lambda r, t: transcripts.append((r, t))
            session._session = _FakeServer(script)
            if kind == "voice":
                session._last_voice_at = time.monotonic()
            am = _FakeAM()
            recv_task = asyncio.create_task(session._receive_loop(am))
            watch_task = asyncio.create_task(
                session._live_watchdog_loop())
            if kind == "typed":
                with mock.patch.object(_ms, "log_message"):
                    await session.send_text(typed_text)
            tripped = None
            try:
                await asyncio.wait_for(asyncio.shield(watch_task),
                                       timeout=watch_s)
                tripped = notes
            except (TimeoutError, asyncio.TimeoutError):
                pass
            finally:
                for task in (recv_task, watch_task):
                    task.cancel()
                await asyncio.gather(recv_task, watch_task,
                                     return_exceptions=True)
                worker = getattr(session, "_tool_worker_task", None)
                if worker is not None:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
            return tripped, transcripts

        return asyncio.run(_run())

    def test_voice_tool_silence_ends_with_watchdog_notice(self):
        _install_types_shims(self)
        fc = SimpleNamespace(name="quick_math",
                             args={"expression": "17*3"}, id="call-1")
        tripped, transcripts = self._run_shape(
            "voice", [(0.5, _resp(tool_calls=[fc]))])
        self.assertIsNotNone(tripped)
        self.assertIn("LIVE_WATCHDOG", tripped)
        self.assertEqual(
            [t for r, t in transcripts if r == "assistant"], [])

    def test_typed_tool_silence_ends_with_live_dead(self):
        _install_types_shims(self)
        fc = SimpleNamespace(name="quick_math",
                             args={"expression": "17*3"}, id="call-1")
        tripped, transcripts = self._run_shape(
            "typed", [(0.5, _resp(tool_calls=[fc]))],
            typed_text="what is 17 times 3")
        self.assertIsNotNone(tripped)
        self.assertIn("LIVE_DEAD", tripped)
        self.assertEqual(
            [t for r, t in transcripts if r == "assistant"], [])


if __name__ == "__main__":
    unittest.main()
