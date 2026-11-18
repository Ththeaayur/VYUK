"""One mic loop (GAPS-16, flaw #8): the single drain → VAD → utterance
state machine every local voice session runs.

Before this module the same loop existed THREE times over — the local
engine (``core/voice/local_client_mic.py``), the lite session
(``core/voice/lite_client_session.py``) and the Groq session
(``core/voice/cloud_text_lifecycle.py``) each drained ``mic_queue``,
scored chunks against a VAD threshold, grouped voiced chunks into
utterances, and dispatched them — and the three copies had DRIFTED:

* Barge-in: local + lite held a would-be interruption until speech
  above the barge-in bar SUSTAINED for 150 ms, then cut playback
  through a faded interrupt, and opened a 250 ms post-playback
  guard during which chunks could not seed an utterance (v5.44
  A4). Groq cut playback on ANY speech chunk while speaking —
  one loud chunk (a spike, her own echo peak) interrupted her.
* VAD: local + lite scored each chunk against the ADAPTIVE floor
  (``_effective_vad_threshold(estimator)`` — the config floor
  raised by the measured room noise, capped at 4x). Groq scored
  against the PLAIN config floor only, with no estimator at all.

This module is the one loop. The per-session differences that are
genuine (not drift) are hooks, passed in by the caller:

* ``running`` — when the loop stops (local/Groq: the session's
  ``_running``; lite: not stopped and not upgraded).
* ``is_speaking`` / ``set_speaking`` — the session's ``_speaking``
  flag, which the loop reads and clears on a barge-in.
* ``interrupt_playback`` — how this session cuts its own speech
  (local: its faded interrupt; lite/Groq: the speech helper's
  faded interrupt, or the audio manager's hard cut when there
  is no helper).
* ``on_utterance`` — what happens to a finished utterance
  (local/Groq: stamp + dispatch as a tracked task; lite: await
  the turn inline).
* ``estimator`` — the adaptive-noise-floor estimator. Local keeps
  ONE persisted estimator on the session (so the floor — and the
  doctor's snapshot of it — survives across loop runs); lite and
  Groq pass a fresh one per loop run. All three now score against
  the adaptive floor.
* ``pre_roll`` — the local engine's ~300 ms ring snapshot, prepended
  at dispatch (v5.43 A2). Local only; lite/Groq pass None and their
  utterances are unchanged (no pre-roll audio is added or removed).
* ``should_stop`` — the local engine's listen timeout (v5.17 A15):
  a silent stretch ends the session. Local only; lite/Groq pass
  None and never time out.

BEHAVIOUR CHANGES (stated, with before/after numbers) — these are
the flaw's fix, not incidental drift:

* Groq barge-in: BEFORE, any chunk with RMS >= the plain VAD
  threshold (default 400) while speaking cut playback at once.
  AFTER, a chunk must reach RMS >= VAD x 1.6 (default 640) AND
  sustain above that bar for 150 ms, and a 250 ms post-playback
  guard applies — the same discipline local/lite have had since
  v5.44. A single loud chunk no longer interrupts a Groq reply;
  a real interruption still lands after 150 ms of speech.
* Groq VAD: BEFORE, the plain config floor (default 400) always.
  AFTER, the adaptive floor — max(400, room-floor x 3.5), capped
  at 1600. In a quiet room the threshold is identical (400); in
  a noisy room it rises with the room, like local/lite.
* Nothing else moves: local and lite run the same state machine
  they ran before (same thresholds, same sustain/guard, same
  pre-roll and timeout for local, same inline dispatch for lite);
  the Gemini live sender (``live_client_receive._send_audio_loop``)
  is NOT this loop and stays untouched — the server is its
  VAD/STT/turn-taker, and a local VAD would double-gate the
  stream.
"""

from __future__ import annotations

import asyncio
import queue
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from ..config import config
from .local_client_vad import (
    MAX_UTTERANCE_S,
    VAD_MIN_SPEECH_S,
    VAD_TRAILING_SILENCE_S,
    _SHORT_UTTERANCE_SILENCE_S,
    NoiseFloorEstimator,
    _barge_in_guard_s,
    _barge_in_sustain_s,
    _barge_in_threshold,
    _effective_vad_threshold,
)

__all__ = ["chunk_rms", "run_mic_loop"]


def chunk_rms(pcm: bytes) -> float:
    """RMS of one 16 kHz int16 mic chunk (the unit every VAD
    threshold in this module is expressed in)."""
    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


async def run_mic_loop(
    audio_manager,
    *,
    running: Callable[[], bool],
    is_speaking: Callable[[], bool],
    set_speaking: Callable[[bool], None],
    interrupt_playback: Callable[[], None],
    on_utterance: Callable[..., Any],
    estimator: NoiseFloorEstimator | None = None,
    pre_roll: tuple[Callable, Callable] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """Drain ``audio_manager.mic_queue``, group voiced chunks into
    utterances, dispatch each finished utterance through
    ``on_utterance``.

    The state machine, moved verbatim from the local engine's loop
    (v5.43 A2 pre-roll, v5.44 A3 adaptive floor + A4 sustain/guard,
    v5.17 A15 listen timeout as the ``should_stop`` hook):

    * a chunk is speech when its RMS reaches the EFFECTIVE VAD
      threshold — the config floor adapted by ``estimator`` (a chunk
      never judges itself: the start threshold comes from the floor
      estimated from EARLIER chunks);
    * while the session is speaking, above-barge-in-bar audio is
      HELD until it sustains for the configured window — one loud
      chunk (a spike, her own echo peak) no longer cuts her off;
      a sustained barge-in cuts playback (faded, via the caller's
      ``interrupt_playback``) and seeds the utterance with the held
      chunks, so the interruption's first words survive;
    * when playback ends WITHOUT a barge-in, the post-playback
      guard opens: chunks are VAD-scored but may not seed an
      utterance, feed the noise floor, or refresh the pre-roll —
      that window is where her echo tail lives. The guard is
      counted in AUDIO time (summed chunk durations), not wall
      time, so a stalled loop cannot shrink the window it guards;
    * an utterance ends when it holds >= VAD_MIN_SPEECH_S of
      speech and either VAD_TRAILING_SILENCE_S of trailing
      silence follows or it reaches MAX_UTTERANCE_S. Each
      dispatched utterance carries that trailing silence
      (seconds) as ``trailing_silence_s`` on ``on_utterance`` —
      the engines forward it to the turn detector's consultation
      (GAPS-25 V-2), so the detector is asked about a turn with
      the turn's real inputs.
    """
    voiced: list[bytes] = []
    speech_s = 0.0
    silence_s = 0.0
    pre_roll_pcm: bytes = b""
    speaking_seen = False  # _speaking was True on an earlier chunk;
                           # its falling edge (playback over, not a
                           # barge-in) opens the post-playback guard
    guard_remaining_s = 0.0  # guard window left, in audio time
    barge_pending: list[bytes] = []  # chunks above the barge-in
                                     # threshold, held until they
                                     # either sustain (seed the
                                     # utterance) or fizzle (dropped
                                     # as a spike/echo)
    barge_sustained_s = 0.0
    chunk_s = config.chunk_size / config.sample_rate_in  # fallback duration

    async def _flush_utterance() -> None:
        """Dispatch the open utterance, if any, through
        on_utterance (pre-roll + trailing silence travel with
        it, exactly as the inline flush did). Voice-control
        batch (2026-11-16, V1/V2): one shared flush for the
        normal rule, the short-utterance rule, and the loop
        exits — the old code flushed inline in exactly one
        place, so a partial utterance at timeout/disconnect
        was dropped with no trace."""
        nonlocal voiced, speech_s, silence_s, pre_roll_pcm
        if not voiced:
            return
        pcm = pre_roll_pcm + b"".join(voiced) if pre_roll_pcm \
            else b"".join(voiced)
        # The silence that satisfied the endpoint rule (or the
        # silence so far when the max-length cap cut the turn)
        # travels with the utterance: the engines hand it to
        # the turn detector's consultation (GAPS-25 V-2).
        trailing_silence = silence_s
        voiced, speech_s, silence_s = [], 0.0, 0.0
        pre_roll_pcm = b""
        await on_utterance(pcm, trailing_silence_s=trailing_silence)

    while running():
        if should_stop is not None and should_stop():
            # Voice-control batch (2026-11-16, V2): the listen
            # timeout fires exactly when the user has been
            # silent — i.e. exactly when an utterance may be
            # sitting below the flush threshold. Flush it
            # instead of dropping the user's last words.
            await _flush_utterance()
            return
        try:
            chunk = audio_manager.mic_queue.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.01)
            continue
        if not chunk:
            continue

        duration = (len(chunk) // 2) / config.sample_rate_in or chunk_s
        rms = chunk_rms(chunk)
        # A3: the start threshold for THIS chunk comes from the
        # floor estimated from EARLIER chunks — a chunk never
        # judges itself, and a loud first chunk of a session
        # still meets the plain config floor.
        is_speech = rms >= _effective_vad_threshold(estimator)

        seeded = False  # a sustained barge-in seeded `voiced`
                        # with this chunk already counted (A4)
        if is_speaking():
            speaking_seen = True
            if rms >= _barge_in_threshold():
                # Barge-in candidate (A4 sustain): the chunk is
                # HELD, not acted on, until above-threshold
                # audio has sustained for the configured window
                # — one loud chunk (a spike, her own echo peak)
                # no longer cuts her off.
                barge_pending.append(chunk)
                barge_sustained_s += duration
                if barge_sustained_s >= _barge_in_sustain_s():
                    # Sustained: the user really is talking
                    # over VYUK — cut her off (faded, not
                    # clipped — see the caller's interrupt) and
                    # seed the utterance with the held chunks,
                    # so the interruption's first words survive.
                    interrupt_playback()
                    set_speaking(False)
                    speaking_seen = False  # ended by barge-in,
                                           # not by playback —
                                           # no guard opens
                    guard_remaining_s = 0.0
                    voiced.extend(barge_pending)
                    speech_s += barge_sustained_s
                    silence_s = 0.0
                    barge_pending = []
                    barge_sustained_s = 0.0
                    seeded = True
                else:
                    continue  # still proving it is a barge-in
            else:
                # Below the barge-in bar: any held candidate
                # fizzled — drop it and keep listening to her.
                barge_pending = []
                barge_sustained_s = 0.0
                continue  # her own voice / room tone while she speaks

        if not seeded:
            if speaking_seen and not is_speaking():
                # Playback just ended (A4 guard): for the
                # guard window, chunks are VAD-scored (above)
                # but may NOT seed an utterance, feed the
                # noise floor, or refresh the pre-roll — that
                # window is where her echo tail lives.
                speaking_seen = False
                guard_remaining_s = _barge_in_guard_s()
                barge_pending = []
                barge_sustained_s = 0.0
            if guard_remaining_s > 0.0:
                guard_remaining_s -= duration
                continue
            if is_speech:
                if not voiced and pre_roll is not None and not pre_roll_pcm:
                    # Onset before any quiet chunk refreshed the
                    # snapshot — take the ring audio that precedes
                    # this chunk instead (A2).
                    before_onset, _ring = pre_roll
                    pre_roll_pcm = before_onset(audio_manager, chunk)
                voiced.append(chunk)
                speech_s += duration
                silence_s = 0.0
            elif voiced:
                silence_s += duration
            else:
                # Quiet, no utterance open: this chunk is room
                # tone — feed the adaptive floor (A3; speech
                # and in-utterance chunks never reach this
                # branch, so the floor measures the ROOM only)
                # and refresh the pre-roll snapshot so it ends
                # with the newest room audio when speech
                # starts (A2).
                if estimator is not None:
                    estimator.observe(rms, in_utterance=False)
                if pre_roll is not None:
                    _before_onset, ring_pcm = pre_roll
                    pre_roll_pcm = ring_pcm(audio_manager)

        # Voice-control batch (2026-11-16, V1): a sub-threshold
        # voiced run ("yes", "no", "stop") is dispatched after a
        # LONGER trailing silence — the old rule never flushed
        # it at all, so it merged into the next utterance or was
        # lost outright. The longer silence is the blip-vs-word
        # distinction the 0.35 s floor never had.
        if voiced and (
            (speech_s >= VAD_MIN_SPEECH_S
             and (silence_s >= VAD_TRAILING_SILENCE_S
                  or speech_s >= MAX_UTTERANCE_S))
            or (speech_s < VAD_MIN_SPEECH_S
                and silence_s >= _SHORT_UTTERANCE_SILENCE_S)
        ):
            await _flush_utterance()
    # Voice-control batch (2026-11-16, V2): speech in progress
    # at disconnect is transcribed, not discarded.
    await _flush_utterance()
