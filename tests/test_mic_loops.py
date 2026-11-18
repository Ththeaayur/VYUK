"""GAPS-16 suite: flaw #8 — one mic loop, one barge-in, one STT
factory, one VAD truth.

Drives the shared loop (core/voice/mic_loop.run_mic_loop) with
scripted mic chunks, and drives each session's thin wrapper
(local, lite, Groq) through the same scripted chunks, proving the
wrappers still dispatch what the loop assembles. faster-whisper
is NOT installed in the lab, so the STT checks cover the factory's
degradation path (None, no raise); the field proves a real load.
"""

import asyncio
import queue
import time
import unittest

import numpy as np

from core.config import config
from core.voice import mic_loop
from core.voice.asr import (
    faster_whisper_available,
    get_whisper_model,
    release_whisper_model,
    resolve_model_arg,
)
from core.voice.local_client_vad import (
    NoiseFloorEstimator,
    VAD_TRAILING_SILENCE_S,
    _barge_in_guard_s,
    _barge_in_sustain_s,
    _barge_in_threshold,
    _effective_vad_threshold,
    _vad_threshold,
)
from core.voice.vad import EnergyVAD, _energy_threshold


def _pcm(rms_value, n_samples=1024):
    return np.full(n_samples, int(rms_value), dtype=np.int16).tobytes()


class _CountingQueue(queue.Queue):
    def __init__(self, on_take=None):
        super().__init__()
        self._on_take = on_take
        self._takes = 0

    def get_nowait(self):
        item = super().get_nowait()
        self._takes += 1
        if self._on_take is not None:
            self._on_take(self._takes - 1)
        return item


class _FakeMic:
    def __init__(self, chunks, on_take=None):
        self.mic_queue = _CountingQueue(on_take=on_take)
        for c in chunks:
            self.mic_queue.put(c)


class _FakeSession:
    def __init__(self, speaking=False):
        self.speaking = speaking
        self.interrupts = 0
        self.utterances = []
        self.silences = []  # trailing silence per dispatched utterance
        self._stop = False

    def running(self):
        return not self._stop

    def is_speaking(self):
        return self.speaking

    def set_speaking(self, value):
        self.speaking = bool(value)

    def interrupt(self):
        self.interrupts += 1

    async def on_utterance(self, pcm, trailing_silence_s=0.0):
        self.utterances.append(pcm)
        self.silences.append(trailing_silence_s)

    def stop(self):
        self._stop = True


def _drive(chunks, session, estimator=None, pre_roll=None,
           should_stop=None, on_take=None, settle_s=0.3):
    async def _run():
        task = asyncio.create_task(mic_loop.run_mic_loop(
            _FakeMic(chunks, on_take=on_take),
            running=session.running,
            is_speaking=session.is_speaking,
            set_speaking=session.set_speaking,
            interrupt_playback=session.interrupt,
            on_utterance=session.on_utterance,
            estimator=estimator, pre_roll=pre_roll,
            should_stop=should_stop))
        await asyncio.sleep(settle_s)
        session.stop()
        await asyncio.wait_for(task, timeout=5.0)

    asyncio.run(_run())


class _Counts:
    """Chunk counts for this config's tunings."""

    def __init__(self):
        from core.voice.local_client_vad import (
            VAD_MIN_SPEECH_S,
            VAD_TRAILING_SILENCE_S,
        )
        chunk_s = config.chunk_size / config.sample_rate_in
        self.chunk_s = chunk_s
        self.n_speech = int(-(-VAD_MIN_SPEECH_S // chunk_s))
        self.n_silence = int(-(-VAD_TRAILING_SILENCE_S // chunk_s))
        self.n_sustain = int(-(-_barge_in_sustain_s() // chunk_s))
        self.n_guard = int(-(-_barge_in_guard_s() // chunk_s))


COUNTS = _Counts()
LOUD = _pcm(1000)
QUIET = _pcm(100)


class SharedLoopTests(unittest.TestCase):
    def test_quiet_only_dispatches_nothing(self):
        fake = _FakeSession()
        _drive([QUIET] * 10, fake)
        self.assertEqual(fake.utterances, [])
        self.assertEqual(fake.interrupts, 0)

    def test_speech_then_silence_dispatches_one_utterance(self):
        fake = _FakeSession()
        _drive([LOUD] * COUNTS.n_speech + [QUIET] * COUNTS.n_silence,
               fake)
        self.assertEqual(len(fake.utterances), 1)
        self.assertEqual(fake.utterances[0], LOUD * COUNTS.n_speech)
        self.assertEqual(fake.interrupts, 0)
        # GAPS-25 (V-2): the utterance carries the trailing
        # silence that ended it — at least the endpoint rule, and
        # less than one chunk more (the dispatch fires on the
        # first chunk that satisfies the rule).
        self.assertEqual(len(fake.silences), 1)
        self.assertGreaterEqual(fake.silences[0],
                                VAD_TRAILING_SILENCE_S)
        self.assertLess(fake.silences[0],
                        VAD_TRAILING_SILENCE_S + COUNTS.chunk_s)

    def test_single_loud_chunk_does_not_barge_in(self):
        fake = _FakeSession(speaking=True)
        _drive([LOUD] + [QUIET] * (COUNTS.n_guard + 10), fake)
        self.assertEqual(fake.interrupts, 0)
        self.assertEqual(fake.utterances, [])

    def test_sustained_speech_barges_in_once_and_seeds(self):
        fake = _FakeSession(speaking=True)
        _drive([LOUD] * COUNTS.n_sustain + [LOUD] * COUNTS.n_speech
               + [QUIET] * COUNTS.n_silence, fake)
        self.assertEqual(fake.interrupts, 1)
        self.assertEqual(len(fake.utterances), 1)
        self.assertEqual(fake.utterances[0],
                         LOUD * (COUNTS.n_sustain + COUNTS.n_speech))

    def test_post_playback_guard_swallows_echo_tail(self):
        fake = _FakeSession(speaking=True)
        script = ([QUIET] + [LOUD] * COUNTS.n_guard
                  + [LOUD] * COUNTS.n_speech + [QUIET] * COUNTS.n_silence)
        _drive(script, fake,
               on_take=lambda i: setattr(fake, "speaking", False)
               if i >= 1 else None)
        self.assertEqual(fake.interrupts, 0)
        self.assertEqual(len(fake.utterances), 1)
        self.assertEqual(fake.utterances[0], LOUD * COUNTS.n_speech)

    def test_adaptive_floor_is_the_live_truth(self):
        est = NoiseFloorEstimator()
        for _ in range(6):
            est.observe(300.0, in_utterance=False)
        self.assertAlmostEqual(_effective_vad_threshold(est), 1050.0)
        fake = _FakeSession()
        speech_hi = _pcm(1200)
        _drive([_pcm(500)] + [speech_hi] * COUNTS.n_speech
               + [QUIET] * COUNTS.n_silence, fake, estimator=est)
        self.assertEqual(len(fake.utterances), 1)
        self.assertEqual(fake.utterances[0],
                         speech_hi * COUNTS.n_speech)

    def test_old_groq_trip_no_longer_interrupts(self):
        # 450 RMS would have cut a Groq reply at once (any chunk
        # >= the plain 400 floor while speaking). Now it does not
        # even reach the barge-in bar (640).
        fake = _FakeSession(speaking=True)
        _drive([_pcm(450)] + [QUIET] * (COUNTS.n_guard + 10), fake)
        self.assertEqual(fake.interrupts, 0)
        self.assertEqual(fake.utterances, [])

    def test_pre_roll_hook_prepends_onset_audio(self):
        fake = _FakeSession()
        _drive([LOUD] * COUNTS.n_speech + [QUIET] * COUNTS.n_silence,
               fake,
               pre_roll=(lambda _am, _c: b"PRE",
                         lambda _am: b"RING"))
        self.assertEqual(len(fake.utterances), 1)
        self.assertTrue(fake.utterances[0].startswith(b"PRE"))
        self.assertEqual(fake.utterances[0],
                         b"PRE" + LOUD * COUNTS.n_speech)

    def test_should_stop_hook_ends_the_loop_at_once(self):
        fake = _FakeSession()
        _drive([LOUD] * COUNTS.n_speech + [QUIET] * COUNTS.n_silence,
               fake, should_stop=lambda: True)
        self.assertEqual(fake.utterances, [])

    def test_chunk_rms_matches_loop_units(self):
        self.assertAlmostEqual(mic_loop.chunk_rms(_pcm(1000)), 1000.0)
        self.assertEqual(mic_loop.chunk_rms(b""), 0.0)


class WrapperTests(unittest.TestCase):
    """Each session's _mic_loop is a thin wrapper over the shared
    loop: drive it with scripted chunks and prove the utterance
    still reaches the session's own dispatch."""

    def _drive_wrapper(self, session, chunks, stop_attr, stop_value,
                       settle_s=0.5):
        async def _run():
            task = asyncio.create_task(
                session._mic_loop(_FakeMic(chunks)))
            await asyncio.sleep(settle_s)
            setattr(session, stop_attr, stop_value)
            await asyncio.wait_for(task, timeout=5.0)
            # Local/Groq dispatch utterances as tasks; drain them
            # in THIS event loop before it closes, or they never run.
            pending = list(getattr(session, "_turn_tasks", ()) or ())
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        asyncio.run(_run())

    def test_lite_wrapper_dispatches_inline(self):
        from core.voice.lite_client_session import LiteLiveSession
        session = LiteLiveSession()
        seen = []
        silences = []
        session._speaking = False

        async def _record(pcm, trailing_silence_s=0.0):
            seen.append(pcm)
            silences.append(trailing_silence_s)

        session._process_utterance = _record
        self._drive_wrapper(
            session,
            [LOUD] * COUNTS.n_speech + [QUIET] * COUNTS.n_silence,
            "_stopped", True)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0], LOUD * COUNTS.n_speech)
        # GAPS-25 (V-2): the trailing silence that ended the
        # utterance reaches the session's handler.
        self.assertEqual(len(silences), 1)
        self.assertGreaterEqual(silences[0], VAD_TRAILING_SILENCE_S)
        self.assertLess(silences[0],
                        VAD_TRAILING_SILENCE_S + COUNTS.chunk_s)

    def test_groq_wrapper_dispatches_as_task(self):
        from core.voice.cloud_text_client import GroqSession
        session = GroqSession()
        session._running = True
        session._speaking = False
        seen = []
        silences = []

        async def _record(pcm, trailing_silence_s=0.0):
            seen.append(pcm)
            silences.append(trailing_silence_s)

        session._process_utterance = _record
        self._drive_wrapper(
            session,
            [LOUD] * COUNTS.n_speech + [QUIET] * COUNTS.n_silence,
            "_running", False)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0], LOUD * COUNTS.n_speech)
        # GAPS-25 (V-2): the trailing silence that ended the
        # utterance reaches the session's handler.
        self.assertEqual(len(silences), 1)
        self.assertGreaterEqual(silences[0], VAD_TRAILING_SILENCE_S)
        self.assertLess(silences[0],
                        VAD_TRAILING_SILENCE_S + COUNTS.chunk_s)

    def test_local_wrapper_dispatches_as_task_with_hooks(self):
        from core.voice.local_client import LocalLiveSession
        session = LocalLiveSession.__new__(LocalLiveSession)
        now = time.monotonic()
        session._running = True
        session._speaking = False
        session._vad_estimator = None
        session._session_tasks = []
        session._turn_tasks = set()
        session._last_utterance_at = now
        session._last_brain_activity = now
        session._last_stt_activity = now
        seen = []
        silences = []
        interrupted = []

        async def _record(pcm, trailing_silence_s=0.0):
            seen.append(pcm)
            silences.append(trailing_silence_s)

        session._process_utterance = _record
        session._interrupt_playback = lambda: interrupted.append(True)
        self._drive_wrapper(
            session,
            [LOUD] * COUNTS.n_speech + [QUIET] * COUNTS.n_silence,
            "_running", False)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0], LOUD * COUNTS.n_speech)
        # GAPS-25 (V-2): the trailing silence that ended the
        # utterance reaches the session's handler.
        self.assertEqual(len(silences), 1)
        self.assertGreaterEqual(silences[0], VAD_TRAILING_SILENCE_S)
        self.assertLess(silences[0],
                        VAD_TRAILING_SILENCE_S + COUNTS.chunk_s)
        self.assertEqual(interrupted, [])

    def test_local_wrapper_barge_in_uses_the_faded_interrupt(self):
        from core.voice.local_client import LocalLiveSession
        session = LocalLiveSession.__new__(LocalLiveSession)
        now = time.monotonic()
        session._running = True
        session._speaking = True
        session._vad_estimator = None
        session._session_tasks = []
        session._turn_tasks = set()
        session._last_utterance_at = now
        session._last_brain_activity = now
        session._last_stt_activity = now
        seen = []
        silences = []
        interrupted = []

        async def _record(pcm, trailing_silence_s=0.0):
            seen.append(pcm)
            silences.append(trailing_silence_s)

        session._process_utterance = _record
        session._interrupt_playback = lambda: interrupted.append(True)
        self._drive_wrapper(
            session,
            [LOUD] * COUNTS.n_sustain + [LOUD] * COUNTS.n_speech
            + [QUIET] * COUNTS.n_silence,
            "_running", False)
        self.assertEqual(len(interrupted), 1)
        self.assertEqual(len(seen), 1)
        # GAPS-25 (V-2): the trailing silence that ended the
        # utterance reaches the session's handler.
        self.assertEqual(len(silences), 1)
        self.assertGreaterEqual(silences[0], VAD_TRAILING_SILENCE_S)
        self.assertLess(silences[0],
                        VAD_TRAILING_SILENCE_S + COUNTS.chunk_s)


class SttFactoryTests(unittest.TestCase):
    def test_factory_resolves_the_same_model_arg(self):
        from core.voice import local_assets
        self.assertEqual(resolve_model_arg(),
                         local_assets.whisper_model_arg())

    def test_factory_degrades_without_faster_whisper(self):
        if faster_whisper_available():
            self.skipTest("faster-whisper IS installed here")
        self.assertIsNone(get_whisper_model())
        release_whisper_model(object())  # uncached: no raise
        from core.voice.lite_client_session import LiteLiveSession
        self.assertIs(LiteLiveSession()._ensure_whisper(), False)

    def test_one_construction_in_asr(self):
        import pathlib
        text = (pathlib.Path(__file__).resolve().parent.parent
                / "core" / "voice" / "asr.py").read_text(encoding="utf-8")
        self.assertEqual(text.count("WhisperModel("), 1)
        self.assertIn("def get_whisper_model", text)


class VadTruthTests(unittest.TestCase):
    def test_delegated_threshold_equals_live_threshold(self):
        self.assertAlmostEqual(_energy_threshold(), _vad_threshold())

    def test_energy_vad_counts_exact_threshold_as_speech(self):
        at = np.full(512, 400.0 / 32768.0, dtype=np.float32)
        below = np.full(512, 100.0 / 32768.0, dtype=np.float32)
        self.assertTrue(EnergyVAD().is_speech(at))
        self.assertFalse(EnergyVAD().is_speech(below))

    def test_boot_turn_taking_beat_is_degraded_without_weights(self):
        from core.desktop.boot_sequence import _check_turn_taking
        beat = _check_turn_taking()
        self.assertEqual(beat.get("verdict"), "degraded")
        self.assertIn("heuristic", str(beat.get("reason", "")))


if __name__ == "__main__":
    unittest.main()
