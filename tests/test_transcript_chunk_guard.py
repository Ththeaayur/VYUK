"""Receive loop: a raising chunk consumer must be surfaced, not swallowed.

Field-check fix #19 (F2 follow-up): the receive loop's
``on_transcript_chunk`` call sat inside ``_handle_output_transcription``'s
wide try/except pass, so a raising chunk consumer was eaten silently —
no log line, no loss note — while the loop continued. Worse than lost
streaming: the raise skipped the rest of that message's handling, so
when the FINISHING chunk's callback raised, the reply's flush never
ran and the whole bubble sat in the buffer until some later message
flushed it (or never, if the session ended first).

The fix wraps just the chunk call, in fix #17's shape: the failure is
surfaced with a loss note (feed line first, best-effort chat line
second) and the loop continues — and the finishing chunk's flush now
runs even when its callback raised, so the full reply still lands as
one bubble. Two differences from fix #17's shape, both forced by the
chunk path's contract: the chunk text itself is already in the buffer
(the loss is the live streaming, not the content — the note says so),
and a reply streams dozens of chunks, so the note fires once per
failure run, not once per chunk (a note per chunk would flood the
feed and the chat). The note re-arms when a chunk succeeds, when a
reply flushes, and when a chunk arrives on an empty buffer (a new
reply — which also covers a reconnect, where _run_session clears the
buffer without a flush; the session object is a reused singleton).

Harness: the same shape as test_receive_ontranscript_guard.py — the
real core.live_client module, a recorder memory_store on both module
bindings, a recorder _log_line, and a fake Live session yielding
output-transcription chunk responses, then nothing (the loop's
empty-receive path ends it after three empties). No test writes to
data/.
"""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "voice_assistant" not in sys.modules:
    _pkg = types.ModuleType("voice_assistant")
    _pkg.__path__ = [str(ROOT)]
    sys.modules["voice_assistant"] = _pkg
if "voice_assistant.core" not in sys.modules:
    _core = types.ModuleType("voice_assistant.core")
    _core.__path__ = [str(ROOT / "core")]
    sys.modules["voice_assistant.core"] = _core

from voice_assistant.core.voice import live_client  # noqa: E402
from voice_assistant.core.voice import live_client_receive as recv_mod  # noqa: E402


class _RecorderStore:
    """Stand-in for memory_store: records, never writes."""

    def __init__(self) -> None:
        self.logged: list[tuple[str, str]] = []

    def log_message(self, role: str, text: str) -> None:
        self.logged.append((role, text))


class _FakeLiveSession:
    """Stand-in for the Live session: yields the given server
    responses once, then nothing (the receive loop's
    empty-receive path ends it after three empties)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def receive(self):
        self.calls += 1
        if self._responses:
            yield self._responses.pop(0)


def _chunk_response(text, finished):
    content = SimpleNamespace(
        model_turn=None,
        output_transcription=SimpleNamespace(
            text=text, finished=finished),
        turn_complete=False,
        interrupted=False, interaction_status=None,
        usage_metadata=None)
    return SimpleNamespace(server_content=content)


class TranscriptChunkGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = live_client.GeminiLiveSession()
        self.transcripts: list[tuple[str, str]] = []
        self.session.on_transcript = lambda role, text: (
            self.transcripts.append((role, text)))
        self.session.on_audio_data = None
        self.session.on_transcript_chunk = None
        self.store = _RecorderStore()
        self._real_store = live_client.memory_store
        self._real_store_recv = recv_mod.memory_store
        live_client.memory_store = self.store
        recv_mod.memory_store = self.store
        self.log_lines: list[str] = []
        self._real_log_line = recv_mod._log_line
        recv_mod._log_line = self.log_lines.append

    def tearDown(self) -> None:
        live_client.memory_store = self._real_store
        recv_mod.memory_store = self._real_store_recv
        recv_mod._log_line = self._real_log_line

    def _run_loop(self, responses):
        fake = _FakeLiveSession(responses)
        self.session._session = fake
        self.session.is_connected = True
        self.session._running = True
        audio = SimpleNamespace(
            enqueue_playback=lambda data: None,
            interrupt_playback=lambda: None)
        asyncio.run(self.session._receive_loop(audio))
        return fake

    def _chunk_notes(self):
        return [line for line in self.log_lines
                if "could not be shown as it arrived" in line]

    def test_loop_survives_raising_chunk_consumer_and_flushes(self):
        # Field-check fix #19: a chunk consumer that raises on
        # every call must neither kill the loop nor lose the
        # reply — the loop runs all three chunks to its normal
        # empty-receive end, and the finishing chunk's flush
        # still runs, so the full reply lands as one bubble and
        # in memory (pre-fix the raise skipped the flush and
        # the bubble never landed).
        self.session.on_transcript_chunk = lambda role, text: (
            (_ for _ in ()).throw(
                RuntimeError("chunk consumer broke")))
        fake = self._run_loop([
            _chunk_response("Hel", False),
            _chunk_response("lo ", False),
            _chunk_response("world", True),
        ])
        self.assertGreaterEqual(fake.calls, 6)
        self.assertIn(("assistant", "Hello world"), self.transcripts)
        self.assertIn(("assistant", "Hello world"), self.store.logged)
        self.assertFalse(any("Receive loop ended on an error" in line
                             for line in self.log_lines))

    def test_raising_chunk_loss_is_surfaced_once(self):
        # Field-check fix #19: the lost streaming is surfaced,
        # not swallowed — exactly ONE loss note for three
        # failing chunks (a note per chunk would flood the feed
        # and the chat), naming the failure, with no loop-death
        # line. The note also says the full reply still appears
        # — the chunk text was never lost, only the streaming.
        self.session.on_transcript_chunk = lambda role, text: (
            (_ for _ in ()).throw(
                RuntimeError("chunk consumer broke")))
        self._run_loop([
            _chunk_response("Hel", False),
            _chunk_response("lo ", False),
            _chunk_response("world", True),
        ])
        notes = self._chunk_notes()
        self.assertEqual(len(notes), 1)
        self.assertIn("RuntimeError: chunk consumer broke", notes[0])
        self.assertIn("will still appear", notes[0])
        self.assertFalse(any("Receive loop ended on an error" in line
                             for line in self.log_lines))

    def test_healthy_chunk_consumer_streams_normally(self):
        # Control: with a healthy consumer every chunk streams
        # in order, with no loss note and no loop-death line,
        # and the reply still flushes as one bubble.
        chunks: list[tuple[str, str]] = []
        self.session.on_transcript_chunk = lambda role, text: (
            chunks.append((role, text)))
        fake = self._run_loop([
            _chunk_response("Hel", False),
            _chunk_response("lo ", False),
            _chunk_response("world", True),
        ])
        self.assertGreaterEqual(fake.calls, 6)
        self.assertEqual(chunks,
                         [("assistant", "Hel"),
                          ("assistant", "lo "),
                          ("assistant", "world")])
        self.assertEqual(self.transcripts,
                         [("assistant", "Hello world")])
        self.assertEqual(self.store.logged, self.transcripts)
        self.assertEqual(self._chunk_notes(), [])
        self.assertFalse(any("Receive loop ended on an error" in line
                             for line in self.log_lines))

    def test_flaky_chunk_consumer_notes_each_failure_run(self):
        # Field-check fix #19: the note re-arms when a chunk
        # succeeds — a consumer that fails, recovers, then
        # fails again surfaces each failure run (two notes),
        # and the full reply still lands.
        calls = {"n": 0}
        chunks: list[tuple[str, str]] = []

        def _flaky(role, text):
            calls["n"] += 1
            if calls["n"] in (1, 3):
                raise RuntimeError("chunk consumer broke")
            chunks.append((role, text))

        self.session.on_transcript_chunk = _flaky
        self._run_loop([
            _chunk_response("Hel", False),
            _chunk_response("lo ", False),
            _chunk_response("world", True),
        ])
        self.assertEqual(chunks, [("assistant", "lo ")])
        self.assertEqual(len(self._chunk_notes()), 2)
        self.assertIn(("assistant", "Hello world"), self.transcripts)
        self.assertIn(("assistant", "Hello world"), self.store.logged)

    def test_next_reply_notes_again_after_flush(self):
        # Field-check fix #19: the note re-arms when a reply
        # flushes — two replies with a broken chunk consumer
        # surface one note each, and both bubbles land.
        self.session.on_transcript_chunk = lambda role, text: (
            (_ for _ in ()).throw(
                RuntimeError("chunk consumer broke")))
        fake = self._run_loop([
            _chunk_response("Hel", False),
            _chunk_response("lo", True),
            _chunk_response("wor", False),
            _chunk_response("ld", True),
        ])
        self.assertGreaterEqual(fake.calls, 7)
        self.assertEqual(len(self._chunk_notes()), 2)
        self.assertIn(("assistant", "Hello"), self.transcripts)
        self.assertIn(("assistant", "world"), self.transcripts)
        self.assertIn(("assistant", "Hello"), self.store.logged)
        self.assertIn(("assistant", "world"), self.store.logged)

    def test_stopped_reply_never_calls_chunk_consumer(self):
        # Control: the stop path is unchanged by the fix — while
        # a stop is requested the chunks accumulate without
        # calling the consumer, the finishing flush logs the
        # partial reply to memory without emitting a bubble,
        # and no loss note appears.
        calls: list[tuple[str, str]] = []
        self.session.on_transcript_chunk = lambda role, text: (
            calls.append((role, text)))
        self.session._reply_stop_requested = True
        self._run_loop([
            _chunk_response("Hel", False),
            _chunk_response("lo world", True),
        ])
        self.assertEqual(calls, [])
        self.assertEqual(self.transcripts, [])
        self.assertEqual(self.store.logged,
                         [("assistant", "Hello world")])
        self.assertEqual(self._chunk_notes(), [])


if __name__ == "__main__":
    unittest.main()
