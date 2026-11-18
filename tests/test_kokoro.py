"""B-R1-3 tests: the Kokoro driver wrapper (core/desktop/kokoro_voice).

Fake backend + synthetic renders — no real mic, no real audio, no
speakers. Real-engine legs assert honest engine_unavailable on
both lab interpreters (kokoro-onnx is absent from system python3
and from .venv-qt; it lives only in .venv-kokoro).
"""

import unittest

import numpy as np

from core.desktop import kokoro_voice as kv


VOICES = ("am_michael", "bm_george", "hm_omega", "jf_alpha",
          "zm_yunjian", "em_alex", "ff_siwis", "im_nicola", "pm_alex")


def _tone(seconds=0.5, amp=0.5, rate=24000):
    n = int(seconds * rate)
    return np.full(n, amp, dtype=np.float32)


class FakeBackend:
    """Scripted Kokoro. speak() returns the next scripted
    (samples, rate) or the last one forever."""

    def __init__(self, renders, voices=VOICES):
        self.renders = list(renders)
        self.voices_list = tuple(voices)
        self.calls = []
        self.reset_calls = 0

    @property
    def backend(self):
        return "kokoro-onnx"

    def voices(self):
        return self.voices_list

    def speak(self, text, voice, speed, lang):
        self.calls.append((text, voice, speed, lang))
        samples, rate = self.renders[-1]
        return samples, rate

    def reset(self):
        self.reset_calls += 1


class FailingBackend(FakeBackend):
    def speak(self, text, voice, speed, lang):
        raise RuntimeError("ONNX session died")


class TestSpeakRenders(unittest.TestCase):
    def test_known_voice_renders(self):
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        out = det.speak("Hello there", voice="bm_george", language="en-gb")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["voice"], "bm_george")
        self.assertEqual(out["language"], "en-gb")
        self.assertEqual(out["sample_rate"], 24000)
        self.assertAlmostEqual(out["duration_s"], 0.5)
        self.assertEqual(out["samples"], 12000)
        self.assertIsInstance(out["audio"], bytes)
        self.assertEqual(len(out["audio"]), 12000 * 2)
        self.assertTrue(out["verification"]["ok"])
        self.assertAlmostEqual(out["verification"]["peak"], 0.5)
        # The fallback is named in every result, never hidden.
        self.assertEqual(out["fallback"]["engine"], "piper")
        self.assertIn("GPL-3.0", out["fallback"]["licence"])

    def test_int16_scaling(self):
        # 0.5 float must reach playback as int16 ~16383, not 0.5.
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        out = det.speak("Hello", voice="bm_george")
        pcm = np.frombuffer(out["audio"], dtype=np.int16)
        self.assertEqual(int(pcm.max()), 16383)
        self.assertEqual(int(pcm.min()), 16383)

    def test_language_defaults_from_voice(self):
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        out = det.speak("नमस्ते", voice="hm_omega")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["language"], "hi")

    def test_explicit_language_picks_curated_voice(self):
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        out = det.speak("नमस्ते, कैसे हो?", language="hi")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["voice"], "hm_omega")
        self.assertEqual(out["language"], "hi")

    def test_auto_switch_picks_hindi_voice_for_hindi_text(self):
        # Home is bm_george; Hindi text switches to hm_omega.
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        out = det.speak("नमस्ते, कैसे हो?")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["voice"], "hm_omega")
        self.assertEqual(out["language"], "hi")

    def test_auto_switch_off_keeps_home_voice(self):
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        out = det.speak("नमस्ते, कैसे हो?", auto_switch=False)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["voice"], "bm_george")

    def test_english_voices_are_one_language(self):
        # en-us voice + en-gb language is not a mismatch.
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        out = det.speak("Hello", voice="am_michael", language="en-gb")
        self.assertTrue(out["ok"], out)


class TestMissingVoiceAndMismatch(unittest.TestCase):
    def test_missing_voice_is_honest_failure(self):
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        out = det.speak("Hello", voice="af_heart")
        self.assertFalse(out["ok"])
        self.assertIn("af_heart", out["reason"])
        self.assertIn("not in the voices bin", out["reason"])
        self.assertIsNone(out["audio"])
        # No silent fallback: the failure names the fallback instead.
        self.assertEqual(out["fallback"]["engine"], "piper")
        self.assertEqual(out["fallback"]["voice"], "en_US-amy-medium.onnx")
        # The backend was never asked to render the missing voice.
        self.assertEqual(det._backend.calls, [])

    def test_voice_language_mismatch_is_refused(self):
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        out = det.speak("नमस्ते", voice="bm_george", language="hi")
        self.assertFalse(out["ok"])
        self.assertIn("mismatch", out["reason"])
        self.assertIsNone(out["audio"])
        self.assertEqual(det._backend.calls, [])

    def test_unknown_language_is_refused(self):
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        out = det.speak("Hello", language="xx")
        self.assertFalse(out["ok"])
        self.assertIn("unknown language", out["reason"])


class TestRenderVerification(unittest.TestCase):
    def test_silent_render_is_refused(self):
        silent = np.zeros(12000, dtype=np.float32)
        det = kv.KokoroSpeaker(backend=FakeBackend([(silent, 24000)]))
        out = det.speak("Hello", voice="bm_george")
        self.assertFalse(out["ok"])
        self.assertIn("silent", out["reason"])
        self.assertIsNone(out["audio"])

    def test_empty_render_is_refused(self):
        empty = np.zeros(0, dtype=np.float32)
        det = kv.KokoroSpeaker(backend=FakeBackend([(empty, 24000)]))
        out = det.speak("Hello", voice="bm_george")
        self.assertFalse(out["ok"])
        self.assertIn("empty", out["reason"])
        self.assertIsNone(out["audio"])

    def test_non_finite_render_is_refused(self):
        bad = _tone()
        bad[0] = float("nan")
        det = kv.KokoroSpeaker(backend=FakeBackend([(bad, 24000)]))
        out = det.speak("Hello", voice="bm_george")
        self.assertFalse(out["ok"])
        self.assertIn("non-finite", out["reason"])
        self.assertIsNone(out["audio"])

    def test_wrong_rate_is_refused_never_resampled(self):
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(rate=16000), 16000)]))
        out = det.speak("Hello", voice="bm_george")
        self.assertFalse(out["ok"])
        self.assertIn("16000", out["reason"])
        self.assertIn("24000", out["reason"])
        self.assertIsNone(out["audio"])

    def test_pinned_at_the_rail_is_refused(self):
        pinned = np.full(12000, 0.99999, dtype=np.float32)
        det = kv.KokoroSpeaker(backend=FakeBackend([(pinned, 24000)]))
        out = det.speak("Hello", voice="bm_george")
        self.assertFalse(out["ok"])
        self.assertIn("rail", out["reason"])
        self.assertIsNone(out["audio"])

    def test_engine_error_is_honest_failure(self):
        det = kv.KokoroSpeaker(backend=FailingBackend([(_tone(), 24000)]))
        out = det.speak("Hello", voice="bm_george")
        self.assertFalse(out["ok"])
        self.assertIn("failed while rendering", out["reason"])
        self.assertIsNone(out["audio"])
        self.assertEqual(out["fallback"]["engine"], "piper")


class TestValidation(unittest.TestCase):
    def test_non_text_is_refused(self):
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        for bad in (None, 42, 3.5, ["hello"], {"text": "hello"}):
            out = det.speak(bad, voice="bm_george")
            self.assertFalse(out["ok"], bad)
            self.assertIn("text must be a string", out["reason"])

    def test_empty_text_is_refused(self):
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        for bad in ("", "   ", "\n\t"):
            out = det.speak(bad, voice="bm_george")
            self.assertFalse(out["ok"], repr(bad))

    def test_text_too_long_is_refused(self):
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        out = det.speak("x" * (kv.MAX_TEXT_CHARS + 1), voice="bm_george")
        self.assertFalse(out["ok"])
        self.assertIn("chunk it first", out["reason"])

    def test_bad_speed_is_refused(self):
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        for bad in (0, -1.0, 4.5, float("nan"), "fast", True):
            out = det.speak("Hello", voice="bm_george", speed=bad)
            self.assertFalse(out["ok"], bad)

    def test_bad_voice_and_language_types_refused(self):
        det = kv.KokoroSpeaker(backend=FakeBackend([(_tone(), 24000)]))
        out = det.speak("Hello", voice=42)
        self.assertFalse(out["ok"])
        out = det.speak("Hello", language=42)
        self.assertFalse(out["ok"])

    def test_reset_clears_backend(self):
        backend = FakeBackend([(_tone(), 24000)])
        det = kv.KokoroSpeaker(backend=backend)
        det.speak("Hello", voice="bm_george")
        out = det.reset()
        self.assertTrue(out["ok"])
        self.assertEqual(backend.reset_calls, 1)
        self.assertIsNone(det._backend)


class TestRealEngineLegs(unittest.TestCase):
    def test_engine_unavailable_without_kokoro_onnx(self):
        # kokoro-onnx is absent from system python3 and .venv-qt
        # (it lives only in .venv-kokoro): the real path degrades
        # honestly instead of faking a render. In .venv-kokoro the
        # package imports but the assets are not on disk, so the
        # honest failure is the missing voices bin instead.
        if kv.engine_available():
            out = kv.KokoroSpeaker().speak("Hello", voice="bm_george")
            self.assertFalse(out["ok"])
            self.assertIn("voices bin", out["reason"])
            self.assertIsNone(out["audio"])
            status = kv.engine_status()
            self.assertTrue(status["available"])
            self.assertTrue(status["degraded"])
            return
        out = kv.KokoroSpeaker().speak("Hello", voice="bm_george")
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "engine_unavailable")
        self.assertIsNone(out["audio"])
        self.assertFalse(kv.engine_available())
        status = kv.engine_status()
        self.assertFalse(status["available"])
        self.assertTrue(status["degraded"])

    def test_fallback_info_names_piper_with_licence_flag(self):
        info = kv.fallback_info()
        self.assertEqual(info["engine"], "piper")
        self.assertEqual(info["voice"], "en_US-amy-medium.onnx")
        self.assertIn("GPL-3.0", info["licence"])
        # On this lab machine the Amy file is not on disk.
        self.assertFalse(info["available"])

    def test_voice_status_without_engine(self):
        status = kv.voice_status()
        self.assertFalse(status["available"])
        self.assertIsNone(status["voices_present"])


if __name__ == "__main__":
    unittest.main()
