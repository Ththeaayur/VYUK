"""Round-3 section C fix queue item 13 — model_turn text-part buffering.

The hole (re-verification residual 2): model_turn text parts emitted
``on_transcript("assistant", part.text)`` DIRECTLY in the receive loop,
bypassing the C1-F2 buffer (item 9) — so a text-part reply could land
above its own question whenever the user's words had not flushed yet
(the same ~0.08 s window as C1-F2). The fix routes the parts through
the same buffer and the same flush: the part is buffered in the
model_turn branch and flushed AFTER the input handler for the
message, so a message carrying both the reply and the input's
finish still orders the question first. The flush is the one
emission point; while the input is open it defers, and the input
flush releases it.

Pinned alongside: the field-check fix #17 guarantee (a raising
transcript consumer must not kill the loop, and the turn's text
must still reach memory — carried by the flush's record_on_failure
mode), the stop behaviour (a stopped reply's text parts are
dropped), and the doc fix in live_client_error_cost.py (the stale
"(consulted by the session owner)" claim about Live preflight is
gone; Live stays record-only by design).

Harness: the real _receive_loop on a one-shot fake session (the
test_receive_ontranscript_guard / test_textreplies pattern) with
canned server messages — no network, no Gemini key. memory_store
is a recorder on both module bindings and _log_line is a recorder,
so no test writes to data/.
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


class _RaisingStore:
    """Stand-in for memory_store: every write fails."""

    def __init__(self) -> None:
        self.attempts = 0

    def log_message(self, role: str, text: str) -> None:
        self.attempts += 1
        raise RuntimeError("memory store broke")


class _FakeLiveSession:
    """Yields canned responses once, then nothing (the receive
    loop's empty-receive path ends it after three empties)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def receive(self):
        self.calls += 1
        if self._responses:
            yield self._responses.pop(0)


def _content(model_text=None, in_text=None, in_finished=False,
             out_text=None, out_finished=False, turn_complete=False,
             interrupted=False):
    return SimpleNamespace(
        model_turn=SimpleNamespace(
            parts=[SimpleNamespace(inline_data=None, text=model_text)])
        if model_text is not None else None,
        output_transcription=SimpleNamespace(text=out_text,
                                             finished=out_finished)
        if out_text is not None else None,
        input_transcription=SimpleNamespace(text=in_text,
                                            finished=in_finished)
        if in_text is not None else None,
        turn_complete=turn_complete,
        interrupted=interrupted,
        interaction_status=None,
        usage_metadata=None)


def _response(content):
    return SimpleNamespace(server_content=content)


class ModelTurnTextBufferTests(unittest.TestCase):
    """Text parts through the real _receive_loop."""

    def setUp(self) -> None:
        self.session = live_client.GeminiLiveSession()
        self.transcripts: list[tuple[str, str]] = []
        self.chunks: list[tuple[str, str]] = []
        self.session.on_transcript = lambda role, text: (
            self.transcripts.append((role, text)))
        self.session.on_transcript_chunk = lambda role, text: (
            self.chunks.append((role, text)))
        self.session.on_audio_data = None
        self.store = _RecorderStore()
        self._real_store = live_client.memory_store
        self._real_store_recv = recv_mod.memory_store
        self._real_log_line = recv_mod._log_line
        live_client.memory_store = self.store
        recv_mod.memory_store = self.store
        self.log_lines: list[str] = []
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

    def test_text_part_held_while_input_open(self):
        # The hole shape: the question streams in, the text-part
        # reply arrives, then the question finishes. The answer
        # must follow its question, in the transcript and in
        # memory — never above it.
        self._run_loop([
            _response(_content(in_text="What is 17 times 3?",
                               in_finished=False)),
            _response(_content(model_text="Fifty-one.")),
            _response(_content(in_text="", in_finished=True)),
        ])
        want = [("user", "What is 17 times 3?"),
                ("assistant", "Fifty-one.")]
        self.assertEqual(self.transcripts, want)
        self.assertEqual(self.store.logged, want)

    def test_text_part_same_message_as_input_finish(self):
        # One server message carries BOTH the reply text AND the
        # input transcription's finish. The model_turn branch
        # runs before the input handler in the loop, so only a
        # buffer-first / flush-after-handlers design orders it
        # right — a flush inside the model_turn branch would
        # still land the answer first here.
        self._run_loop([
            _response(_content(in_text="What is 17 times 3?",
                               in_finished=False)),
            _response(_content(model_text="Fifty-one.",
                               in_text="", in_finished=True)),
        ])
        want = [("user", "What is 17 times 3?"),
                ("assistant", "Fifty-one.")]
        self.assertEqual(self.transcripts, want)
        self.assertEqual(self.store.logged, want)

    def test_text_part_immediate_when_no_input(self):
        # Control: with no input at all the part still lands
        # immediately, one bubble + one memory row — the #17
        # per-part shape, unchanged.
        self._run_loop([_response(_content(model_text="Fifty-one."))])
        want = [("assistant", "Fifty-one.")]
        self.assertEqual(self.transcripts, want)
        self.assertEqual(self.store.logged, want)

    def test_two_text_parts_held_concatenate_into_one_bubble(self):
        # Two parts held for one unflushed question are one
        # reply: they flush as a single bubble (and a single
        # memory row) after the question — the same buffer the
        # output transcription uses, so the same consequence.
        self._run_loop([
            _response(_content(in_text="What is 17 times 3?",
                               in_finished=False)),
            _response(_content(model_text="Fifty-one.")),
            _response(_content(model_text=" Fifty-two.")),
            _response(_content(in_text="", in_finished=True)),
        ])
        want = [("user", "What is 17 times 3?"),
                ("assistant", "Fifty-one. Fifty-two.")]
        self.assertEqual(self.transcripts, want)
        self.assertEqual(self.store.logged, want)

    def test_interruption_flushes_input_first(self):
        # A held text part + a barge-in: the spoken words show
        # first as one bubble, and only then the partial reply.
        self._run_loop([
            _response(_content(in_text="What is 17 times 3?",
                               in_finished=False)),
            _response(_content(model_text="Fifty-one.")),
            _response(_content(interrupted=True)),
        ])
        want = [("user", "What is 17 times 3?"),
                ("assistant", "Fifty-one.")]
        self.assertEqual(self.transcripts, want)
        self.assertEqual(self.store.logged, want)

    def test_stop_drops_text_parts(self):
        # stop_reply's contract: model-turn text parts are
        # dropped, not buffered — a stopped reply leaves no
        # bubble and no memory row.
        self.session._reply_stop_requested = True
        self._run_loop([_response(_content(model_text="Fifty-one."))])
        self.assertEqual(self.transcripts, [])
        self.assertEqual(self.store.logged, [])

    def test_raising_consumer_text_still_in_memory(self):
        # Field-check fix #17, through the new path: a
        # transcript consumer that raises on every call must
        # not kill the loop, and both turn texts must still
        # reach memory, with the loss surfaced once per part.
        self.session.on_transcript = lambda role, text: (
            (_ for _ in ()).throw(
                RuntimeError("transcript consumer broke")))
        fake = self._run_loop([
            _response(_content(model_text="first words")),
            _response(_content(model_text="second words")),
        ])
        self.assertGreaterEqual(fake.calls, 5)
        self.assertEqual(self.transcripts, [])
        self.assertEqual(self.store.logged,
                         [("assistant", "first words"),
                          ("assistant", "second words")])
        shown = [line for line in self.log_lines
                 if "could not be shown in the chat" in line]
        self.assertEqual(len(shown), 2)
        self.assertTrue(all(
            "RuntimeError: transcript consumer broke" in line
            for line in shown))
        self.assertFalse(any("Receive loop ended on an error" in line
                             for line in self.log_lines))

    def test_memory_failure_bubble_still_lands(self):
        # Field-check fix #2 (T7 #16), through the new path: a
        # memory-write failure must not kill the session — the
        # spoken text still reaches the transcript, and the
        # lost row is surfaced with exactly one memory attempt.
        store = _RaisingStore()
        recv_mod.memory_store = store
        fake = self._run_loop(
            [_response(_content(model_text="hello world"))])
        self.assertGreaterEqual(fake.calls, 4)
        self.assertIn(("assistant", "hello world"), self.transcripts)
        self.assertEqual(store.attempts, 1)

    def test_no_callback_no_raise(self):
        # A session with no transcript consumer: the part is
        # still recorded to memory, and nothing raises.
        self.session.on_transcript = None
        self._run_loop([_response(_content(model_text="Fifty-one."))])
        self.assertEqual(self.transcripts, [])
        self.assertEqual(self.store.logged,
                         [("assistant", "Fifty-one.")])

    def test_text_part_and_output_transcription_share_one_buffer(self):
        # One turn, both reply shapes: the text part and the
        # streamed transcription chunk are the same reply's
        # words in one buffer, so they land as one bubble with
        # one memory row — not two bubbles for one reply.
        self._run_loop([
            _response(_content(
                model_text="Fifty-one.",
                out_text=" The result is fifty-one.",
                out_finished=True)),
        ])
        want = [("assistant",
                 "Fifty-one. The result is fifty-one.")]
        self.assertEqual(self.transcripts, want)
        self.assertEqual(self.store.logged, want)

    def test_held_output_chunk_released_after_question(self):
        # The C1-F2 hold, with a text part in the same turn: the
        # streamed chunk is held while the input is open and
        # released by the flush that follows the question — the
        # live bubble is created after the user bubble, and the
        # text part lands in the same flush.
        self._run_loop([
            _response(_content(in_text="What is 17 times 3?",
                               in_finished=False)),
            _response(_content(model_text="Fifty-one.",
                               out_text="Fifty-one.",
                               out_finished=False)),
            _response(_content(in_text="", in_finished=True)),
        ])
        want = [("user", "What is 17 times 3?"),
                ("assistant", "Fifty-one.Fifty-one.")]
        self.assertEqual(self.transcripts, want)
        self.assertEqual(self.store.logged, want)
        self.assertEqual(self.chunks, [("assistant", "Fifty-one.")])


class FlushRecordOnFailureTests(unittest.TestCase):
    """The flush's record_on_failure mode, driven directly."""

    def setUp(self) -> None:
        self.session = live_client.GeminiLiveSession()
        self.transcripts: list[tuple[str, str]] = []
        self.session.on_transcript = lambda role, text: (
            self.transcripts.append((role, text)))
        self.session.on_transcript_chunk = lambda role, text: None
        self.session.on_audio_data = None
        self.store = _RecorderStore()
        self._real_store_recv = recv_mod.memory_store
        self._real_log_line = recv_mod._log_line
        recv_mod.memory_store = self.store
        self.log_lines: list[str] = []
        recv_mod._log_line = self.log_lines.append

    def tearDown(self) -> None:
        recv_mod.memory_store = self._real_store_recv
        recv_mod._log_line = self._real_log_line

    def test_record_on_failure_logs_memory_and_drops(self):
        # A bubble that cannot be shown is still recorded to
        # memory and surfaced — not kept for a retry that may
        # never come (field-check fix #17).
        self.session.on_transcript = lambda role, text: (
            (_ for _ in ()).throw(RuntimeError("consumer broke")))
        self.session._output_transcript_buf = "spoken words"
        self.session._flush_output_transcript(record_on_failure=True)
        self.assertEqual(self.transcripts, [])
        self.assertEqual(self.store.logged,
                         [("assistant", "spoken words")])
        self.assertEqual(self.session._output_transcript_buf, "")
        self.assertTrue(any("could not be shown in the chat" in line
                            for line in self.log_lines))
        self.assertFalse(any("kept in the transcript buffer" in line
                             for line in self.log_lines))

    def test_default_keeps_text_on_failure(self):
        # The transcription path is unchanged: a failed bubble
        # stays in the buffer for the next flush, with the
        # "kept" note and no memory row yet.
        self.session.on_transcript = lambda role, text: (
            (_ for _ in ()).throw(RuntimeError("consumer broke")))
        self.session._output_transcript_buf = "spoken words"
        self.session._flush_output_transcript()
        self.assertEqual(self.transcripts, [])
        self.assertEqual(self.store.logged, [])
        self.assertEqual(self.session._output_transcript_buf,
                         "spoken words")
        self.assertTrue(any("kept in the transcript buffer" in line
                            for line in self.log_lines))

    def test_record_on_failure_healthy_still_emits(self):
        # With a healthy consumer the flag changes nothing: one
        # bubble + one memory row, buffer cleared.
        self.session._output_transcript_buf = "spoken words"
        self.session._flush_output_transcript(record_on_failure=True)
        self.assertEqual(self.transcripts,
                         [("assistant", "spoken words")])
        self.assertEqual(self.store.logged,
                         [("assistant", "spoken words")])
        self.assertEqual(self.session._output_transcript_buf, "")

    def test_flush_defers_while_input_open_then_releases(self):
        # Item-9 control, driven directly: while the input is
        # open the flush defers and the buffer is kept; the
        # input flush releases it after the question lands.
        self.session._input_transcript_open = True
        self.session._input_transcript_buf = "What is 17 times 3?"
        self.session._output_transcript_buf = "Fifty-one."
        self.session._flush_output_transcript(record_on_failure=True)
        self.assertEqual(self.transcripts, [])
        self.assertEqual(self.session._output_transcript_buf,
                         "Fifty-one.")
        self.assertTrue(self.session._output_flush_deferred)
        self.session._flush_input_transcript()
        self.assertEqual(self.transcripts,
                         [("user", "What is 17 times 3?"),
                          ("assistant", "Fifty-one.")])
        self.assertEqual(self.store.logged,
                         [("user", "What is 17 times 3?"),
                          ("assistant", "Fifty-one.")])


class Item9OrderControlTests(unittest.TestCase):
    """The item-9 shapes, pinned so this item cannot regress them."""

    def setUp(self) -> None:
        self.session = live_client.GeminiLiveSession()
        self.transcripts: list[tuple[str, str]] = []
        self.session.on_transcript = lambda role, text: (
            self.transcripts.append((role, text)))
        self.session.on_transcript_chunk = lambda role, text: None
        self.session.on_audio_data = None
        self.store = _RecorderStore()
        self._real_store_recv = recv_mod.memory_store
        recv_mod.memory_store = self.store

    def tearDown(self) -> None:
        recv_mod.memory_store = self._real_store_recv

    def _sc(self, text=None, finished=False):
        return SimpleNamespace(
            model_turn=None,
            output_transcription=SimpleNamespace(text=text,
                                                 finished=finished)
            if text is not None else None,
            input_transcription=None,
            turn_complete=False,
            interrupted=False)

    def _sc_in(self, text=None, finished=False, turn_complete=False):
        return SimpleNamespace(
            model_turn=None,
            output_transcription=None,
            input_transcription=SimpleNamespace(text=text,
                                                finished=finished)
            if text is not None else None,
            turn_complete=turn_complete,
            interrupted=False)

    def _sc_both(self, out_text, out_finished, in_text, in_finished,
                 turn_complete=False):
        return SimpleNamespace(
            model_turn=None,
            output_transcription=SimpleNamespace(text=out_text,
                                                 finished=out_finished),
            input_transcription=SimpleNamespace(text=in_text,
                                                finished=in_finished),
            turn_complete=turn_complete,
            interrupted=False)

    def test_output_finishing_before_input_orders_question_first(self):
        # Item-9 leg A variant 1: the output finishes first, the
        # input finishes 0.08 s later — the question still lands
        # first.
        self.session._handle_input_transcription(
            self._sc_in("Calculate 17 * 3.", finished=False))
        self.session._handle_output_transcription(
            self._sc("The result of seventeen multiplied by three "
                     "is fifty-one.", finished=True))
        self.session._handle_input_transcription(
            self._sc_in("", finished=True))
        self.assertEqual([r for r, _ in self.transcripts],
                         ["user", "assistant"])

    def test_turn_complete_message_orders_question_first(self):
        # Item-9 leg A variant 2: both finish on one
        # turn_complete message, with the input already open.
        self.session._handle_input_transcription(
            self._sc_in("Calculate 17 * 3.", finished=False))
        self.session._handle_output_transcription(
            self._sc_both("The result remains fifty-one.", True,
                          "", True, turn_complete=True))
        self.session._handle_input_transcription(
            self._sc_both("The result remains fifty-one.", True,
                          "", True, turn_complete=True))
        self.assertEqual([r for r, _ in self.transcripts],
                         ["user", "assistant"])


class LivePreflightDocTests(unittest.TestCase):
    """The live_client_error_cost.py doc fix (item 13b)."""

    def test_no_session_owner_preflight_claim(self):
        # Parent-ruled: Live stays record-only by design — no
        # session-start gate. The stale comment claiming the
        # ledger's preflight API is "(consulted by the session
        # owner)" is gone. (The claim wraps across two comment
        # lines in the original, so the pin is the phrase
        # "session owner", which appears nowhere in the file
        # once the claim is deleted.)
        path = ROOT / "core" / "voice" / "live_client_error_cost.py"
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("session owner", text)
        self.assertIn("Record-only", text)
        self.assertIn("preflight API", text)


if __name__ == "__main__":
    unittest.main()
