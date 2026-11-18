from __future__ import annotations

import math
import re
from dataclasses import dataclass
import numpy as np

# ------------------------------------------------------------------ #
# MO-12 + MO-11 + MO-16 + MO-9: prosody stack
#
# phrasplit-style clause chunking (original code, same idea:
# split at clause punctuation / conjunctions / dandas, keep the
# delimiter with the segment it ends). Deterministic pauses:
# clause 200 ms (band 150–250), sentence 375 ms (300–450),
# paragraph 750 ms (600–900). Per-segment speed: emphasis spans
# 0.92 (the shipped emphasis pace), everything else 1.0.
# Brain-emitted SSMD-subset tags (MO-9, MIT kokoro profile
# convention): ONLY <break time="…ms"/>, <prosody rate="…"> and
# <emphasis> are honoured; every other tag is stripped, and
# break times are clamped into the bands above. MO-13/15 DSP
# re-normalises to -18 dBFS (see match_to_standard()); MO-21
# dynamic loudnorm is barred and absent.
# ------------------------------------------------------------------ #

CLAUSE_PAUSE_MS = 200
CLAUSE_PAUSE_MIN_MS = 150
CLAUSE_PAUSE_MAX_MS = 250
SENTENCE_PAUSE_MS = 375
SENTENCE_PAUSE_MIN_MS = 300
SENTENCE_PAUSE_MAX_MS = 450
PARAGRAPH_PAUSE_MS = 750
PARAGRAPH_PAUSE_MIN_MS = 600
PARAGRAPH_PAUSE_MAX_MS = 900
EMPHASIS_SPEED = 0.92
DEFAULT_SPEED = 1.0
LOUDNESS_TARGET_DBFS = -18.0

_CLAUSE_RE = re.compile(
    r"[,;:]\s+|।\s*|॥\s*|\s+[—–-]\s+"
    r"|\s+(?=(?:and|but|or|nor|which|while|because|although|though"
    r"|when|where|और|लेकिन|क्योंकि)\s)")
_SENTENCE_END_RE = re.compile(r"[.!?…]+\s+")
_SSMD_BREAK_RE = re.compile(
    r'<break\s+time="(\d+)ms"\s*/>', re.IGNORECASE)
_SSMD_PROSODY_RE = re.compile(
    r'<prosody\s+rate="([\d.]+)">(.*?)</prosody>',
    re.IGNORECASE | re.DOTALL)
_SSMD_EMPHASIS_RE = re.compile(
    r"<emphasis>(.*?)</emphasis>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def prosody_pause_ms(kind: str) -> int:
    """Deterministic pause for a break kind, inside its band."""
    if kind == "clause":
        return CLAUSE_PAUSE_MS
    if kind == "sentence":
        return SENTENCE_PAUSE_MS
    if kind == "paragraph":
        return PARAGRAPH_PAUSE_MS
    return 0


def clause_chunks(text: str) -> list[str]:
    """phrasplit-style clause segments of one sentence: split
    at commas/semicolons/colons, dandas (। ॥), dashes and
    conjunctions; delimiters stay with the segment they end.
    A text with no clause boundary is one segment."""
    text = " ".join((text or "").split())
    if not text:
        return []
    segments: list[str] = []
    start = 0
    for match in _CLAUSE_RE.finditer(text):
        piece = text[start:match.end()].strip()
        if piece:
            segments.append(piece)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        segments.append(tail)
    return segments or [text]


@dataclass
class ProsodySegment:
    text: str
    speed: float = DEFAULT_SPEED
    pause_after_ms: int = 0
    break_kind: str = "none"  # none | clause | sentence | paragraph
    emphasis: bool = False


def segment_plan(text: str) -> list[ProsodySegment]:
    """The prosody plan for a reply: paragraphs split, then
    sentences, then clauses. Clause joins get the clause pause,
    sentence ends the sentence pause, paragraph ends the
    paragraph pause; the final segment gets no trailing pause.
    Deterministic — the same text always yields the same plan."""
    paragraphs = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    if not paragraphs:
        return []
    plan: list[ProsodySegment] = []
    for pi, para in enumerate(paragraphs):
        sentences: list[str] = []
        start = 0
        for match in _SENTENCE_END_RE.finditer(para):
            piece = para[start:match.end()].strip()
            if piece:
                sentences.append(piece)
            start = match.end()
        tail = para[start:].strip()
        if tail:
            sentences.append(tail)
        for si, sentence in enumerate(sentences):
            clauses = clause_chunks(sentence)
            last_para = pi == len(paragraphs) - 1
            last_sent = si == len(sentences) - 1
            for ci, clause in enumerate(clauses):
                last_clause = ci == len(clauses) - 1
                if not last_clause:
                    kind, pause = "clause", CLAUSE_PAUSE_MS
                elif not last_sent:
                    kind, pause = "sentence", SENTENCE_PAUSE_MS
                elif not last_para:
                    kind, pause = "paragraph", PARAGRAPH_PAUSE_MS
                else:
                    kind, pause = "none", 0
                plan.append(ProsodySegment(
                    text=clause, speed=DEFAULT_SPEED,
                    pause_after_ms=pause, break_kind=kind))
    return plan


def parse_ssmd_tags(text: str) -> tuple[str, list[ProsodySegment]]:
    """Parses the brain-emitted SSMD SUBSET (MO-9) out of text.

    Honoured: <break time="Nms"/> (clamped into the clause /
    sentence / paragraph bands by nearest band — a break under
    300 ms is a clause break, under 600 ms a sentence break,
    else a paragraph break), <prosody rate="R"> (clamped to
    0.8–1.2, the shipped Kokoro pace band) and <emphasis>
    (speed 0.92). Everything else that looks like a tag is
    STRIPPED, never forwarded to an engine. Returns
    (clean_text, segments)."""
    segments: list[ProsodySegment] = []
    work = text or ""
    # Prosody spans first (they carry their own segment).
    for match in _SSMD_PROSODY_RE.finditer(work):
        try:
            rate = float(match.group(1))
        except ValueError:
            rate = DEFAULT_SPEED
        rate = min(1.2, max(0.8, rate))
        segments.append(ProsodySegment(
            text=match.group(2).strip(), speed=rate,
            pause_after_ms=0, break_kind="none"))
    work = _SSMD_PROSODY_RE.sub(lambda m: m.group(2), work)
    for match in _SSMD_EMPHASIS_RE.finditer(work):
        segments.append(ProsodySegment(
            text=match.group(1).strip(), speed=EMPHASIS_SPEED,
            pause_after_ms=0, break_kind="none", emphasis=True))
    work = _SSMD_EMPHASIS_RE.sub(lambda m: m.group(1), work)
    breaks: list[int] = []
    for match in _SSMD_BREAK_RE.finditer(work):
        ms = int(match.group(1))
        if ms < SENTENCE_PAUSE_MIN_MS:
            breaks.append(min(CLAUSE_PAUSE_MAX_MS,
                              max(CLAUSE_PAUSE_MIN_MS, ms)))
        elif ms < PARAGRAPH_PAUSE_MIN_MS:
            breaks.append(min(SENTENCE_PAUSE_MAX_MS,
                              max(SENTENCE_PAUSE_MIN_MS, ms)))
        else:
            breaks.append(min(PARAGRAPH_PAUSE_MAX_MS,
                              max(PARAGRAPH_PAUSE_MIN_MS, ms)))
    work = _SSMD_BREAK_RE.sub(" ", work)
    work = _TAG_RE.sub("", work)
    work = " ".join(work.split())
    if not segments:
        segments = segment_plan(work)
    return work, segments


def strip_ssmd_to_plan(text: str) -> tuple[str, list[ProsodySegment]]:
    """Alias for parse_ssmd_tags (the name the render path uses)."""
    return parse_ssmd_tags(text)


# --- DSP: joins + re-normalisation to -18 dBFS (MO-13/15) -------- #

def rms_dbfs(samples: np.ndarray) -> float:
    arr = np.asarray(samples, dtype=np.float64).ravel()
    if arr.size == 0:
        return float("-inf")
    rms = float(np.sqrt(np.mean(arr * arr)))
    return 20.0 * math.log10(rms) if rms > 0 else float("-inf")


def match_to_standard(pcm_chunks: list[bytes],
                      target_dbfs: float = LOUDNESS_TARGET_DBFS,
                      sample_rate: int = 24000) -> list[bytes]:
    """ONE RMS gain over a whole utterance (the shipped M-2
    rule, re-applied at the prosody render site): measure over
    the concatenation, apply one gain, soft-limit under -1 dBFS.
    This is NOT dynamic loudnorm (MO-21, barred): the gain is
    computed once per utterance, never per segment, never
    time-varying."""
    if not pcm_chunks:
        return pcm_chunks
    arrays = [np.frombuffer(c, dtype=np.int16).astype(np.float64) / 32768.0
              for c in pcm_chunks]
    joined = np.concatenate(arrays) if len(arrays) > 1 else arrays[0]
    measured = rms_dbfs(joined)
    if measured == float("-inf") or measured < -60.0:
        gain = 1.0
    else:
        gain_db = min(24.0, max(-24.0, target_dbfs - measured))
        gain = float(10.0 ** (gain_db / 20.0))
    out: list[bytes] = []
    for arr in arrays:
        gained = arr * gain
        # Tanh soft knee above 0.85 * 0.8913 (the shipped shape).
        knee, ceiling = 0.85 * 0.8913, 0.8913
        mag = np.abs(gained)
        over = mag > knee
        if bool(np.any(over)):
            span = ceiling - knee
            gained = np.sign(gained) * np.where(
                over, knee + span * np.tanh((mag - knee) / span), mag)
        out.append((np.clip(gained, -1.0, 1.0) * 32767
                    ).astype(np.int16).tobytes())
    return out


def crossfade_join(a_pcm: bytes, b_pcm: bytes,
                   fade_ms: float = 10.0,
                   sample_rate: int = 24000) -> bytes:
    """Equal-power crossfade of two PCM blocks (10 ms default,
    the shipped crossfade). Pure concatenation when either
    block is shorter than the fade."""
    a = np.frombuffer(a_pcm, dtype=np.int16).astype(np.float64) / 32768.0
    b = np.frombuffer(b_pcm, dtype=np.int16).astype(np.float64) / 32768.0
    n = min(int(sample_rate * fade_ms / 1000.0), a.size, b.size)
    if n <= 0:
        return a_pcm + b_pcm
    ramp = np.linspace(0.0, math.pi / 2.0, n)
    joined = np.concatenate([
        a[:-n], a[-n:] * np.cos(ramp) + b[:n] * np.sin(ramp), b[n:]])
    return (np.clip(joined, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def clause_boundary_join(a_pcm: bytes, b_pcm: bytes,
                         at_clause_boundary: bool,
                         sample_rate: int = 24000) -> bytes:
    """Joins two rendered segments: crossfaded ONLY at a clause
    boundary; mid-clause joins are plain concatenation (a
    crossfade inside a clause would smear a word)."""
    if at_clause_boundary:
        return crossfade_join(a_pcm, b_pcm, sample_rate=sample_rate)
    return a_pcm + b_pcm


def silence_pcm(ms: int, sample_rate: int = 24000) -> bytes:
    """Pure silence PCM for a designed prosody pause."""
    return b"\x00\x00" * max(0, int(sample_rate * ms / 1000))


def pyloudnorm_lufs(pcm: bytes,
                    sample_rate: int = 24000) -> float | None:
    """BS.1770 LUFS MEASUREMENT probe only, via pyloudnorm when
    installed — else None. This is a measurement, never the
    standard: adopting it as the standard would re-derive
    -18 dBFS, which this workstream explicitly does not do.
    Callers must treat None as 'unmeasured', never as 0."""
    try:
        import pyloudnorm as pyln
    except Exception:  # noqa: BLE001 — absence is the normal case
        return None
    try:
        meter = pyln.Meter(sample_rate)
        samples = np.frombuffer(pcm, dtype=np.int16).astype(
            np.float64) / 32768.0
        return float(meter.integrated_loudness(samples))
    except Exception:  # noqa: BLE001 — a measurement failure is None
        return None


