"""B-R1-1 (2026-11-09) Silero VAD speech detector.

Runs against a FAKE backend (scripted probabilities, the same
shape as the real _RealBackend: is_speech(window) ->
(is_speech, backend, flipped)) on BOTH interpreters — no real
mic, no real audio file of his voice — so the WRAPPER's rules
are proved everywhere: N consecutive speech windows to start
(a single speech frame never starts it), M consecutive
silence windows to end (a pause mid-sentence never ends it),
noise frames below the threshold never start speech, a stuck
state is impossible (an engine error returns an honest failure
and the machine resets to silence), and non-16 kHz / partial /
empty / bytes / NaN frames are refused without touching the
machine.

The REAL engine is proved where it exists: on this Linux lab
machine the tree's core/voice/vad.py loads on BOTH
interpreters (numpy is declared and present in both), but no
silero_vad.onnx exists anywhere, so the real-engine tests
assert the honest ENERGY fallback (backend "energy", silence
and loud frames scored correctly) instead of skipping
silently. A suite that silently skips the VAD checks on an
interpreter without the library is a lie, not a pass. The
Silero path itself (a real .onnx session scoring real speech)
is a field leg on his Windows machine, owed, not this batch —
the flip from Silero to energy mid-stream is proved here with
a patched engine dict, so the seam is covered even without
weights.

The real-Windows leg (real speech on his machine, in his
room, with his mic, through the wrapper) is a field leg,
owed, not this batch.
"""

import sys
import unittest

import numpy as np

from core.desktop import voice_activity as va

INTERPRETER = sys.executable
ENGINE_AVAILABLE = va.engine_available()


class FakeProbabilityBackend:
    """Scripted Silero stand-in: consumes one probability per
    window (is_speech = prob >= 0.5, the engine's threshold);
    when the script runs out, the window is silence. Records
    every window it was handed, so tests can assert the
    wrapper scored each window exactly once and (for the
    int16 test) handed over float32 samples in [-1, 1]."""

    def __init__(self, probs, name="silero-onnx"):
        self._probs = list(probs)
        self._calls = 0
        self.name = name
        self.windows_seen = []
        self.reset_calls = 0

    @property
    def backend(self):
        return self.name

    def is_speech(self, window):
        self._calls += 1
        self.windows_seen.append(np.asarray(window, dtype=np.float32))
        if self._calls - 1 < len(self._probs):
            prob = self._probs[self._calls - 1]
        else:
            prob = 0.0
        return (bool(prob >= 0.5), self.name, False)

    def reset(self):
        self.reset_calls += 1


class FakeErrorBackend:
    """Raises on the Nth window it scores (1-based), so the
    wrapper's error path is proved: honest failure, machine
    reset to silence, never a stale "speaking"."""

    def __init__(self, fail_on=1, name="silero-onnx"):
        self._fail_on = fail_on
        self._calls = 0
        self.name = name
        self.reset_calls = 0

    @property
    def backend(self):
        return self.name

    def is_speech(self, window):
        self._calls += 1
        if self._calls == self._fail_on:
            raise RuntimeError("fake engine failure")
        return (True, self.name, False)

    def reset(self):
        self.reset_calls += 1


class FakeSilero:
    """Stand-in for the engine's SileroVAD class (used to
    patch va._VAD and prove the Silero->energy flip)."""

    def __init__(self, weights=None):
        self.available = True
        self.weights_path = None
        self.threshold = 0.5
        self._probs = [0.9, 0.9, None]  # two speech, then the session dies
        self._calls = 0
        self.reset_calls = 0

    def probability(self, frame):
        self._calls += 1
        return self._probs[min(self._calls - 1, len(self._probs) - 1)]

    def reset(self):
        self.reset_calls += 1


class FakeEnergy:
    """Stand-in for the engine's EnergyVAD class."""

    def __init__(self):
        self.calls = 0

    def is_speech(self, frame):
        self.calls += 1
        return True


def _frames(n_windows, value=0.0):
    """n_windows x 512 samples of a constant float32 value."""
    return np.full(n_windows * va.WINDOW_SAMPLES, value, dtype=np.float32)


class TestSpeechDetectorFake(unittest.TestCase):
    def test_speech_frames_report_speaking(self):
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 11))
        out = det.feed(_frames(11))
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "speaking")
        self.assertEqual(out["backend"], "silero-onnx")
        self.assertEqual(out["speech_frames"], 11)
        self.assertEqual(out["windows"], 11)
        self.assertFalse(out["ended"])

    def test_silence_frames_report_silence(self):
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.05] * 30))
        out = det.feed(_frames(30))
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "silence")
        self.assertEqual(out["speech_frames"], 0)
        self.assertFalse(out["ended"])

    def test_single_speech_frame_does_not_start(self):
        # A door slam is one loud window: it must never open an
        # utterance on its own.
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9, 0.05, 0.05]))
        out = det.feed(_frames(3))
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "silence")
        self.assertEqual(out["speech_frames"], 0)
        self.assertEqual(out["speech_run"], 0)

    def test_n_minus_one_speech_windows_do_not_start(self):
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 10))
        out = det.feed(_frames(10))
        self.assertEqual(out["state"], "silence")
        self.assertEqual(out["speech_frames"], 0)
        self.assertEqual(out["speech_run"], 10)

    def test_n_consecutive_speech_windows_start(self):
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 11))
        det.feed(_frames(10))
        out = det.feed(_frames(1))
        self.assertEqual(out["state"], "speaking")
        # The build-up windows ARE speech: they count toward
        # the utterance (the mic path's pre-roll keeps the onset).
        self.assertEqual(out["speech_frames"], 11)

    def test_speech_run_resets_on_silence(self):
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 5 + [0.05] + [0.9] * 5))
        out = det.feed(_frames(11))
        self.assertEqual(out["state"], "silence")
        self.assertEqual(out["speech_frames"], 0)
        self.assertEqual(out["speech_run"], 5)

    def test_pause_mid_sentence_does_not_end(self):
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 11 + [0.05] * 24))
        det.feed(_frames(11))
        out = det.feed(_frames(24))
        self.assertEqual(out["state"], "speaking")
        self.assertEqual(out["speech_frames"], 11)
        self.assertEqual(out["silence_frames"], 24)
        self.assertFalse(out["ended"])

    def test_m_consecutive_silence_windows_end(self):
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 11 + [0.05] * 25))
        det.feed(_frames(11))
        out = det.feed(_frames(25))
        self.assertEqual(out["state"], "ended")
        self.assertTrue(out["ended"])
        self.assertEqual(len(out["ended_utterances"]), 1)
        ended = out["ended_utterances"][0]
        self.assertEqual(ended["speech_frames"], 11)
        self.assertEqual(ended["silence_frames"], 25)
        self.assertEqual(ended["end_reason"], "silence")
        self.assertAlmostEqual(ended["utterance_s"], 11 * va.WINDOW_S)
        # The machine is idle again: counts live in the ended
        # record, not in the live state.
        self.assertEqual(out["speech_frames"], 0)
        self.assertEqual(out["silence_frames"], 0)

    def test_noise_frames_do_not_start_speech(self):
        # 30 windows of sub-threshold probability (a fan, music,
        # room noise as the model would score it): never speech.
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.3] * 30))
        out = det.feed(_frames(30))
        self.assertEqual(out["state"], "silence")
        self.assertEqual(out["speech_frames"], 0)

    def test_error_returns_failure_not_stale_state(self):
        # The engine dies on the 5th window, while an utterance
        # is open: the feed fails honestly and the machine
        # resets to silence — a stuck "speaking" is impossible.
        det = va.SpeechDetector(backend=FakeErrorBackend(fail_on=5))
        det.feed(_frames(4))  # 4 speech windows, no start yet
        det.feed(_frames(7))  # windows 5..11: fails on window 5
        # Re-drive with a clean backend: the machine must be
        # idle, not stuck speaking.
        det._backend = FakeProbabilityBackend([0.05] * 30)
        out = det.feed(_frames(30))
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "silence")
        self.assertEqual(out["speech_frames"], 0)

    def test_error_feed_itself_is_honest(self):
        det = va.SpeechDetector(backend=FakeErrorBackend(fail_on=1))
        out = det.feed(_frames(1))
        self.assertFalse(out["ok"])
        self.assertEqual(out["state"], "silence")
        self.assertTrue(out["reset_on_error"])
        self.assertIn("failed", out["reason"])

    def test_non_16khz_refused_and_machine_untouched(self):
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 11))
        det.feed(_frames(11))
        self.assertEqual(det.state, "speaking")
        for rate in (8000, 44100, 48000):
            out = det.feed(_frames(1), sample_rate=rate)
            self.assertFalse(out["ok"], f"rate {rate} must be refused")
            self.assertIn("16000", out["reason"])
            # The refusal never touches the machine: still
            # speaking, counts intact.
            self.assertEqual(out["state"], "speaking")
            self.assertEqual(out["speech_frames"], 11)
        self.assertEqual(det.state, "speaking")

    def test_partial_window_refused(self):
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 11))
        det.feed(_frames(11))
        out = det.feed(np.zeros(600, dtype=np.float32))
        self.assertFalse(out["ok"])
        self.assertIn("512", out["reason"])
        self.assertEqual(out["state"], "speaking")
        self.assertEqual(out["speech_frames"], 11)

    def test_empty_frame_refused(self):
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 11))
        out = det.feed(np.zeros(0, dtype=np.float32))
        self.assertFalse(out["ok"])
        self.assertIn("empty", out["reason"])

    def test_bytes_refused(self):
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 11))
        out = det.feed(b"\x00\x00" * 512)
        self.assertFalse(out["ok"])
        self.assertIn("bytes", out["reason"])

    def test_nan_refused(self):
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 11))
        frame = _frames(1)
        frame[0] = float("nan")
        out = det.feed(frame)
        self.assertFalse(out["ok"])
        self.assertIn("non-finite", out["reason"])

    def test_int16_array_scaled_to_float(self):
        # int16 16384 must reach the backend as 0.5, not 16384.0.
        backend = FakeProbabilityBackend([0.9] * 11)
        det = va.SpeechDetector(backend=backend)
        frame = np.full(va.WINDOW_SAMPLES, 16384, dtype=np.int16)
        out = det.feed(frame)
        self.assertTrue(out["ok"])
        self.assertEqual(len(backend.windows_seen), 1)
        self.assertAlmostEqual(float(backend.windows_seen[0].max()), 0.5)
        self.assertAlmostEqual(float(backend.windows_seen[0].min()), 0.5)

    def test_int32_array_scaled_by_its_own_range(self):
        # Parent regression (2026-11-09): the original divided
        # every integer dtype by 32768.0, so an int32 frame was
        # 65536x too loud — a quiet int32 frame counted as
        # speech. int32 1073741824 (0.5 x 2**31) must reach the
        # backend as 0.5, and a quiet int32 frame (0.001 x 2**31)
        # must reach it as 0.001.
        backend = FakeProbabilityBackend([0.9] * 11)
        det = va.SpeechDetector(backend=backend)
        frame = np.full(va.WINDOW_SAMPLES, 1073741824, dtype=np.int32)
        out = det.feed(frame)
        self.assertTrue(out["ok"])
        self.assertEqual(len(backend.windows_seen), 1)
        self.assertAlmostEqual(float(backend.windows_seen[0].max()), 0.5)
        backend2 = FakeProbabilityBackend([0.9] * 11)
        det2 = va.SpeechDetector(backend=backend2)
        quiet = np.full(va.WINDOW_SAMPLES, 2147484, dtype=np.int32)
        out2 = det2.feed(quiet)
        self.assertTrue(out2["ok"])
        self.assertEqual(len(backend2.windows_seen), 1)
        self.assertAlmostEqual(float(backend2.windows_seen[0].max()),
                               0.001, places=3)

    def test_other_integer_dtype_is_refused(self):
        # Parent regression (2026-11-09): an integer encoding
        # with no declared scale (e.g. int8) is refused, not
        # silently divided by 32768.0.
        det = va.SpeechDetector(backend=FakeProbabilityBackend([0.9]))
        frame = np.full(va.WINDOW_SAMPLES, 64, dtype=np.int8)
        out = det.feed(frame)
        self.assertFalse(out["ok"])
        self.assertIn("int16 or int32", out["reason"])

    def test_max_utterance_ends(self):
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 10),
            start_frames=3, end_frames=5, max_speech_frames=5)
        out = det.feed(_frames(5))
        self.assertEqual(out["state"], "ended")
        self.assertTrue(out["ended"])
        ended = out["ended_utterances"][0]
        self.assertEqual(ended["end_reason"], "max_utterance")
        self.assertEqual(ended["speech_frames"], 5)
        self.assertAlmostEqual(ended["utterance_s"], 5 * va.WINDOW_S)

    def test_ended_utterance_survives_new_start_in_same_call(self):
        # One call, 4 windows: silence, silence (ends the open
        # utterance), speech, speech (opens a new one). The
        # ended utterance must still be reported.
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 2 + [0.05] * 2
                                           + [0.9] * 2),
            start_frames=2, end_frames=2)
        det.feed(_frames(2))
        self.assertEqual(det.state, "speaking")
        out = det.feed(_frames(4))
        self.assertEqual(out["state"], "speaking")
        self.assertTrue(out["ended"])
        self.assertEqual(len(out["ended_utterances"]), 1)
        self.assertEqual(out["ended_utterances"][0]["end_reason"],
                         "silence")
        self.assertEqual(out["ended_utterances"][0]["speech_frames"], 2)
        self.assertEqual(out["speech_frames"], 2)  # the new utterance

    def test_reset_clears_state_and_backend(self):
        backend = FakeProbabilityBackend([0.9] * 11)
        det = va.SpeechDetector(backend=backend)
        det.feed(_frames(11))
        self.assertEqual(det.state, "speaking")
        out = det.reset()
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "silence")
        self.assertEqual(out["speech_frames"], 0)
        self.assertEqual(backend.reset_calls, 1)
        self.assertEqual(det.state, "silence")

    def test_timestamp_carried(self):
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 11 + [0.05] * 25))
        det.feed(_frames(11), timestamp=1234.5)
        out = det.feed(_frames(25), timestamp=1235.0)
        self.assertEqual(out["timestamp"], 1235.0)
        self.assertEqual(out["ended_utterances"][0]["ended_at"], 1235.0)

    def test_mic_chunk_is_two_windows_per_feed(self):
        # The mic delivers 1024-sample chunks: 2 windows each.
        det = va.SpeechDetector(
            backend=FakeProbabilityBackend([0.9] * 12))
        for _ in range(5):
            out = det.feed(_frames(2))  # 1024 samples
            self.assertEqual(out["windows"], 2)
            self.assertEqual(out["state"], "silence")
        out = det.feed(_frames(2))  # 11th and 12th windows
        self.assertEqual(out["state"], "speaking")
        # The 11th window opened the utterance; the 12th window
        # is speech too, so it counts: 12, not 11.
        self.assertEqual(out["speech_frames"], 12)

    def test_constructor_rejects_bad_counts(self):
        with self.assertRaises(ValueError):
            va.SpeechDetector(backend=FakeProbabilityBackend([]),
                              start_frames=0)
        with self.assertRaises(ValueError):
            va.SpeechDetector(backend=FakeProbabilityBackend([]),
                              end_frames=0)
        with self.assertRaises(ValueError):
            va.SpeechDetector(backend=FakeProbabilityBackend([]),
                              start_frames=5, max_speech_frames=4)


class TestModuleLevelSurface(unittest.TestCase):
    def setUp(self):
        va.reset_backend()

    def tearDown(self):
        va.reset_backend()

    def test_module_feed_and_reset_use_injected_backend(self):
        va.set_backend(FakeProbabilityBackend([0.9] * 11))
        out = va.feed(_frames(11))
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "speaking")
        out = va.reset()
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "silence")


class TestRealBackendFlip(unittest.TestCase):
    def test_silero_death_flips_to_energy(self):
        # Patch the engine dict: a Silero that scores two
        # windows then dies (probability() -> None), and an
        # energy backend that always says speech. The wrapper
        # must score the dying window on energy, report the
        # flip, and stay on energy afterwards.
        original = va._VAD
        try:
            va._VAD = {
                "numpy": np,
                "SileroVAD": FakeSilero,
                "EnergyVAD": FakeEnergy,
                "threshold": 0.5,
                "licence": "MIT",
                "weights_note": "test",
                "find_weights": lambda: None,
                "onnxruntime_available": lambda: False,
            }
            backend = va._RealBackend()
            self.assertEqual(backend.backend, "silero-onnx")
            speech, ran, flipped = backend.is_speech(_frames(1))
            self.assertTrue(speech)
            self.assertEqual(ran, "silero-onnx")
            self.assertFalse(flipped)
            speech, ran, flipped = backend.is_speech(_frames(1))
            self.assertTrue(speech)
            self.assertEqual(ran, "silero-onnx")
            # Third window: the Silero session dies; the window
            # is still scored (on energy) and the flip reported.
            speech, ran, flipped = backend.is_speech(_frames(1))
            self.assertTrue(speech)
            self.assertEqual(ran, "energy")
            self.assertTrue(flipped)
            self.assertEqual(backend.backend, "energy")
            # Fourth window: still energy, no second flip.
            speech, ran, flipped = backend.is_speech(_frames(1))
            self.assertTrue(speech)
            self.assertEqual(ran, "energy")
            self.assertFalse(flipped)
        finally:
            va._VAD = original


class TestRealEngine(unittest.TestCase):
    """The real engine on THIS interpreter (named in the
    failure message when it is absent — no silent skips)."""

    def test_engine_imports_on_this_interpreter(self):
        self.assertTrue(
            ENGINE_AVAILABLE,
            f"core/voice/vad.py did not import on {INTERPRETER} — "
            "the real-engine tests cannot be skipped silently")

    def test_engine_status_is_honest_energy_fallback(self):
        status = va.engine_status()
        self.assertTrue(status["ok"])
        self.assertTrue(status["available"])
        # No weights file exists in the lab tree on either
        # interpreter, so the honest backend is energy.
        self.assertFalse(status["silero_available"])
        self.assertIsNone(status["weights_path"])
        self.assertEqual(status["backend"], "energy")
        self.assertTrue(status["degraded"])

    def test_real_engine_silence_and_speech(self):
        det = va.SpeechDetector()  # real engine, no weights
        out = det.feed(_frames(30))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["backend"], "energy")
        self.assertEqual(out["state"], "silence")
        # Loud frames: constant 0.5 -> RMS 16384 (int16 scale),
        # far above the energy threshold of 400.
        out = det.feed(_frames(11, value=0.5))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["backend"], "energy")
        self.assertEqual(out["state"], "speaking")
        self.assertEqual(out["speech_frames"], 11)
        # Trailing silence ends it.
        out = det.feed(_frames(25))
        self.assertEqual(out["state"], "ended")
        self.assertTrue(out["ended"])
        self.assertEqual(out["ended_utterances"][0]["end_reason"],
                         "silence")

    def test_real_engine_single_loud_window_does_not_start(self):
        det = va.SpeechDetector()
        out = det.feed(_frames(1, value=0.5))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["state"], "silence")


if __name__ == "__main__":
    unittest.main()
