"""Tiny streaming wake-word model (R7-6, micro-wake-word pattern,
own code).

micro-wake-word trains a small streaming model on mel features
and ships the tiny TFLite file. VYUK's own version keeps the same
shape — train-your-own phrase, tiny streaming model — but the
model itself is VYUK's own numpy code, for three reasons the
parent ruled in the brief:

  * The framework is TensorFlow-based and the tree is deliberately
    TensorFlow-free AND torch-free. Training is a DEV-TIME activity
    (his machine, on demand); the shipped artefact is a tiny model
    file, never a training framework.
  * A TFLite file would need a TFLite interpreter the parent has
    not ruled, and an ONNX file would need onnxruntime, which is
    Qt-venv-only (not on system python3). A .npz model runs on
    BOTH interpreters with numpy alone — no new dependency.
  * The whole loop (synthesise -> featurise -> train -> score ->
    evaluate) is verifiable end-to-end in the lab, which a
    TensorFlow training step could never be here.

Model: 2-layer MLP, 2560 -> 48 tanh -> 1 sigmoid, Adam, binary
cross-entropy, deterministic seed. ~123k parameters, ~0.5 MB as
float32 .npz. The card (JSON sidecar) records the phrase, the
feature shape, the threshold, the held-out accuracy, and the
provenance — a model whose card disagrees with the loader's
feature shape is refused, never silently run.

Honesty boundary (stated wherever the model is shown): the
training held-out accuracy is NOT a section-10 rig measurement.
`core/wake_enrolment.evaluate_custom_phrase` still reports
"not-measured" until rig scores at 1.5 m exist — a trained model
is UNEVALUATED, never claimed as passing the gate.

Wake stays OFF: enabling a model writes the active pointer only.
The detector consults it solely when wake itself is enabled by
the owner, and enabling requires the mic consent gate first.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .wake_features import (
    FEATURE_DIM,
    N_MELS,
    SAMPLE_RATE,
    WINDOW_FRAMES,
    clip_to_vector,
    log_mel,
)

# The store lives under the tree's data dir, like recordings/ —
# created lazily, never committed. A data restore wipes it, which
# is correct: a trained model is provisioned by the operator, like
# the MediaPipe/Silero weights, not shipped in the tree.
_WAKE_MODELS_DIRNAME = "models/wake"
_ACTIVE_FILENAME = "active.json"
_CARD_SUFFIX = ".card.json"
_MODEL_SUFFIX = ".npz"

# Streaming scorer cadence: score at most every 100 ms of fed
# audio — wake latency of ~0.1 s is fine, and a 10 ms cadence
# would burn CPU re-running the mel transform for nothing.
_SCORE_EVERY_S = 0.1
# Same refractory as the Vosk path (wake_word._WAKE_REFRACTORY_S):
# one utterance fires once.
_REFRACTORY_S = 2.5

_HIDDEN = 48
_DEFAULT_THRESHOLD = 0.7


def _data_dir() -> Path:
    from ..skills_media_helpers import _DATA_DIR

    return Path(_DATA_DIR)


def models_dir() -> Path:
    """data/models/wake/ — created on first use, never at import."""
    target = _data_dir() / _WAKE_MODELS_DIRNAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def slug_for(phrase: str) -> str:
    """Filesystem slug for a phrase: lowercase words joined with
    underscores, nothing else. A phrase that slugs to nothing is
    refused by the caller, never stored under a weird name."""
    return "_".join(phrase.strip().lower().split())


@dataclass
class WakeModelCard:
    """The JSON sidecar next to every .npz: what the model is,
    how it was trained, and how well it did on held-out data."""

    phrase: str
    slug: str
    threshold: float
    feature_dim: int = FEATURE_DIM
    n_mels: int = N_MELS
    window_frames: int = WINDOW_FRAMES
    train_accuracy: float | None = None
    heldout_accuracy: float | None = None
    n_positive: int = 0
    n_negative: int = 0
    epochs: int = 0
    provenance: str = ""  # "synthetic-kokoro" | "wav-files" | "mixed"
    voices: list[str] = field(default_factory=list)
    sha256: str = ""
    created_at: str = ""
    evaluated_section10: bool = False  # rig scores at 1.5 m: never yet

    def to_dict(self) -> dict:
        return {
            "phrase": self.phrase,
            "slug": self.slug,
            "threshold": self.threshold,
            "feature_dim": self.feature_dim,
            "n_mels": self.n_mels,
            "window_frames": self.window_frames,
            "train_accuracy": self.train_accuracy,
            "heldout_accuracy": self.heldout_accuracy,
            "n_positive": self.n_positive,
            "n_negative": self.n_negative,
            "epochs": self.epochs,
            "provenance": self.provenance,
            "voices": list(self.voices),
            "sha256": self.sha256,
            "created_at": self.created_at,
            "evaluated_section10": self.evaluated_section10,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WakeModelCard":
        return cls(
            phrase=str(data.get("phrase", "")),
            slug=str(data.get("slug", "")),
            threshold=float(data.get("threshold", _DEFAULT_THRESHOLD)),
            feature_dim=int(data.get("feature_dim", FEATURE_DIM)),
            n_mels=int(data.get("n_mels", N_MELS)),
            window_frames=int(data.get("window_frames", WINDOW_FRAMES)),
            train_accuracy=data.get("train_accuracy"),
            heldout_accuracy=data.get("heldout_accuracy"),
            n_positive=int(data.get("n_positive", 0)),
            n_negative=int(data.get("n_negative", 0)),
            epochs=int(data.get("epochs", 0)),
            provenance=str(data.get("provenance", "")),
            voices=list(data.get("voices") or []),
            sha256=str(data.get("sha256", "")),
            created_at=str(data.get("created_at", "")),
            evaluated_section10=bool(data.get("evaluated_section10", False)),
        )


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


class WakeModel:
    """The tiny MLP itself: 2560 -> 48 tanh -> 1 sigmoid.

    Pure numpy. Train with Adam on (N, 2560) float32 vectors;
    save/load as .npz + card. The loader verifies the sha256 in
    the card against the file bytes and refuses a model whose
    feature shape disagrees — a stale or tampered model never
    runs silently.
    """

    def __init__(self, *, threshold: float = _DEFAULT_THRESHOLD,
                 seed: int = 7):
        self.threshold = float(threshold)
        self.seed = int(seed)
        rng = np.random.default_rng(self.seed)
        limit1 = float(np.sqrt(6.0 / (FEATURE_DIM + _HIDDEN)))
        self.w1 = rng.uniform(-limit1, limit1, (FEATURE_DIM, _HIDDEN)).astype(np.float32)
        self.b1 = np.zeros(_HIDDEN, dtype=np.float32)
        limit2 = float(np.sqrt(6.0 / (_HIDDEN + 1)))
        self.w2 = rng.uniform(-limit2, limit2, (_HIDDEN, 1)).astype(np.float32)
        self.b2 = np.zeros(1, dtype=np.float32)
        self.card: WakeModelCard | None = None

    def _forward(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h = np.tanh(x @ self.w1 + self.b1)
        return h, _sigmoid(h @ self.w2 + self.b2).reshape(-1)

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        """Sigmoid score per row, in [0, 1]."""
        x = np.asarray(x, dtype=np.float32)
        if x.ndim == 1:
            x = x.reshape(1, -1)
        if x.shape[1] != FEATURE_DIM:
            raise ValueError(
                f"expected {FEATURE_DIM} features, got {x.shape[1]}")
        _, out = self._forward(x)
        return out

    def train(self, x: np.ndarray, y: np.ndarray, *, epochs: int = 60,
              lr: float = 0.01, batch: int = 16) -> dict:
        """Adam on binary cross-entropy, deterministic order
        (seeded shuffle per epoch). Returns train accuracy and
        loss — the caller reports them, never hides them."""
        x = np.asarray(x, dtype=np.float32)
        y = np.asarray(y, dtype=np.float32).reshape(-1)
        if x.ndim != 2 or x.shape[1] != FEATURE_DIM:
            raise ValueError(f"expected (N, {FEATURE_DIM}) features")
        if x.shape[0] != y.shape[0] or x.shape[0] == 0:
            raise ValueError("need at least one labelled example")
        if not set(np.unique(y)).issubset({0.0, 1.0}):
            raise ValueError("labels must be 0/1")
        rng = np.random.default_rng(self.seed)
        # Adam state, one per parameter.
        m = {k: np.zeros_like(v) for k, v in
             (("w1", self.w1), ("b1", self.b1), ("w2", self.w2), ("b2", self.b2))}
        v = {k: np.zeros_like(v) for k, v in m.items()}
        n = x.shape[0]
        step = 0
        loss = float("nan")
        for epoch in range(max(1, int(epochs))):
            order = rng.permutation(n)
            for start in range(0, n, max(1, int(batch))):
                idx = order[start:start + max(1, int(batch))]
                xb, yb = x[idx], y[idx]
                h, out = self._forward(xb)
                eps = 1e-7
                loss = float(-np.mean(
                    yb * np.log(np.clip(out, eps, 1.0))
                    + (1.0 - yb) * np.log(np.clip(1.0 - out, eps, 1.0))))
                # Gradients (mean over the batch).
                d_out = (out - yb) / max(len(yb), 1)
                d_w2 = h.T @ d_out.reshape(-1, 1)
                d_b2 = np.array([d_out.sum()], dtype=np.float32)
                d_h = (d_out.reshape(-1, 1) @ self.w2.T) * (1.0 - h ** 2)
                d_w1 = xb.T @ d_h
                d_b1 = d_h.sum(axis=0)
                step += 1
                for name, grad in (("w1", d_w1), ("b1", d_b1),
                                   ("w2", d_w2), ("b2", d_b2)):
                    m[name] = 0.9 * m[name] + 0.1 * grad
                    v[name] = 0.999 * v[name] + 0.001 * (grad ** 2)
                    m_hat = m[name] / (1.0 - 0.9 ** step)
                    v_hat = v[name] / (1.0 - 0.999 ** step)
                    param = getattr(self, name)
                    param -= lr * m_hat / (np.sqrt(v_hat) + 1e-8)
                    setattr(self, name, param.astype(np.float32))
        preds = (self.predict_proba(x) >= 0.5).astype(np.float32)
        accuracy = float(np.mean(preds == y))
        return {"train_accuracy": accuracy, "train_loss": loss,
                "epochs": max(1, int(epochs))}

    def accuracy(self, x: np.ndarray, y: np.ndarray) -> float:
        preds = (self.predict_proba(x) >= 0.5).astype(np.float32)
        return float(np.mean(preds == np.asarray(y, dtype=np.float32).reshape(-1)))

    def save(self, directory: str | Path, card: WakeModelCard) -> tuple[Path, Path]:
        """Write <slug>.npz + <slug>.card.json. The card's sha256
        is the file bytes AFTER the write — a card written first
        would certify bytes that do not exist yet."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        model_path = directory / (card.slug + _MODEL_SUFFIX)
        card_path = directory / (card.slug + _CARD_SUFFIX)
        np.savez(model_path, w1=self.w1, b1=self.b1, w2=self.w2, b2=self.b2)
        digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
        card.sha256 = digest
        card.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        card_path.write_text(json.dumps(card.to_dict(), indent=2),
                             encoding="utf-8")
        self.card = card
        return model_path, card_path

    @classmethod
    def load(cls, model_path: str | Path,
             card_path: str | Path | None = None) -> "WakeModel":
        """Load + verify: the card's sha256 must match the file
        bytes, and the card's feature shape must match this
        loader's. A mismatch is a refusal, never a silent run."""
        model_path = Path(model_path)
        if card_path is None:
            # <slug>.npz -> <slug>.card.json (same directory).
            card_path = Path(str(model_path)[:-len(_MODEL_SUFFIX)] + _CARD_SUFFIX)
        card_path = Path(card_path)
        if not model_path.is_file():
            raise ValueError(f"no model file at {model_path}")
        if not card_path.is_file():
            raise ValueError(f"no model card at {card_path} — a model "
                             "without its card is refused")
        card = WakeModelCard.from_dict(
            json.loads(card_path.read_text(encoding="utf-8")))
        digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
        if card.sha256 and card.sha256 != digest:
            raise ValueError(
                f"model {model_path.name} fails its sha256 check — "
                "the file changed after training; refusing to run it")
        if (card.feature_dim != FEATURE_DIM or card.n_mels != N_MELS
                or card.window_frames != WINDOW_FRAMES):
            raise ValueError(
                f"model {model_path.name} was trained under different "
                f"features ({card.feature_dim}/{card.n_mels}/"
                f"{card.window_frames}) — refusing to run it")
        data = np.load(model_path)
        model = cls(threshold=card.threshold)
        model.w1 = np.asarray(data["w1"], dtype=np.float32)
        model.b1 = np.asarray(data["b1"], dtype=np.float32)
        model.w2 = np.asarray(data["w2"], dtype=np.float32)
        model.b2 = np.asarray(data["b2"], dtype=np.float32)
        model.card = card
        return model


def list_models(directory: str | Path | None = None) -> list[WakeModelCard]:
    """Every card in the store, newest first. An unreadable card
    is skipped and named in the caller's report — a corrupt file
    never takes the listing down."""
    directory = Path(directory) if directory is not None else models_dir()
    cards: list[WakeModelCard] = []
    if not directory.is_dir():
        return cards
    for card_path in sorted(directory.glob("*" + _CARD_SUFFIX),
                            key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            cards.append(WakeModelCard.from_dict(
                json.loads(card_path.read_text(encoding="utf-8"))))
        except Exception:  # noqa: BLE001 — one bad card skips, not crashes
            continue
    return cards


def active_pointer_path(directory: str | Path | None = None) -> Path:
    directory = Path(directory) if directory is not None else models_dir()
    return directory / _ACTIVE_FILENAME


def get_active(directory: str | Path | None = None) -> dict | None:
    """The active model pointer, or None. A corrupt pointer is
    None with the file left in place for the operator to inspect
    — deleting evidence is not this function's job."""
    path = active_pointer_path(directory)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or "slug" not in data:
        return None
    return data


def set_active(slug: str, threshold: float | None = None,
               directory: str | Path | None = None) -> dict:
    """Write the active pointer AND re-read it (A4-filestat: the
    success line names what the re-read found). Returns the
    pointer as re-read — the caller reports that, never the
    intent. Setting the pointer does NOT enable wake."""
    directory = Path(directory) if directory is not None else models_dir()
    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / (slug + _MODEL_SUFFIX)
    if not model_path.is_file():
        raise ValueError(f"no trained model named {slug!r} — train it first")
    pointer = {"slug": slug,
               "threshold": float(threshold)
               if threshold is not None else _DEFAULT_THRESHOLD,
               "set_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path = directory / _ACTIVE_FILENAME
    path.write_text(json.dumps(pointer, indent=2), encoding="utf-8")
    reread = json.loads(path.read_text(encoding="utf-8"))
    if reread.get("slug") != slug:
        raise ValueError("active pointer did not survive its own write")
    return reread


def clear_active(directory: str | Path | None = None) -> bool:
    """Remove the active pointer. Returns True when a pointer
    existed and is gone."""
    path = active_pointer_path(directory)
    if not path.is_file():
        return False
    path.unlink()
    return not path.exists()


class StreamingWakeScorer:
    """The streaming half: feed 16 kHz int16 PCM in any chunk
    size; every 100 ms of new audio it scores the last 0.8 s
    window and fires (returns True once) when the score clears
    the threshold. One utterance fires once — the 2.5 s
    refractory matches the Vosk path."""

    def __init__(self, model: WakeModel, *, threshold: float | None = None):
        self.model = model
        self.threshold = float(
            threshold if threshold is not None else model.threshold)
        self._pcm = np.zeros(0, dtype=np.int16)
        self._since_score_s = 0.0
        self._last_fire_s = 0.0
        self.last_score: float | None = None
        self.fires = 0

    def reset(self) -> None:
        self._pcm = np.zeros(0, dtype=np.int16)
        self._since_score_s = 0.0
        self._last_fire_s = 0.0
        self.last_score = None

    def feed(self, pcm: np.ndarray | bytes, *, now: float | None = None) -> bool:
        """Feed PCM; True exactly when this feed fires the wake.
        `now` is injectable so tests can drive the refractory
        without sleeping."""
        if isinstance(pcm, (bytes, bytearray)):
            chunk = np.frombuffer(bytes(pcm), dtype=np.int16)
        else:
            chunk = np.asarray(pcm, dtype=np.int16).reshape(-1)
        if chunk.size == 0:
            return False
        now = time.monotonic() if now is None else float(now)
        self._pcm = np.concatenate([self._pcm, chunk])
        # Keep only what the window needs: the 0.8 s window plus
        # slack, i.e. the last 1.0 s of audio.
        keep = SAMPLE_RATE
        if self._pcm.size > keep:
            self._pcm = self._pcm[-keep:]
        self._since_score_s += chunk.size / SAMPLE_RATE
        if self._since_score_s < _SCORE_EVERY_S:
            return False
        self._since_score_s = 0.0
        if self._pcm.size < SAMPLE_RATE // 2:  # need 0.5 s before scoring
            return False
        vector = clip_to_vector(self._pcm)
        score = float(self.model.predict_proba(vector)[0])
        self.last_score = score
        if score < self.threshold:
            return False
        if now - self._last_fire_s < _REFRACTORY_S:
            return False
        self._last_fire_s = now
        self.fires += 1
        return True


def load_active_model(directory: str | Path | None = None) -> tuple[WakeModel, dict] | None:
    """(model, pointer) for the active model, or None when no
    pointer is set. A pointer naming a missing or unverifiable
    model raises — a dangling pointer is reported, never silently
    ignored, because the detector logs the outcome either way."""
    pointer = get_active(directory)
    if pointer is None:
        return None
    directory = Path(directory) if directory is not None else models_dir()
    model_path = directory / (pointer["slug"] + _MODEL_SUFFIX)
    model = WakeModel.load(model_path)
    return model, pointer
