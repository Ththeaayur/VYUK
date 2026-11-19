# Round-3 fix #5 (#56): live input transcription.
#
# Ruling #56: "what you say is transcribed on screen as you speak,
# so mishearing is visible immediately ... exactly what the STT
# heard, never a cleaned-up version." Section A found the live path
# PARTIAL: the local path is verbatim through the real turn and the
# real GUI bubble, but the live path never transcribed the user's
# spoken words at all (no input_audio_transcription configured,
# no input handler in the receive loop).
#
# The fix mirrors the v5.94 output-transcription path:
#  - the session config requests input_audio_transcription
#    (live_client_lifecycle.py);
#  - the receive loop accumulates server input_transcription
#    chunks into _input_transcript_buf and flushes once per
#    utterance as exactly ONE on_transcript("user", ...) — the
#    same channel the local path and the typed echo use — plus
#    one memory row, verbatim as STT heard.
#
# Harness mirrors tests/test_textreplies.py (the output-path
# suite): a bare GeminiLiveSession, on_transcript recorded, the
# memory store patched on both bindings (B16 pattern).
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.voice import live_client
from core.voice import live_client_receive as live_client_receive_mod

ROOT = Path(__file__).resolve().parent.parent

STUMBLE = "um, what time is it, uh, right now"


def _strip_full_line_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#"))


class _RecorderStore:
    def __init__(self) -> None:
        self.logged: list[tuple[str, str]] = []

    def log_message(self, role: str, text: str) -> None:
        self.logged.append((role, text))


class _RaisingStore:
    def __init__(self) -> None:
        self.attempts = 0

    def log_message(self, role: str, text: str) -> None:
        self.attempts += 1
        raise RuntimeError("memory unavailable")


class InputTranscriptionConfigTests(unittest.TestCase):
    @unittest.skipIf(live_client.types is None,
                      "google-genai unavailable on this interpreter")
    def test_config_carries_input_and_output_transcription(self):
        types_mod = live_client.types
        cfg = types_mod.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription=types_mod.AudioTranscriptionConfig(),
            input_audio_transcription=types_mod.AudioTranscriptionConfig(),
        )
        dumped = cfg.model_dump(exclude_none=True, by_alias=True)
        self.assertEqual(dumped["responseModalities"], ["AUDIO"])
        self.assertIn("outputAudioTranscription", dumped)
        self.assertIn("inputAudioTranscription", dumped)

    def test_tree_config_enables_input_transcription(self):
        source = _strip_full_line_comments(
            (ROOT / "core" / "voice"
             / "live_client_lifecycle.py").read_text(encoding="utf-8"))
        self.assertIn(
            "input_audio_transcription=types.AudioTranscriptionConfig()",
            source)
        self.assertIn(
            "output_audio_transcription=types.AudioTranscriptionConfig()",
            source)


class InputTranscriptionSessionTests(unittest.TestCase):
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
        # INTENTIONAL (skills split B47): _flush_input_transcript
        # lives in the receive mixin and reads memory_store from
        # its own globals — patch both bindings (B16 pattern,
        # same recorder, both restored).
        self._real_store_recv = live_client_receive_mod.memory_store
        live_client_receive_mod.memory_store = self.store

    def tearDown(self) -> None:
        live_client.memory_store = self._real_store
        live_client_receive_mod.memory_store = self._real_store_recv

    def _content(self, text=None, finished=False, turn_complete=False):
        inp = None if text is None else SimpleNamespace(
            text=text, finished=finished)
        return SimpleNamespace(
            input_transcription=inp, turn_complete=turn_complete)

    def test_chunks_accumulate_and_flush_once_on_finished(self):
        self.session._handle_input_transcription(
            self._content(text="um, what time is it, ", finished=False))
        self.assertEqual(self.transcripts, [])
        self.assertEqual(self.session._input_transcript_buf,
                         "um, what time is it, ")
        self.session._handle_input_transcription(
            self._content(text="uh, right now", finished=True))
        self.assertEqual(self.transcripts, [("user", STUMBLE)])
        self.assertEqual(self.session._input_transcript_buf, "")
        self.assertEqual(self.store.logged, [("user", STUMBLE)])

    def test_verbatim_never_cleaned_up(self):
        # The ruling's core: exactly what STT heard, fillers and
        # mis-hearings included — no tidying, no dedup, no case
        # fix. A second utterance pins that nothing is normalised.
        self.session._handle_input_transcription(
            self._content(text="uh huh, open, open notepad",
                          finished=True))
        self.assertEqual(
            self.transcripts,
            [("user", "uh huh, open, open notepad")])
        self.assertEqual(
            self.store.logged,
            [("user", "uh huh, open, open notepad")])

    def test_turn_complete_flushes_partial_text(self):
        self.session._handle_input_transcription(
            self._content(text="partial ", finished=False))
        self.assertEqual(self.transcripts, [])
        self.session._handle_input_transcription(
            self._content(text=None, turn_complete=True))
        self.assertEqual(self.transcripts, [("user", "partial ")])

    def test_interruption_flush_path_preserves_partial(self):
        self.session._handle_input_transcription(
            self._content(text="barge in words", finished=False))
        # The receive loop's interruption branch calls exactly this.
        self.session._flush_input_transcript()
        self.assertEqual(self.transcripts, [("user", "barge in words")])
        self.session._flush_input_transcript()
        self.assertEqual(self.transcripts, [("user", "barge in words")])

    def test_no_transcription_no_emit(self):
        self.session._handle_input_transcription(
            self._content(text=None, turn_complete=False))
        self.assertEqual(self.transcripts, [])
        self.assertEqual(self.session._input_transcript_buf, "")

    def test_whitespace_only_emit_nothing_but_clears(self):
        self.session._handle_input_transcription(
            self._content(text="   ", finished=True))
        self.assertEqual(self.transcripts, [])
        self.assertEqual(self.session._input_transcript_buf, "")

    def test_audio_path_untouched(self):
        self.session._handle_input_transcription(
            self._content(text="spoken words", finished=True))
        self.assertEqual(self.audio_calls, [])
        self.assertIsNotNone(self.session.on_audio_data)

    def test_output_path_untouched_by_input(self):
        self.session._handle_input_transcription(
            self._content(text="my words", finished=True))
        self.assertEqual(self.transcripts, [("user", "my words")])
        self.assertEqual(self.session._output_transcript_buf, "")

    def test_stop_does_not_suppress_user_words(self):
        # Stop applies to the assistant's reply, never to the
        # user's own words — a fresh spoken turn is what clears
        # the stopped marker (fix #7 U4 #29).
        self.session._reply_stop_requested = True
        self.session._handle_input_transcription(
            self._content(text="stop", finished=True))
        self.assertEqual(self.transcripts, [("user", "stop")])
        self.assertTrue(self.session._reply_stop_requested)

    def test_failure_degrades_to_voice_only(self):
        # Field-check fix #2 (T6, re-baselined): a transcript
        # consumer that raises must not break the session — and
        # must not lose the words either. The flush keeps the
        # text for the next flush (a loss note is surfaced), and
        # a later good utterance delivers the kept text with it.
        self.session.on_transcript = lambda role, text: (_ for _ in ()).throw(
            RuntimeError("consumer broke"))
        self.session._handle_input_transcription(
            self._content(text="lost?", finished=True))
        self.assertEqual(self.session._input_transcript_buf, "lost?")
        # A transcription object whose text access raises is
        # swallowed by the handler itself.
        class _Breaking:
            @property
            def text(self):
                raise RuntimeError("transcription broke")

            finished = False

        broken = SimpleNamespace(
            input_transcription=_Breaking(), turn_complete=False)
        self.session._handle_input_transcription(broken)  # must not raise
        # And the session still serves a later good utterance — the
        # kept text rides along with it.
        self.session.on_transcript = lambda role, text: (
            self.transcripts.append((role, text)))
        self.session._handle_input_transcription(
            self._content(text="still here", finished=True))
        self.assertEqual(self.transcripts,
                         [("user", "lost?still here")])
        self.assertEqual(self.session._input_transcript_buf, "")

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
        self.session._handle_input_transcription(
            self._content(text="shown words", finished=True))
        self.assertEqual(self.session._input_transcript_buf, "")
        self.assertEqual(self.transcripts[0], ("user", "shown words"))
        self.assertEqual(len(self.transcripts), 2)
        self.assertEqual(self.transcripts[1][0], "assistant")
        self.assertIn("could not be saved to memory",
                      self.transcripts[1][1])
        self.assertEqual(store.attempts, 1)


if __name__ == "__main__":
    unittest.main()
