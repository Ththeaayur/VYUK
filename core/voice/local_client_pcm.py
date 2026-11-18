# Batch 1 (plan §3.3): PCM/audio, moved verbatim from
# core/voice/local_client.py. INTENTIONAL: imports below are the
# subset of the old module's import block that this group's code uses.
import datetime
import numpy as np
from typing import Any  # INTENTIONAL (batch 1, prover-caught pre-test):
# _BLEND_CACHE AnnAssign + _parse_hhmm annotation use Any at def time.
from .. import loudness
from ..config import config
from . import mouth as _mouth_v558  # INTENTIONAL (batch 1): module-level
# _PAUSE_* assigns read _mouth_v558.* constants at import time.
from .local_client_chunking import (
    _BREAK_CLAUSE,
    _BREAK_PARAGRAPH,
    _BREAK_SENTENCE,
)  # INTENTIONAL (batch 1): chunking break constants used by group-2 code


_CROSSFADE_SAMPLES = max(1, int(config.sample_rate_out * 0.010))  # 10 ms


_BARGE_FADE_SAMPLES = max(1, int(config.sample_rate_out * 0.090))  # 90 ms


_LOUDNESS_TARGET_RMS = 0.14   # ≈ −17 dBFS — a consistent speech level


_LOUDNESS_MIN_GAIN = 0.7


_LOUDNESS_MAX_GAIN = 1.4


_LOUDNESS_KNEE = 0.9          # soft-ceiling knee (full-scale fraction)


def _pcm_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _float_to_pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


_PAUSE_CLAUSE_MS = _mouth_v558.CLAUSE_PAUSE_MS


_PAUSE_SENTENCE_MS = _mouth_v558.SENTENCE_PAUSE_MS


_PAUSE_PARAGRAPH_MS = _mouth_v558.PARAGRAPH_PAUSE_MS


_PAUSE_EDGE_MS = 5             # fade at each silence edge


def _pause_sample_count(break_strength: int) -> int:
    """Silence samples for a break strength at the consumer's
    PCM rate; 0 for _BREAK_NONE / unknown strengths."""
    if break_strength == _BREAK_CLAUSE:
        ms = _PAUSE_CLAUSE_MS
    elif break_strength == _BREAK_SENTENCE:
        ms = _PAUSE_SENTENCE_MS
    elif break_strength == _BREAK_PARAGRAPH:
        ms = _PAUSE_PARAGRAPH_MS
    else:
        return 0
    return int(config.sample_rate_out * ms / 1000)


def _pause_pcm(break_strength: int) -> bytes:
    """Pure shaped silence for a break (int16 zeros at
    config.sample_rate_out); empty bytes when no pause is due."""
    count = _pause_sample_count(break_strength)
    if count <= 0:
        return b""
    return b"\x00\x00" * count


def _fade_edge_pcm(pcm: bytes, fade_out: bool) -> bytes:
    """Fades one edge of a PCM block to/from zero over
    _PAUSE_EDGE_MS on a raised-cosine (flat-slope) curve — the
    de-click shaping at a silence boundary. Blocks shorter than
    the fade are faded over their second half at most."""
    if not pcm:
        return pcm
    samples = _pcm_to_float(pcm)
    edge = min(int(config.sample_rate_out * _PAUSE_EDGE_MS / 1000),
               samples.size // 2)
    if edge <= 0:
        return pcm
    ramp = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, edge)))
    if fade_out:
        samples[-edge:] = samples[-edge:] * ramp
    else:
        samples[:edge] = samples[:edge] * ramp[::-1]
    return _float_to_pcm(samples)


def _join_with_pause(tail_pcm: bytes | None, break_strength: int,
                     body_pcm: bytes,
                     pause_samples: int | None = None) -> bytes:
    """The playable PCM for a chunk that follows a sentence or
    paragraph break: the previous chunk's withheld tail (faded
    out), the shaped silence, then this chunk's body (faded in).
    Pure concatenation — the pause lives INSIDE this chunk's one
    playback call, so it dies with the chunk when the speech
    queue drains (see the section header).

    `pause_samples` (v5.51, M-3) overrides the designed pause
    length: the consumer passes the designed count minus the
    silence the neighbouring chunks' trimmed edges already
    supply, so the TOTAL inter-chunk silence still measures at
    the designed length. None (every pre-v5.51 caller) keeps the
    designed count exactly."""
    parts = []
    if tail_pcm:
        parts.append(_fade_edge_pcm(tail_pcm, fade_out=True))
    pause = (_pause_pcm(break_strength) if pause_samples is None
             else b"\x00\x00" * max(0, int(pause_samples)))
    if pause:
        parts.append(pause)
    if body_pcm:
        parts.append(_fade_edge_pcm(body_pcm, fade_out=False)
                     if pause else body_pcm)
    return b"".join(parts)


def _normalize_samples(samples: np.ndarray) -> np.ndarray:
    """Gains float samples toward _LOUDNESS_TARGET_RMS (gain clamped
    to 0.7–1.4×, silence untouched), then applies a soft ceiling: a
    tanh knee above 0.9 full scale, so peaks approach 1.0 but never
    clip. Returns the input unchanged when it is empty or silent."""
    if samples.size == 0:
        return samples
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    if rms < 1e-5:
        return samples
    gain = min(_LOUDNESS_MAX_GAIN,
               max(_LOUDNESS_MIN_GAIN, _LOUDNESS_TARGET_RMS / rms))
    out = samples * gain
    if float(np.max(np.abs(out))) > _LOUDNESS_KNEE:
        magnitude = np.abs(out)
        span = 1.0 - _LOUDNESS_KNEE
        over = magnitude > _LOUDNESS_KNEE
        magnitude = np.where(
            over,
            _LOUDNESS_KNEE + span * np.tanh((magnitude - _LOUDNESS_KNEE) / span),
            magnitude,
        )
        out = np.sign(out) * magnitude
    return out


def _normalize_pcm(pcm: bytes) -> bytes:
    """_normalize_samples for int16 PCM bytes."""
    if not pcm:
        return pcm
    return _float_to_pcm(_normalize_samples(_pcm_to_float(pcm)))


def _loudness_enabled() -> bool:
    """config.tts_loudness_enabled, garbage degrading to enabled
    (the shipped default) rather than to a crash or a mute."""
    return bool(getattr(config, "tts_loudness_enabled", True))


def _loudness_target_dbfs() -> float:
    """The configured RMS target, clamped to a sane speech band;
    garbage / NaN degrades to core.loudness' −18 dBFS default."""
    try:
        target = float(getattr(config, "tts_loudness_target_dbfs",
                               loudness.DEFAULT_TARGET_DBFS))
    except (TypeError, ValueError):
        return loudness.DEFAULT_TARGET_DBFS
    if target != target:  # NaN
        return loudness.DEFAULT_TARGET_DBFS
    return min(-9.0, max(-36.0, target))


def _utterance_loudness_pcm(chunks: list[bytes]) -> list[bytes]:
    """v5.51 M-2: the RMS loudness standard applied ONCE to an
    assembled utterance's chunks (one gain across all of them,
    soft-limited under a −1 dBFS peak ceiling — see
    core/loudness.py). Disabled ⇒ the chunks pass through
    unchanged, byte for byte."""
    if not chunks or not _loudness_enabled():
        return chunks
    return loudness.normalize_pcm_chunks(
        chunks, target_dbfs=_loudness_target_dbfs(), enabled=True)


def _trim_chunk_pcm(pcm: bytes,
                    prosody_exempt: bool = False,
                    prosody_kind: str = "clause") -> bytes:
    """v5.51 M-3: conservative silence-edge trim of ONE rendered
    chunk (see core/loudness.trim_pcm): near-silence below
    −45 dBFS is cut from the edges only, ~30 ms of pad is kept at
    each edge so onsets/tails are never clipped, and internal
    pauses are untouched. Applied per chunk BEFORE assembly in
    _synthesize_kokoro / _synthesize_piper (and on the whole-text
    Piper path); the assembly credits the retained pads against
    the designed breathing pauses (see _speak_chunked).

    v5.58 (Wave 3, plan §6 C-4) PROSODY TRIM EXEMPTION — a
    change to shipped behaviour, OPT-IN and NAMED: when
    `prosody_exempt` is True, the trim runs with a
    loudness.ProsodyPauseExemption (kind = `prosody_kind`,
    preserve_ms = the v5.58 prosody pause for that kind from
    core/voice/mouth.py), so a designed clause/sentence/
    paragraph pause baked into the chunk's edge survives trim
    instead of being cut to the 30 ms pad. Default False keeps
    byte-identical v5.51 behaviour at every existing call site
    (no existing caller passes True)."""
    if not pcm:
        return pcm
    if not prosody_exempt:
        return loudness.trim_pcm(pcm, sample_rate=config.sample_rate_out)
    from . import mouth  # Phase 3 V1b: moved to core/voice/

    exempt = loudness.ProsodyPauseExemption(
        kind=prosody_kind,
        preserve_ms=mouth.prosody_pause_ms(prosody_kind),
        sample_rate=config.sample_rate_out)
    return loudness.trim_pcm(pcm, sample_rate=config.sample_rate_out,
                             exempt=exempt)


def _pcm_edge_silence(pcm: bytes | None, from_end: bool) -> int:
    """How many samples of near-silence (at/below the M-3 trim
    threshold) a PCM block starts / ends with — the pause credit
    its edges already supply toward a designed breathing pause."""
    if not pcm:
        return 0
    samples = _pcm_to_float(pcm)
    if from_end:
        return loudness.trailing_silence_samples(samples)
    return loudness.leading_silence_samples(samples)


def _kokoro_speed() -> float:
    """The configured Kokoro pace, clamped to 0.8–1.2 at read time
    (garbage in config degrades to the natural rate, never a crash)."""
    try:
        speed = float(getattr(config, "kokoro_speed", 1.0))
    except (TypeError, ValueError):
        return 1.0
    if speed != speed:  # NaN
        return 1.0
    return min(1.2, max(0.8, speed))


_KOKORO_LANG_BY_PREFIX = {
    "a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
    "i": "it", "j": "ja", "p": "pt-br", "z": "cmn",
}


def kokoro_lang_for_voice(voice: str) -> str:
    """The kokoro-onnx synthesis language for a voice id."""
    prefix = str(voice or "")[:1].lower()
    return _KOKORO_LANG_BY_PREFIX.get(prefix, "en-us")


_BLEND_CACHE: dict[tuple, Any] = {}


def _blend_voice_tensor(voices, base: str, partner: str,
                        ratio: float):
    """The blended style tensor for (base, partner, ratio), or
    None when the blend is not honourable. Pure: no I/O, the
    inputs are never mutated."""
    try:
        ratio = float(ratio)
    except (TypeError, ValueError):
        return None
    if ratio != ratio or ratio <= 0.0 or ratio > 1.0:
        return None
    if not base or not partner or base == partner:
        return None
    try:
        base_arr = np.asarray(voices[base], dtype=np.float32)
        partner_arr = np.asarray(voices[partner], dtype=np.float32)
    except Exception:  # noqa: BLE001 — a missing voice is a fallback
        return None
    if base_arr.shape != partner_arr.shape or base_arr.size == 0:
        return None
    blended = (1.0 - ratio) * base_arr + ratio * partner_arr
    return blended.astype(np.float32, copy=False)


def _configured_blend() -> tuple[str, float] | None:
    """The (partner_voice, ratio) blend spec from config, or None
    when no blend is configured (the shipped default) or the
    values are garbage — garbage degrades to the pure named-voice
    path, never to a crash or a surprise voice."""
    partner = getattr(config, "kokoro_blend_voice", "") or ""
    if not isinstance(partner, str) or not partner.strip():
        return None
    try:
        ratio = float(getattr(config, "kokoro_blend_ratio", 0.0))
    except (TypeError, ValueError):
        return None
    if ratio != ratio or ratio <= 0.0 or ratio > 1.0:
        return None
    return (partner.strip(), ratio)


def _parse_hhmm(value: Any, fallback_minutes: int) -> int:
    """Minutes after midnight for an "HH:MM" string, or the
    fallback when the string is not a valid time of day."""
    try:
        hours_text, minutes_text = str(value).strip().split(":", 1)
        hours, minutes = int(hours_text), int(minutes_text)
    except (ValueError, AttributeError):
        return fallback_minutes
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return fallback_minutes
    return hours * 60 + minutes


def whisper_active_now(now: datetime.datetime | None = None) -> bool:
    """True when `now` (default: the current local time) falls in
    the whisper-hours window (M15). The window is
    [config.whisper_start, config.whisper_end) and may wrap
    midnight ("22:00" → "07:00"); the enabled flag
    (config.whisper_hours_enabled) is required, and a degenerate
    window (start == end) is never active. Garbage time strings
    degrade to the 22:00/07:00 defaults."""
    if not getattr(config, "whisper_hours_enabled", False):
        return False
    if now is None:
        now = datetime.datetime.now()
    current = now.hour * 60 + now.minute
    start = _parse_hhmm(getattr(config, "whisper_start", "22:00"), 22 * 60)
    end = _parse_hhmm(getattr(config, "whisper_end", "07:00"), 7 * 60)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _whisper_factor() -> float:
    """The whisper-hours volume scale, clamped to [0, 1]. Garbage
    degrades to 1.0 — no scaling — because a broken setting must
    never mute her outright."""
    try:
        factor = float(getattr(config, "whisper_factor", 0.5))
    except (TypeError, ValueError):
        return 1.0
    if factor != factor:  # NaN
        return 1.0
    return min(1.0, max(0.0, factor))
