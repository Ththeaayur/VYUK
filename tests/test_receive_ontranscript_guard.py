"""Receive loop: a raising transcript consumer must not kill it.

Field-check fix #17 (F2 follow-up): the receive loop's model-turn
``on_transcript`` call was the one unguarded consumer in the
model-turn branch — its neighbours were already guarded (the audio
sink with try/except pass, the memory write with a loss note since
fix #2, T7 #16), but a raising transcript consumer died with
``[Receive Loop Error]`` and ended a healthy session, exactly the
way a memory failure used to. The fix wraps the call the same way
as the memory guard: the failure is surfaced with a loss note
(feed line first, best-effort chat line second) and the loop
continues — the turn was already spoken, and its memory row is
still recorded.

Harness: the real core.live_client module is imported (it guards
its google-genai import itself, so it loads on interpreters with
and without the SDK). memory_store is a recorder on both module
bindings and _log_line is a recorder, so no test writes to data/.
The fake Live session yields model-turn text responses, then
nothing — the loop's empty-receive path ends it after three
empties (the same shape as test_textreplies'
test_receive_loop_survives_memory_write_failure).
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


def _response(text):
    part = SimpleNamespace(inline_data=None, text=text)
    content = SimpleNamespace(
        model_turn=SimpleNamespace(parts=[part]),
        output_transcription=None, turn_complete=False,
        interrupted=False, interaction_status=None,
        usage_metadata=None)
    return SimpleNamespace(server_content=content)


class ReceiveOntranscriptGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = live_client.GeminiLiveSession()
        self.transcripts: list[tuple[str, str]] = []
        self.session.on_transcript = lambda role, text: (
            self.transcripts.append((role, text)))
        self.session.on_audio_data = None
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

    def test_receive_loop_survives_raising_transcript_consumer(self):
        # Field-check fix #17: a transcript consumer that raises
        # on every call must not kill a healthy session — the
        # loop runs both turns to its normal empty-receive end,
        # and both turn texts still reach the memory store (the
        # memory call follows the guarded consumer call).
        self.session.on_transcript = lambda role, text: (
            (_ for _ in ()).throw(
                RuntimeError("transcript consumer broke")))
        fake = self._run_loop(
            [_response("first words"), _response("second words")])
        self.assertGreaterEqual(fake.calls, 5)
        self.assertIn(("assistant", "first words"), self.store.logged)
        self.assertIn(("assistant", "second words"), self.store.logged)
        self.assertEqual(self.transcripts, [])

    def test_raising_consumer_loss_is_surfaced_in_log(self):
        # Field-check fix #17: the lost bubble is surfaced, not
        # swallowed — the feed line names the failure, and there
        # is no loop-death line.
        self.session.on_transcript = lambda role, text: (
            (_ for _ in ()).throw(
                RuntimeError("transcript consumer broke")))
        self._run_loop([_response("first words")])
        shown = [line for line in self.log_lines
                 if "could not be shown in the chat" in line]
        self.assertEqual(len(shown), 1)
        self.assertIn("RuntimeError: transcript consumer broke",
                      shown[0])
        self.assertFalse(any("Receive loop ended on an error" in line
                             for line in self.log_lines))

    def test_healthy_consumer_still_receives_transcript(self):
        # Control: with a healthy consumer the model-turn text
        # still reaches the transcript exactly as before, with
        # no loss note and no loop-death line.
        fake = self._run_loop(
            [_response("first words"), _response("second words")])
        self.assertGreaterEqual(fake.calls, 5)
        self.assertEqual(self.transcripts,
                         [("assistant", "first words"),
                          ("assistant", "second words")])
        self.assertEqual(self.store.logged, self.transcripts)
        self.assertFalse(any("could not be shown" in line
                             for line in self.log_lines))
        self.assertFalse(any("Receive loop ended on an error" in line
                             for line in self.log_lines))

    def test_loop_serves_next_turn_after_raising_consumer(self):
        # Field-check fix #17: the loop keeps processing turns
        # after the failure — a consumer that recovers serves
        # the next turn normally, and the first turn's text is
        # still in memory even though its bubble never landed.
        calls = {"n": 0}

        def _flaky(role, text):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transcript consumer broke")
            self.transcripts.append((role, text))

        self.session.on_transcript = _flaky
        fake = self._run_loop(
            [_response("first words"), _response("second words")])
        self.assertGreaterEqual(fake.calls, 5)
        self.assertIn(("assistant", "second words"), self.transcripts)
        self.assertNotIn(("assistant", "first words"), self.transcripts)
        self.assertIn(("assistant", "first words"), self.store.logged)
        self.assertIn(("assistant", "second words"), self.store.logged)
        # The loss note for turn 1 rode the recovered consumer
        # as a chat line (its feed line landed first).
        self.assertTrue(any("could not be shown in the chat" in text
                            for _, text in self.transcripts))


if __name__ == "__main__":
    unittest.main()
