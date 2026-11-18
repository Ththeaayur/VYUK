"""B-R1-3s (2026-11-09): the session TTS path routes Kokoro
synthesis through the isolated child process — no in-process
kokoro-onnx anywhere in the product.

The session path is the LIVE voice path (the B-R1-3 wrapper is
the driver surface; LocalLiveSession._kokoro_synthesize is what
actually speaks). Before this batch it built a Kokoro ONNX
session in-process (from kokoro_onnx import Kokoro) and
presence-checked it with a bare `import kokoro_onnx` — so every
utterance ran GPL-3.0 code (phonemizer + espeak-ng, through
kokoro-onnx) in-process, even after B-R1-3r isolated the wrapper.

These tests drive a REAL LocalLiveSession with a FAKE child
(a plain object with synthesize()/close() — no subprocess, no
real mic, no real audio, no Kokoro assets on disk). The real
Windows leg — a real Kokoro child process on his machine,
rendering real speech through the session path, judged by his
ear — is a field leg, owed, not this batch.

Covered:
* a session synthesis through the boundary returns PCM, with
  the right voice/lang/speed in the request, and the main
  process never imports the GPL engine;
* a child failure is an honest failure (None / False), never a
  silent Piper swap — the rung record says what spoke;
* a non-text input is refused before the child is ever called;
* a configured blend drops to the named base voice honestly
  (the child protocol carries a voice NAME, not a tensor);
* the fallback chain records the winning rung;
* the session's Kokoro child dies with the session (stop());
* the engine-absent probe gate refuses before the child.
"""
import asyncio
import contextlib
import io
import sys
import unittest
from unittest import mock

import numpy as np

from core.voice.local_client import LocalLiveSession
from core.desktop import kokoro_isolate as _isolate
from core.voice import local_assets
from core.voice import local_client_voices as voices_mod


def _tone(seconds=0.5, freq=440.0, rate=24000, amp=0.5):
    """A float32 sine tone — the fake child's render."""
    n = int(seconds * rate)
    t = np.arange(n, dtype=np.float64) / rate
    return (np.sin(2.0 * np.pi * freq * t) * amp).astype(np.float32)


class FakeKokoroChild:
    """A stand-in for KokoroChild: synthesize()/close(), no
    subprocess. Records every request; returns a tone, or a
    configured failure."""

    def __init__(self, samples=None, ok=True, reason="fake failure"):
        self._samples = _tone() if samples is None else samples
        self._ok = ok
        self._reason = reason
        self.calls = []
        self.closed = 0

    def synthesize(self, request):
        self.calls.append(dict(request))
        if not self._ok:
            return {"ok": False, "reason": self._reason}
        return {"ok": True, "rate": 24000,
                "samples": self._samples}

    def close(self):
        self.closed += 1


class TestKokoroSessionRewire(unittest.TestCase):
    def _session_with_fake(self, child, *, engine_available=True):
        """Patch the session path's two gates (assets present,
        engine probe available) and inject the fake child into a
        fresh real session. Returns (session, exit-stack)."""
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(
            local_assets, "kokoro_ready", lambda: True))
        stack.enter_context(mock.patch.object(
            _isolate, "probe_engine",
            lambda *a, **k: {"available": engine_available,
                             "reason": "fake probe",
                             "python": sys.executable}))
        session = LocalLiveSession()
        session._kokoro_child = child
        return session, stack

    def test_session_synthesis_returns_pcm_through_child(self):
        child = FakeKokoroChild()
        session, stack = self._session_with_fake(child)
        with stack:
            pcm = session._kokoro_synthesize("hello")
        self.assertIsInstance(pcm, bytes)
        # A 0.5 s tone at 24 kHz is 12,000 int16 samples; the
        # trim keeps the body (the tone has no silent edges).
        samples = np.frombuffer(pcm, dtype=np.int16)
        self.assertGreater(samples.size, 6000)
        self.assertLessEqual(samples.size, 12000)
        self.assertGreater(np.max(np.abs(samples)), 1000)
        # The child got exactly one request, with the resolved
        # voice name (a string), the language for that voice,
        # and the composed speed (0.95 config default).
        self.assertEqual(len(child.calls), 1)
        req = child.calls[0]
        self.assertEqual(req["voice"], "bm_george")
        self.assertIsInstance(req["voice"], str)
        self.assertEqual(req["lang"], "en-gb")
        self.assertAlmostEqual(req["speed"], 0.95)
        self.assertEqual(req["text"], "hello")
        # The main process never imported the GPL engine.
        self.assertNotIn("kokoro_onnx", sys.modules)
        self.assertNotIn("phonemizer", sys.modules)

    def test_child_failure_is_honest_failure(self):
        child = FakeKokoroChild(ok=False, reason="child crashed")
        session, stack = self._session_with_fake(child)
        with stack:
            self.assertIsNone(session._kokoro_synthesize("hello"))
            self.assertFalse(asyncio.run(session._speak_kokoro("hello")))
        # The failure is honest: no rung is recorded as having
        # spoken — the chain would try Piper next, and the rung
        # record would say "piper", never a silent swap.
        self.assertIsNone(session._last_tts_rung)
        self.assertNotIn("kokoro_onnx", sys.modules)

    def test_non_text_refused_without_child(self):
        child = FakeKokoroChild()
        session, stack = self._session_with_fake(child)
        with stack:
            self.assertIsNone(session._kokoro_synthesize(123))
            self.assertIsNone(session._kokoro_synthesize(""))
            self.assertIsNone(session._kokoro_synthesize("   "))
            self.assertIsNone(session._kokoro_synthesize(None))
        # The child was never called for a refused input.
        self.assertEqual(child.calls, [])
        self.assertNotIn("kokoro_onnx", sys.modules)

    def test_configured_blend_drops_to_named_voice_honestly(self):
        child = FakeKokoroChild()
        session, stack = self._session_with_fake(child)
        stack.enter_context(mock.patch.object(
            voices_mod, "_configured_blend",
            lambda: ("am_michael", 0.5)))
        buf = io.StringIO()
        with stack, contextlib.redirect_stdout(buf):
            pcm = session._kokoro_synthesize("hello")
        self.assertIsInstance(pcm, bytes)
        # The child received the base voice NAME (a string) —
        # a blended tensor cannot cross the frozen protocol.
        self.assertEqual(len(child.calls), 1)
        self.assertEqual(child.calls[0]["voice"], "bm_george")
        self.assertIsInstance(child.calls[0]["voice"], str)
        # The dropped blend is recorded honestly, once.
        self.assertIn("blend", buf.getvalue().lower())
        self.assertTrue(session._kokoro_blend_warned)
        # A second synthesis does not print the note again.
        buf2 = io.StringIO()
        with stack, contextlib.redirect_stdout(buf2):
            session._kokoro_synthesize("hello again")
        self.assertNotIn("blend", buf2.getvalue().lower())

    def test_chain_records_winning_rung(self):
        session = LocalLiveSession()
        order = []

        async def _tracked_fail_kokoro(text):
            order.append("kokoro")
            return False

        async def _tracked_ok_piper(text):
            order.append("piper")
            return True

        async def _tracked_fail_sapi(text):
            order.append("sapi")
            return False

        # The stubs' __name__ is what the rung record reads —
        # give them the real provider names.
        _tracked_fail_kokoro.__name__ = "_speak_kokoro"
        _tracked_ok_piper.__name__ = "_speak_piper"
        _tracked_fail_sapi.__name__ = "_speak_sapi"
        rung = asyncio.run(session._speak_via_chain(
            "hi", [_tracked_fail_kokoro, _tracked_ok_piper]))
        self.assertEqual(rung, "piper")
        self.assertEqual(session._last_tts_rung, "piper")
        self.assertEqual(order, ["kokoro", "piper"])
        # All failing: no rung, no silent swap.
        rung = asyncio.run(session._speak_via_chain(
            "hi", [_tracked_fail_kokoro, _tracked_fail_sapi]))
        self.assertIsNone(rung)
        self.assertIsNone(session._last_tts_rung)

    def test_speak_records_kokoro_rung_end_to_end(self):
        child = FakeKokoroChild(samples=_tone(seconds=0.05))
        session, stack = self._session_with_fake(child)
        with stack:
            asyncio.run(session._speak("hello"))
        self.assertEqual(session._last_tts_rung, "kokoro")
        self.assertEqual(len(child.calls), 1)
        self.assertFalse(session._speaking)
        # All rungs failing: no rung is recorded, and the
        # "no local voice" note fires exactly once.
        child_fail = FakeKokoroChild(ok=False)
        session2, stack2 = self._session_with_fake(child_fail)
        buf = io.StringIO()
        with stack2, contextlib.redirect_stdout(buf):
            asyncio.run(session2._speak("hello"))
        self.assertIsNone(session2._last_tts_rung)
        self.assertIn("No local voice available", buf.getvalue())

    def test_speak_chunked_records_kokoro_rung(self):
        child = FakeKokoroChild(samples=_tone(seconds=0.05))
        session, stack = self._session_with_fake(child)
        session._running = True
        session._speaking = True
        with stack:
            ok = asyncio.run(session._speak_chunked(
                [("hello", 1.0, 0, False)],
                [("hello", "hello")]))
        self.assertTrue(ok)
        self.assertEqual(session._last_tts_rung, "kokoro")
        self.assertEqual(len(child.calls), 1)

    def test_child_dies_with_session_stop(self):
        child = FakeKokoroChild()
        session = LocalLiveSession()
        session._kokoro_child = child
        session.stop()
        self.assertEqual(child.closed, 1)
        self.assertIsNone(session._kokoro_child)
        # stop() is idempotent on the child — a second stop does
        # not close it again.
        session.stop()
        self.assertEqual(child.closed, 1)

    def test_probe_gate_refuses_before_child(self):
        child = FakeKokoroChild()
        session, stack = self._session_with_fake(
            child, engine_available=False)
        with stack:
            self.assertIsNone(
                asyncio.run(session._synthesize_kokoro("hello")))
            self.assertFalse(asyncio.run(session._speak_kokoro("hello")))
        # The child was never created or called — the probe gate
        # refused first.
        self.assertEqual(child.calls, [])
        self.assertNotIn("kokoro_onnx", sys.modules)

    def test_session_never_imports_gpl_engine(self):
        child = FakeKokoroChild(samples=_tone(seconds=0.05))
        session, stack = self._session_with_fake(child)
        with stack:
            session._kokoro_synthesize("hello")
            asyncio.run(session._speak_kokoro("hello"))
            asyncio.run(session._speak("hello again"))
            session.stop()
        self.assertNotIn("kokoro_onnx", sys.modules)
        self.assertNotIn("phonemizer", sys.modules)
        self.assertNotIn("espeakng_loader", sys.modules)


if __name__ == "__main__":
    unittest.main()
