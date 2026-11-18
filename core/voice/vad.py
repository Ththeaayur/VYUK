"""Voice activity detection slot (v5.58, Wave 3, PR-11): Silero VAD.

Licence position (primary source, read 2026-10-24):
- Silero VAD CODE is MIT: the upstream repository
  github.com/snakers4/silero-vad reports "License: MIT License
  (MIT)" and its README states "Published under permissive
  license (MIT)". That clears the code path in this module.
- Silero VAD WEIGHTS are NOT bundled by this module and are NOT
  verified clean for commercial distribution here: the same
  upstream README header carries a CC BY-NC 4.0 badge whose scope
  is not stated in the fetched README text, so the weights'
  licence scope is ambiguous from the primary text read. Until
  the coordinator confirms with upstream whether
  ``silero_vad.onnx`` falls under the MIT licence or the
  non-commercial badge, NO weights file ships in the tree and
  this module never downloads one.

Loading pattern (the existing local-assets pattern, read-only):
weights are found, never fetched, in this order —
  1. ``VYUK_SILERO_VAD_ONNX`` (explicit operator-supplied path),
  2. ``<bundled assets>/vad/silero_vad.onnx`` via
     ``core.voice.local_assets.bundled_assets_dir()`` (the same root
     the installer pack uses for whisper/kokoro/models),
  3. an explicit path passed to the constructor.
When no weights file exists, or onnxruntime is absent, or the
ONNX session fails to run, the detector degrades to the previous
energy VAD — it never crashes and never reports a Silero
probability it did not compute. ``status()`` always says which
backend actually ran.

Silero facts used here (upstream README): 8/16 kHz input, ONNX
runtime supported, ~2 MB model, <1 ms per 30+ ms chunk on CPU.
The ONNX graph interface this module attempts is the documented
silero-vad ONNX one: inputs ``input`` (float32 [1, samples]),
``sr`` (int64 scalar, 16000), ``h``/``c`` state (float32
[2, 1, 64]); output ``output`` is the speech probability. If a
supplied file's graph differs, the session attempt fails, the
backend degrades, and the error is reported — never papered over.
"""

from __future__ import annotations

import os
from .. import config_spec
from pathlib import Path
from typing import Any

import numpy as np

SILERO_SAMPLE_RATE = 16000
SILERO_STATE_SHAPE = (2, 1, 64)
SILERO_DEFAULT_THRESHOLD = 0.5
SILERO_LICENCE = (
    "MIT (code: snakers4/silero-vad, GitHub repo licence field + "
    "README 'Published under permissive license (MIT)')"
)
SILERO_WEIGHTS_LICENCE_NOTE = (
    "Weights NOT bundled: upstream README header also carries a "
    "CC BY-NC 4.0 badge of unstated scope, so the weights' licence "
    "scope is ambiguous from the primary text read 2026-10-24. "
    "Activation requires an operator-supplied silero_vad.onnx and "
    "a coordinator licence confirmation before any distribution."
)
# Mirrors core/voice/local_client.py's previous VAD (VAD_RMS_THRESHOLD):
# RMS on the int16 scale above this counts as speech. The live
# value is config.vad_rms_threshold when it holds a sane positive
# number, exactly as local_client._vad_threshold() resolves it.
# GAPS-16: this module no longer keeps its own copy of that
# resolution — _energy_threshold() below delegates to the one
# live resolver (core/voice/local_client_vad._vad_threshold), so
# the two RMS truths cannot drift. This module is the Silero
# slot (neural VAD when weights exist, energy otherwise) and a
# status surface (boot, doctor, front-end status); the LIVE mic
# loop's VAD truth is local_client_vad (adaptive floor, barge-in
# discipline) — see core/voice/mic_loop.py.
ENERGY_VAD_RMS_THRESHOLD = 400.0
ENERGY_VAD_MIN_SPEECH_S = 0.35


def _energy_threshold() -> float:
    # GAPS-16: one resolver — the live one. (The local fallback
    # below only fires if the live module itself cannot import,
    # which would already have broken the session; it keeps this
    # status-only module importable on its own.)
    try:
        from .local_client_vad import _vad_threshold

        return float(_vad_threshold())
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..config import config  # Phase 3 V1a-1: audio chain moved to core/voice/

        value = float(getattr(config, "vad_rms_threshold",
                              ENERGY_VAD_RMS_THRESHOLD))
        if value > 0:
            return value
    except Exception:  # noqa: BLE001
        pass
    return ENERGY_VAD_RMS_THRESHOLD


def find_silero_weights(explicit: str | os.PathLike | None = None) -> Path | None:
    """Locate a user-supplied silero_vad.onnx. Never downloads.

    Returns None when nothing is found — that is the normal
    no-weights state, not an error.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = config_spec.env("VYUK_SILERO_VAD_ONNX").strip()
    if env:
        candidates.append(Path(env))
    try:
        from . import local_assets  # Phase 3 V2: local_assets now in core/voice/; V1a-1 interim closed

        root = local_assets.bundled_assets_dir()
        if root is not None:
            candidates.append(Path(root) / "vad" / "silero_vad.onnx")
    except Exception:  # noqa: BLE001
        pass
    for path in candidates:
        try:
            if path.is_file():
                return path
        except Exception:  # noqa: BLE001
            continue
    return None


def onnxruntime_available() -> bool:
    """True when onnxruntime imports (already a declared dep)."""
    try:
        import onnxruntime  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


class EnergyVAD:
    """The previous VAD: RMS energy against the configured floor.

    Frames are float32 audio in [-1, 1] (the mic callback's
    post-gain float form); ``is_speech`` rescales RMS to the
    int16 domain before comparing to the threshold, matching
    local_client's units.

    Backend name is always ``"energy"`` so no caller can mistake
    its output for a neural VAD score. ``probability`` is a
    clipped RMS/threshold ratio — an energy proxy, labelled as
    such in status(), not a neural probability.
    """

    backend = "energy"

    def __init__(self, rms_threshold: float | None = None):
        self.rms_threshold = float(
            rms_threshold if rms_threshold else _energy_threshold())

    def rms(self, frame: np.ndarray) -> float:
        arr = np.asarray(frame, dtype=np.float32).reshape(-1)
        if arr.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(arr ** 2)))

    def is_speech(self, frame: np.ndarray) -> bool:
        # int16-scale RMS, matching local_client's threshold units.
        # GAPS-16: >=, not > — the live mic loop (mic_loop.py)
        # counts a chunk at exactly the threshold as speech, and
        # this fallback must give the same answer at the boundary.
        return self.rms(frame) * 32768.0 >= self.rms_threshold

    def probability(self, frame: np.ndarray) -> float:
        ratio = (self.rms(frame) * 32768.0) / max(self.rms_threshold, 1e-9)
        return float(min(1.0, ratio))

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "silero_available": False,
            "weights_path": None,
            "licence": "previous energy VAD (no model, no licence question)",
            "degraded": True,
            "reason": "Silero weights not present — energy fallback",
        }


class SileroVAD:
    """Silero VAD via onnxruntime. Weights must already exist.

    ``available`` is False until a weights file is found AND an
    ONNX session builds. ``probability`` returns None (not a
    fabricated score) when unavailable, and resets state on
    ``reset()``. State is carried across frames like the upstream
    streaming usage; a fresh utterance should call ``reset()``.
    """

    backend = "silero-onnx"

    def __init__(self, weights: str | os.PathLike | None = None,
                 threshold: float = SILERO_DEFAULT_THRESHOLD):
        self.threshold = float(threshold)
        self.weights_path = find_silero_weights(weights)
        self._session = None
        self._error: str | None = None
        self._h = np.zeros(SILERO_STATE_SHAPE, dtype=np.float32)
        self._c = np.zeros(SILERO_STATE_SHAPE, dtype=np.float32)
        if self.weights_path is None:
            self._error = "no silero_vad.onnx found (see find_silero_weights)"
            return
        try:
            import onnxruntime as ort

            self._session = ort.InferenceSession(
                str(self.weights_path),
                providers=["CPUExecutionProvider"])
        except Exception as e:  # noqa: BLE001
            self._session = None
            self._error = f"ONNX session failed: {e}"

    @property
    def available(self) -> bool:
        return self._session is not None

    def reset(self) -> None:
        self._h = np.zeros(SILERO_STATE_SHAPE, dtype=np.float32)
        self._c = np.zeros(SILERO_STATE_SHAPE, dtype=np.float32)

    def probability(self, frame_16k_float: np.ndarray) -> float | None:
        """Speech probability for one float32 16 kHz frame.

        Returns None when unavailable or when the session run
        fails (and then marks itself unavailable with the error).
        Never returns a made-up number.
        """
        if self._session is None:
            return None
        try:
            arr = np.asarray(frame_16k_float, dtype=np.float32).reshape(-1)
            if arr.size == 0:
                return 0.0
            out = self._session.run(
                None,
                {
                    "input": arr.reshape(1, -1),
                    "sr": np.array([SILERO_SAMPLE_RATE], dtype=np.int64),
                    "h": self._h,
                    "c": self._c,
                },
            )
            # Upstream ONNX graph returns (probability, h, c).
            if len(out) >= 3:
                self._h = np.asarray(out[1], dtype=np.float32)
                self._c = np.asarray(out[2], dtype=np.float32)
            prob = out[0]
            return float(np.asarray(prob, dtype=np.float32).reshape(-1)[0])
        except Exception as e:  # noqa: BLE001
            self._session = None
            self._error = f"ONNX run failed: {e}"
            return None

    def is_speech(self, frame_16k_float: np.ndarray) -> bool:
        prob = self.probability(frame_16k_float)
        return bool(prob is not None and prob >= self.threshold)

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.backend if self.available else "energy-fallback",
            "silero_available": self.available,
            "weights_path": (str(self.weights_path)
                             if self.weights_path else None),
            "licence": SILERO_LICENCE,
            "weights_licence_note": SILERO_WEIGHTS_LICENCE_NOTE,
            "degraded": not self.available,
            "reason": self._error,
        }


class VoiceActivityDetector:
    """The VAD slot: Silero when weights exist, energy otherwise.

    ``backend`` is the backend that actually ran on the last call
    (``"silero-onnx"`` or ``"energy"``) — callers must read it,
    never assume Silero. ``probability`` returns the energy proxy
    on the fallback path; ``status()["degraded"]`` says so.
    """

    def __init__(self, weights: str | os.PathLike | None = None,
                 threshold: float = SILERO_DEFAULT_THRESHOLD):
        self._silero = SileroVAD(weights, threshold=threshold)
        self._energy = EnergyVAD()
        self.backend = ("silero-onnx" if self._silero.available
                        else "energy")

    def probability(self, frame: np.ndarray) -> float | None:
        if self._silero.available:
            prob = self._silero.probability(frame)
            if prob is not None:
                self.backend = "silero-onnx"
                return prob
            self.backend = "energy"
        return self._energy.probability(frame)

    def is_speech(self, frame: np.ndarray) -> bool:
        if self._silero.available:
            return self._silero.is_speech(frame)
        return self._energy.is_speech(frame)

    def reset(self) -> None:
        self._silero.reset()

    def status(self) -> dict[str, Any]:
        if self._silero.available:
            return self._silero.status()
        merged = self._energy.status()
        merged["weights_path"] = (
            str(self._silero.weights_path)
            if self._silero.weights_path else None)
        merged["weights_licence_note"] = SILERO_WEIGHTS_LICENCE_NOTE
        merged["silero_licence"] = SILERO_LICENCE
        return merged
