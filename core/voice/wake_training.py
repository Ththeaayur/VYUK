"""Train-your-own wake word (R7-6, micro-wake-word pattern, own code).

What micro-wake-word gives its users: a custom wake phrase,
trained from synthetic speech, as a tiny streaming model. What
this module gives VYUK: the same capability as VYUK's own code —
synthesise (Kokoro) or supply (WAV files) the training audio,
train a tiny numpy model, store it in the model slot, and arm it
in the detector.

The four funnel tools:
  * train_custom_wake_word — dev-time training run (synthesis or
    WAV files in, a .npz model + card out). Minutes of CPU + TTS.
  * enable_custom_wake_model — point the slot at a trained model.
    Mic-consent-gated. Does NOT enable wake.
  * list_custom_wake_models — read-only inventory.
  * custom_wake_model_status — read-only state report.

What this batch does NOT do (stated wherever the tools are
shown): it does not turn wake on (WAKE_SHIPS_ENABLED_BY_DEFAULT
is False and stays False; enabling wake is the owner's
decision), it does not claim a section-10 pass (the training
held-out accuracy is not a rig measurement — a trained model is
UNEVALUATED until rig scores at 1.5 m exist), and it does not
import TensorFlow, torch, or onnxruntime anywhere in the shipped
path (the model is numpy; training is VYUK's own Adam loop).
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from .. import capture_guard
from ..wake_enrolment import (
    MIN_ENROLMENT_UTTERANCES,
    CustomWakeEnrolment,
    evaluate_custom_phrase,
)
from .wake_features import clip_to_vector
from .wake_model import (
    WakeModel,
    get_active,
    list_models,
    load_active_model,
    models_dir,
    set_active,
    slug_for,
)
from .wake_synth import (
    DEFAULT_VOICES,
    NEGATIVE_PHRASES,
    build_dataset_from_wavs,
    build_dataset_synthetic,
    load_dataset,
)

# One training run at a time: two concurrent runs writing the
# same slug would corrupt each other's model file, and a second
# run is never worth the CPU anyway.
_TRAINING_LOCK = threading.Lock()

_DEFAULT_EPOCHS = 60
_DEFAULT_THRESHOLD = 0.7


def _split_voices(raw: str | None) -> list[str]:
    if raw is None or not str(raw).strip():
        return list(DEFAULT_VOICES)
    voices = [v.strip() for v in str(raw).split(",") if v.strip()]
    return voices or list(DEFAULT_VOICES)


def _split_phrases(raw: str | None) -> list[str]:
    if raw is None or not str(raw).strip():
        return list(NEGATIVE_PHRASES)
    phrases = [p.strip().lower() for p in str(raw).split(",") if p.strip()]
    return phrases or list(NEGATIVE_PHRASES)


def _clamp_int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clamp_float(value, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(low, min(high, number))


def train_custom_wake_word(phrase: str = "", voices: str = "",
                           renders_per_voice: int = 4,
                           negative_phrases: str = "",
                           epochs: int = _DEFAULT_EPOCHS,
                           threshold: float = _DEFAULT_THRESHOLD,
                           wav_dir: str = "") -> str:
    """Train a custom wake-word model for one phrase.

    Two sources, one pipeline. Without wav_dir the phrase is
    rendered by the Kokoro child in each voice (synthetic
    speech — the micro-wake-word trick); with wav_dir the
    positives/negatives come from .wav files under it. Either
    way the model lands in data/models/wake/ as <slug>.npz +
    <slug>.card.json, and the result reports the held-out
    accuracy honestly — including when it is bad.
    """
    phrase = str(phrase or "").strip().lower()
    errors = CustomWakeEnrolment(phrase=phrase).validation_errors
    if errors:
        return ("That phrase cannot be a custom wake word: "
                + "; ".join(errors) + ".")
    slug = slug_for(phrase)
    if not slug:
        return "That phrase cannot be a custom wake word: it has no words."
    epochs = _clamp_int(epochs, _DEFAULT_EPOCHS, 1, 500)
    threshold = _clamp_float(threshold, _DEFAULT_THRESHOLD, 0.05, 0.99)
    renders = _clamp_int(renders_per_voice, 4, 1, 10)
    voice_list = _split_voices(voices)
    negative_list = _split_phrases(negative_phrases)
    if not _TRAINING_LOCK.acquire(blocking=False):
        return ("A custom wake-word training run is already going — "
                "wait for it to finish before starting another.")
    try:
        with tempfile.TemporaryDirectory(prefix="wake_train_") as tmp:
            dataset_dir = Path(tmp) / "dataset"
            if str(wav_dir or "").strip():
                try:
                    manifest = build_dataset_from_wavs(
                        phrase, str(wav_dir).strip(), dataset_dir)
                except ValueError as exc:
                    return (f"Could not build the training set from "
                            f"{str(wav_dir).strip()!r}: {exc}")
                provenance = "wav-files"
            else:
                try:
                    manifest = build_dataset_synthetic(
                        phrase, voices=tuple(voice_list),
                        renders_per_voice=renders,
                        negative_phrases=tuple(negative_list),
                        out_dir=dataset_dir)
                except ValueError as exc:
                    return (f"Could not synthesise the training set: {exc} "
                            "Train from .wav files instead — give wav_dir "
                            "with positives/ and negatives/ folders.")
                provenance = "synthetic-kokoro"
            positives, negatives, _manifest = load_dataset(dataset_dir)
            # Features: one 2560-vector per clip, stratified 80/20.
            import numpy as np

            pos_x = np.stack([clip_to_vector(p) for p in positives])
            neg_x = np.stack([clip_to_vector(n) for n in negatives])
            rng = np.random.default_rng(7)
            pos_order = rng.permutation(len(pos_x))
            neg_order = rng.permutation(len(neg_x))
            pos_cut = max(1, int(len(pos_x) * 0.8))
            neg_cut = max(1, int(len(neg_x) * 0.8))
            train_x = np.concatenate(
                [pos_x[pos_order[:pos_cut]], neg_x[neg_order[:neg_cut]]])
            train_y = np.concatenate(
                [np.ones(pos_cut), np.zeros(neg_cut)]).astype(np.float32)
            held_x = np.concatenate(
                [pos_x[pos_order[pos_cut:]], neg_x[neg_order[neg_cut:]]])
            held_y = np.concatenate(
                [np.ones(len(pos_x) - pos_cut),
                 np.zeros(len(neg_x) - neg_cut)]).astype(np.float32)
            model = WakeModel(threshold=threshold)
            result = model.train(train_x, train_y, epochs=epochs)
            heldout = model.accuracy(held_x, held_y) if len(held_x) else None
            from .wake_model import WakeModelCard

            card = WakeModelCard(
                phrase=phrase, slug=slug, threshold=threshold,
                train_accuracy=round(result["train_accuracy"], 4),
                heldout_accuracy=(round(heldout, 4)
                                  if heldout is not None else None),
                n_positive=len(positives), n_negative=len(negatives),
                epochs=int(result["epochs"]), provenance=provenance,
                voices=list(voice_list) if provenance == "synthetic-kokoro"
                else [])
            model_path, _card_path = model.save(models_dir(), card)
            # The honest evaluation: held-out accuracy is NOT a
            # section-10 rig measurement. Run the gate with no rig
            # scores so the record says "not-measured" — a trained
            # model is UNEVALUATED, never a claimed pass.
            enrolment = CustomWakeEnrolment(
                phrase=phrase, utterances=len(positives))
            evaluation = evaluate_custom_phrase(
                enrolment, clips=[], threshold=threshold,
                corpus_path="", negative_hours=0.0)
            size_kb = model_path.stat().st_size // 1024
            failures = manifest.get("synthesis_failures") or []
            lines = [
                f"Trained a custom wake-word model for {phrase!r} "
                f"(saved as {model_path.name}, {size_kb} KB).",
                f"Training accuracy {result['train_accuracy']:.1%}; "
                f"held-out accuracy "
                f"{(heldout if heldout is not None else 0.0):.1%} "
                f"on {len(held_x)} held-out clips "
                f"({len(positives)} positive, {len(negatives)} negative).",
                f"Section-10 gate: {evaluation.reason} — the model is "
                "trained but UNEVALUATED; it has not passed the "
                "1.5 m rig test and must not be described as having done so.",
                "Wake itself is still OFF. Enable the model with "
                f"enable_custom_wake_model '{slug}', and wake only "
                "starts listening when you enable it.",
            ]
            if failures:
                lines.append(
                    f"{len(failures)} renders failed during synthesis "
                    f"(first: {failures[0]}).")
            return " ".join(lines)
    finally:
        _TRAINING_LOCK.release()


def enable_custom_wake_model(model: str = "",
                             threshold: float | None = None) -> str:
    """Point the wake slot at a trained model.

    Mic-consent-gated (the same guard as the meeting capture):
    a model that listens to the microphone is enabled only with
    the mic grant behind it. Sets the active pointer only — wake
    itself stays OFF until the owner enables it, and the model
    only fires when wake is enabled.
    """
    slug = str(model or "").strip().lower()
    if not slug:
        return ("Give the model name, e.g. enable_custom_wake_model "
                "'computer'. List models with list_custom_wake_models.")
    allowed, why = capture_guard.ensure_capture_allowed("microphone")
    if not allowed:
        return (f"Cannot enable a custom wake model: {why} — the "
                "microphone consent gate refused, so nothing was enabled.")
    threshold = (_clamp_float(threshold, _DEFAULT_THRESHOLD, 0.05, 0.99)
                 if threshold is not None else None)
    try:
        from .wake_model import WakeModel as _WakeModel

        model_path = models_dir() / (slug + ".npz")
        loaded = _WakeModel.load(model_path)
    except ValueError as exc:
        return f"Cannot enable {slug!r}: {exc}"
    try:
        pointer = set_active(slug, threshold)
    except ValueError as exc:
        return f"Cannot enable {slug!r}: {exc}"
    # Best-effort live arm: if the listener is already running it
    # picks the model up now; otherwise it arms on next start.
    armed = ""
    try:
        from . import wake_word as _wake_word

        ok, detail = _wake_word.wake_word_detector.set_custom_model(slug)
        armed = f" {detail}" if ok else f" (live arm deferred: {detail})"
    except Exception:  # noqa: BLE001 — pointer is set either way
        armed = " (it arms the next time the wake listener starts)"
    return (
        f"Custom wake model {loaded.card.phrase!r} is now the active "
        f"model (threshold {pointer['threshold']:.2f}, held-out "
        f"accuracy "
        f"{(loaded.card.heldout_accuracy or 0.0):.1%}).{armed} "
        "Wake itself is still OFF — the model only listens when "
        "you enable wake."
    )


def list_custom_wake_models() -> str:
    """Read-only inventory of trained custom wake-word models."""
    cards = list_models()
    active = get_active()
    active_slug = active.get("slug") if active else None
    if not cards:
        return ("No custom wake-word models are trained yet. Train one "
                "with train_custom_wake_word — e.g. phrase 'computer'.")
    lines = [f"{len(cards)} custom wake-word model(s):"]
    for card in cards:
        model_path = models_dir() / (card.slug + ".npz")
        size_kb = model_path.stat().st_size // 1024 if model_path.is_file() else 0
        held = (f"{card.heldout_accuracy:.1%}"
                if card.heldout_accuracy is not None else "not measured")
        mark = " [ACTIVE]" if card.slug == active_slug else ""
        lines.append(
            f"- {card.slug}{mark}: {card.phrase!r}, threshold "
            f"{card.threshold:.2f}, held-out {held}, {size_kb} KB, "
            f"trained {card.created_at or 'unknown'} "
            f"({card.provenance or 'unknown source'})")
    return "\n".join(lines)


def _mic_consent_line() -> str:
    """Microphone consent as a PURE READ — no asking. The status
    tool is read-only, and a read-only report must never pop a
    permission question; the asking lives in the enable tool's
    ensure_capture_allowed call, not here."""
    try:
        from .. import permission_grants as _pg

        durable = _pg.get_state("microphone")
    except Exception:  # noqa: BLE001 — unknown, never a crash
        durable = "unknown"
    session = capture_guard.is_capture_approved("microphone")
    if durable == "denied":
        return ("Microphone consent: denied in Settings — enabling "
                "a model will refuse until it is switched back on.")
    if durable == "granted":
        return "Microphone consent: granted (durable)."
    if session:
        return "Microphone consent: granted (this session)."
    return ("Microphone consent: not granted — enabling a model "
            "will ask first.")


def custom_wake_model_status() -> str:
    """Read-only state: active model, detector state, consent."""
    active = get_active()
    if active is None:
        base = "No custom wake-word model is active."
    else:
        cards = {c.slug: c for c in list_models()}
        card = cards.get(active["slug"])
        phrase = card.phrase if card else active["slug"]
        base = (f"Active custom wake model: {phrase!r} "
                f"(threshold {active.get('threshold', _DEFAULT_THRESHOLD):.2f}).")
    try:
        from . import wake_word as _wake_word

        detector = _wake_word.wake_word_detector
        armed = getattr(detector, "_custom_model_slug", None)
        enabled = bool(getattr(detector, "enabled", False))
        base += (f" Detector: wake is {'ON' if enabled else 'OFF'}; "
                 f"custom model {'armed (' + armed + ')' if armed else 'not armed'}.")
    except Exception:  # noqa: BLE001 — status never fails on the detector
        base += " Detector state could not be read."
    base += " " + _mic_consent_line()
    return base


def attach_active_model(detector) -> tuple[bool, str]:
    """Arm the detector with the active model, if one is set.
    The detector calls this at start (mirroring _arm_spotter):
    the outcome is logged either way, never raised."""
    try:
        loaded = load_active_model()
    except ValueError as exc:
        return False, f"active custom wake model is unusable: {exc}"
    if loaded is None:
        return False, "no custom wake model is active"
    model, pointer = loaded
    try:
        ok, detail = detector.set_custom_model(
            pointer["slug"], threshold=pointer.get("threshold"))
    except Exception as exc:  # noqa: BLE001
        return False, f"could not arm custom wake model: {exc}"
    return ok, detail


def register_all(registry) -> None:
    """Register the four wake-training tools (R7-6,
    micro-wake-word pattern)."""
    registry.register(
        name="train_custom_wake_word",
        description="Train a custom wake-word model for a phrase you choose ('computer', 'hey jarvis', your name) from synthetic speech rendered by the on-device voice — no real audio collection. This is a dev-time training run (minutes of CPU + voice rendering) that saves a tiny model file; it does NOT turn wake on, and the trained model is UNEVALUATED until it passes the 1.5 m rig test. When the voice is not provisioned, give wav_dir with positives/ and negatives/ folders of .wav files instead.",
        parameters={
            "type": "object",
            "properties": {
                "phrase": {"type": "string", "description": "The wake phrase to train, 2-4 words, e.g. 'computer' (single words false-fire too easily and are refused)"},
                "voices": {"type": "string", "description": "Comma-separated Kokoro voice names to render the phrase in (default: af_heart,af_bella,am_adam,am_michael)"},
                "renders_per_voice": {"type": "integer", "description": "How many renders of the phrase per voice (default 4; 1-10)"},
                "negative_phrases": {"type": "string", "description": "Comma-separated everyday phrases that are NOT the wake phrase, rendered as negatives (default: the built-in bank)"},
                "epochs": {"type": "integer", "description": "Training epochs for the tiny model (default 60; 1-500)"},
                "threshold": {"type": "number", "description": "Firing threshold for the trained model, 0.05-0.99 (default 0.7)"},
                "wav_dir": {"type": "string", "description": "Folder with positives/ and negatives/ subfolders of .wav files — use instead of voice synthesis when the voice is not provisioned"},
            },
            "required": ["phrase"],
        },
        handler=train_custom_wake_word,
    )
    registry.register(
        name="enable_custom_wake_model",
        description="Make a trained custom wake-word model the active one, so the wake listener uses it when wake is enabled. Mic-consent-gated. This does NOT enable wake itself — wake stays off until the owner enables it, and the model only listens then.",
        parameters={
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name (slug), e.g. 'computer' — see list_custom_wake_models"},
            },
            "required": ["model"],
        },
        handler=enable_custom_wake_model,
    )
    registry.register(
        name="list_custom_wake_models",
        description="List the trained custom wake-word models on this machine, with their phrase, threshold, held-out accuracy and size, and which one is active. It only reports — it never trains or enables anything.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=list_custom_wake_models,
    )
    registry.register(
        name="custom_wake_model_status",
        description="Say whether a custom wake-word model is active, whether wake is on, and whether the microphone consent behind it is granted. It only reports — it never trains or enables anything.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=custom_wake_model_status,
    )
