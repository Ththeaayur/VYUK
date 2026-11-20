"""Round-3 section C fix queue item 9 (C1-F1 + C1-F2) — the receive
side of the live loop.

C1-F1: loud background noise ended the session — the noise stamped
_last_voice_at (RMS stamping cannot tell speech from noise) and the
server correctly sent nothing, so the watchdog's first rule tripped
~12 s later. The fix classifies each voice-level episode by its RMS
variation and rolls the watchdog stamps back when the episode was
stationary loudness, not speech. Measured on the section-C clips:
white noise CV 0.018, every speech clip CV 0.35-0.75.

C1-F2: the assistant bubble flushed ~0.08 s before its own user
bubble (input transcription finalises only at turn end; the receive
loop handled output before input). The fix orders the transcript by
sequence, not flush time: the reply flush and its live chunks wait
for the question, and the receive loop handles input before output.

No network in this suite: the loop tests drive the real send +
watchdog loops on a fake session (the test_micfix B16/B47 Blob
pattern); the live proof is repro_c1f1f2.py.
"""

import asyncio
import math
import queue
import random
import struct
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from core.voice import live_client as _lc
from core.voice import live_client_helpers as _lh
from core.voice import live_client_receive as _lrecv

_SR = 16000
_CHUNK = 3200  # 100 ms of 16 kHz int16 PCM, the driver's chunk


# ---------------------------------------------------------------------------
# PCM synthesis (stdlib only — the Live path must not drag numpy in)
# ---------------------------------------------------------------------------

def _noise_pcm(seconds=5.0, rms=0.05, seed=1234):
    """White noise at a steady RMS — the C1-8a shape."""
    rng = random.Random(seed)
    n = int(seconds * _SR)
    raw = [rng.gauss(0.0, 1.0) for _ in range(n)]
    mean_sq = sum(x * x for x in raw) / n
    scale = rms / math.sqrt(mean_sq)
    return struct.pack(f"<{n}h", *[
        max(-32767, min(32767, int(round(x * scale * 32767))))
        for x in raw])


def _speech_pcm(seconds=3.0):
    """Bursty speech-like PCM: 0.3 s voiced bursts (300 Hz, raised
    cosine envelope) separated by 0.2 s gaps — RMS varies by an
    order of magnitude across the utterance, the way real speech
    does (measured CV ~0.6 on the section-C clips)."""
    out = bytearray()
    t = 0.0
    while t < seconds:
        burst = min(0.3, seconds - t)
        n = int(burst * _SR)
        for i in range(n):
            frac = i / max(1, n)
            env = 0.5 * (1.0 - math.cos(2.0 * math.pi * frac))
            val = 0.11 * env * math.sin(
                2.0 * math.pi * 300.0 * (i / _SR))
            out += struct.pack(
                "<h", max(-32767, min(32767, int(round(val * 32767)))))
        t += burst
        gap = min(0.2, seconds - t)
        if gap <= 0:
            break
        out += b"\x00" * int(gap * _SR) * 2
        t += gap
    return bytes(out)


def _rms_series(pcm_bytes):
    return [_lh._chunk_rms(pcm_bytes[i:i + _CHUNK])
            for i in range(0, len(pcm_bytes), _CHUNK)]


# ---------------------------------------------------------------------------
# Canned server messages
# ---------------------------------------------------------------------------

def _sc_out(text=None, finished=False, turn_complete=False):
    return SimpleNamespace(
        model_turn=None,
        output_transcription=SimpleNamespace(text=text, finished=finished)
        if text is not None else None,
        input_transcription=None,
        turn_complete=turn_complete,
        interrupted=False)


def _sc_in(text=None, finished=False, turn_complete=False):
    return SimpleNamespace(
        model_turn=None,
        output_transcription=None,
        input_transcription=SimpleNamespace(text=text, finished=finished)
        if text is not None else None,
        turn_complete=turn_complete,
        interrupted=False)


def _sc_both(out_text, out_finished, in_text, in_finished,
             turn_complete=False):
    return SimpleNamespace(
        model_turn=None,
        output_transcription=SimpleNamespace(text=out_text,
                                             finished=out_finished),
        input_transcription=SimpleNamespace(text=in_text,
                                            finished=in_finished),
        turn_complete=turn_complete,
        interrupted=False)


# ---------------------------------------------------------------------------
# The stationarity classifier (pure, fast)
# ---------------------------------------------------------------------------

class StationaryClassifierTests(unittest.TestCase):
    def test_white_noise_is_stationary(self):
        rms = _rms_series(_noise_pcm(5.0))
        self.assertTrue(_lh._voice_episode_is_stationary(rms, 5.0))

    def test_speech_like_is_not_stationary(self):
        rms = _rms_series(_speech_pcm(3.0))
        self.assertFalse(_lh._voice_episode_is_stationary(rms, 3.0))

    def test_vibrato_tone_reads_stationary(self):
        # Documented residual (see the constants block): a
        # sustained near-pure tone with shallow vibrato has too
        # little RMS variation to separate from noise. The
        # session survives it, which is the safe direction.
        n = int(5.0 * _SR)
        vals = []
        for i in range(n):
            t = i / _SR
            vals.append(0.05 * math.sqrt(2.0)
                        * (1.0 + 0.2 * math.sin(2.0 * math.pi * 5.0 * t))
                        * math.sin(2.0 * math.pi * 220.0 * t))
        pcm = struct.pack(f"<{n}h", *[
            max(-32767, min(32767, int(round(v * 32767)))) for v in vals])
        rms = _rms_series(pcm)
        self.assertTrue(_lh._voice_episode_is_stationary(rms, 5.0))

    def test_short_episode_never_classified(self):
        rms = _rms_series(_noise_pcm(0.3))
        self.assertFalse(_lh._voice_episode_is_stationary(rms, 0.3))

    def test_too_few_samples_never_classified(self):
        self.assertFalse(
            _lh._voice_episode_is_stationary([0.05, 0.05, 0.05], 5.0))

    def test_empty_and_silent_never_classified(self):
        self.assertFalse(_lh._voice_episode_is_stationary([], 5.0))
        self.assertFalse(
            _lh._voice_episode_is_stationary([0.0] * 50, 5.0))

    def test_speech_under_noise_is_not_stationary(self):
        # The C1-8b shape: speech on top of the same loud noise
        # floor — the variation survives, so the turn still arms
        # the watchdog.
        rng = random.Random(7)
        n = int(2.0 * _SR)
        vals = []
        for i in range(n):
            t = i / _SR
            burst = 0.5 * (1.0 - math.cos(
                2.0 * math.pi * ((t % 0.5) / 0.5)))
            # Speech clearly above the noise floor (the real
            # C1-8b clip measures CV 0.348): the burst lifts
            # the chunk RMS from ~0.03 to ~0.09.
            vals.append(rng.gauss(0.0, 0.03)
                        + 0.12 * burst * math.sin(
                            2.0 * math.pi * 300.0 * t))
        pcm = struct.pack(f"<{n}h", *[
            max(-32767, min(32767, int(round(v * 32767)))) for v in vals])
        rms = _rms_series(pcm)
        self.assertFalse(_lh._voice_episode_is_stationary(rms, 2.0))


# ---------------------------------------------------------------------------
# Episode close (fake times — deterministic, no loops)
# ---------------------------------------------------------------------------

class VoiceEpisodeCloseTests(unittest.TestCase):
    def _session(self):
        sess = _lc.GeminiLiveSession()
        sess._last_voice_at = None
        sess._last_audible_at = None
        return sess

    def _feed(self, sess, rms_values, t0=100.0, step=0.1):
        for i, rms in enumerate(rms_values):
            sess._voice_episode_note_chunk(rms, t0 + i * step)

    def test_stationary_episode_rolls_stamps_back(self):
        sess = self._session()
        self._feed(sess, [0.05] * 50)
        sess._last_voice_at = 104.9
        sess._last_audible_at = 104.9
        sess._voice_episode_close_if_ended(105.5)
        self.assertIsNone(sess._last_voice_at)
        self.assertIsNone(sess._last_audible_at)

    def test_speech_episode_keeps_stamps(self):
        sess = self._session()
        self._feed(sess, [0.12, 0.11, 0.02, 0.001, 0.13, 0.09,
                          0.01, 0.12, 0.10, 0.03] * 5)
        sess._last_voice_at = 104.9
        sess._last_audible_at = 104.9
        sess._voice_episode_close_if_ended(105.5)
        self.assertEqual(sess._last_voice_at, 104.9)
        self.assertEqual(sess._last_audible_at, 104.9)

    def test_episode_does_not_close_inside_the_gap(self):
        sess = self._session()
        self._feed(sess, [0.05] * 50)
        sess._last_voice_at = 104.9
        # 0.4 s after the last chunk: still inside the 0.5 s gap.
        sess._voice_episode_close_if_ended(105.3)
        self.assertEqual(sess._last_voice_at, 104.9)
        self.assertIsNotNone(sess._voice_episode_started_at)
        # 0.6 s after: the episode closes and rolls back.
        sess._voice_episode_close_if_ended(105.5)
        self.assertIsNone(sess._last_voice_at)

    def test_short_episode_keeps_stamps(self):
        sess = self._session()
        self._feed(sess, [0.05] * 3, step=0.1)  # 0.2 s of noise
        sess._last_voice_at = 100.2
        sess._voice_episode_close_if_ended(101.0)
        self.assertEqual(sess._last_voice_at, 100.2)

    def test_later_audible_stamp_survives_rollback(self):
        sess = self._session()
        self._feed(sess, [0.05] * 50)
        sess._last_voice_at = 104.9
        # A quiet sound after the episode (past the end gap)
        # stamped audible later — it is not the episode's, so
        # the rollback must leave it alone.
        sess._last_audible_at = 106.0
        sess._voice_episode_close_if_ended(105.5)
        self.assertIsNone(sess._last_voice_at)
        self.assertEqual(sess._last_audible_at, 106.0)

    def test_episode_reopens_after_close(self):
        sess = self._session()
        self._feed(sess, [0.05] * 50)
        sess._last_voice_at = 104.9
        sess._voice_episode_close_if_ended(105.5)
        self.assertIsNone(sess._last_voice_at)
        # A second, speech-like episode opens fresh and keeps
        # its stamps.
        self._feed(sess, [0.12, 0.02, 0.13, 0.01, 0.11] * 10,
                   t0=110.0)
        sess._last_voice_at = 114.9
        sess._voice_episode_close_if_ended(115.5)
        self.assertEqual(sess._last_voice_at, 114.9)


# ---------------------------------------------------------------------------
# Transcript bubble order (canned server messages, no network)
# ---------------------------------------------------------------------------

class _TranscriptBase(unittest.TestCase):
    def setUp(self):
        self.sess = _lc.GeminiLiveSession()
        self.seen = []  # (kind, role, text): kind "bubble"|"chunk"
        self.memory_rows = []
        self.log_lines = []
        self.sess.on_transcript = lambda r, t: self.seen.append(
            ("bubble", r, t))
        self.sess.on_transcript_chunk = lambda r, t: self.seen.append(
            ("chunk", r, t))
        patchers = [
            mock.patch.object(
                _lrecv, "memory_store",
                SimpleNamespace(
                    log_message=lambda r, t: self.memory_rows.append(
                        (r, t)))),
            mock.patch.object(
                _lrecv, "_log_line", self.log_lines.append),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _roles(self):
        # Turn bubbles only: loss notes ride the same
        # on_transcript channel as assistant lines ("The user's
        # spoken words could not be shown…"), so they are
        # filtered out here — they are a separate channel, not
        # turns.
        return [r for kind, r, t in self.seen
                if kind == "bubble"
                and not t.startswith(
                    ("The user's spoken words could not be shown",
                     "The assistant reply could not be shown",
                     "The live reply text could not be shown"))]


class TranscriptOrderTests(_TranscriptBase):
    def test_output_finishes_first_user_still_first(self):
        self.sess._handle_input_transcription(
            _sc_in("Calculate 17 * 3.", finished=False))
        self.sess._handle_output_transcription(_sc_out(
            "The result of seventeen multiplied by three is "
            "fifty-one.", finished=True))
        self.sess._handle_input_transcription(_sc_in("", finished=True))
        self.assertEqual(self._roles(), ["user", "assistant"])
        self.assertEqual(self.memory_rows,
                         [("user", "Calculate 17 * 3."),
                          ("assistant", "The result of seventeen "
                           "multiplied by three is fifty-one.")])

    def test_turn_complete_single_message_user_first(self):
        self.sess._handle_input_transcription(
            _sc_in("Calculate 17 * 3.", finished=False))
        self.sess._handle_output_transcription(_sc_both(
            "The result remains fifty-one.", True,
            "", True, turn_complete=True))
        self.sess._handle_input_transcription(_sc_both(
            "The result remains fifty-one.", True,
            "", True, turn_complete=True))
        self.assertEqual(self._roles(), ["user", "assistant"])

    def test_chunks_held_until_question_lands(self):
        timeline = []
        self.sess.on_transcript = lambda r, t: timeline.append(
            ("bubble", r))
        self.sess.on_transcript_chunk = lambda r, t: timeline.append(
            ("chunk", r))
        self.sess._handle_input_transcription(
            _sc_in("Calculate 17 * 3.", finished=False))
        for piece in ("The result ", "of seventeen ", "is fifty-one."):
            self.sess._handle_output_transcription(
                _sc_out(piece, finished=False))
        # No chunk streamed yet: the question has not landed.
        self.assertEqual(timeline, [])
        self.sess._handle_output_transcription(
            _sc_out("", finished=True))
        self.assertEqual(timeline, [])
        self.sess._handle_input_transcription(_sc_in("", finished=True))
        self.assertEqual(
            timeline,
            [("bubble", "user"),
             ("chunk", "assistant"), ("chunk", "assistant"),
             ("chunk", "assistant"), ("bubble", "assistant")])

    def test_no_input_transcription_streams_at_once(self):
        # A turn with no input transcription at all (the feature
        # off, or a text-only reply) streams and flushes exactly
        # as before — nothing is held for a question that never
        # comes.
        self.sess._handle_output_transcription(
            _sc_out("Hello.", finished=False))
        self.sess._handle_output_transcription(
            _sc_out("", finished=True))
        self.assertEqual(self._roles(), ["assistant"])
        self.assertEqual(
            [s for s in self.seen if s[0] == "chunk"],
            [("chunk", "assistant", "Hello.")])

    def test_stopped_reply_held_for_input_stays_stopped(self):
        self.sess._handle_input_transcription(
            _sc_in("Calculate 17 * 3.", finished=False))
        self.sess._reply_stop_requested = True
        self.sess._handle_output_transcription(_sc_out(
            "The result is fifty-one.", finished=True))
        self.assertEqual(self.seen, [])
        self.sess._handle_input_transcription(_sc_in("", finished=True))
        self.assertEqual(self._roles(), ["user"])
        self.assertEqual(self.memory_rows,
                         [("user", "Calculate 17 * 3."),
                          ("assistant", "The result is fifty-one.")])

    def test_failed_input_flush_keeps_output_held(self):
        calls = {"n": 0}

        def _flaky(role, text):
            calls["n"] += 1
            if role == "user" and calls["n"] == 1:
                raise RuntimeError("bubble down")
            self.seen.append(("bubble", role, text))

        self.sess.on_transcript = _flaky
        self.sess._handle_input_transcription(
            _sc_in("Calculate 17 * 3.", finished=False))
        self.sess._handle_output_transcription(_sc_out(
            "The result is fifty-one.", finished=True))
        # The input flush failed, so the question never landed
        # and the held reply must not follow it.
        self.sess._handle_input_transcription(_sc_in("", finished=True))
        self.assertEqual(self._roles(), [])
        # A retry lands the question, then releases the reply.
        self.sess._handle_input_transcription(_sc_in("", finished=True))
        self.assertEqual(self._roles(), ["user", "assistant"])

    def test_output_flush_retries_finished_input(self):
        calls = {"n": 0}

        def _flaky(role, text):
            calls["n"] += 1
            if role == "user" and calls["n"] == 1:
                raise RuntimeError("bubble down")
            self.seen.append(("bubble", role, text))

        self.sess.on_transcript = _flaky
        self.sess._handle_input_transcription(
            _sc_in("Calculate 17 * 3.", finished=True))
        self.assertEqual(self._roles(), [])
        self.sess._handle_output_transcription(_sc_out(
            "The result is fifty-one.", finished=True))
        self.assertEqual(self._roles(), ["user", "assistant"])


class TranscriptInterruptionTests(_TranscriptBase):
    def test_interruption_flushes_question_before_partial_reply(self):
        async def _run():
            self.sess._running = True
            self.sess.is_connected = True
            self.sess._session = _OneShotReceive(
                _sc_both("", False, "", False, turn_complete=False))
            self.sess._input_transcript_buf = "What time is it?"
            self.sess._input_transcript_open = True
            self.sess._output_transcript_buf = "It is half past"
            am = SimpleNamespace(
                mic_queue=queue.Queue(),
                enqueue_playback=lambda data: None,
                interrupt_playback=lambda: None)
            await asyncio.wait_for(
                self.sess._receive_loop(am), timeout=5.0)

        # The canned response carries interrupted=True (see
        # _OneShotReceive): the loop must flush the barge-in's
        # words before the partial reply.
        _OneShotReceive.interrupted = True
        asyncio.run(_run())
        self.assertEqual(self._roles(), ["user", "assistant"])


class _OneShotReceive:
    """A fake server session yielding one canned response, then
    empty receives (the loop ends after three, B2)."""
    interrupted = False

    def __init__(self, content):
        self._content = content
        self._yielded = False

    async def receive(self):
        if not self._yielded:
            self._yielded = True
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    model_turn=None,
                    output_transcription=self._content
                    .output_transcription,
                    input_transcription=self._content
                    .input_transcription,
                    turn_complete=False,
                    interrupted=self.interrupted,
                    interaction_status=None),
                session_resumption_update=None,
                go_away=None,
                tool_call_cancellation=None)
        if False:
            yield


# ---------------------------------------------------------------------------
# The live loops, no network (fake session, real send + watchdog loops)
# ---------------------------------------------------------------------------

class _Blob:
    def __init__(self, data=None, mime_type=None):
        self.data = data
        self.mime_type = mime_type


class _FakeLiveSession:
    async def send_realtime_input(self, audio=None, **kw):
        return None


class LiveLoopNoiseTests(unittest.TestCase):
    """The C1-F1 shape end to end on the real loops: noise in,
    silence after, and the watchdog must not fire; bursty speech
    with no server response must still trip it on the same
    envelope (the budgets did not move). Slow by construction
    (real seconds) — the trip window is 7 s silence + 5 s quiet.
    """

    def setUp(self):
        patchers = [
            mock.patch.object(_lc, "types",
                              SimpleNamespace(Blob=_Blob)),
            mock.patch.object(_lrecv, "types",
                              SimpleNamespace(Blob=_Blob)),
            mock.patch.object(_lrecv, "_log_line", lambda *a: None),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _drive(self, pcm_bytes, watch_s):
        notes = []
        result = {}

        async def _run():
            sess = _lc.GeminiLiveSession()
            sess.on_status_change = notes.append
            sess._running = True
            sess.is_connected = True
            sess._session = _FakeLiveSession()
            sess._last_response_at = time.monotonic()
            sess._last_voice_at = None
            sess._last_audible_at = None
            am = SimpleNamespace(mic_queue=queue.Queue())
            send_task = asyncio.create_task(
                sess._send_audio_loop(am))
            wd_task = asyncio.create_task(
                sess._live_watchdog_loop())
            t_start = time.monotonic()
            for i in range(0, len(pcm_bytes), _CHUNK):
                am.mic_queue.put(pcm_bytes[i:i + _CHUNK])
                await asyncio.sleep(0.1)
            for _ in range(5):
                am.mic_queue.put(b"\x00" * _CHUNK)
                await asyncio.sleep(0.1)
            # Wait for the episode to close, then read the stamps.
            # getattr: on a pre-fix tree the episode state does
            # not exist at all — the test must still reach the
            # watchdog assertion (which fails there, the bug)
            # instead of erroring on the missing attribute.
            await asyncio.sleep(1.0)
            result["voice_at"] = sess._last_voice_at
            result["audible_at"] = sess._last_audible_at
            result["episode_open"] = (
                getattr(sess, "_voice_episode_started_at", None)
                is not None)
            # Absolute deadline from the run start: the feed
            # itself takes len/SR seconds (one 100 ms chunk per
            # 0.1 s), then 0.5 s of silence, 1.0 s of close
            # wait, then the watch window.
            deadline = (t_start + len(pcm_bytes) / _SR
                        + 0.5 + 1.0 + watch_s)
            while time.monotonic() < deadline:
                if notes:
                    break
                await asyncio.sleep(0.25)
            result["notes"] = list(notes)
            sess._running = False
            for task in (send_task, wd_task):
                task.cancel()
            await asyncio.gather(send_task, wd_task,
                                 return_exceptions=True)

        asyncio.run(_run())
        return result

    def test_loud_noise_does_not_trip_watchdog(self):
        res = self._drive(_noise_pcm(5.0), watch_s=8.0)
        self.assertIsNone(res["voice_at"])
        self.assertIsNone(res["audible_at"])
        self.assertFalse(res["episode_open"])
        self.assertNotIn("LIVE_WATCHDOG", res["notes"])
        self.assertNotIn("LIVE_DEAD", res["notes"])

    def test_bursty_speech_still_trips_dead_session(self):
        res = self._drive(_speech_pcm(3.0), watch_s=16.0)
        self.assertIn("LIVE_WATCHDOG", res["notes"])


if __name__ == "__main__":
    unittest.main()
