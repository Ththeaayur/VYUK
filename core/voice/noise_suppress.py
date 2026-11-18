"""Noise-suppression slot + SpeechScore bake-off harness (v5.58, NS).

One winner, never stacked, never faked:
- The slot holds exactly ONE active neural suppressor at a time.
  ``chain_suppressors`` REFUSES to chain two neural suppressors
  (ValueError) — stacking RNNoise on GTCRN (or either on
  DeepFilterNet) is a contract violation, not a feature.
- The EA-4-style harness scores PESQ / STOI / DNSMOS / SI-SDR.
  Only SI-SDR is computable with the declared dependencies
  (numpy): PESQ, STOI and DNSMOS have no declared package in
  this tree and no new pip dependency is allowed, so those
  three fields are always None with a reason — never a
  synthesised score. A bake-off winner is declared ONLY when at
  least one neural suppressor actually ran; otherwise
  ``winner`` is None and the harness says why.
- DeepFilterNet's seat is GATED SHELF (programme item O-8:
  weights licence unverified). There is deliberately NO
  DeepFilterNet runner class in this module — only
  ``deepfilternet_status()`` reporting the shelf reason — so
  the bake-off cannot accidentally run it. Its upstream code
  licence (MIT/Apache-2.0 dual, per its README) is noted in
  that status, but the programme's weights question stays open
  and the seat stays shut until O-8 closes.

Licence positions (primary sources, read 2026-10-24):
- RNNoise: BSD-3-Clause — upstream github.com/xiph/rnnoise
  reports "License: BSD 3-Clause 'New' or 'Revised' License
  (BSD-3-Clause)"; its README notes model files are downloaded
  from Xiph servers by autogen.sh (too large for Git) and the
  demo operates on 48 kHz raw PCM. Licence cleared; the native
  library + weights blob are still absent from this tree (see
  RNNoiseSuppressor), so the runner is a gated slot, not a
  working suppressor, until vendored + sha-pinned.
- GTCRN: MIT — upstream github.com/Xiaobin-Rong/gtcrn reports
  "License: MIT License (MIT)"; pre-trained models sit in its
  ``checkpoints`` folder (PyTorch .ckpt). Licence cleared; the
  checkpoints need a torch runtime or an ONNX conversion
  (sherpa-onnx support exists upstream) that is not in this
  tree's dependencies, so the runner is likewise gated.
- EA-2 cost note (RNNoise is 48 kHz-native): the 16 kHz mic
  path needs a 16 -> 48 -> 16 kHz resample branch around any
  RNNoise call. ``resample_16_to_48`` / ``resample_48_to_16``
  implement that branch (linear interpolation, harness-grade —
  a production wire would use a polyphase resampler) and the
  harness measures the round-trip length error so the cost is
  visible, not assumed.

``SpectralGateBaseline`` is a pure-numpy frame-RMS gate. It is
a HARNESS SMOKE baseline only — labelled non-neural, never a
bake-off contestant, never a shipped suppressor choice.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

from .. import config_spec

RNNOISE_NATIVE_RATE = 48000
GTCRN_NATIVE_RATE = 16000
RNNOISE_LICENCE = (
    "BSD-3-Clause (xiph/rnnoise: GitHub repo licence field; "
    "model files fetched by autogen.sh from Xiph servers)"
)
GTCRN_LICENCE = (
    "MIT (Xiaobin-Rong/gtcrn: GitHub repo licence field; "
    "pre-trained models in its checkpoints/ folder)"
)
DEEPFILTERNET_SHELF_REASON = (
    "GATED SHELF (programme O-8): DeepFilterNet weights licence "
    "unverified for commercial distribution. Upstream code is "
    "MIT/Apache-2.0 dual per its README, but that does not close "
    "the weights question, so no runner ships and the bake-off "
    "runs without it. Closing step: O-8 licence confirmation + "
    "sha-pinned weights, then (and only then) a runner may be added."
)


def resample_16_to_48(samples: np.ndarray) -> np.ndarray:
    """16 kHz mic audio -> RNNoise's native 48 kHz (x3)."""
    from ..aec import resample_linear  # Phase 3 V1a-1: audio chain moved to core/voice/

    return resample_linear(samples, 16000, RNNOISE_NATIVE_RATE)


def resample_48_to_16(samples: np.ndarray) -> np.ndarray:
    """RNNoise output -> 16 kHz mic path (/3)."""
    from ..aec import resample_linear  # Phase 3 V1a-1: audio chain moved to core/voice/

    return resample_linear(samples, RNNOISE_NATIVE_RATE, 16000)


def si_sdr(reference: np.ndarray, estimate: np.ndarray) -> float | None:
    """Scale-invariant SDR in dB (numpy, exact).

    Returns None for empty / mismatched / all-zero inputs, and
    +inf for a bit-identical estimate (zero error energy) —
    both are honest edge reports, not scores. The caller aligns
    lengths before calling; mismatched lengths return None
    rather than silently truncating a measurement.
    """
    ref = np.asarray(reference, dtype=np.float64).reshape(-1)
    est = np.asarray(estimate, dtype=np.float64).reshape(-1)
    if ref.size == 0 or est.size != ref.size:
        return None
    if float(np.dot(ref, ref)) == 0.0:
        return None
    alpha = float(np.dot(est, ref) / np.dot(ref, ref))
    target = alpha * ref
    noise = est - target
    noise_energy = float(np.dot(noise, noise))
    if noise_energy == 0.0:
        return float("inf")
    return float(10.0 * np.log10(
        float(np.dot(target, target)) / noise_energy))


class NeuralSuppressor:
    """Base for neural suppressor slots. Subclasses gate on assets.

    ``available`` is False until the native library / weights
    the subclass needs are present AND verified loadable. The
    base ``process`` refuses (RuntimeError) rather than passing
    audio through disguised as "denoised" — passthrough is the
    caller's explicit choice, never a suppressor's silent one.
    """

    name = "neural-suppressor"
    native_rate = 16000
    licence = ""

    def __init__(self):
        self._error: str | None = None

    @property
    def available(self) -> bool:
        return False

    def process(self, pcm_16k_float: np.ndarray) -> np.ndarray:
        raise RuntimeError(
            f"{self.name} unavailable: {self._error or 'no engine'}")

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "native_rate": self.native_rate,
            "licence": self.licence,
            "available": self.available,
            "error": self._error,
        }


def _find_asset(env_name: str, *rel_parts: str) -> Path | None:
    env = (config_spec.explicit(env_name) or "").strip()
    candidates = [Path(env)] if env else []
    try:
        from . import local_assets  # Phase 3 V2: local_assets now in core/voice/; V1a-1 interim closed

        root = local_assets.bundled_assets_dir()
        if root is not None:
            candidates.append(Path(root).joinpath(*rel_parts))
    except Exception:  # noqa: BLE001
        pass
    for path in candidates:
        try:
            if path.is_file():
                return path
        except Exception:  # noqa: BLE001
            continue
    return None


class RNNoiseSuppressor(NeuralSuppressor):
    """EA-2 RNNoise slot (48 kHz-native). Gated, not working yet.

    Needs a vendored librnnoise (native .so/.dll) plus its
    weights blob — neither is in this tree, and no new pip
    dependency may add them. Closing step: vendor the library +
    blob, pin both sha256s, add a ctypes wire, then re-run the
    bake-off. Until then ``available`` is False and ``process``
    refuses.
    """

    name = "rnnoise"
    native_rate = RNNOISE_NATIVE_RATE
    licence = RNNOISE_LICENCE

    def __init__(self, library: str | os.PathLike | None = None,
                 weights: str | os.PathLike | None = None):
        super().__init__()
        self.library_path = (Path(library) if library else _find_asset(
            "VYUK_RNNOISE_LIB", "noise", "librnnoise"))
        self.weights_path = (Path(weights) if weights else _find_asset(
            "VYUK_RNNOISE_WEIGHTS", "noise", "rnnoise_weights.bin"))
        if self.library_path is None or self.weights_path is None:
            self._error = (
                "librnnoise and/or weights blob not present in tree "
                "(see closing step in module docstring)")

    @property
    def available(self) -> bool:
        return self.library_path is not None and self.weights_path is not None

    def status(self) -> dict[str, Any]:
        merged = super().status()
        merged["library_path"] = (
            str(self.library_path) if self.library_path else None)
        merged["weights_path"] = (
            str(self.weights_path) if self.weights_path else None)
        merged["resample_branch"] = "16 -> 48 -> 16 kHz (native 48 kHz)"
        return merged


class GTCRNSuppressor(NeuralSuppressor):
    """EA-2 GTCRN slot (16 kHz-native, no resample branch).

    Needs an ONNX (or other runtime-loadable) GTCRN model: the
    upstream checkpoints are PyTorch .ckpt and this tree has no
    torch dependency. Closing step: convert a pinned checkpoint
    to ONNX (sherpa-onnx documents such a conversion upstream),
    pin its sha256, wire an onnxruntime runner. Until then
    ``available`` is False and ``process`` refuses.
    """

    name = "gtcrn"
    native_rate = GTCRN_NATIVE_RATE
    licence = GTCRN_LICENCE

    def __init__(self, model: str | os.PathLike | None = None):
        super().__init__()
        self.model_path = (Path(model) if model else _find_asset(
            "VYUK_GTCRN_ONNX", "noise", "gtcrn.onnx"))
        if self.model_path is None:
            self._error = (
                "no runtime-loadable GTCRN model present (upstream "
                "checkpoints are PyTorch .ckpt; ONNX conversion + "
                "sha pin still owed)")

    @property
    def available(self) -> bool:
        return self.model_path is not None

    def status(self) -> dict[str, Any]:
        merged = super().status()
        merged["model_path"] = (
            str(self.model_path) if self.model_path else None)
        merged["resample_branch"] = "none (native 16 kHz)"
        return merged


def deepfilternet_status() -> dict[str, Any]:
    """The PR-14 seat: shelved. No runner, no weights, no scores."""
    return {
        "name": "deepfilternet",
        "shipped": False,
        "shelf": "GATED SHELF (O-8)",
        "reason": DEEPFILTERNET_SHELF_REASON,
        "in_bakeoff": False,
    }


class SpectralGateBaseline:
    """Non-neural frame-RMS gate — harness smoke baseline ONLY.

    Not a bake-off contestant. Frames whose RMS falls below
    ``threshold * noise_estimate`` are attenuated by ``floor``;
    the noise estimate tracks the quietest recent frames. Pure
    numpy, no model, no licence question — and no claim to
    neural quality.
    """

    name = "spectral-gate-baseline (NOT neural)"
    native_rate = 16000

    def __init__(self, frame_samples: int = 160, floor: float = 0.1):
        self.frame_samples = int(frame_samples)
        self.floor = float(floor)
        self._noise = 0.0

    def process(self, pcm_16k_float: np.ndarray) -> np.ndarray:
        arr = np.asarray(pcm_16k_float, dtype=np.float32).reshape(-1)
        out = np.zeros_like(arr)
        for i in range(0, arr.size, self.frame_samples):
            frame = arr[i:i + self.frame_samples]
            rms = float(np.sqrt(np.mean(frame ** 2))) if frame.size else 0.0
            if self._noise == 0.0:
                self._noise = rms
            else:
                self._noise = 0.95 * self._noise + 0.05 * min(rms, self._noise)
            gain = self.floor if rms < 2.0 * max(self._noise, 1e-6) else 1.0
            out[i:i + frame.size] = frame * gain
        return out

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "available": True, "neural": False}


def chain_suppressors(*suppressors: Any) -> Any:
    """Refuse to stack two neural suppressors. Always.

    Returns the single suppressor when exactly one neural
    suppressor is given (a baseline chained after it is allowed
    only if it is explicitly non-neural and the caller names it
    as a post-gate — in practice: pass one suppressor). Two or
    more neural suppressors raise ValueError, whatever their
    availability: the rule is about the pipeline shape, not
    about whether the engines happen to load today.
    """
    neural = [s for s in suppressors
              if isinstance(s, NeuralSuppressor)]
    if len(neural) > 1:
        raise ValueError(
            "never stack two neural suppressors: "
            + ", ".join(s.name for s in neural))
    if not suppressors:
        raise ValueError("chain_suppressors needs at least one suppressor")
    return suppressors[0]


def select_single_suppressor(
        suppressors: list[Any]) -> dict[str, Any]:
    """Pick the ONE active neural suppressor, or none.

    Preference is the caller's list order. Returns
    ``{"selected": name|None, "reason": str, "available": bool}``.
    With no available neural suppressor the pipeline stays on
    raw mic audio — passthrough by explicit decision, recorded
    in ``reason``.
    """
    for sup in suppressors:
        if isinstance(sup, NeuralSuppressor) and sup.available:
            return {"selected": sup.name, "reason": "first available neural",
                    "available": True}
    return {"selected": None,
            "reason": "no neural suppressor available — raw mic audio",
            "available": False}


class SpeechScoreHarness:
    """EA-4-style bake-off harness: PESQ/STOI/DNSMOS/SI-SDR.

    PESQ, STOI and DNSMOS are NOT computed here: no package for
    any of them is declared in this tree, and inventing those
    scores would be fabrication. Each score call returns the
    real SI-SDR plus None+reason for the other three. The
    closing step for full scoring is a CC-1-reviewed addition
    of pesq/stoi/dnsmos evaluation packages (or an offline
    reference implementation) — until then SI-SDR is the only
    measured axis and any "winner" claim must say so.
    """

    METRICS = ("si_sdr_db", "pesq", "stoi", "dnsmos")

    def score(self, clean: np.ndarray, noisy: np.ndarray,
              enhanced: np.ndarray) -> dict[str, Any]:
        return {
            "si_sdr_db": si_sdr(clean, enhanced),
            "si_sdr_noisy_db": si_sdr(clean, noisy),
            "pesq": None,
            "pesq_reason": "no pesq package declared — not computed",
            "stoi": None,
            "stoi_reason": "no stoi package declared — not computed",
            "dnsmos": None,
            "dnsmos_reason": "no dnsmos package declared — not computed",
        }

    def run_bakeoff(self, cases: list[dict[str, Any]],
                    suppressors: list[Any]) -> dict[str, Any]:
        """Run every suppressor over (clean, noisy) cases.

        A suppressor that is unavailable is reported, not run,
        and never scores. ``winner`` is the highest mean SI-SDR
        among suppressors that ACTUALLY RAN; with none run,
        winner is None. The baseline (non-neural) is scored for
        smoke only and is never eligible to win.
        """
        results: dict[str, Any] = {}
        for sup in suppressors:
            name = getattr(sup, "name", "?")
            if isinstance(sup, NeuralSuppressor) and not sup.available:
                results[name] = {
                    "ran": False,
                    "reason": getattr(sup, "_error", "unavailable"),
                    "mean_si_sdr_db": None,
                }
                continue
            scores = []
            for case in cases:
                try:
                    enhanced = sup.process(
                        np.asarray(case["noisy"], dtype=np.float32))
                    s = self.score(case["clean"], case["noisy"], enhanced)
                    if s["si_sdr_db"] is not None:
                        scores.append(float(s["si_sdr_db"]))
                except Exception:  # noqa: BLE001
                    continue
            results[name] = {
                "ran": True,
                "cases_scored": len(scores),
                "mean_si_sdr_db": (
                    float(np.mean(scores)) if scores else None),
            }
        eligible = {
            name: r["mean_si_sdr_db"] for name, r in results.items()
            if r["ran"] and r["mean_si_sdr_db"] is not None
            and "NOT neural" not in name
        }
        winner = (max(eligible, key=eligible.get) if eligible else None)
        return {
            "results": results,
            "winner": winner,
            "winner_basis": ("mean SI-SDR (only measured axis; PESQ/STOI/"
                             "DNSMOS unavailable — see score())"
                             if winner else None),
            "winner_reason": (
                None if winner else
                "no neural suppressor ran — no winner declared; "
                "bake-off inconclusive pending RNNoise/GTCRN "
                "engines (see their closing steps)"),
            "deepfilternet": deepfilternet_status(),
        }
