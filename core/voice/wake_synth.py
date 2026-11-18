"""Synthetic training data for a custom wake word (R7-6,
micro-wake-word pattern, own code).

micro-wake-word's trick is training from synthetic speech: no
real audio collection, the phrase rendered by a TTS engine in
many voices. VYUK's own version renders the phrase through the
tree's own Kokoro child (core/desktop/kokoro_voice.py — the
B-R1-3r boundary, the ONLY place Kokoro may run) when the voice
is provisioned, and takes operator-supplied WAV files otherwise.
Both legs write the same dataset shape:

  dataset/<slug>/
    positives/*.wav   — the phrase, one render per file
    negatives/*.wav   — everything that is NOT the phrase
    manifest.json     — phrase, counts, provenance, voices

The negative bank is built in: a dozen everyday phrases that
are not the wake phrase ("what time is it", "play some music",
...), rendered in the same voices, plus a few seconds of
low-level noise. A model trained only on the phrase would fire
on anything loud; the negatives are what make it a detector.

Bars (mirroring the enrolment bar in core/wake_enrolment.py):
at least 5 positive utterances and at least 8 negatives, or the
dataset is refused — fewer than that, and the phrase has no
evidence base at all. The Kokoro leg is honest about what it
cannot do: when the engine or the model is not provisioned it
reports "unavailable" with the reason, never a silent empty
dataset. In the lab the Kokoro model is not on disk, so the
WAV-file leg is the testable one — fixtures are numpy-written
WAVs, deterministic, no TTS needed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .wake_features import SAMPLE_RATE, read_wav_16k, write_wav_16k
from ..wake_enrolment import MIN_ENROLMENT_UTTERANCES

MIN_POSITIVES = MIN_ENROLMENT_UTTERANCES  # 5, the enrolment bar
MIN_NEGATIVES = 8

# Everyday phrases that are NOT the wake phrase. Rendered in the
# same voices as the positives, they are the synthetic negatives
# — the model learns the phrase's shape, not "any speech".
NEGATIVE_PHRASES = (
    "what time is it",
    "play some music",
    "open the browser",
    "set a timer for ten minutes",
    "how is the weather today",
    "send an email to mum",
    "turn off the lights",
    "what is on my calendar",
    "read my messages",
    "take a screenshot",
    "remind me tomorrow morning",
    "how do you spell this",
)

# Default voices for synthesis: two female, two male US English
# Kokoro voices. A voice the engine does not know fails that
# render honestly; the dataset reports which voices rendered.
DEFAULT_VOICES = ("af_heart", "af_bella", "am_adam", "am_michael")
DEFAULT_LANGUAGE = "en-us"


def _slug(phrase: str) -> str:
    return "_".join(phrase.strip().lower().split())


def _noise_clip(seconds: float, *, seed: int, level: float = 0.02) -> np.ndarray:
    """Deterministic low-level noise — a negative that is not
    speech at all, so the model also learns quiet != wake."""
    rng = np.random.default_rng(seed)
    n = max(1, int(seconds * SAMPLE_RATE))
    return (rng.normal(0.0, level, n) * 32767).astype(np.int16)


def synthesize_text(text: str, *, voice: str,
                    language: str = DEFAULT_LANGUAGE,
                    speed: float = 1.0) -> tuple[np.ndarray, str | None]:
    """Render one text through the Kokoro child. Returns
    (int16 PCM at 16 kHz, None) on success, or (empty, reason)
    on failure — the engine, the model, or the voice may all be
    absent, and each absence names itself.

    The child renders at 24 kHz; the PCM is resampled to 16 kHz
    explicitly (linear interpolation, wake_features).
    """
    try:
        from ..desktop.kokoro_voice import KokoroSpeaker
    except Exception as exc:  # noqa: BLE001 — no engine, honest refusal
        return np.zeros(0, dtype=np.int16), f"kokoro voice unavailable: {exc}"
    try:
        speaker = KokoroSpeaker()
        result = speaker.speak(text, voice=voice, language=language,
                               speed=speed)
    except Exception as exc:  # noqa: BLE001
        return np.zeros(0, dtype=np.int16), f"kokoro render failed: {exc}"
    if not result.get("ok"):
        return np.zeros(0, dtype=np.int16), (
            "kokoro render refused: "
            + str(result.get("reason") or "engine unavailable"))
    audio = result.get("audio")
    rate = result.get("sample_rate") or 24000
    if audio is None:
        return np.zeros(0, dtype=np.int16), "kokoro render returned no audio"
    pcm = np.asarray(audio)
    if pcm.dtype.kind == "f":
        pcm = np.clip(pcm, -1.0, 1.0)
        pcm = (pcm * 32767).astype(np.int16)
    else:
        pcm = pcm.astype(np.int16)
    pcm = pcm.reshape(-1)
    if int(rate) != SAMPLE_RATE:
        from .wake_features import resample_to_16k

        pcm = resample_to_16k(pcm, int(rate))
    if pcm.size == 0:
        return pcm, "kokoro render returned empty audio"
    return pcm, None


def engine_available() -> tuple[bool, str]:
    """(available, reason): whether the Kokoro child can render
    right now. The model lives under data/voices/kokoro/ and is
    operator-provisioned; in the lab it is absent, and this
    reports that plainly."""
    try:
        from ..desktop.kokoro_voice import engine_available as _available
        from ..desktop.kokoro_voice import engine_status
    except Exception as exc:  # noqa: BLE001
        return False, f"kokoro voice unavailable: {exc}"
    if not _available():
        try:
            status = engine_status()
            reason = status.get("reason") or "kokoro engine unavailable"
        except Exception:  # noqa: BLE001
            reason = "kokoro engine unavailable"
        return False, str(reason)
    return True, ""


def build_dataset(phrase: str, positives: list[np.ndarray],
                  negatives: list[np.ndarray], out_dir: str | Path,
                  *, provenance: str, voices: list[str] | None = None,
                  ) -> dict:
    """Write a dataset directory from in-memory PCM clips.

    Refuses when the bars are unmet (< 5 positives, < 8
    negatives) or when a clip is empty — a dataset that cannot
    train is refused, never written half-built. Returns the
    manifest dict; the manifest.json on disk is the record.
    """
    phrase = phrase.strip().lower()
    if not phrase:
        raise ValueError("phrase is empty")
    if len(positives) < MIN_POSITIVES:
        raise ValueError(
            f"need at least {MIN_POSITIVES} positive utterances, "
            f"got {len(positives)}")
    if len(negatives) < MIN_NEGATIVES:
        raise ValueError(
            f"need at least {MIN_NEGATIVES} negatives, got {len(negatives)}")
    for i, clip in enumerate(list(positives) + list(negatives)):
        if np.asarray(clip).size == 0:
            raise ValueError(f"clip {i} is empty audio")
    out_dir = Path(out_dir)
    pos_dir = out_dir / "positives"
    neg_dir = out_dir / "negatives"
    pos_dir.mkdir(parents=True, exist_ok=True)
    neg_dir.mkdir(parents=True, exist_ok=True)
    for i, clip in enumerate(positives):
        write_wav_16k(pos_dir / f"positive_{i:03d}.wav",
                      np.asarray(clip, dtype=np.int16))
    for i, clip in enumerate(negatives):
        write_wav_16k(neg_dir / f"negative_{i:03d}.wav",
                      np.asarray(clip, dtype=np.int16))
    manifest = {
        "phrase": phrase,
        "slug": _slug(phrase),
        "n_positive": len(positives),
        "n_negative": len(negatives),
        "provenance": provenance,
        "voices": list(voices or []),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def build_dataset_from_wavs(phrase: str, wav_dir: str | Path,
                            out_dir: str | Path) -> dict:
    """WAV-file leg: positives/*.wav + negatives/*.wav under
    wav_dir become a dataset. Operator-supplied recordings (or
    numpy fixtures in tests) — the leg that works when Kokoro
    is not provisioned. The bar is the same as the synth leg."""
    wav_dir = Path(wav_dir)
    pos_dir, neg_dir = wav_dir / "positives", wav_dir / "negatives"
    if not pos_dir.is_dir() or not neg_dir.is_dir():
        raise ValueError(
            f"{wav_dir} needs positives/ and negatives/ subfolders "
            "of .wav files")
    positives = [read_wav_16k(p) for p in sorted(pos_dir.glob("*.wav"))]
    negatives = [read_wav_16k(p) for p in sorted(neg_dir.glob("*.wav"))]
    return build_dataset(phrase, positives, negatives, out_dir,
                         provenance="wav-files")


def build_dataset_synthetic(phrase: str, *, voices: tuple[str, ...] = DEFAULT_VOICES,
                            renders_per_voice: int = 4,
                            negative_phrases: tuple[str, ...] = NEGATIVE_PHRASES,
                            out_dir: str | Path,
                            speed: float = 1.0) -> dict:
    """Kokoro leg: render the phrase in each voice, renders_per_
    voice times (speed jittered per render so the model does not
    learn one tempo), render the negative bank in the first two
    voices, add deterministic noise negatives. Returns the
    manifest; a `synthesis` key names every render that failed,
    so a half-synthetic dataset is visible, not hidden.

    Refuses outright when the engine is unavailable — the caller
    (the train tool) then says so and points at the WAV-file leg.
    """
    available, reason = engine_available()
    if not available:
        raise ValueError(f"synthetic rendering unavailable: {reason}")
    if renders_per_voice < 1:
        raise ValueError("renders_per_voice must be at least 1")
    positives: list[np.ndarray] = []
    failures: list[str] = []
    for voice in voices:
        for i in range(renders_per_voice):
            # Speed jitter (0.9 / 1.0 / 1.1 cycling) so the model
            # hears the phrase at more than one pace.
            pcm, err = synthesize_text(
                phrase, voice=voice,
                speed=(0.9, 1.0, 1.1)[i % 3] * speed)
            if err is not None:
                failures.append(f"{voice} render {i}: {err}")
                continue
            positives.append(pcm)
    negatives: list[np.ndarray] = []
    for text in negative_phrases:
        for voice in voices[:2]:
            pcm, err = synthesize_text(text, voice=voice, speed=1.0)
            if err is not None:
                failures.append(f"negative {text!r} ({voice}): {err}")
                continue
            negatives.append(pcm)
    for i in range(4):
        negatives.append(_noise_clip(1.0, seed=100 + i))
    manifest = build_dataset(phrase, positives, negatives, out_dir,
                             provenance="synthetic-kokoro",
                             voices=list(voices))
    manifest["synthesis_failures"] = failures
    (Path(out_dir) / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load_dataset(dataset_dir: str | Path) -> tuple[list[np.ndarray], list[np.ndarray], dict]:
    """(positives, negatives, manifest) for a dataset dir. The
    manifest must name the phrase; a dir without one is refused."""
    dataset_dir = Path(dataset_dir)
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"{dataset_dir} is not a dataset (no manifest.json)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    positives = [read_wav_16k(p) for p in
                 sorted((dataset_dir / "positives").glob("*.wav"))]
    negatives = [read_wav_16k(p) for p in
                 sorted((dataset_dir / "negatives").glob("*.wav"))]
    return positives, negatives, manifest
