"""Audio-to-MIDI transcription — a PORT of spotify/basic-pitch (R8-7).

Hum, sing, or play any instrument; get back polyphonic MIDI with
pitch bends. The tree queues this by name
(core/creative_midi_core.py:8); no audio-to-MIDI path existed.

NOT the basic-pitch package, NOT TensorFlow, NOT librosa: own code,
numpy + stdlib + the tree's already-declared onnxruntime (Qt only —
system python3 in this lab has no onnxruntime, so the transcription
refuses honestly there). The package's closure (librosa, mir-eval,
scipy, scikit-learn, pretty-midi, resampy, tensorflow) is the whole
reason for the port: the package hard-requires TensorFlow on Linux
Python >= 3.11, which the tree refuses.

THE MODEL: the upstream ICASSP-2022 ONNX model (nmp.onnx, 230,444 B,
Apache-2.0 — the repo LICENSE, read from the repo itself; the model
ships in the same repo under that licence). OPERATOR-SUPPLIED ONLY
in this batch (the R8-1 MiniLM precedent): the operator places it at
data/models/basic-pitch/nmp.onnx with the pinned sha256 below; it is
NEVER bundled in this batch — bundling is a future
model-distribution-gate decision. No basic-pitch dependency is added.

DIVERGENCE FROM UPSTREAM, MEASURED, NOT ASSUMED: upstream's
midi_pitch_to_contour_bin maps a frequency to contour bin
36*log2(f/27.5). On five pure tones (220.00, 261.63, 440.00, 523.25,
880.00 Hz) run through the real model, the contour peak sits
exactly ONE bin above that formula in every case (109 vs 108, 118
vs 117, 145 vs 144, 154 vs 153, 181 vs 180) — while the note bins
are exactly right (36/39/48/51/60). The model's contour bins are
offset +1 from upstream's formula, matching constants.py's own
note that 27.5 Hz is the CENTRAL bin of the first semitone (the
second bin at 3 bins per semitone). Upstream's pitch bends therefore
carry a systematic +1/3-semitone (+33 cents) bias. This port uses
the corrected mapping (formula + 1): a pure 440 Hz tone bends 0
(upstream's formula gives +1) and a +/-30-cent vibrato bends -1..+1
(upstream's formula gives 0..2).

One more divergence: get_infered_onsets guards the frame_diff
normalisation against a zero maximum (all-silent input) — upstream
divides by zero there and produces NaNs.

SCOPE, STATED HONESTLY: WAV input only (no ffmpeg binary ships in
the tree, so no MP3/FLAC); mono or stereo; any sample rate
(resampled to 22050 Hz by a Kaiser-windowed sinc in numpy);
2-second windows with 30-frame overlap, upstream-faithful.
No librosa analysis, no 8 GB pilot, no Spleeter weights, no
Tone.js samples — the queue's own words still bind.
"""

import hashlib
import math
import os
import wave
from pathlib import Path

import numpy as np
from core.funnel.tools_registry_core import _funnel_only_fn


# ---------------------------------------------------------------------------
# Constants (upstream basic_pitch/constants.py, Apache-2.0).
# ---------------------------------------------------------------------------
SAMPLE_RATE = 22050
FFT_HOP = 256
ANNOTATIONS_FPS = SAMPLE_RATE // FFT_HOP  # 86
AUDIO_WINDOW_LENGTH = 2  # seconds per model window
ANNOT_N_FRAMES = ANNOTATIONS_FPS * AUDIO_WINDOW_LENGTH  # 172
AUDIO_N_SAMPLES = SAMPLE_RATE * AUDIO_WINDOW_LENGTH - FFT_HOP  # 43844
ANNOTATIONS_BASE_FREQUENCY = 27.5  # lowest key on a piano
ANNOTATIONS_N_SEMITONES = 88
CONTOURS_BINS_PER_SEMITONE = 3
N_FREQ_BINS_CONTOURS = ANNOTATIONS_N_SEMITONES * CONTOURS_BINS_PER_SEMITONE  # 264
MIDI_OFFSET = 21
MAX_FREQ_IDX = 87
DEFAULT_OVERLAPPING_FRAMES = 30
ENERGY_TOLERANCE = 11
MAGIC_ALIGNMENT_OFFSET = 0.0018
PITCH_BEND_SCALE = 4096  # MIDI ticks per semitone of bend
N_PITCH_BEND_TICKS = 8192  # max bend offset: +/-2 semitones

DEFAULT_ONSET_THRESHOLD = 0.5
DEFAULT_FRAME_THRESHOLD = 0.3
DEFAULT_MIN_NOTE_LENGTH_MS = 127.7
DEFAULT_TEMPO_BPM = 120.0
MAX_AUDIO_SECONDS = 600  # 10 minutes: a transcription is a short take, not a set

# ---------------------------------------------------------------------------
# Model: operator-supplied only (R8-1 MiniLM precedent).
# ---------------------------------------------------------------------------
MODEL_DIR = Path(__file__).resolve().parent.parent / "data" / "models" / "basic-pitch"
MODEL_FILE = "nmp.onnx"
MODEL_SHA256 = "2c3c1d144bfa61ad236e92e169c13535c880469a12a047d4e73451f2c059a0ec"
MODEL_BYTES = 230444
MODEL_SOURCE_URL = (
    "https://github.com/spotify/basic-pitch/raw/main/"
    "basic_pitch/saved_models/icassp_2022/nmp.onnx"
)
MODEL_LICENSE = "Apache-2.0 (spotify/basic-pitch LICENSE, read from the repo)"


def model_path() -> Path:
    """Where the operator places the model."""
    return MODEL_DIR / MODEL_FILE


def _file_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def model_ready() -> bool:
    """Size floor AND hash-pin readiness, local_assets style: a
    same-size corrupted or substituted model must NOT count as
    ready."""
    path = model_path()
    try:
        if path.stat().st_size != MODEL_BYTES:
            return False
    except OSError:
        return False
    return _file_sha256(path) == MODEL_SHA256


def model_status() -> str:
    """Plain-words model + runtime status (read-only)."""
    path = model_path()
    try:
        import onnxruntime as ort

        ort_line = f"onnxruntime {ort.__version__} is available"
    except ImportError:
        ort_line = "onnxruntime is NOT available on this Python"
    if not path.exists():
        return (
            "The Basic Pitch model is not placed yet — the operator puts "
            f"nmp.onnx ({MODEL_BYTES} bytes, sha256 {MODEL_SHA256[:16]}…) at "
            f"{path} (from {MODEL_SOURCE_URL}). {ort_line}."
        )
    sha = _file_sha256(path)
    if sha != MODEL_SHA256:
        return (
            "The file at "
            f"{path} does not match the pinned sha256 — it is not the "
            f"Basic Pitch model, so it will not be used. {ort_line}."
        )
    return (
        "The Basic Pitch model is placed and verified "
        f"({MODEL_BYTES} bytes, sha256 matches). {ort_line}."
    )


# ---------------------------------------------------------------------------
# WAV reading (stdlib wave + numpy; PCM only — no ffmpeg in the tree).
# ---------------------------------------------------------------------------
def read_wav(path: str | os.PathLike) -> tuple[np.ndarray, int]:
    """Read a WAV file -> (float32 mono samples in [-1, 1], sample rate).

    PCM 8/16/24/32-bit, mono or stereo (mixed down). Anything else
    is refused with a plain-words reason, never a silent fallback.
    """
    try:
        with wave.open(str(path), "rb") as wf:
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            comptype = wf.getcomptype()
            raw = wf.readframes(n_frames)
    except (wave.Error, EOFError, OSError) as exc:
        raise ValueError(f"{path} is not a readable WAV file ({exc}).")
    if comptype != "NONE":
        raise ValueError(
            f"{path} is a compressed WAV ({comptype}) — only plain PCM WAV "
            "files can be transcribed here."
        )
    if sample_width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sample_width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_width == 3:
        packed = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        values = (
            packed[:, 0].astype(np.int32)
            | (packed[:, 1].astype(np.int32) << 8)
            | (packed[:, 2].astype(np.int32) << 16)
        )
        values = np.where(values >= (1 << 23), values - (1 << 24), values)
        data = values.astype(np.float32) / 8388608.0
    elif sample_width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(
            f"{path} has {sample_width * 8}-bit samples — only 8/16/24/32-bit "
            "PCM WAV files can be transcribed here."
        )
    if n_channels > 1:
        data = data.reshape(-1, n_channels).mean(axis=1)
    return np.ascontiguousarray(data, dtype=np.float32), sample_rate


# ---------------------------------------------------------------------------
# Resampling: Kaiser-windowed sinc in numpy (no resampy/scipy).
# ---------------------------------------------------------------------------
def _sinc(x: np.ndarray) -> np.ndarray:
    out = np.ones_like(x)
    nonzero = x != 0
    out[nonzero] = np.sin(np.pi * x[nonzero]) / (np.pi * x[nonzero])
    return out


def resample(samples: np.ndarray, sr_in: int, sr_out: int = SAMPLE_RATE) -> np.ndarray:
    """Resample mono float32 audio to sr_out.

    Windowed-sinc interpolation (Kaiser beta 5, 16 taps each side at
    the output rate, stretched for downsampling so content above the
    new Nyquist is attenuated, not aliased). The first and last few
    milliseconds ramp from the zero padding — the transcription
    windows overlap, so no note is lost to it.
    """
    samples = np.asarray(samples, dtype=np.float32)
    if sr_in == sr_out:
        return samples
    if sr_in <= 0 or sr_out <= 0:
        raise ValueError("sample rates must be positive")
    ratio = sr_out / sr_in
    cutoff = min(1.0, ratio)
    half = 16.0 / cutoff  # half-width, in input samples
    taps = int(math.ceil(half)) * 2 + 1
    beta = 5.0
    n_out = int(round(len(samples) * ratio))
    if n_out == 0:
        return np.zeros(0, dtype=np.float32)
    padded = np.concatenate([
        np.zeros(int(math.ceil(half)), dtype=np.float32),
        samples,
        np.zeros(int(math.ceil(half)), dtype=np.float32),
    ])
    pad = int(math.ceil(half))
    out = np.empty(n_out, dtype=np.float32)
    block = 8192
    offsets = np.arange(taps) - taps // 2
    for start in range(0, n_out, block):
        stop = min(start + block, n_out)
        centres = np.arange(start, stop, dtype=np.float64) / ratio + pad
        base = np.floor(centres).astype(np.int64)
        frac = centres - base
        # tap positions relative to each centre, in input samples
        rel = offsets[None, :] - frac[:, None]
        kernel = _sinc(rel * cutoff) * np.i0(
            beta * np.sqrt(np.maximum(0.0, 1.0 - (rel / half) ** 2))
        ) / np.i0(beta)
        kernel *= (np.abs(rel) <= half)
        kernel /= kernel.sum(axis=1, keepdims=True)
        idx = base[:, None] + offsets[None, :]
        idx = np.clip(idx, 0, len(padded) - 1)
        out[start:stop] = (padded[idx] * kernel).sum(axis=1)
    return out.astype(np.float32)


# ---------------------------------------------------------------------------
# Model inference (upstream inference.py, reimplemented in numpy).
# ---------------------------------------------------------------------------
_session = None


def _load_session():
    """The ONNX session, loaded once per process. Raises an honest
    RuntimeError when the model or the runtime is absent — the
    caller turns it into a plain-words refusal, never a crash."""
    global _session
    if _session is not None:
        return _session
    try:
        import onnxruntime as ort
    except ImportError:
        raise RuntimeError(
            "onnxruntime is not available on this Python — transcription "
            "runs on the Qt runtime, which declares it."
        )
    if not model_ready():
        raise RuntimeError(
            "The Basic Pitch model is not placed or does not match its "
            f"pinned sha256 — the operator puts nmp.onnx at {model_path()}."
        )
    _session = ort.InferenceSession(str(model_path()), providers=["CPUExecutionProvider"])
    return _session


def _window_audio(audio: np.ndarray, hop_size: int):
    for i in range(0, audio.shape[0], hop_size):
        window = audio[i:i + AUDIO_N_SAMPLES]
        if len(window) < AUDIO_N_SAMPLES:
            window = np.pad(window, (0, AUDIO_N_SAMPLES - len(window)))
        yield np.expand_dims(window, axis=-1), float(i) / SAMPLE_RATE


def _unwrap_output(output, audio_original_length, n_overlapping_frames, hop_size):
    if len(output.shape) != 3:
        return None
    n_olap = int(0.5 * n_overlapping_frames)
    if n_olap > 0:
        output = output[:, n_olap:-n_olap, :]
    shape = output.shape
    unwrapped = output.reshape(shape[0] * shape[1], shape[2])
    n_expected_windows = audio_original_length / hop_size
    n_frames_per_window = (AUDIO_WINDOW_LENGTH * ANNOTATIONS_FPS) - n_overlapping_frames
    return unwrapped[: int(n_expected_windows * n_frames_per_window), :]


def run_inference(audio: np.ndarray, session) -> dict:
    """Run the model over windowed audio -> unwrapped posteriorgrams
    {'note': (T, 88), 'onset': (T, 88), 'contour': (T, 264)}."""
    n_overlapping_frames = DEFAULT_OVERLAPPING_FRAMES
    overlap_len = n_overlapping_frames * FFT_HOP
    hop_size = AUDIO_N_SAMPLES - overlap_len
    original_length = audio.shape[0]
    audio = np.concatenate(
        [np.zeros((overlap_len // 2,), dtype=np.float32), audio]
    )
    output = {"note": [], "onset": [], "contour": []}
    for window, _t in _window_audio(audio, hop_size):
        x = np.expand_dims(window, axis=0).astype(np.float32)
        note, onset, contour = session.run(
            ["StatefulPartitionedCall:1",
             "StatefulPartitionedCall:2",
             "StatefulPartitionedCall:0"],
            {"serving_default_input_2:0": x},
        )
        output["note"].append(note)
        output["onset"].append(onset)
        output["contour"].append(contour)
    return {
        k: _unwrap_output(np.concatenate(output[k]), original_length,
                          n_overlapping_frames, hop_size)
        for k in output
    }


# ---------------------------------------------------------------------------
# Post-processing (upstream note_creation.py, reimplemented in numpy).
# ---------------------------------------------------------------------------
def midi_to_hz(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def hz_to_midi(hz: float) -> float:
    return 69 + 12.0 * math.log2(hz / 440.0)


def _argrelmax_0(a: np.ndarray):
    """scipy.signal.argrelmax(a, axis=0), default order=1, mode='clip':
    strictly greater than both neighbours along time; an edge frame
    compares against the single neighbour that exists."""
    prev = np.vstack([a[0:1, :], a[:-1, :]])
    nxt = np.vstack([a[1:, :], a[-1:, :]])
    return np.where((a > prev) & (a > nxt))


def _gaussian(length: int, std: float) -> np.ndarray:
    n = np.arange(length) - (length - 1) / 2.0
    return np.exp(-0.5 * (n / std) ** 2)


def get_infered_onsets(onsets, frames, n_diff=2):
    diffs = []
    for n in range(1, n_diff + 1):
        frames_appended = np.concatenate([np.zeros((n, frames.shape[1])), frames])
        diffs.append(frames_appended[n:, :] - frames_appended[:-n, :])
    frame_diff = np.min(diffs, axis=0)
    frame_diff[frame_diff < 0] = 0
    frame_diff[:n_diff, :] = 0
    # DIVERGENCE from upstream: for all-silent input max(frame_diff)
    # is 0 and upstream divides by it (NaNs). A zero maximum means
    # there is no frame movement to infer onsets from.
    max_diff = np.max(frame_diff)
    if max_diff > 0:
        frame_diff = np.max(onsets) * frame_diff / max_diff
    else:
        frame_diff = np.zeros_like(frame_diff)
    return np.max([onsets, frame_diff], axis=0)


def constrain_frequency(onsets, frames, max_freq, min_freq):
    n_freqs = onsets.shape[1]
    min_freq_idx = 0
    max_freq_idx = n_freqs
    if min_freq is not None:
        min_freq_idx = int(round(hz_to_midi(min_freq) - MIDI_OFFSET))
    if max_freq is not None:
        max_freq_idx = int(round(hz_to_midi(max_freq) - MIDI_OFFSET))
    onsets[:, :min_freq_idx] = 0
    frames[:, :min_freq_idx] = 0
    onsets[:, max_freq_idx:] = 0
    frames[:, max_freq_idx:] = 0
    return onsets, frames


def output_to_notes_polyphonic(frames, onsets, onset_thresh=DEFAULT_ONSET_THRESHOLD,
                               frame_thresh=DEFAULT_FRAME_THRESHOLD,
                               min_note_len=11, infer_onsets=True,
                               max_freq=None, min_freq=None,
                               melodia_trick=True, energy_tol=ENERGY_TOLERANCE):
    n_frames = frames.shape[0]
    onsets, frames = constrain_frequency(onsets, frames, max_freq, min_freq)
    if infer_onsets:
        onsets = get_infered_onsets(onsets, frames)
    peak_thresh_mat = np.zeros(onsets.shape)
    peaks = _argrelmax_0(onsets)
    peak_thresh_mat[peaks] = onsets[peaks]
    onset_idx = np.where(peak_thresh_mat >= onset_thresh)
    onset_time_idx = onset_idx[0][::-1]
    onset_freq_idx = onset_idx[1][::-1]
    remaining_energy = np.zeros(frames.shape)
    remaining_energy[:, :] = frames[:, :]
    note_events = []
    for note_start_idx, freq_idx in zip(onset_time_idx, onset_freq_idx):
        if note_start_idx >= n_frames - 1:
            continue
        i = note_start_idx + 1
        k = 0
        while i < n_frames - 1 and k < energy_tol:
            if remaining_energy[i, freq_idx] < frame_thresh:
                k += 1
            else:
                k = 0
            i += 1
        i -= k
        if i - note_start_idx <= min_note_len:
            continue
        remaining_energy[note_start_idx:i, freq_idx] = 0
        if freq_idx < MAX_FREQ_IDX:
            remaining_energy[note_start_idx:i, freq_idx + 1] = 0
        if freq_idx > 0:
            remaining_energy[note_start_idx:i, freq_idx - 1] = 0
        amplitude = float(np.mean(frames[note_start_idx:i, freq_idx]))
        note_events.append((note_start_idx, i, freq_idx + MIDI_OFFSET, amplitude))
    if melodia_trick:
        energy_shape = remaining_energy.shape
        while np.max(remaining_energy) > frame_thresh:
            i_mid, freq_idx = np.unravel_index(
                np.argmax(remaining_energy), energy_shape)
            remaining_energy[i_mid, freq_idx] = 0
            i = i_mid + 1
            k = 0
            while i < n_frames - 1 and k < energy_tol:
                if remaining_energy[i, freq_idx] < frame_thresh:
                    k += 1
                else:
                    k = 0
                remaining_energy[i, freq_idx] = 0
                if freq_idx < MAX_FREQ_IDX:
                    remaining_energy[i, freq_idx + 1] = 0
                if freq_idx > 0:
                    remaining_energy[i, freq_idx - 1] = 0
                i += 1
            i_end = i - 1 - k
            i = i_mid - 1
            k = 0
            while i > 0 and k < energy_tol:
                if remaining_energy[i, freq_idx] < frame_thresh:
                    k += 1
                else:
                    k = 0
                remaining_energy[i, freq_idx] = 0
                if freq_idx < MAX_FREQ_IDX:
                    remaining_energy[i, freq_idx + 1] = 0
                if freq_idx > 0:
                    remaining_energy[i, freq_idx - 1] = 0
                i -= 1
            i_start = i + 1 + k
            assert i_start >= 0
            assert i_end < n_frames
            if i_end - i_start <= min_note_len:
                continue
            amplitude = float(np.mean(frames[i_start:i_end, freq_idx]))
            note_events.append((i_start, i_end, freq_idx + MIDI_OFFSET, amplitude))
    return note_events


def midi_pitch_to_contour_bin(pitch_midi: int) -> float:
    """Contour-bin index for a MIDI pitch — CORRECTED +1 vs
    upstream's midi_pitch_to_contour_bin (see the module docstring:
    five pure tones put the model's contour peak exactly one bin
    above upstream's formula in every case)."""
    return (
        12.0 * CONTOURS_BINS_PER_SEMITONE
        * math.log2(midi_to_hz(pitch_midi) / ANNOTATIONS_BASE_FREQUENCY)
        + 1.0
    )


def get_pitch_bends(contours, note_events, n_bins_tolerance=25):
    window_length = n_bins_tolerance * 2 + 1
    freq_gaussian = _gaussian(window_length, std=5)
    out = []
    for start_idx, end_idx, pitch_midi, amplitude in note_events:
        freq_idx = int(round(midi_pitch_to_contour_bin(pitch_midi)))
        freq_start_idx = max(freq_idx - n_bins_tolerance, 0)
        freq_end_idx = min(N_FREQ_BINS_CONTOURS, freq_idx + n_bins_tolerance + 1)
        sub = (
            contours[start_idx:end_idx, freq_start_idx:freq_end_idx]
            * freq_gaussian[
                max(0, n_bins_tolerance - freq_idx): window_length
                - max(0, freq_idx - (N_FREQ_BINS_CONTOURS - n_bins_tolerance - 1))
            ]
        )
        pb_shift = n_bins_tolerance - max(0, n_bins_tolerance - freq_idx)
        bends = list(np.argmax(sub, axis=1) - pb_shift)  # units: 1/3 semitone
        out.append((start_idx, end_idx, pitch_midi, amplitude, bends))
    return out


def model_frames_to_time(n_frames: int) -> np.ndarray:
    original_times = np.arange(n_frames) * FFT_HOP / SAMPLE_RATE
    window_numbers = np.floor(np.arange(n_frames) / ANNOT_N_FRAMES)
    window_offset = (FFT_HOP / SAMPLE_RATE) * (
        ANNOT_N_FRAMES - (AUDIO_N_SAMPLES / FFT_HOP)
    ) + MAGIC_ALIGNMENT_OFFSET
    return original_times - (window_offset * window_numbers)


def model_output_to_notes(output, onset_thresh=DEFAULT_ONSET_THRESHOLD,
                          frame_thresh=DEFAULT_FRAME_THRESHOLD,
                          min_note_len=11, min_freq=None, max_freq=None):
    """Posteriorgrams -> note events: (start_s, end_s, pitch_midi,
    amplitude 0-1, pitch_bends in 1/3-semitone units)."""
    frames, onsets, contours = output["note"], output["onset"], output["contour"]
    estimated = output_to_notes_polyphonic(
        frames, onsets, onset_thresh=onset_thresh, frame_thresh=frame_thresh,
        min_note_len=min_note_len, min_freq=min_freq, max_freq=max_freq)
    with_bends = get_pitch_bends(contours, estimated)
    times_s = model_frames_to_time(contours.shape[0])
    return [(float(times_s[s]), float(times_s[e]), p, a, b)
            for s, e, p, a, b in with_bends]


# ---------------------------------------------------------------------------
# Transcription driver + MIDI writing.
# ---------------------------------------------------------------------------
def _non_clobbering(path: Path) -> Path:
    """An existing file is never overwritten: ' (2)', ' (3)', …"""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 2
    while True:
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def transcribe(audio_path: str | os.PathLike, session=None,
               onset_threshold: float = DEFAULT_ONSET_THRESHOLD,
               frame_threshold: float = DEFAULT_FRAME_THRESHOLD,
               min_note_length_ms: float = DEFAULT_MIN_NOTE_LENGTH_MS,
               min_freq=None, max_freq=None):
    """Transcribe a WAV file -> note events.

    Raises ValueError (bad audio) or RuntimeError (model/runtime
    absent) with plain-words reasons. The caller turns them into
    the funnel reply; nothing here raises into the registry.
    """
    samples, sr = read_wav(audio_path)
    if len(samples) == 0:
        raise ValueError(f"{audio_path} has no audio in it.")
    if len(samples) / sr > MAX_AUDIO_SECONDS:
        raise ValueError(
            f"{audio_path} is {len(samples) / sr:.0f} seconds long — "
            f"transcription covers up to {MAX_AUDIO_SECONDS // 60} minutes."
        )
    if sr != SAMPLE_RATE:
        samples = resample(samples, sr, SAMPLE_RATE)
    if session is None:
        session = _load_session()
    output = run_inference(samples, session)
    min_note_len = int(round(min_note_length_ms / 1000 * (SAMPLE_RATE / FFT_HOP)))
    return model_output_to_notes(
        output, onset_thresh=onset_threshold, frame_thresh=frame_threshold,
        min_note_len=min_note_len, min_freq=min_freq, max_freq=max_freq)


def audio_to_midi(audio_path: str = "", output: str = "",
                  onset_threshold: float = DEFAULT_ONSET_THRESHOLD,
                  frame_threshold: float = DEFAULT_FRAME_THRESHOLD,
                  min_note_length_ms: float = DEFAULT_MIN_NOTE_LENGTH_MS,
                  tempo_bpm: float = DEFAULT_TEMPO_BPM) -> str:
    """Transcribe a WAV file to a MIDI file, in plain words."""
    from .creative_midi_core import midi_to_note_name, read_midi, write_midi

    if not audio_path:
        return "Give the WAV file to transcribe — a path to a .wav file."
    src = Path(os.path.expanduser(str(audio_path)))
    if not src.exists():
        return f"I couldn't find {audio_path} — check the path and try again."
    if src.suffix.lower() != ".wav":
        return (
            f"{src.name} is not a WAV file — only plain PCM WAV files can "
            "be transcribed here (no ffmpeg ships with VYUK)."
        )
    if not (0.05 <= onset_threshold <= 0.95):
        return "The onset threshold must be between 0.05 and 0.95."
    if not (0.05 <= frame_threshold <= 0.95):
        return "The frame threshold must be between 0.05 and 0.95."
    if not (10.0 <= min_note_length_ms <= 2000.0):
        return "The minimum note length must be between 10 and 2000 ms."
    if not (40.0 <= tempo_bpm <= 240.0):
        return "The tempo must be between 40 and 240 BPM."
    try:
        notes = transcribe(
            src, onset_threshold=onset_threshold, frame_threshold=frame_threshold,
            min_note_length_ms=min_note_length_ms)
    except ValueError as exc:
        return str(exc)
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 — ONNX runtime failures only
        # The model file is sha256-pinned, so a corrupt model cannot
        # reach the session; what can still fail here is the runtime
        # itself (environment, memory). Those become a plain-words
        # refusal; anything else is a real bug and propagates.
        if type(exc).__module__.startswith("onnxruntime"):
            return f"The transcription failed inside the model runtime ({exc})."
        raise
    division = 480
    ticks_per_second = (tempo_bpm / 60.0) * division
    midi_notes = []
    for start_s, end_s, pitch, amplitude, bends in notes:
        start_tick = int(round(start_s * ticks_per_second))
        duration = max(1, int(round((end_s - start_s) * ticks_per_second)))
        bend_events = []
        if bends:
            bend_times = np.linspace(start_s, end_s, len(bends))
            for bend_time, bend_units in zip(bend_times, bends):
                offset = int(round(bend_units * PITCH_BEND_SCALE
                                   / CONTOURS_BINS_PER_SEMITONE))
                offset = max(-N_PITCH_BEND_TICKS,
                             min(N_PITCH_BEND_TICKS - 1, offset))
                bend_events.append((int(round(bend_time * ticks_per_second)),
                                    8192 + offset))
        midi_notes.append({
            "pitch": pitch,
            "start": start_tick,
            "duration": duration,
            "velocity": max(1, min(127, int(round(127 * amplitude)))),
            "bends": bend_events,
        })
    midi_bytes = write_midi(midi_notes, division=division, tempo_bpm=tempo_bpm)
    if output:
        out_path = Path(os.path.expanduser(str(output)))
        if out_path.suffix.lower() != ".mid":
            out_path = out_path.with_suffix(".mid")
    else:
        music_dir = Path.home() / "Music" / "Nova"
        out_path = music_dir / f"{src.stem}_basic_pitch.mid"
    out_path = _non_clobbering(out_path)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(midi_bytes)
    except OSError as exc:
        return f"Couldn't save the MIDI to {out_path}: {exc}"
    # A4-filestat: the written MIDI is re-read before the success
    # line names it — a write that did not land is never claimed.
    try:
        back = read_midi(out_path.read_bytes())
    except (OSError, ValueError) as exc:
        return f"The MIDI file was written but could not be read back ({exc})."
    if len(back["notes"]) != len(midi_notes):
        return (
            f"The MIDI file was written but the read-back found "
            f"{len(back['notes'])} notes, not {len(midi_notes)} — "
            "something went wrong with the write."
        )
    if not notes:
        return (
            f"No notes came out of {src.name} — the model heard nothing "
            f"it could turn into notes. An empty MIDI file was still "
            f"written to {out_path}."
        )
    shown = ", ".join(
        f"{midi_to_note_name(p)} at {s:.1f}s"
        for s, _e, p, _a, _b in sorted(notes)[:8]
    )
    more = f" (and {len(notes) - 8} more)" if len(notes) > 8 else ""
    return (
        f"Transcribed {src.name}: {len(notes)} notes, saved to {out_path}. "
        f"{shown}{more}."
    )


def audio_to_midi_status() -> str:
    """Plain-words model + runtime status (read-only)."""
    return model_status()


# ---------------------------------------------------------------------------
# Funnel handlers + registration.
# ---------------------------------------------------------------------------
@_funnel_only_fn
def _tool_audio_to_midi(audio_path: str = "", output: str = "",
                        onset_threshold: float = DEFAULT_ONSET_THRESHOLD,
                        frame_threshold: float = DEFAULT_FRAME_THRESHOLD,
                        min_note_length_ms: float = DEFAULT_MIN_NOTE_LENGTH_MS,
                        tempo_bpm: float = DEFAULT_TEMPO_BPM) -> str:
    """Funnel handler: transcribe audio to MIDI, in plain words."""
    return audio_to_midi(
        audio_path=audio_path, output=output,
        onset_threshold=onset_threshold, frame_threshold=frame_threshold,
        min_note_length_ms=min_note_length_ms, tempo_bpm=tempo_bpm)


@_funnel_only_fn
def _tool_audio_to_midi_status() -> str:
    """Funnel handler: model + runtime status."""
    return audio_to_midi_status()


def register_all(registry) -> None:
    """Register the basic-pitch pack (R8-7, basic-pitch port)."""
    registry.register(
        name="audio_to_midi",
        description="Turn a hummed tune, a sung line, or a played instrument into a MIDI file: give a WAV file and get back polyphonic notes with pitch bends, saved as a .mid file. Runs the Basic Pitch model on this machine (no network, no cloud) — the model file is placed by the operator, so the first run says so plainly if it is not there yet. Only plain PCM WAV files work (no MP3 — no ffmpeg ships with VYUK). Use when the user hums, sings, or plays something and wants the notes as MIDI.",
        parameters={
            "type": "object",
            "properties": {
                "audio_path": {"type": "string",
                               "description": "The WAV file to transcribe (full path)"},
                "output": {"type": "string",
                           "description": "Where to save the .mid file (optional; defaults to ~/Music/Nova/<name>_basic_pitch.mid)"},
                "onset_threshold": {"type": "number",
                                    "description": "How strong an onset must be to start a note (0.05-0.95, default 0.5; lower finds more notes, higher finds fewer)"},
                "frame_threshold": {"type": "number",
                                    "description": "How strong a frame must be for a note to keep sounding (0.05-0.95, default 0.3)"},
                "min_note_length_ms": {"type": "number",
                                       "description": "The shortest note to keep, in milliseconds (10-2000, default 127.7)"},
                "tempo_bpm": {"type": "number",
                              "description": "The tempo written into the MIDI file (40-240 BPM, default 120)"},
            },
            "required": ["audio_path"],
        },
        handler=_tool_audio_to_midi,
    )
    registry.register(
        name="audio_to_midi_status",
        description="Whether the audio-to-MIDI model is placed and verified, and whether the runtime that runs it is available. Pure read — nothing is started, stopped or changed. Use when the user asks if humming-to-MIDI is ready or why a transcription refused.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_tool_audio_to_midi_status,
    )
