"""Tests for core/desktop/turn_taking.py (B-R1-2, SmartTurn).

Fake backend (scripted decisions) + synthetic transcripts and
PCM — no mic, no real audio, no real transcript of his voice.
Real-engine legs assert the honest heuristic fallback on both
interpreters (no weights on either).
"""

import unittest

import numpy as np

from core.desktop import turn_taking as tt


def _pcm(seconds=1.0):
    """Silence PCM: int16 zeros at 16 kHz (the model scores it;
    the fake backend ignores it)."""
    n = int(seconds * tt.SAMPLE_RATE)
    return (np.zeros(n, dtype=np.int16)).tobytes()


class FakeBackend:
    """Scripted decisions. decide() returns the next scripted
    dict (or the last one forever)."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = []
        self.reset_calls = 0

    @property
    def backend(self):
        return "smartturn-onnx"

    def decide(self, pcm, trailing_silence_s, transcript):
        self.calls.append({
            "pcm": pcm,
            "trailing_silence_s": trailing_silence_s,
            "transcript": transcript,
        })
        if len(self.decisions) > 1:
            return dict(self.decisions.pop(0))
        return dict(self.decisions[0])

    def reset(self):
        self.reset_calls += 1


def _endpoint(prob=0.9):
    return {"decision": "endpoint", "probability": prob,
            "backend": "smartturn-onnx",
            "reason": "SmartTurn ONNX endpoint probability >= 0.5",
            "features": {"endpoint_probability": prob}}


def _hold(prob=0.2):
    return {"decision": "hold", "probability": prob,
            "backend": "smartturn-onnx",
            "reason": "below threshold", "features": {}}


class FakeRaisingBackend:
    @property
    def backend(self):
        return "smartturn-onnx"

    def decide(self, pcm, trailing_silence_s, transcript):
        raise RuntimeError("ONNX run failed: boom")

    def reset(self):
        pass


class FakeFlakyBackend:
    """Succeeds once (hold), then raises — so the machine is
    provably in_turn when the error lands."""

    def __init__(self):
        self.calls = 0

    @property
    def backend(self):
        return "smartturn-onnx"

    def decide(self, pcm, trailing_silence_s, transcript):
        self.calls += 1
        if self.calls == 1:
            return dict(_hold(0.1))
        raise RuntimeError("ONNX run failed: boom")

    def reset(self):
        pass


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class TestCompleteTurn(unittest.TestCase):
    def test_finished_sentence_plus_vad_ended_reports_turn_complete(self):
        det = tt.TurnPredictor(backend=FakeBackend([_endpoint(0.9)]))
        out = det.predict(_pcm(), transcript="see you tomorrow.",
                          vad_state="ended", trailing_silence_s=1.2,
                          timestamp=1000.0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "turn_complete")
        self.assertTrue(out["completed"])
        self.assertEqual(out["probability"], 0.9)
        self.assertEqual(out["backend"], "smartturn-onnx")
        self.assertFalse(out["turn_open"])

    def test_no_vad_state_but_silence_above_floor_reports_turn_complete(self):
        # The AND rule's second arm: no VAD state reported, but
        # trailing silence >= 0.8 s is "audio stopped".
        det = tt.TurnPredictor(backend=FakeBackend([_endpoint(0.85)]))
        out = det.predict(_pcm(), transcript="see you tomorrow.",
                          vad_state=None, trailing_silence_s=1.0,
                          timestamp=1000.0)
        self.assertEqual(out["state"], "turn_complete")
        self.assertTrue(out["completed"])


class TestIncompleteTurn(unittest.TestCase):
    def test_mid_sentence_pause_does_not_complete(self):
        det = tt.TurnPredictor(backend=FakeBackend([_hold(0.2)]))
        out = det.predict(_pcm(), transcript="so I was thinking",
                          vad_state="ended", trailing_silence_s=1.2,
                          timestamp=1000.0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "listening")
        self.assertFalse(out["completed"])
        self.assertEqual(out["probability"], 0.2)
        # The turn STAYS OPEN: a VAD-ended utterance below
        # threshold is a pause in the same turn.
        self.assertTrue(out["turn_open"])

    def test_turn_spans_utterances_then_completes(self):
        # Utterance 1 ends below threshold (turn stays open);
        # the user speaks again; utterance 2 ends above threshold
        # (the turn completes, re-scored with fuller audio).
        det = tt.TurnPredictor(
            backend=FakeBackend([_hold(0.2), _endpoint(0.9)]))
        out1 = det.predict(_pcm(), transcript="so I was",
                           vad_state="ended", trailing_silence_s=1.2,
                           timestamp=1000.0)
        self.assertEqual(out1["state"], "listening")
        self.assertTrue(out1["turn_open"])
        out2 = det.predict(_pcm(), transcript="so I was thinking",
                           vad_state="speaking", trailing_silence_s=0.0,
                           timestamp=1001.0)
        self.assertEqual(out2["state"], "listening")
        self.assertTrue(out2["turn_open"])
        out3 = det.predict(_pcm(), transcript="so I was thinking.",
                           vad_state="ended", trailing_silence_s=1.2,
                           timestamp=1002.0)
        self.assertEqual(out3["state"], "turn_complete")
        self.assertTrue(out3["completed"])
        self.assertFalse(out3["turn_open"])


class TestAndRule(unittest.TestCase):
    def test_model_endpoint_while_speaking_is_suppressed(self):
        det = tt.TurnPredictor(backend=FakeBackend([_endpoint(0.95)]))
        out = det.predict(_pcm(), transcript="see you tomorrow.",
                          vad_state="speaking", trailing_silence_s=0.0,
                          timestamp=1000.0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "listening")
        self.assertFalse(out["completed"])
        self.assertTrue(out["turn_open"])
        self.assertEqual(out["probability"], 0.95)
        self.assertIn("still speaking", out["reason"])

    def test_model_endpoint_below_silence_floor_is_suppressed(self):
        # Silence alone (0.2 s) never ends a turn, even with a
        # model endpoint: the audio has not stopped.
        det = tt.TurnPredictor(backend=FakeBackend([_endpoint(0.9)]))
        out = det.predict(_pcm(), transcript="see you tomorrow.",
                          vad_state="silence", trailing_silence_s=0.2,
                          timestamp=1000.0)
        self.assertEqual(out["state"], "listening")
        self.assertFalse(out["completed"])
        self.assertIn("below", out["reason"])

    def test_silence_alone_never_ends_a_turn(self):
        # No model endpoint (hold), long silence, no VAD ended:
        # still listening. Silence alone is never enough.
        det = tt.TurnPredictor(backend=FakeBackend([_hold(0.1)]))
        out = det.predict(_pcm(), transcript="see you tomorrow.",
                          vad_state="silence", trailing_silence_s=5.0,
                          timestamp=1000.0)
        self.assertEqual(out["state"], "listening")
        self.assertFalse(out["completed"])


class TestMaxTurn(unittest.TestCase):
    def test_long_turn_flushes_at_max_turn_bound(self):
        clock = Clock(1000.0)
        det = tt.TurnPredictor(backend=FakeBackend([_hold(0.1)]),
                               now_fn=clock)
        out1 = det.predict(_pcm(), transcript="and then",
                           vad_state="speaking", timestamp=1000.0)
        self.assertTrue(out1["turn_open"])
        clock.now = 1000.0 + tt.MAX_TURN_S + 1.0
        out2 = det.predict(_pcm(), transcript="and then and then",
                           vad_state="speaking", timestamp=clock.now)
        self.assertEqual(out2["state"], "max_turn")
        self.assertTrue(out2["completed"])
        self.assertFalse(out2["turn_open"])
        self.assertGreaterEqual(out2["turn_s"], tt.MAX_TURN_S)

    def test_max_turn_fires_even_while_speaking(self):
        # The flush is checked before scoring: a rambling user
        # mid-speech still gets flushed at the bound.
        clock = Clock(1000.0)
        det = tt.TurnPredictor(backend=FakeBackend([_endpoint(0.99)]),
                               now_fn=clock)
        det.predict(_pcm(), transcript="and", vad_state="speaking",
                    timestamp=1000.0)
        clock.now = 1000.0 + tt.MAX_TURN_S
        out = det.predict(_pcm(), transcript="and and and",
                          vad_state="speaking", timestamp=clock.now)
        self.assertEqual(out["state"], "max_turn")
        self.assertTrue(out["completed"])


class TestErrorsAndValidation(unittest.TestCase):
    def test_engine_error_returns_honest_failure_and_resets(self):
        det = tt.TurnPredictor(backend=FakeFlakyBackend())
        first = det.predict(_pcm(), transcript="hello",
                            vad_state="speaking", timestamp=1000.0)
        self.assertTrue(first["ok"])
        self.assertEqual(det.state, "in_turn")
        out = det.predict(_pcm(), transcript="hello again",
                          vad_state="speaking", timestamp=1001.0)
        self.assertFalse(out["ok"])
        self.assertEqual(out["state"], "listening")
        self.assertFalse(out["completed"])
        self.assertTrue(out.get("reset_on_error"))
        # The machine was reset: a stale "complete" is impossible.
        self.assertEqual(det.state, "listening")

    def test_non_text_transcript_is_refused_and_machine_untouched(self):
        det = tt.TurnPredictor(backend=FakeBackend([_endpoint(0.9)]))
        out = det.predict(_pcm(), transcript=123, vad_state="ended",
                          trailing_silence_s=1.2, timestamp=1000.0)
        self.assertFalse(out["ok"])
        self.assertIn("string", out["reason"])
        self.assertEqual(det.state, "listening")

    def test_bad_vad_state_is_refused(self):
        det = tt.TurnPredictor(backend=FakeBackend([_endpoint(0.9)]))
        out = det.predict(_pcm(), transcript="hi", vad_state="talking",
                          timestamp=1000.0)
        self.assertFalse(out["ok"])
        self.assertIn("vad_state", out["reason"])

    def test_non_16khz_is_refused(self):
        det = tt.TurnPredictor(backend=FakeBackend([_endpoint(0.9)]))
        out = det.predict(_pcm(), transcript="hi", vad_state="ended",
                          trailing_silence_s=1.2, sample_rate=8000,
                          timestamp=1000.0)
        self.assertFalse(out["ok"])
        self.assertIn("16000", out["reason"])

    def test_odd_length_pcm_is_refused(self):
        det = tt.TurnPredictor(backend=FakeBackend([_endpoint(0.9)]))
        out = det.predict(b"\x00\x00\x00", transcript="hi",
                          vad_state="ended", trailing_silence_s=1.2,
                          timestamp=1000.0)
        self.assertFalse(out["ok"])
        self.assertIn("whole int16", out["reason"])

    def test_negative_trailing_silence_is_refused(self):
        det = tt.TurnPredictor(backend=FakeBackend([_endpoint(0.9)]))
        out = det.predict(_pcm(), transcript="hi", vad_state="ended",
                          trailing_silence_s=-1.0, timestamp=1000.0)
        self.assertFalse(out["ok"])
        self.assertIn(">= 0", out["reason"])

    def test_unknown_decision_is_a_hold_not_a_complete(self):
        det = tt.TurnPredictor(backend=FakeBackend([{
            "decision": "unknown", "probability": None,
            "backend": "smartturn-onnx",
            "reason": "ONNX run failed: boom", "features": {}}]))
        out = det.predict(_pcm(), transcript="see you tomorrow.",
                          vad_state="ended", trailing_silence_s=1.2,
                          timestamp=1000.0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "listening")
        self.assertFalse(out["completed"])
        self.assertIsNone(out["probability"])

    def test_reset_abandons_the_open_turn(self):
        det = tt.TurnPredictor(backend=FakeBackend([_hold(0.1)]))
        det.predict(_pcm(), transcript="and", vad_state="speaking",
                    timestamp=1000.0)
        self.assertEqual(det.state, "in_turn")
        out = det.reset()
        self.assertTrue(out["ok"])
        self.assertEqual(det.state, "listening")
        self.assertFalse(out["turn_open"])


class TestProbabilityReported(unittest.TestCase):
    def test_heuristic_path_reports_probability_none_not_a_number(self):
        # A rule has no probability: the wrapper must report None,
        # labelled heuristic, never invent one.
        det = tt.TurnPredictor(backend=FakeBackend([{
            "decision": "endpoint", "probability": None,
            "backend": "heuristic-end-of-turn",
            "reason": "silence >= 1.5 s, no hold cue",
            "features": {}}]))
        out = det.predict(None, transcript="see you tomorrow.",
                          vad_state="ended", trailing_silence_s=1.5,
                          timestamp=1000.0)
        self.assertEqual(out["state"], "turn_complete")
        self.assertIsNone(out["probability"])
        self.assertEqual(out["backend"], "heuristic-end-of-turn")

    def test_no_audio_routes_to_heuristic_without_killing_the_model(self):
        # pcm=None must not reach the facade (it would hand None
        # to SmartTurnDetector.predict and mark the session dead).
        # The fake records what it was given; the real backend
        # routes None to the heuristic directly.
        backend = FakeBackend([{
            "decision": "hold", "probability": None,
            "backend": "heuristic-end-of-turn",
            "reason": "silence below 0.8 s floor", "features": {}}])
        det = tt.TurnPredictor(backend=backend)
        out = det.predict(None, transcript="um", vad_state="silence",
                          trailing_silence_s=0.2, timestamp=1000.0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "listening")
        self.assertIsNone(backend.calls[0]["pcm"])


class TestRealEngine(unittest.TestCase):
    """Honest heuristic fallback on both interpreters: no weights
    on either, so the neural path is unavailable and every
    decision is labelled heuristic-end-of-turn."""

    def test_engine_status_is_honest(self):
        st = tt.engine_status()
        self.assertTrue(st["ok"])
        self.assertTrue(st["available"])
        self.assertFalse(st["smartturn_available"])
        self.assertIsNone(st["weights_path"])
        self.assertEqual(st["backend"], "heuristic-end-of-turn")
        self.assertTrue(st["degraded"])
        self.assertIn("BSD-2-Clause", st["licence"])

    def test_sentence_final_plus_long_silence_completes_via_heuristic(self):
        det = tt.TurnPredictor()
        out = det.predict(_pcm(0.5), transcript="see you tomorrow.",
                          vad_state="ended", trailing_silence_s=1.5,
                          timestamp=1000.0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "turn_complete")
        self.assertEqual(out["backend"], "heuristic-end-of-turn")
        self.assertIsNone(out["probability"])

    def test_trailing_filler_holds_via_heuristic(self):
        det = tt.TurnPredictor()
        out = det.predict(_pcm(0.5), transcript="so um",
                          vad_state="ended", trailing_silence_s=2.0,
                          timestamp=1000.0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "listening")
        self.assertFalse(out["completed"])
        self.assertEqual(out["backend"], "heuristic-end-of-turn")

    def test_module_level_predict_and_reset(self):
        tt.reset_backend()
        out = tt.predict(_pcm(0.5), transcript="see you tomorrow.",
                         vad_state="ended", trailing_silence_s=1.5,
                         timestamp=1000.0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "turn_complete")
        out2 = tt.reset()
        self.assertTrue(out2["ok"])
        tt.reset_backend()


if __name__ == "__main__":
    unittest.main()
