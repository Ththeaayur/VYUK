"""Wave 9 (v5.64) — procedural soundscape PILOT (WF-51).

Her own DSP focus room: a bed of procedurally generated sound
— brown-noise wash plus a slow pad — synthesised in code at
runtime, never sampled from a recording, so no audio asset and
no audio licence is involved at all.

WF-86 IS NOT BUILT HERE. One of WF-51 / WF-86 builds, never
both: this module is WF-51, and ``WF86_BUILT`` is False by
construction — there is no WF-86 code path in this module to
enable by accident.

LOUDNESS, by the shipped standard: TTS sits at -18 dBFS
(``core/loudness.py`` ``DEFAULT_TARGET_DBFS``). The bed sits
12 dB BELOW that by default (-30 dBFS), so it is texture under
speech, never competition with it. DUCKING UNDER TTS: while
TTS is active the bed is ducked a further ``DUCK_DB`` (6 dB
by default), and it returns to bed level when TTS stops — the
ducking is a gain envelope over the bed, applied per-sample
from a TTS-active flag, never a mute that clicks.

PILOT, not live: generation and ducking are computed and
tested, but no playback path feeds them to the speakers yet
— wiring is the closing step. Stdlib only (no numpy: sample
lists keep this module dependency-free and trivially testable).
No telemetry.
"""

from __future__ import annotations

import math
import random

MODULE_VERSION = "5.64.0"

# WF-51 builds; WF-86 does not. Never both.
THIS_WF = "WF-51"
WF51_BUILT = True
WF86_BUILT = False

# Loudness standard, relative to the shipped TTS standard.
TTS_STANDARD_DBFS = -18.0
BED_BELOW_TTS_DB = 12.0
BED_TARGET_DBFS = TTS_STANDARD_DBFS - BED_BELOW_TTS_DB  # -30 dBFS
DUCK_DB = 6.0  # further duck while TTS is active
SAMPLE_RATE = 16000

__all__ = [
    "MODULE_VERSION",
    "THIS_WF",
    "WF51_BUILT",
    "WF86_BUILT",
    "TTS_STANDARD_DBFS",
    "BED_BELOW_TTS_DB",
    "BED_TARGET_DBFS",
    "DUCK_DB",
    "SAMPLE_RATE",
    "dbfs_to_amplitude",
    "amplitude_to_dbfs",
    "rms_dbfs",
    "generate_brown_wash",
    "generate_pad",
    "generate_bed",
    "normalise_to_dbfs",
    "duck_bed",
]


def dbfs_to_amplitude(dbfs: float) -> float:
    """Linear amplitude for a dBFS level (0 dBFS = 1.0)."""
    return 10.0 ** (dbfs / 20.0)


def amplitude_to_dbfs(amplitude: float) -> float:
    if amplitude <= 0:
        return float("-inf")
    return 20.0 * math.log10(amplitude)


def rms_dbfs(samples: list[float]) -> float:
    """RMS level of float samples in dBFS; -inf for empty/silent."""
    if not samples:
        return float("-inf")
    mean_square = sum(s * s for s in samples) / len(samples)
    if mean_square <= 0:
        return float("-inf")
    return amplitude_to_dbfs(math.sqrt(mean_square))


def generate_brown_wash(seconds: float, seed: int = 0,
                        sample_rate: int = SAMPLE_RATE) -> list[float]:
    """Brown-noise wash: a leaky integrator over white noise,
    so low frequencies dominate and the wash sounds soft, not
    hissy. Deterministic per seed, so tests reproduce it."""
    count = max(0, int(seconds * sample_rate))
    rng = random.Random(seed)
    out: list[float] = []
    last = 0.0
    for _ in range(count):
        white = rng.uniform(-1.0, 1.0)
        last = (last + 0.02 * white) / 1.02
        out.append(last * 3.5)
    return out


def generate_pad(seconds: float, frequencies: tuple[float, ...] = (220.0, 277.18),
                 sample_rate: int = SAMPLE_RATE) -> list[float]:
    """Slow pad: a chord of sine partials with a raised-cosine
    swell in and out, so the bed breathes instead of starting
    and stopping abruptly."""
    count = max(0, int(seconds * sample_rate))
    if count == 0:
        return []
    out: list[float] = []
    for i in range(count):
        t = i / sample_rate
        envelope = 0.5 - 0.5 * math.cos(2.0 * math.pi * i / count)
        value = sum(math.sin(2.0 * math.pi * f * t)
                    for f in frequencies) / max(1, len(frequencies))
        out.append(value * envelope)
    return out


def normalise_to_dbfs(samples: list[float], target_dbfs: float) -> list[float]:
    """Scale samples so their RMS equals ``target_dbfs``.
    Silent input stays silent — scaling silence is still silence,
    not a division by zero."""
    current = rms_dbfs(samples)
    if current == float("-inf"):
        return list(samples)
    gain = dbfs_to_amplitude(target_dbfs - current)
    return [s * gain for s in samples]


def generate_bed(seconds: float, seed: int = 0,
                 target_dbfs: float = BED_TARGET_DBFS,
                 sample_rate: int = SAMPLE_RATE) -> list[float]:
    """One focus-room bed: brown wash + pad, mixed and
    normalised to the bed target (-30 dBFS by default, 12 dB
    below the -18 dBFS TTS standard)."""
    wash = generate_brown_wash(seconds, seed, sample_rate)
    pad = generate_pad(seconds, sample_rate=sample_rate)
    mixed = [(w * 0.7 + p * 0.3) for w, p in zip(wash, pad)]
    return normalise_to_dbfs(mixed, target_dbfs)


def duck_bed(bed: list[float], tts_active: list[bool],
             duck_db: float = DUCK_DB) -> list[float]:
    """Apply the TTS ducking envelope: samples where TTS is
    active are scaled down by ``duck_db``; the rest pass
    unchanged. ``tts_active`` shorter than the bed is treated
    as inactive beyond its end; longer is truncated. The gain
    change is a plain scale, so no click is introduced by the
    envelope itself (callers crossfade at TTS boundaries)."""
    duck_gain = dbfs_to_amplitude(-abs(duck_db))
    out: list[float] = []
    for i, sample in enumerate(bed):
        active = i < len(tts_active) and bool(tts_active[i])
        out.append(sample * duck_gain if active else sample)
    return out
