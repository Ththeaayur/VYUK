"""Text-replies: assistant output transcription → one text bubble.

The Live session is configured AUDIO-only, so the model answers by
audio and the receive loop's model-turn text branch never fires —
assistant replies never appeared as chat bubbles (v5.92 and v5.93
alike). This build adds assistant-side output transcription: the
server additionally streams a transcription of its spoken reply,
which the session accumulates and emits ONCE per reply through the
existing on_transcript("assistant", ...) callback — the same
callback the bridge wires to _on_transcript → _add_bubble. No new
render path, no GUI change, no modality change, no input
transcription (the user's spoken words stay out of chat).

These tests prove:
(a) the session config carries output transcription while
    response_modalities stays ["AUDIO"], and input transcription
    is NOT enabled;
(b) transcribed assistant text reaches the existing transcript
    path as exactly one ("assistant", full_text) emission per
    reply (mocked server content — no live server, no Qt);
(c) the audio playback path is untouched;
(d) the typed echo (session send_text) is unchanged;
(e) transcription failure degrades to today's voice-only
    behavior without error.

Harness: the real core.live_client module is imported (it guards
its google-genai import itself, so it loads on interpreters with
and without the SDK). SDK-dependent config tests skip when the
SDK is absent (auditfix1 precedent); every other test runs on
both interpreters. memory_store is replaced by a recorder per
test so no test writes to data/.
"""

from __future__ import annotations

import asyncio
import re
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

from voice_assistant.core.voice import live_client  # noqa: E402  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from voice_assistant.core.voice import live_client_receive as live_client_receive_mod  # noqa: E402  # B47 INTENTIONAL: receive mixin binding for dual patch


def _strip_full_line_comments(source: str) -> str:
    return "\n".join(
        line for line in source.splitlines()
        if not line.strip().startswith("#"))


def _send_text_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    match = re.search(
        r"    async def send_text\(.*?\n(?=    async def |\nclass |\Z)",
        source, re.S)
    assert match is not None, f"send_text not found in {path}"
    return match.group(0)


# INTENTIONAL (skills split B47): send_text moved to the send mixin —
# same regex over concatenated facade + mixin text (the backup
# fixture stays single-file and keeps using _send_text_source).
def _send_text_source_text(source: str, label: str) -> str:
    match = re.search(
        r"    async def send_text\(.*?\n(?=    async def |\nclass |\Z)",
        source, re.S)
    assert match is not None, f"send_text not found in {label}"
    return match.group(0)


class _RecorderStore:
    """Stand-in for memory_store: records, never writes."""

    def __init__(self) -> None:
        self.logged: list[tuple[str, str]] = []

    def log_message(self, role: str, text: str) -> None:
        self.logged.append((role, text))


class _RaisingStore:
    """Stand-in for memory_store: every write fails (field-check
    fix #2, T6/T7 #16 — the session must survive it)."""

    def __init__(self) -> None:
        self.attempts = 0

    def log_message(self, role: str, text: str) -> None:
        self.attempts += 1
        raise RuntimeError("memory unavailable")


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


class _FakeSendSession:
    """Stand-in for the Live session's typed-turn send."""

    async def send_client_content(self, turns=None,
                                  turn_complete=False):
        return None


class _FakePart:
    @staticmethod
    def from_text(text):
        return SimpleNamespace(text=text)


class _FakeContent:
    def __init__(self, parts=None, role=None):
        self.parts = parts
        self.role = role


class _FakeTypes:
    """Minimal google.genai types stand-in (send_text needs
    Content/Part; the real SDK is absent on the sys
    interpreter)."""

    Part = _FakePart
    Content = _FakeContent


class TextRepliesConfigTests(unittest.TestCase):
    @unittest.skipIf(live_client.types is None,
                      "google-genai unavailable on this interpreter")
    def test_config_carries_output_transcription_with_audio(self):
        types_mod = live_client.types
        cfg = types_mod.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription=types_mod.AudioTranscriptionConfig(),
        )
        dumped = cfg.model_dump(exclude_none=True, by_alias=True)
        self.assertEqual(dumped["responseModalities"], ["AUDIO"])
        self.assertIn("outputAudioTranscription", dumped)
        self.assertNotIn("inputAudioTranscription", dumped)

    def test_tree_config_enables_output_not_input(self):
        source = _strip_full_line_comments(
            (ROOT / "core" / "voice" / "live_client.py").read_text(encoding="utf-8")  # Phase 3 V1b INTENTIONAL: moved to core/voice/
            # INTENTIONAL (skills split B47): start() moved to the
            # lifecycle mixin — read facade + mixin text (same
            # assertions, superset, never weakened).
            + (ROOT / "core" / "voice" / "live_client_lifecycle.py").read_text(encoding="utf-8"))
        self.assertIn(
            "output_audio_transcription=types.AudioTranscriptionConfig()",
            source)
        self.assertNotIn("input_audio_transcription=", source)
        self.assertIn('response_modalities=["AUDIO"]', source)


class TextRepliesSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = live_client.GeminiLiveSession()
        self.transcripts: list[tuple[str, str]] = []
        self.session.on_transcript = lambda role, text: (
            self.transcripts.append((role, text)))
        self.audio_calls: list[bytes] = []
        self.session.on_audio_data = lambda data: (
            self.audio_calls.append(data))
        self._real_store = live_client.memory_store
        self.store = _RecorderStore()
        live_client.memory_store = self.store
        # INTENTIONAL (skills split B47): _flush_output_transcript
        # moved to the receive mixin and reads memory_store from its
        # own globals — patch both bindings (B16 pattern, same
        # recorder, both restored).
        self._real_store_recv = live_client_receive_mod.memory_store
        live_client_receive_mod.memory_store = self.store

    def tearDown(self) -> None:
        live_client.memory_store = self._real_store
        live_client_receive_mod.memory_store = self._real_store_recv

    def _content(self, text=None, finished=False, turn_complete=False):
        out = None if text is None else SimpleNamespace(
            text=text, finished=finished)
        return SimpleNamespace(
            output_transcription=out, turn_complete=turn_complete)

    def test_chunks_accumulate_and_flush_once_on_finished(self):
        self.session._handle_output_transcription(
            self._content(text="Hello ", finished=False))
        self.assertEqual(self.transcripts, [])
        self.assertEqual(self.session._output_transcript_buf, "Hello ")
        self.session._handle_output_transcription(
            self._content(text="world", finished=True))
        self.assertEqual(self.transcripts, [("assistant", "Hello world")])
        self.assertEqual(self.session._output_transcript_buf, "")
        self.assertEqual(self.store.logged, [("assistant", "Hello world")])

    def test_turn_complete_flushes_partial_text(self):
        self.session._handle_output_transcription(
            self._content(text="partial ", finished=False))
        self.assertEqual(self.transcripts, [])
        self.session._handle_output_transcription(
            self._content(text=None, turn_complete=True))
        self.assertEqual(self.transcripts, [("assistant", "partial ")])

    def test_interruption_flush_path_preserves_partial(self):
        self.session._handle_output_transcription(
            self._content(text="cut off", finished=False))
        # The receive loop's interruption branch calls exactly this.
        self.session._flush_output_transcript()
        self.assertEqual(self.transcripts, [("assistant", "cut off")])
        self.session._flush_output_transcript()
        self.assertEqual(self.transcripts, [("assistant", "cut off")])

    def test_no_transcription_no_emit(self):
        self.session._handle_output_transcription(
            self._content(text=None, turn_complete=False))
        self.assertEqual(self.transcripts, [])
        self.assertEqual(self.session._output_transcript_buf, "")

    def test_whitespace_only_emit_nothing_but_clears(self):
        self.session._handle_output_transcription(
            self._content(text="   ", finished=True))
        self.assertEqual(self.transcripts, [])
        self.assertEqual(self.session._output_transcript_buf, "")

    def test_audio_path_untouched(self):
        self.session._handle_output_transcription(
            self._content(text="spoken words", finished=True))
        self.assertEqual(self.audio_calls, [])
        self.assertIsNotNone(self.session.on_audio_data)

    def test_failure_degrades_to_voice_only(self):
        # Field-check fix #2 (T6, re-baselined): a transcript
        # consumer that raises must not break the session — and
        # must not lose the reply either. The flush keeps the
        # text for the next flush (a loss note is surfaced), and
        # a later good reply delivers the kept text with it.
        self.session.on_transcript = lambda role, text: (_ for _ in ()).throw(
            RuntimeError("consumer broke"))
        self.session._handle_output_transcription(
            self._content(text="lost?", finished=True))
        self.assertEqual(self.session._output_transcript_buf, "lost?")
        # A transcription object whose text access raises is
        # swallowed by the handler itself.
        class _Breaking:
            @property
            def text(self):
                raise RuntimeError("transcription broke")

            finished = False

        broken = SimpleNamespace(
            output_transcription=_Breaking(), turn_complete=False)
        self.session._handle_output_transcription(broken)  # must not raise
        # And the session still serves a later good reply — the
        # kept text rides along with it.
        self.session.on_transcript = lambda role, text: (
            self.transcripts.append((role, text)))
        self.session._handle_output_transcription(
            self._content(text="still here", finished=True))
        self.assertEqual(self.transcripts,
                         [("assistant", "lost?still here")])
        self.assertEqual(self.session._output_transcript_buf, "")

    def test_flush_memory_failure_after_bubble_surfaces_loss_note(self):
        # Field-check fix #2 (T6): the bubble lands, the memory
        # row fails — the buffer still clears (the bubble IS the
        # record) and the missing memory row is surfaced with a
        # loss note, never swallowed.
        store = _RaisingStore()
        live_client_receive_mod.memory_store = store
        self.addCleanup(
            lambda: setattr(live_client_receive_mod, "memory_store",
                            self.store))
        self.session._handle_output_transcription(
            self._content(text="shown text", finished=True))
        self.assertEqual(self.session._output_transcript_buf, "")
        self.assertEqual(self.transcripts[0], ("assistant", "shown text"))
        self.assertEqual(len(self.transcripts), 2)
        self.assertEqual(self.transcripts[1][0], "assistant")
        self.assertIn("could not be saved to memory",
                      self.transcripts[1][1])
        self.assertEqual(store.attempts, 1)

    def test_stopped_flush_memory_failure_surfaces_loss_note(self):
        # Field-check fix #2 (T6): the stopped reply is already
        # shown, so no second bubble — but its memory failure is
        # still surfaced, and the buffer clears.
        store = _RaisingStore()
        live_client_receive_mod.memory_store = store
        self.addCleanup(
            lambda: setattr(live_client_receive_mod, "memory_store",
                            self.store))
        self.session._reply_stop_requested = True
        self.session._handle_output_transcription(
            self._content(text="partial words", finished=True))
        self.assertEqual(self.session._output_transcript_buf, "")
        self.assertEqual(len(self.transcripts), 1)
        self.assertIn("could not be saved to memory",
                      self.transcripts[0][1])
        self.assertNotIn("partial words", self.transcripts[0][1])
        self.assertEqual(store.attempts, 1)

    def test_receive_loop_survives_memory_write_failure(self):
        # Field-check fix #2 (T7 #16): a memory-write failure
        # in the receive loop must not kill a healthy session —
        # the turn is still shown, the loop runs on, and the
        # lost row is surfaced.
        store = _RaisingStore()
        live_client_receive_mod.memory_store = store
        self.addCleanup(
            lambda: setattr(live_client_receive_mod, "memory_store",
                            self.store))
        part = SimpleNamespace(inline_data=None, text="hello world")
        content = SimpleNamespace(
            model_turn=SimpleNamespace(parts=[part]),
            output_transcription=None, turn_complete=False,
            interrupted=False, interaction_status=None,
            usage_metadata=None)
        response = SimpleNamespace(server_content=content)
        fake = _FakeLiveSession([response])
        self.session._session = fake
        self.session.is_connected = True
        self.session._running = True
        audio = SimpleNamespace(
            enqueue_playback=lambda data: None,
            interrupt_playback=lambda: None)
        asyncio.run(self.session._receive_loop(audio))
        # The loop ran past the failing turn to its normal
        # empty-receive end (1 turn + 3 empties), and the
        # spoken text still reached the transcript.
        self.assertGreaterEqual(fake.calls, 4)
        self.assertIn(("assistant", "hello world"), self.transcripts)
        self.assertEqual(store.attempts, 1)

    def test_send_text_survives_memory_write_failure(self):
        # Field-check fix #2 (T7 #16): a memory-write failure
        # in send_text must not fail a typed turn — the text
        # was already sent, so the turn stands and the lost
        # row is surfaced.
        from voice_assistant.core.voice import live_client_send as send_mod  # noqa: E402

        real_types = send_mod.types
        send_mod.types = _FakeTypes
        self.addCleanup(lambda: setattr(send_mod, "types", real_types))
        real_store = send_mod.memory_store
        store = _RaisingStore()
        send_mod.memory_store = store
        self.addCleanup(lambda: setattr(send_mod, "memory_store",
                                        real_store))
        self.session._session = _FakeSendSession()
        self.session.is_connected = True
        asyncio.run(self.session.send_text("hello"))
        self.assertIn(("user", "hello"), self.transcripts)
        self.assertEqual(store.attempts, 1)

    def test_no_callback_no_raise(self):
        self.session.on_transcript = None
        self.session._handle_output_transcription(
            self._content(text="nobody listens", finished=True))
        self.assertEqual(self.session._output_transcript_buf, "")

    def test_typed_echo_send_text_unchanged(self):
        backup = Path.home() / "workspace" / "code-lab" / \
            "backup_textreplies_pre" / "core" / "live_client.py"
        if not backup.exists():
            self.skipTest("pre-build backup not present")
        # INTENTIONAL RE-BASELINE (Phase 3 V1b, 2026-11-02): the
        # fixture's send_text was re-baselined to the authorised
        # B1a-1+V1b moves ONLY — verified by diff, exactly 2 lines
        # changed (turn_context path comment core/turn_context ->
        # core/brain/turn_context, and the import line . -> ..brain
        # when live_client moved to core/voice/); zero logic change.
        # INTENTIONAL RE-BASELINE (field-check fix #2, 2026-11-17):
        # the fixture was re-baselined again to the authorised
        # T7 #16 memory guard ONLY — verified by diff, exactly
        # the 9-line try/except around memory_store.log_message
        # (no other line differs). The test still guards every
        # other line of send_text against future drift.
        self.assertEqual(
            _send_text_source_text(
                (ROOT / "core" / "voice" / "live_client.py").read_text(encoding="utf-8")  # Phase 3 V1b INTENTIONAL: moved to core/voice/
                + (ROOT / "core" / "voice" / "live_client_send.py").read_text(encoding="utf-8"),
                "live send_text"),
            _send_text_source(backup))


if __name__ == "__main__":
    unittest.main()
