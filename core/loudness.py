"""Output loudness standard + silence-edge trim (v5.51, M-2 / M-3).

Pure numpy, no Qt, no config import — every function here is a
deterministic transform over float samples or int16 PCM bytes, so
the suites can pin the maths without a session, a model, or audio
hardware.

M-2 — RMS loudness standard. This is deliberately an RMS-based
standard, NOT a LUFS / BS.1770 measurement: pyloudnorm is not
available in this environment (it needs scipy), and naming an RMS
measure "LUFS" would be a lie about what was measured. What the
standard guarantees:

  * ONE gain decision per assembled utterance. Callers pass the
    utterance's chunks together (``normalize_pcm_chunks``); the
    RMS is measured over the concatenation, a single gain is
    computed, and that same gain is applied to every chunk — so
    chunk-to-chunk relative levels are preserved exactly and
    sentence boundaries cannot pump.
  * The gain aims the utterance's RMS at a fixed target
    (``DEFAULT_TARGET_DBFS``, −18 dBFS unless config overrides).
  * After the gain, a soft limiter keeps every peak at or below
    the hard ceiling of −1 dBFS: a tanh soft knee, so peaks are
    compressed smoothly toward the ceiling and never hard-clip.
  * ``enabled=False`` is a strict pass-through: the exact input
    objects are returned unchanged (byte-identical PCM), which is
    the kill-switch contract core/voice/local_client.py relies on.

M-3 — silence-edge trim. ``trim_samples`` / ``trim_pcm`` cut
near-silence from a chunk's EDGES ONLY, keeping a pad of silence
at each edge so onsets and tails are never clipped. Internal
silence is untouched by construction (the result is one
contiguous slice of the input). The assembly in
core/voice/local_client.py credits the retained pads against the
designed breathing pauses, so a trimmed chunk's pause still
measures at its intended length.
"""

from __future__ import annotations

import numpy as np

# --- M-2 constants ------------------------------------------------- #

#: Default RMS target: −18 dBFS leaves ≈17 dB of headroom under
#: the −1 dBFS peak ceiling for speech crest factors (~12–15 dB),
#: so the limiter rarely engages on ordinary speech.
DEFAULT_TARGET_DBFS = -18.0

#: Hard peak ceiling: nothing leaves the limiter above −1 dBFS.
PEAK_CEILING_DBFS = -1.0
PEAK_CEILING = float(10.0 ** (PEAK_CEILING_DBFS / 20.0))  # ≈ 0.8913

#: The soft knee starts at this fraction of the ceiling; below it
#: the limiter is the identity, above it a tanh curve approaches
#: the ceiling asymptotically (C1-smooth at the knee: the tanh
#: segment's slope there is exactly 1, matching the identity).
_SOFT_KNEE_FRACTION = 0.85

#: Below this RMS the utterance is treated as silence: no gain is
#: applied (a huge gain on a noise floor would manufacture speech
#: out of hiss, and dividing by ~0 is not a level decision).
_SILENCE_FLOOR_DBFS = -60.0

#: Gain sanity clamp, in dB. Wide enough for genuinely quiet or
#: loud renders to reach the target, narrow enough that a broken
#: measurement can never produce a 100x surprise.
_MAX_GAIN_DB = 24.0

# --- M-3 constants ------------------------------------------------- #

#: Edge-trim threshold: samples at or below −45 dBFS count as
#: silence for trimming and for pause-credit measurement.
TRIM_THRESHOLD_DBFS = -45.0

#: Silence kept at each trimmed edge (~30 ms at 24 kHz = 720
#: samples) so a soft onset or a decaying tail is never clipped.
TRIM_PAD_MS = 30.0


def threshold_amplitude(threshold_dbfs: float = TRIM_THRESHOLD_DBFS) -> float:
    """The linear amplitude a dBFS threshold names."""
    return float(10.0 ** (float(threshold_dbfs) / 20.0))


# ------------------------------------------------------------------ #
# Measurement
# ------------------------------------------------------------------ #

def rms_dbfs(samples: np.ndarray) -> float:
    """RMS level of float samples in dBFS; −inf for empty/silent."""
    arr = np.asarray(samples, dtype=np.float64).ravel()
    if arr.size == 0:
        return float("-inf")
    rms = float(np.sqrt(np.mean(arr * arr)))
    if rms <= 0.0:
        return float("-inf")
    return 20.0 * float(np.log10(rms))


def gain_for_target(measured_dbfs: float,
                    target_dbfs: float = DEFAULT_TARGET_DBFS) -> float:
    """The single linear gain that moves `measured_dbfs` RMS onto
    `target_dbfs`, clamped to ±_MAX_GAIN_DB. A silent / unmeasurable
    input gets unity gain (pass-through by another name)."""
    if measured_dbfs == float("-inf") or measured_dbfs != measured_dbfs:
        return 1.0
    if measured_dbfs < _SILENCE_FLOOR_DBFS:
        return 1.0
    gain_db = float(target_dbfs) - measured_dbfs
    gain_db = min(_MAX_GAIN_DB, max(-_MAX_GAIN_DB, gain_db))
    return float(10.0 ** (gain_db / 20.0))


# ------------------------------------------------------------------ #
# Soft limiter
# ------------------------------------------------------------------ #

def soft_limit(samples: np.ndarray,
               ceiling: float = PEAK_CEILING) -> np.ndarray:
    """Tanh soft-knee limiter with a hard peak ceiling.

    Samples at or below the knee (0.85 × ceiling) pass unchanged.
    Above it, magnitude follows knee + span·tanh((mag − knee)/span)
    with span = ceiling − knee, which approaches `ceiling`
    asymptotically and never exceeds it; a final clip to
    [−ceiling, ceiling] makes the ceiling guarantee exact even
    where float tanh rounds to 1.0. The curve is monotonic and
    C1-smooth at the knee, so a hot signal is compressed, never
    hard-clipped into a plateau."""
    arr = np.asarray(samples, dtype=np.float64)
    if arr.size == 0:
        return arr
    knee = ceiling * _SOFT_KNEE_FRACTION
    span = ceiling - knee
    magnitude = np.abs(arr)
    over = magnitude > knee
    if not bool(np.any(over)):
        return arr
    limited = np.where(
        over,
        knee + span * np.tanh((magnitude - knee) / span),
        magnitude,
    )
    out = np.sign(arr) * limited
    return np.clip(out, -ceiling, ceiling)


# ------------------------------------------------------------------ #
# M-2: the RMS loudness standard
# ------------------------------------------------------------------ #

def normalize_samples(samples: np.ndarray,
                      target_dbfs: float = DEFAULT_TARGET_DBFS,
                      enabled: bool = True) -> np.ndarray:
    """One RMS gain + soft limiter over a whole float utterance.

    With `enabled` false, or for an empty/silent utterance, the
    input is returned unchanged — the kill-switch pass-through."""
    arr = np.asarray(samples, dtype=np.float64)
    if not enabled or arr.size == 0:
        return arr
    gain = gain_for_target(rms_dbfs(arr), target_dbfs)
    if gain == 1.0:
        # Already on target (or silence): still run the limiter so
        # an on-target-but-hot utterance cannot clip.
        return soft_limit(arr)
    return soft_limit(arr * gain)


def normalize_pcm_chunks(chunks: list[bytes],
                         target_dbfs: float = DEFAULT_TARGET_DBFS,
                         enabled: bool = True) -> list[bytes]:
    """The M-2 standard for an assembled utterance in int16 PCM
    chunks: ONE gain measured over the concatenation, applied to
    every chunk, then the soft limiter; the result is split back
    into chunks of exactly the original lengths (gain and limiting
    never change a sample count), so chunk-to-chunk relative levels
    are preserved exactly (up to the final int16 rounding).

    Disabled ⇒ the input list is returned as-is: the same bytes
    objects, byte-identical — the strict pass-through contract."""
    if not enabled or not chunks:
        return chunks
    arrays = [np.frombuffer(c, dtype=np.int16).astype(np.float64) / 32768.0
              for c in chunks]
    joined = (np.concatenate(arrays) if len(arrays) > 1
              else arrays[0])
    gained = normalize_samples(joined, target_dbfs=target_dbfs,
                               enabled=True)
    pcm = (np.clip(gained, -1.0, 1.0) * 32767).astype(np.int16)
    out: list[bytes] = []
    pos = 0
    for arr in arrays:
        out.append(pcm[pos:pos + arr.size].tobytes())
        pos += arr.size
    return out


def normalize_pcm(pcm: bytes,
                  target_dbfs: float = DEFAULT_TARGET_DBFS,
                  enabled: bool = True) -> bytes:
    """`normalize_pcm_chunks` for a single-block utterance."""
    if not enabled or not pcm:
        return pcm
    return normalize_pcm_chunks([pcm], target_dbfs=target_dbfs,
                                enabled=True)[0]


# ------------------------------------------------------------------ #
# M-3: silence-edge trim + edge-silence measurement
# ------------------------------------------------------------------ #

class ProsodyPauseExemption:
    """v5.58 (Wave 3, plan §6 C-4) — the EXPLICIT, NAMED
    exemption class for the v5.51 pause-exact trim.

    WHY IT EXISTS: the v5.58 prosody stack designs pauses
    (clause ≈150–250 ms, sentence ≈300–450 ms, paragraph
    ≈600–900 ms, core/voice/mouth.py) as silence that MUST
    survive trimming. A plain trim would eat a designed pause
    baked into a chunk's edge down to the 30 ms pad, or delete
    a pure-pause block outright. When a caller passes an
    exemption, trim keeps at least `preserve_ms` of silence at
    each edge (instead of only the 30 ms pad), and a block with
    no above-threshold sample at all — a pure designed pause —
    is returned UNCHANGED. Callers that do not pass an
    exemption get byte-identical v5.51 behaviour: the default
    path is unchanged, the exemption is opt-in and named at
    every call site (see core/local_client._trim_chunk_pcm's
    `prosody_exempt` parameter). `kind` is informational
    ("clause" / "sentence" / "paragraph") and is recorded on
    the exemption so a fixture can pin which pause was exempted.
    """

    def __init__(self, kind: str, preserve_ms: float,
                 sample_rate: int = 24000):
        self.kind = kind
        self.preserve_ms = float(preserve_ms)
        self.sample_rate = int(sample_rate)

    def preserve_samples(self) -> int:
        return max(0, int(round(self.sample_rate
                                * self.preserve_ms / 1000.0)))


def trim_samples(samples: np.ndarray,
                 sample_rate: int = 24000,
                 threshold_dbfs: float = TRIM_THRESHOLD_DBFS,
                 pad_ms: float = TRIM_PAD_MS,
                 exempt: ProsodyPauseExemption | None = None
                 ) -> np.ndarray:
    """Trims near-silence from a chunk's edges only, keeping
    `pad_ms` of silence at each edge. Samples strictly above the
    threshold count as sound; everything between the first and
    last such sample is preserved byte-for-byte (internal silence
    untouched). A chunk with no above-threshold sample at all is
    returned unchanged — trimming a whole chunk of near-silence
    to nothing would delete audio the caller queued on purpose.
    With `exempt` (a ProsodyPauseExemption, v5.58 C-4), the
    kept edge silence is max(pad, the exemption's preserve_ms)
    at each edge, so a designed prosody pause survives trim."""
    arr = np.asarray(samples)
    if arr.size == 0:
        return arr
    thr = threshold_amplitude(threshold_dbfs)
    loud = np.abs(arr.astype(np.float64)) > thr
    if not bool(np.any(loud)):
        return arr
    first = int(np.argmax(loud))
    last = int(arr.size - 1 - np.argmax(loud[::-1]))
    pad = max(0, int(round(sample_rate * pad_ms / 1000.0)))
    if exempt is not None:
        pad = max(pad, exempt.preserve_samples())
    start = max(0, first - pad)
    end = min(arr.size, last + 1 + pad)
    if start == 0 and end == arr.size:
        return arr
    return arr[start:end]


def trim_pcm(pcm: bytes,
             sample_rate: int = 24000,
             threshold_dbfs: float = TRIM_THRESHOLD_DBFS,
             pad_ms: float = TRIM_PAD_MS,
             exempt: ProsodyPauseExemption | None = None) -> bytes:
    """`trim_samples` for int16 PCM bytes (dtype/levels preserved
    exactly — the result is a byte-slice of the input).
    `exempt` forwards the v5.58 prosody-pause exemption; None
    keeps byte-identical v5.51 behaviour."""
    if not pcm:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16)
    trimmed = trim_samples(samples, sample_rate=sample_rate,
                           threshold_dbfs=threshold_dbfs,
                           pad_ms=pad_ms, exempt=exempt)
    if trimmed.size == samples.size:
        return pcm
    return trimmed.tobytes()


def leading_silence_samples(samples: np.ndarray,
                            threshold_dbfs: float = TRIM_THRESHOLD_DBFS) -> int:
    """Count of leading samples at/below the silence threshold —
    the pause credit a chunk's head already supplies."""
    arr = np.asarray(samples, dtype=np.float64).ravel()
    if arr.size == 0:
        return 0
    quiet = np.abs(arr) <= threshold_amplitude(threshold_dbfs)
    if bool(np.all(quiet)):
        return int(arr.size)
    return int(np.argmax(~quiet))


def trailing_silence_samples(samples: np.ndarray,
                             threshold_dbfs: float = TRIM_THRESHOLD_DBFS) -> int:
    """Count of trailing samples at/below the silence threshold —
    the pause credit a chunk's tail already supplies."""
    arr = np.asarray(samples, dtype=np.float64).ravel()
    if arr.size == 0:
        return 0
    quiet = np.abs(arr) <= threshold_amplitude(threshold_dbfs)
    if bool(np.all(quiet)):
        return int(arr.size)
    return int(np.argmax(~quiet[::-1]))
