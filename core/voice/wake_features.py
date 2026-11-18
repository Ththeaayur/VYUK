"""Log-mel features for the custom wake-word pipeline (R7-6,
micro-wake-word pattern, own code).

micro-wake-word trains a tiny streaming model on mel spectrograms
of synthetic speech. This module is the feature half of VYUK's own
version of that pipeline: pure numpy + stdlib, no TensorFlow, no
torch, no librosa — the whole loop stays verifiable in the lab.

Shape (pinned — the model card records it, the loader refuses a
model trained under different features):
  * 16 kHz mono int16 PCM in, 32 log-mel bins out.
  * 25 ms window, 10 ms hop, Hann window, power spectrum, log1p.
  * One fixed window per clip: 80 frames (~0.8 s), centred;
    shorter clips are edge-padded, longer ones centre-cropped.
    A wake phrase is 2-4 words; 0.8 s covers it at normal pace,
    and a fixed window is what the streaming scorer consumes.

WAV handling is stdlib `wave` only. Non-16 kHz input is resampled
EXPLICITLY by linear interpolation (the meeting.py precedent —
asr.py refuses non-16 kHz input rather than silently resampling,
so every consumer states its resampling). 8/16/24/32-bit PCM is
accepted; anything else is an honest refusal, never a guess.
"""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
N_MELS = 32
WIN_MS = 25.0
HOP_MS = 10.0
WINDOW_FRAMES = 80  # 80 frames x 10 ms hop = ~0.8 s of speech
FEATURE_DIM = N_MELS * WINDOW_FRAMES  # 2560

# Mel scale bounds: speech energy lives here; the top bin ends
# below Nyquist (8 kHz) so the last filter is never empty.
_MEL_LOW_HZ = 80.0
_MEL_HIGH_HZ = 7600.0


def _hz_to_mel(hz: float) -> float:
    return 2595.0 * math.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: float) -> float:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _mel_filterbank(n_mels: int, n_fft: int, sr: int) -> np.ndarray:
    """(n_mels, n_fft // 2 + 1) triangular filters, numpy only."""
    low_mel = _hz_to_mel(_MEL_LOW_HZ)
    high_mel = _hz_to_mel(_MEL_HIGH_HZ)
    mel_points = np.linspace(low_mel, high_mel, n_mels + 2)
    hz_points = np.array([_mel_to_hz(m) for m in mel_points])
    # Bin index of each mel point: floor((n_fft + 1) * hz / sr).
    bin_points = np.floor((n_fft + 1) * hz_points / sr).astype(int)
    filters = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float64)
    for m in range(1, n_mels + 1):
        left, centre, right = bin_points[m - 1], bin_points[m], bin_points[m + 1]
        if centre > left:
            filters[m - 1, left:centre] = (
                np.arange(left, centre) - left) / max(centre - left, 1)
        if right > centre:
            filters[m - 1, centre:right] = (
                right - np.arange(centre, right)) / max(right - centre, 1)
    return filters


def resample_to_16k(pcm: np.ndarray, sr: int) -> np.ndarray:
    """Linear-interpolation resample of int16 mono PCM to 16 kHz.

    16 kHz input passes through untouched (same array values).
    Anything else is resampled EXPLICITLY — the caller never has
    to wonder whether a rate was silently assumed.
    """
    pcm = np.asarray(pcm, dtype=np.float64)
    if sr == SAMPLE_RATE or pcm.size == 0:
        return pcm.astype(np.int16) if pcm.dtype != np.int16 else pcm
    if pcm.size == 1:
        return np.zeros(1, dtype=np.int16)
    n_out = max(1, int(round(pcm.size * SAMPLE_RATE / sr)))
    src_idx = np.linspace(0, pcm.size - 1, n_out)
    out = np.interp(src_idx, np.arange(pcm.size), pcm)
    return np.clip(np.round(out), -32768, 32767).astype(np.int16)


def read_wav_16k(path: str | Path) -> np.ndarray:
    """Read a WAV file as 16 kHz mono int16 PCM.

    Refuses (ValueError) anything that is not PCM WAV, or whose
    channels/sample-width cannot be folded to mono int16 — a bad
    file is an honest refusal, never a silent reinterpretation.
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"no WAV file at {path}")
    with wave.open(str(path), "rb") as handle:
        n_channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        n_frames = handle.getnframes()
        raw = handle.readframes(n_frames)
        if handle.getcomptype() != "NONE":
            raise ValueError(f"{path.name}: compressed WAV is not supported")
    if width == 1:
        # 8-bit WAV is unsigned.
        pcm = (np.frombuffer(raw, dtype=np.uint8).astype(np.int32) - 128) * 256
    elif width == 2:
        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.int32)
    elif width == 3:
        raw3 = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        pcm = ((raw3[:, 2].astype(np.int32) << 16)
               | (raw3[:, 1].astype(np.int32) << 8)
               | raw3[:, 0].astype(np.int32))
        pcm = np.where(pcm >= 1 << 23, pcm - (1 << 24), pcm) // 256
    elif width == 4:
        pcm = (np.frombuffer(raw, dtype=np.int32) // 65536).astype(np.int32)
    else:
        raise ValueError(f"{path.name}: {width}-byte samples are not supported")
    if n_channels > 1:
        pcm = pcm.reshape(-1, n_channels).mean(axis=1)
    pcm = np.clip(np.round(pcm), -32768, 32767).astype(np.int16)
    return resample_to_16k(pcm, rate)


def write_wav_16k(path: str | Path, pcm: np.ndarray) -> Path:
    """Write 16 kHz mono int16 PCM as a WAV file (stdlib only)."""
    path = Path(path)
    pcm = np.asarray(pcm, dtype=np.int16).reshape(-1)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())
    return path


def log_mel(pcm: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """(n_frames, 32) log-mel features for 16 kHz mono int16 PCM.

    Deterministic: same samples in, same features out, on either
    interpreter — the prover pins this. Frames are 25 ms with a
    10 ms hop; a clip shorter than one window yields one frame
    (zero-padded); empty input yields zero frames.
    """
    pcm = np.asarray(pcm)
    if pcm.size == 0:
        return np.zeros((0, N_MELS), dtype=np.float32)
    if sr != SAMPLE_RATE:
        pcm = resample_to_16k(pcm, sr)
    samples = np.asarray(pcm, dtype=np.float64) / 32768.0
    win = int(SAMPLE_RATE * WIN_MS / 1000.0)  # 400
    hop = int(SAMPLE_RATE * HOP_MS / 1000.0)  # 160
    n_fft = 1
    while n_fft < win:
        n_fft *= 2  # 512
    filters = _mel_filterbank(N_MELS, n_fft, SAMPLE_RATE)
    window = np.hanning(win)
    if samples.size < win:
        padded = np.zeros(win, dtype=np.float64)
        padded[:samples.size] = samples
        frames = [padded]
    else:
        n_frames = 1 + (samples.size - win) // hop
        frames = [samples[i * hop:i * hop + win] for i in range(n_frames)]
    out = np.zeros((len(frames), N_MELS), dtype=np.float32)
    for i, frame in enumerate(frames):
        spectrum = np.abs(np.fft.rfft(frame * window, n=n_fft)) ** 2
        mel = filters @ spectrum
        out[i] = np.log1p(mel).astype(np.float32)
    return out


def fixed_window(features: np.ndarray) -> np.ndarray:
    """One (80, 32) window from (T, 32) features: centre-crop a
    long clip, edge-pad a short one. The training example and the
    streaming scorer consume exactly this shape."""
    feats = np.asarray(features, dtype=np.float32)
    if feats.shape[0] == 0:
        return np.zeros((WINDOW_FRAMES, N_MELS), dtype=np.float32)
    if feats.shape[0] >= WINDOW_FRAMES:
        start = (feats.shape[0] - WINDOW_FRAMES) // 2
        return feats[start:start + WINDOW_FRAMES].copy()
    pad = WINDOW_FRAMES - feats.shape[0]
    top = pad // 2
    bottom = pad - top
    return np.pad(feats, ((top, bottom), (0, 0)), mode="edge").copy()


def clip_to_vector(pcm: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """One flat (2560,) float32 vector per clip — the model input."""
    return fixed_window(log_mel(pcm, sr)).reshape(-1).astype(np.float32)
