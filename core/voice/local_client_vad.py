"""vad concern extracted from core/voice/local_client.py (refactor Phase 1 step 2). Behaviour identical; every name here stays importable from core.local_client."""

import time
from collections import deque
from typing import Any
from ..config import DATA_DIR, config  # Phase 3 V1b: moved to core/voice/


VAD_RMS_THRESHOLD = 400        # chunk RMS above this counts as speech
VAD_MIN_SPEECH_S = 0.35        # ignore voiced blips shorter than this
VAD_TRAILING_SILENCE_S = 0.8   # this much trailing silence ends an utterance
# Voice-control batch (2026-11-16, V1): the short-utterance
# flush — a voiced run UNDER the 0.35 s floor ("yes", "no",
# "stop") is dispatched after this much trailing silence
# instead of sitting un-dispatched until the next utterance
# merges into it (or forever, if no next utterance comes).
# Longer than the normal trailing silence on purpose: a short
# run followed by a long silence is a finished short word, not
# the start of a longer utterance.
_SHORT_UTTERANCE_SILENCE_S = 2.0
MAX_UTTERANCE_S = 30.0         # hard flush so a noisy room can't buffer forever
def _vad_threshold() -> float:
    """The speech/no-speech RMS threshold actually used (v5.17, EARS):
    config.vad_rms_threshold when it holds a sane positive number,
    else the module constant — a garbage setting degrades to today's
    behaviour, never to a deaf or hair-trigger session."""
    try:
        value = float(getattr(config, "vad_rms_threshold", VAD_RMS_THRESHOLD))
    except (TypeError, ValueError):
        return float(VAD_RMS_THRESHOLD)
    if value != value or value <= 0:  # NaN or non-positive
        return float(VAD_RMS_THRESHOLD)
    return value
def _barge_in_threshold() -> float:
    """The RMS a chunk must reach to cut VYUK off mid-sentence
    (v5.17, A9): the VAD threshold scaled by config.barge_in_factor.
    v5.44 (A4): the factor's default is 1.6 (see core/config.py) —
    at the old 1.0 her own speaker echo could clear the bar; higher
    asks for a firmer voice before she yields, lower makes her
    easier to interrupt. Garbage degrades to the factor-1.0
    threshold."""
    base = _vad_threshold()
    try:
        factor = float(getattr(config, "barge_in_factor", 1.6))
    except (TypeError, ValueError):
        return base
    if factor != factor or factor <= 0:  # NaN or non-positive
        return base
    return base * factor
VAD_NOISE_WINDOW_S = 10.0      # rolling history kept for the floor
VAD_NOISE_MULTIPLIER = 3.5     # floor x this = candidate threshold
VAD_NOISE_CAP_FACTOR = 4.0     # effective <= config floor x this
VAD_NOISE_MIN_SAMPLES = 5      # below this the estimate is not
_LAST_NOISE_FLOOR_SNAPSHOT: dict[str, Any] | None = None
def _publish_noise_floor_snapshot(noise_floor, effective, base,
                                  samples) -> None:
    """Records the estimator's latest reading at module level so
    doctor.py (which runs without a live session) can report the
    LAST KNOWN floor honestly instead of inventing one."""
    global _LAST_NOISE_FLOOR_SNAPSHOT
    _LAST_NOISE_FLOOR_SNAPSHOT = {
        "noise_floor_rms": noise_floor,
        "effective_threshold_rms": effective,
        "config_floor_rms": base,
        "samples": int(samples),
        "updated_at": time.time(),
    }
def _noise_floor_snapshot() -> dict[str, Any] | None:
    """A copy of the last known adaptive-VAD snapshot, or None
    when no estimator has observed a room yet in this process."""
    snap = _LAST_NOISE_FLOOR_SNAPSHOT
    return dict(snap) if snap is not None else None
def _reset_noise_floor_snapshot() -> None:
    global _LAST_NOISE_FLOOR_SNAPSHOT
    _LAST_NOISE_FLOOR_SNAPSHOT = None
class NoiseFloorEstimator:
    """Rolling room-noise floor + effective VAD threshold (A3).

    Feed every idle-listening chunk's RMS via observe(); chunks
    belonging to an utterance are refused (in_utterance=True) —
    speech must never raise the floor it is measured against.
    """

    def __init__(self, window_s: float = VAD_NOISE_WINDOW_S,
                 multiplier: float = VAD_NOISE_MULTIPLIER,
                 cap_factor: float = VAD_NOISE_CAP_FACTOR):
        self.window_s = float(window_s)
        self.multiplier = float(multiplier)
        self.cap_factor = float(cap_factor)
        self._samples: deque[tuple[float, float]] = deque()
        # GAPS-25 (V-7a): a new estimator is a new listening
        # period — it starts with NO floor from the previous
        # session. Before this, the module-global snapshot kept
        # the last session's floor (the doctor read it, nothing
        # ever cleared it), so a new session inherited a stale
        # room. Local persists one estimator per session, lite
        # and Groq create fresh ones per loop run, so each period
        # now starts clean and the doctor reports the CURRENT
        # session's floor, or None before any chunk is observed.
        _reset_noise_floor_snapshot()

    def observe(self, rms, *, in_utterance: bool = False,
                now: float | None = None):
        """Records one chunk RMS unless it belongs to an utterance.
        Returns the current floor estimate (or None). Never raises:
        a garbage sample is simply not recorded."""
        if in_utterance:
            return self.noise_floor()
        try:
            value = float(rms)
        except (TypeError, ValueError):
            return self.noise_floor()
        if value != value or value < 0:  # NaN or negative
            return self.noise_floor()
        ts = time.monotonic() if now is None else float(now)
        self._samples.append((ts, value))
        cutoff = ts - self.window_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        # Publish on every accepted sample: the snapshot always
        # carries the effective threshold this floor implies.
        base = _vad_threshold()
        floor = self.noise_floor()
        effective = base if floor is None else min(
            max(base, floor * self.multiplier), base * self.cap_factor)
        _publish_noise_floor_snapshot(floor, effective, base,
                                      len(self._samples))
        return floor

    def noise_floor(self) -> float | None:
        """~The 10th percentile of the window's RMS values, or
        None until VAD_NOISE_MIN_SAMPLES samples have arrived."""
        if len(self._samples) < VAD_NOISE_MIN_SAMPLES:
            return None
        values = sorted(v for _ts, v in self._samples)
        index = int(0.10 * (len(values) - 1))
        return values[index]

    def sample_count(self) -> int:
        return len(self._samples)

    def effective_threshold(self, config_floor=None) -> float:
        """max(config floor, floor x multiplier), capped at
        cap_factor x the config floor (see the section header).
        With no trusted floor yet, the config floor rules alone."""
        if config_floor is None:
            base = _vad_threshold()
        else:
            try:
                base = float(config_floor)
            except (TypeError, ValueError):
                base = _vad_threshold()
            if base != base or base <= 0:
                base = _vad_threshold()
        floor = self.noise_floor()
        if floor is None:
            return base
        return min(max(base, floor * self.multiplier),
                   base * self.cap_factor)
def _effective_vad_threshold(estimator=None) -> float:
    """The utterance-start threshold for this chunk: the config
    floor adapted by `estimator` when one is supplied and has a
    trusted reading, else the plain config floor. Never raises —
    adaptation is a refinement, never a new failure mode."""
    if estimator is None:
        return _vad_threshold()
    try:
        return float(estimator.effective_threshold())
    except Exception:  # noqa: BLE001
        return _vad_threshold()
POST_PLAYBACK_GUARD_MS = 250   # config.barge_in_guard_ms's default
BARGE_IN_SUSTAIN_MS = 150      # config.barge_in_sustain_ms's default
def _barge_in_guard_s() -> float:
    """The post-playback guard window in seconds (A4): after her
    playback ends, chunks are VAD-scored but may not seed a new
    utterance for this long — her echo tail's favourite window.
    Garbage/negative degrades to the 250 ms default; 0 disables."""
    try:
        value = float(getattr(config, "barge_in_guard_ms",
                              POST_PLAYBACK_GUARD_MS))
    except (TypeError, ValueError):
        return POST_PLAYBACK_GUARD_MS / 1000.0
    if value != value or value < 0:  # NaN or negative
        return POST_PLAYBACK_GUARD_MS / 1000.0
    return value / 1000.0
def _barge_in_sustain_s() -> float:
    """How long a would-be barge-in must stay above the barge-in
    threshold before it cuts her off (A4), in seconds. Garbage/
    negative degrades to the 150 ms default; 0 = a single chunk
    interrupts, the pre-v5.44 behaviour."""
    try:
        value = float(getattr(config, "barge_in_sustain_ms",
                              BARGE_IN_SUSTAIN_MS))
    except (TypeError, ValueError):
        return BARGE_IN_SUSTAIN_MS / 1000.0
    if value != value or value < 0:  # NaN or negative
        return BARGE_IN_SUSTAIN_MS / 1000.0
    return value / 1000.0
