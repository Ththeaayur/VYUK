"""End-of-turn detection slot (v5.58, Wave 3, PR-13).

Licence position (primary source, read 2026-10-24):
- SmartTurn CODE + WEIGHTS are BSD 2-Clause: the upstream
  repository github.com/pipecat-ai/smart-turn reports "License:
  BSD 2-Clause 'Simplified' License (BSD-2-Clause)", and its
  README states "This is a truly open model (BSD 2-clause
  license)" and "The datasets, training script, and model
  weights are all open source." The weights live on Hugging
  Face (pipecat-ai/smart-turn-v3, linked from that README).
  Licence is therefore CLEARED — unlike the programme's
  pre-check expectation, SmartTurn is NOT licence-blocked.
- What is still missing is the WEIGHTS FILE itself: no
  smart-turn ONNX is bundled in this tree and this module never
  downloads one. Until an operator places a pinned ONNX file
  (see ``find_smartturn_weights``) and the coordinator pins its
  sha256, the neural detector reports unavailable and the
  heuristic fallback runs. That weights-provisioning step is
  STILL-OWED; the licence step is not.

Architecture (upstream README, the contract the turn-behaviour
workstream builds on):
- SmartTurn v3: Whisper-Tiny encoder + linear classifier, ~8M
  params, int8 ONNX ~8 MB, 16 kHz mono PCM, up to 8 seconds,
  23 languages, ~10 ms CPU inference. It is designed to run
  ONLY during silence, after a lightweight VAD (e.g. Silero)
  detects it — never on every frame.
- Input preparation the upstream notes demand: give the FULL
  current turn (truncated from the BEGINNING to ~8 s if longer),
  padded at the BEGINNING with zeros when shorter, speech at
  the END of the vector. ``prepare_turn_audio`` implements
  exactly that, so the harness and the future ONNX runner share
  one preparation step.
- Re-run on the whole turn (not just the new segment) when
  speech resumes before the model finishes.

THE FALLBACK IS NEVER CALLED "SmartTurn": the heuristic
detector's backend string is ``"heuristic-end-of-turn"`` and its
decisions carry ``backend`` + ``reason`` fields saying so. Any
consumer that needs the neural verdict must check
``result["backend"] == "smartturn-onnx"``.

Interface consumed by the turn-behaviour workstream::

    from core.smartturn import EndOfTurnDetector
    det = EndOfTurnDetector()
    result = det.decide(pcm_16k, trailing_silence_s=1.2,
                        transcript="see you tomorrow")
    # result keys: decision ("endpoint"|"hold"|"unknown"),
    #   probability (float|None), backend (str), reason (str),
    #   features (dict)
    det.status()  # backend, smartturn_available, weights_path,
                  # licence, degraded

``decide`` never raises for missing weights or a failed ONNX
run: those degrade to ``decision="unknown"`` on the neural path
or to the heuristic verdict on the facade path, with the reason
recorded.
"""

from __future__ import annotations

import os
from .. import config_spec
import re
from pathlib import Path
from typing import Any

import numpy as np

SMARTTURN_SAMPLE_RATE = 16000
SMARTTURN_MAX_SECONDS = 8.0
SMARTTURN_LICENCE = (
    "BSD-2-Clause (pipecat-ai/smart-turn: GitHub repo licence "
    "field + README 'truly open model (BSD 2-clause)'; README "
    "states datasets, training script and model weights are all "
    "open source)"
)
SMARTTURN_WEIGHTS_NOTE = (
    "Weights NOT bundled and never downloaded here. Closing "
    "step: place a pinned smart-turn ONNX (v3/v3.2, CPU int8) "
    "at <bundled assets>/smartturn/smart-turn.onnx or "
    "VYUK_SMART_TURN_ONNX, record its sha256, and verify the "
    "ONNX input tensor names on first load — the runner below "
    "refuses to guess a schema."
)

# Heuristic fallback tuning. These are deliberately conservative:
# a wrong "endpoint" cuts the user off mid-thought (the expensive
# error); a wrong "hold" only waits for more silence.
HEURISTIC_MIN_SILENCE_S = 0.8
HEURISTIC_ENDPOINT_SILENCE_S = 1.5
_FILLERS = {
    "um", "uh", "hmm", "hm", "er", "ah", "like", "you", "know",
    "so", "well", "actually", "basically",
}
_TRAILING_HOLD_WORDS = {
    "and", "but", "or", "so", "because", "however", "although",
    "then", "well", "um", "uh", "hmm", "er",
}


def find_smartturn_weights(
        explicit: str | os.PathLike | None = None) -> Path | None:
    """Locate an operator-supplied SmartTurn ONNX. Never downloads."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = config_spec.env("VYUK_SMART_TURN_ONNX").strip()
    if env:
        candidates.append(Path(env))
    try:
        from . import local_assets  # Phase 3 V2: local_assets now in core/voice/; V1a-1 interim closed

        root = local_assets.bundled_assets_dir()
        if root is not None:
            candidates.append(
                Path(root) / "smartturn" / "smart-turn.onnx")
    except Exception:  # noqa: BLE001
        pass
    for path in candidates:
        try:
            if path.is_file():
                return path
        except Exception:  # noqa: BLE001
            continue
    return None


def prepare_turn_audio(pcm_bytes: bytes,
                       sample_rate: int = SMARTTURN_SAMPLE_RATE) -> np.ndarray:
    """Upstream input preparation: full turn, speech at the end.

    Returns float32 mono at 16 kHz, exactly 8 s long: longer
    turns are truncated from the beginning, shorter ones are
    zero-padded at the beginning. Non-16 kHz input raises
    ValueError rather than being silently resampled into a
    wrong timescale.
    """
    if int(sample_rate) != SMARTTURN_SAMPLE_RATE:
        raise ValueError(
            f"SmartTurn takes 16 kHz PCM, got {sample_rate}")
    if not isinstance(pcm_bytes, (bytes, bytearray)):
        raise ValueError("pcm_bytes must be bytes of int16 PCM")
    raw = bytes(pcm_bytes)
    if len(raw) % 2 != 0:
        raise ValueError("pcm_bytes must hold whole int16 samples")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    target = int(SMARTTURN_MAX_SECONDS * SMARTTURN_SAMPLE_RATE)
    if audio.size > target:
        return audio[-target:]
    if audio.size < target:
        return np.concatenate(
            [np.zeros(target - audio.size, dtype=np.float32), audio])
    return audio


def heuristic_features(transcript: str | None,
                       trailing_silence_s: float) -> dict[str, Any]:
    """The features the heuristic fallback decides on. Pure."""
    text = " ".join(str(transcript or "").split())
    words = re.findall(r"[A-Za-z']+", text.lower())
    last = words[-1] if words else ""
    ends_sentence = bool(text) and text[-1] in ".?!"
    return {
        "word_count": len(words),
        "last_word": last,
        "ends_with_filler": last in _FILLERS,
        "ends_with_hold_word": last in _TRAILING_HOLD_WORDS,
        "ends_sentence": ends_sentence,
        "trailing_silence_s": float(trailing_silence_s),
    }


class HeuristicTurnDetector:
    """Clearly-labelled heuristic fallback. NOT SmartTurn.

    Rules (conservative: cutting a user off is the expensive
    error):
    - silence < 0.8 s -> hold (too early to judge);
    - trailing filler / hold-word ("um", "and", "so", ...) ->
      hold however long the silence;
    - silence >= 1.5 s with a sentence-final transcript, or any
      transcript-free silence >= 1.5 s after speech -> endpoint;
    - otherwise hold.
    ``probability`` is None: a rule has no probability, and
    inventing one would let a consumer mistake it for a model
    score.
    """

    backend = "heuristic-end-of-turn"

    def decide(self, pcm_bytes: bytes | None = None, *,
               trailing_silence_s: float = 0.0,
               transcript: str | None = None,
               **_ignored: Any) -> dict[str, Any]:
        feats = heuristic_features(transcript, trailing_silence_s)
        silence = feats["trailing_silence_s"]
        if silence < HEURISTIC_MIN_SILENCE_S:
            decision, reason = "hold", "silence below 0.8 s floor"
        elif feats["ends_with_filler"] or feats["ends_with_hold_word"]:
            decision, reason = (
                "hold",
                f"trailing hold word {feats['last_word']!r}")
        elif silence >= HEURISTIC_ENDPOINT_SILENCE_S:
            decision, reason = "endpoint", "silence >= 1.5 s, no hold cue"
        else:
            decision, reason = "hold", "silence in the 0.8–1.5 s grey zone"
        return {
            "decision": decision,
            "probability": None,
            "backend": self.backend,
            "reason": reason,
            "features": feats,
        }

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "smartturn_available": False,
            "weights_path": None,
            "licence": SMARTTURN_LICENCE,
            "degraded": True,
            "reason": "heuristic fallback — not a neural turn model",
        }


class SmartTurnDetector:
    """SmartTurn ONNX slot. Runs only when weights are present.

    The ONNX input schema is verified against the supplied file's
    own input names at load time: the runner looks for an input
    whose name suggests audio (``input``/``audio``/``pcm``) and
    refuses (available=False, error recorded) when it cannot
    identify one — it never guesses tensor shapes into a model
    it has not seen. Until a real weights file is exercised, this
    class's ``predict`` path is unproven against a live graph and
    must be re-verified on first weights delivery (see
    SMARTTURN_WEIGHTS_NOTE); that is stated here, not hidden.
    """

    backend = "smartturn-onnx"

    def __init__(self, weights: str | os.PathLike | None = None):
        self.weights_path = find_smartturn_weights(weights)
        self._session = None
        self._audio_input: str | None = None
        self._error: str | None = None
        if self.weights_path is None:
            self._error = ("no SmartTurn ONNX found (see "
                           "find_smartturn_weights)")
            return
        try:
            import onnxruntime as ort

            self._session = ort.InferenceSession(
                str(self.weights_path),
                providers=["CPUExecutionProvider"])
            names = [i.name for i in self._session.get_inputs()]
            for name in names:
                low = name.lower()
                if any(k in low for k in ("input", "audio", "pcm", "wav")):
                    self._audio_input = name
                    break
            if self._audio_input is None:
                self._session = None
                self._error = (f"cannot identify audio input in ONNX "
                               f"inputs {names!r}")
        except Exception as e:  # noqa: BLE001
            self._session = None
            self._error = f"ONNX session failed: {e}"

    @property
    def available(self) -> bool:
        return self._session is not None

    def predict(self, pcm_bytes: bytes, *,
                sample_rate: int = SMARTTURN_SAMPLE_RATE,
                **_ignored: Any) -> dict[str, Any]:
        """Endpoint probability for one full turn's audio.

        Returns decision "unknown" (probability None) when the
        model is unavailable or a run fails — never a fabricated
        probability. The caller supplies the trailing-silence /
        transcript context via the facade; this slot scores audio
        only, as the upstream model does.
        """
        if self._session is None:
            return {
                "decision": "unknown",
                "probability": None,
                "backend": self.backend,
                "reason": self._error or "unavailable",
                "features": {},
            }
        try:
            audio = prepare_turn_audio(pcm_bytes, sample_rate)
            out = self._session.run(
                None, {self._audio_input: audio.reshape(1, -1)})
            prob = float(np.asarray(out[0], dtype=np.float32).reshape(-1)[0])
            prob = min(1.0, max(0.0, prob))
            return {
                "decision": "endpoint" if prob >= 0.5 else "hold",
                "probability": prob,
                "backend": self.backend,
                "reason": "SmartTurn ONNX endpoint probability >= 0.5",
                "features": {"endpoint_probability": prob},
            }
        except Exception as e:  # noqa: BLE001
            self._session = None
            self._error = f"ONNX run failed: {e}"
            return {
                "decision": "unknown",
                "probability": None,
                "backend": self.backend,
                "reason": self._error,
                "features": {},
            }

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.backend if self.available else "smartturn-unavailable",
            "smartturn_available": self.available,
            "weights_path": (str(self.weights_path)
                             if self.weights_path else None),
            "licence": SMARTTURN_LICENCE,
            "weights_note": SMARTTURN_WEIGHTS_NOTE,
            "degraded": not self.available,
            "reason": self._error,
        }


class EndOfTurnDetector:
    """Facade: SmartTurn when weights exist, heuristic otherwise.

    ``decide`` signature is the turn-behaviour contract:
    ``decide(pcm, trailing_silence_s=..., transcript=...)``.
    The neural path scores audio only (upstream design) and the
    facade still honours the silence floor: a neural endpoint
    with < 0.8 s trailing silence is reported as "hold" with a
    reason, because SmartTurn is specified to run during
    silence, not mid-speech.
    """

    def __init__(self, weights: str | os.PathLike | None = None,
                 prefer: str = "smartturn"):
        self._smart = SmartTurnDetector(weights)
        self._heur = HeuristicTurnDetector()
        self.backend = ("smartturn-onnx" if self._smart.available
                        else "heuristic-end-of-turn")

    def decide(self, pcm_bytes: bytes, *,
               trailing_silence_s: float = 0.0,
               transcript: str | None = None) -> dict[str, Any]:
        if self._smart.available:
            result = self._smart.predict(pcm_bytes)
            if (result["decision"] == "endpoint"
                    and float(trailing_silence_s) < HEURISTIC_MIN_SILENCE_S):
                result = dict(result, decision="hold",
                              reason=("SmartTurn endpoint suppressed: "
                                      "trailing silence below 0.8 s"))
            self.backend = "smartturn-onnx"
            return result
        self.backend = "heuristic-end-of-turn"
        return self._heur.decide(
            pcm_bytes, trailing_silence_s=trailing_silence_s,
            transcript=transcript)

    def status(self) -> dict[str, Any]:
        if self._smart.available:
            return self._smart.status()
        merged = self._heur.status()
        merged["weights_path"] = (
            str(self._smart.weights_path)
            if self._smart.weights_path else None)
        merged["weights_note"] = SMARTTURN_WEIGHTS_NOTE
        return merged


def evaluate_turn_detector(detector: Any,
                           cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Harness: confusion counts + endpoint precision/recall.

    Each case: ``{"pcm": bytes|None, "trailing_silence_s": float,
    "transcript": str|None, "expected": "endpoint"|"hold"}``.
    Cases with an expected value outside endpoint/hold are
    ignored (counted in ``ignored``). No case is ever scored
    against a fabricated probability: detectors returning
    "unknown" count as misses in ``unknown``.
    """
    tp = fp = tn = fn = unknown = ignored = 0
    for case in cases:
        expected = case.get("expected")
        if expected not in ("endpoint", "hold"):
            ignored += 1
            continue
        try:
            got = detector.decide(
                case.get("pcm") or b"\x00\x00",
                trailing_silence_s=float(
                    case.get("trailing_silence_s", 0.0)),
                transcript=case.get("transcript"))["decision"]
        except Exception:  # noqa: BLE001
            got = "unknown"
        if got == "unknown":
            unknown += 1
        elif expected == "endpoint" and got == "endpoint":
            tp += 1
        elif expected == "endpoint":
            fn += 1
        elif got == "endpoint":
            fp += 1
        else:
            tn += 1
    total = tp + fp + tn + fn + unknown
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return {
        "cases": total,
        "ignored": ignored,
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "unknown": unknown,
        "endpoint_precision": precision,
        "endpoint_recall": recall,
        "backend": getattr(detector, "backend", "?"),
    }


def session_turn_detector(session):
    """The session's ONE turn detector (lazy).

    GAPS-25 (V-2): the live pipeline consults the turn detector
    here, through this function — one EndOfTurnDetector per
    session, created on first use and kept on the session. One
    instance per session (not per turn) so a neural backend, when
    weights exist, is not rebuilt per utterance; and no
    module-global detector (a shared one would be a second turn
    home — V-7). The facade is stateless across turns, so sharing
    one instance within a session changes nothing about its
    decisions. Never raises: a session that cannot hold the
    attribute still gets a working detector back.
    """
    detector = getattr(session, "_turn_detector", None)
    if detector is None:
        detector = EndOfTurnDetector()
        try:
            session._turn_detector = detector
        except Exception:  # noqa: BLE001 — attribute or not, it works
            pass
    return detector


def consult_turn_detector(session, pcm, transcript, trailing_silence_s):
    """Ask the session's turn detector for its verdict on a
    finished turn, and record it on the session.

    GAPS-25 (V-2): this is the pipeline's consultation of the
    turn detector — the live mic loop ends utterances on its own
    trailing-silence rule, and every finished turn ALSO asks the
    detector what it thinks (neural when weights exist, heuristic
    otherwise) with the turn's real inputs (audio, transcript,
    trailing silence). The verdict is OBSERVATION ONLY: it is
    recorded as ``session._last_turn_verdict`` and never read for
    control flow — dispatch proceeds exactly as before, so the
    voice loop's behaviour does not change. The record is what
    makes the detector's judgement visible (and comparable
    against the loop's rule) for the first time; letting it drive
    endpointing is a later behaviour decision, not this batch.
    Never raises: a failed consultation records an "unknown"
    verdict with the reason, never a fabricated decision.
    """
    try:
        verdict = session_turn_detector(session).decide(
            pcm, trailing_silence_s=float(trailing_silence_s),
            transcript=transcript)
    except Exception as exc:  # noqa: BLE001 — never break a turn
        verdict = {
            "decision": "unknown",
            "probability": None,
            "backend": "heuristic-end-of-turn",
            "reason": f"turn consultation failed: {exc}",
            "features": {},
        }
    try:
        session._last_turn_verdict = verdict
    except Exception:  # noqa: BLE001 — the verdict is a record, not a gate
        pass
    return verdict
