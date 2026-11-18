"""B-R1-2 (2026-11-09) SmartTurn turn-taking — the ONE dict-returning
wrapper a driver calls to predict when the user's turn is over
(has the user FINISHED speaking, vs a pause mid-thought).

THE GAP (proved first, file/line evidence)
------------------------------------------
A SmartTurn slot ALREADY exists in the tree — this batch builds
only the remainder:

* ``core/voice/smartturn.py`` (v5.58 Wave 3 PR-13) already
  implements the full slot: ``SmartTurnDetector`` (ONNX runner,
  verifies the audio input name against the supplied file's own
  inputs and refuses to guess a schema), ``HeuristicTurnDetector``
  (0.8 s silence floor, 1.5 s endpoint, filler / hold-word rules,
  probability None — a rule has no probability), the
  ``EndOfTurnDetector`` facade (neural when weights exist,
  heuristic otherwise; a neural endpoint below 0.8 s trailing
  silence is suppressed), ``prepare_turn_audio`` (full turn,
  truncated from the BEGINNING to 8 s, zero-padded at the
  BEGINNING, speech at the END — the upstream preparation),
  ``find_smartturn_weights`` (operator-supplied only via
  ``VYUK_SMART_TURN_ONNX`` or the bundled assets dir — never
  downloaded), and an ``evaluate_turn_detector`` harness.
* But the slot is DORMANT in the live path: its only caller is
  ``core/voice/audio_streams_playback.py:150-152``, which reads
  ``EndOfTurnDetector().status()`` for a report. No weights file
  exists anywhere in the tree (proved by find, 2026-11-09 — only
  the ``.py`` file), so the neural path has never run.
* The live utterance assembly is a FIXED SILENCE TIMEOUT:
  ``core/voice/local_client_vad.py:10-12`` — ``VAD_MIN_SPEECH_S``
  0.35, ``VAD_TRAILING_SILENCE_S`` 0.8 (that much trailing
  silence ends an utterance), ``MAX_UTTERANCE_S`` 30.0 — and the
  B-R1-1 wrapper mirrors it (N=11 windows to start, M=25 to end,
  MAX=938). A fixed timeout cannot tell a mid-sentence pause
  from a finished turn: it either interrupts or waits too long.
* ``core/brain/turn_machine.py`` sits ABOVE the detector and
  consumes its events (utterance / barge-in / backchannel), but
  no driver feeds it live detector decisions — the integration
  point exists, the feed does not.
* And no dict-returning driver wrapper exists: the slot returns
  bare decision dicts with no state machine, no turn-open
  tracking, no max-turn flush, no VAD-state gate, no lazy import
  / engine_unavailable degradation, no fake-backend seam.

What is genuinely missing, and what this module adds, is the
driver surface: predict the turn from the turn audio + the
partial transcript + the VAD state, with the AND rule (model
endpoint AND audio stopped), a state machine (listening /
in_turn, reporting turn_complete / max_turn), a max-turn flush,
and an error that can never leave a stale "complete". The
utterance-assembly logic is NOT rewired here (it still ends
utterances on the 0.8 s timeout — a rewire is a separate
decision for the parent). The voice loop is NOT wired (Stage 1
hold stands); Wake stays OFF — SmartTurn predicts turn
completion, it does not listen for a wake word.

CORRECTION TO THE BRIEF (recorded, not hidden)
-----------------------------------------------
The brief said "smart-turn needs the transcript (text) as
input, not just audio". That is WRONG about the upstream model:
SmartTurn v3 is AUDIO-in — a Whisper-Tiny encoder + linear
classifier over the turn's 16 kHz PCM (up to 8 s), returning an
endpoint probability. The transcript is used ONLY by the
heuristic fallback (filler / hold-word / sentence-final
features) and as context. This wrapper therefore takes the turn
AUDIO (int16 PCM bytes) as the model's input, plus the partial
transcript, the VAD state, and the trailing silence as the
gating/context inputs — following the real model, not the
brief's premise.

LOPHOLE FAMILY — "answering at the wrong moment"
-------------------------------------------------
* Interrupting mid-thought (answering before the user finished).
* Waiting too long in silence (the user finished but VYUK keeps
  listening).
* A turn that never ends (a rambling user holds the floor
  forever).
* A turn decision that never resets (a stale "complete" after
  an error).

ONE design closes the class: every turn decision goes through
this module; the wrapper takes the turn audio + the partial
transcript + the VAD state (speaking/ended from B-R1-1) and runs
the real SmartTurn model when weights exist (the honest
heuristic fallback otherwise, always labelled as such); a
turn-complete decision requires the model's endpoint
(probability >= 0.5) AND the audio actually stopped (VAD state
"ended", or trailing silence >= 0.8 s when no VAD state is
reported) — silence alone never ends a turn, the model alone
never ends one while speech is still going; the wrapper reports
a state machine (listening / in_turn, reporting turn_complete /
max_turn) with the probability and the turn age, never a bare
boolean; a max-turn flush ends the turn after 30 s (a rambling
user cannot hold the floor forever); an engine error resets the
machine to listening and returns an honest failure, never a
stale "complete".

Rejected alternatives (recorded):
* A fixed silence timeout alone (cannot tell a pause from a
  finish). (What the live utterance assembly does today; the
  wrapper runs the model first and labels the timeout path as
  the heuristic, never as SmartTurn.)
* The model alone without VAD state (a complete sentence spoken
  over continuing speech is not a turn end). (The AND gate:
  a model endpoint while vad_state == "speaking" is suppressed
  and reported as such.)
* A bare boolean return (the driver cannot tell a pause from an
  end). (The state machine + probability + turn age + reason.)
* A state that survives an error (a stale "complete" after a
  failure). (An engine error resets the machine to listening
  and returns ok=False; a validation refusal never touches the
  machine at all.)
* Inventing a probability for the heuristic (a rule has no
  probability; a fabricated number would let a consumer mistake
  it for a model score). (The heuristic path reports
  probability None, labelled heuristic-end-of-turn — the slot's
  own rule, kept.)
* Treating a failed neural run as a heuristic verdict (the
  slot's contract: a failed ONNX run degrades to decision
  "unknown" on the neural path, and the wrapper reports that
  as a hold with the reason — the driver keeps listening, and
  the max-turn bound still guarantees the turn eventually
  flushes, so no decision path can hold the turn forever).

THE STATE MACHINE
-----------------
Held states: "listening" (no turn open) and "in_turn" (a turn
is open). Report states: "turn_complete" (this call closed the
turn — model endpoint AND audio stopped) and "max_turn" (this
call flushed at the bound). A turn is a SEMANTIC unit spanning
multiple VAD utterances: a VAD-ended utterance whose model
probability is below threshold does NOT close the turn — the
turn stays open and the next utterance continues it, re-scored
with the fuller audio. A turn closes only on turn_complete,
max_turn, reset(), or an engine error.

* ``TURN_THRESHOLD = 0.5`` — the slot's endpoint threshold
  (prob >= 0.5 → endpoint).
* ``MIN_SILENCE_S = 0.8`` — the floor below which a neural
  endpoint is suppressed (mirrors ``VAD_TRAILING_SILENCE_S``
  0.8 and the facade's own suppression rule).
* ``MAX_TURN_S = 30.0`` — a turn open longer than this flushes
  as max_turn (mirrors ``MAX_UTTERANCE_S`` 30.0: a noisy room
  can never hold the turn open forever).

SAMPLE-RATE + INPUT RULES
--------------------------
* Turn audio is int16 PCM bytes at 16 kHz (the slot's form).
  Anything else is refused, never silently resampled (the slot
  raises ValueError on non-16 kHz / odd-length input; the
  wrapper pre-validates and refuses without touching the
  machine). Audio is optional (None): without it only the
  heuristic can decide, labelled as such.
* The transcript, when given, must be a string. A non-text
  transcript is refused. The transcript is heuristic context
  only — it never reaches the neural model.
* ``vad_state`` must be None or one of "silence" / "speaking" /
  "ended" (the B-R1-1 voice_activity states). Anything else is
  refused.
* Every predict carries a timestamp (monotonic seconds,
  caller-supplied or now); the returned dict reports the turn
  age in seconds.

DEPENDENCY (proposed — parent rules)
------------------------------------
* NO new dependency. The engine is the tree's existing slot
  (``core/voice/smartturn.py``) over onnxruntime 1.31.0 —
  already declared (requirements.txt, THIRD_PARTY row 26, lock
  pin 1.31.0; MIT, licence read from its wheel 2026-10-24) and
  already shipped for kokoro-onnx + the B-R1-1 VAD. Turn-taking
  adds NO new onnxruntime weight.
* There is NO smart-turn package on PyPI to declare:
  ``smart-turn`` and ``pipecat-ai-smart-turn`` both 404 on
  PyPI (checked 2026-11-09). The upstream distribution is the
  GitHub repo + Hugging Face weights; the tree's direct-ONNX
  slot over the already-declared runtime is the only route,
  and it adds zero new runtime weight. The upstream repo's own
  requirements.txt is the TRAINING stack (torch 2.9.0,
  torchaudio, transformers, datasets, scikit-learn, librosa,
  soundfile, wandb, torchcodec, onnx, onnxruntime-gpu,
  onnxscript) — none of it is needed to RUN the model, which
  takes only onnxruntime.
* SmartTurn licence, re-read 2026-11-09: BSD-2-Clause (repo
  LICENSE file, "BSD 2-Clause License, Copyright (c) 2024–2025,
  Daily"; GitHub repo licence field BSD-2-Clause; README
  "This is a truly open model (BSD 2-clause license)" and "The
  datasets, training script, and model weights are all open
  source"; Hugging Face pipecat-ai/smart-turn-v3 cardData
  licence bsd-2-clause). Code AND weights are BSD-2-Clause —
  the 2026-10-24 record already cleared it, and the fresh read
  agrees.
* Weights, measured 2026-11-09 from the Hugging Face tree API:
  smart-turn-v3.2-cpu.onnx 8,679,182 B (~8.3 MB, the CPU int8
  file the slot's note names); v3.0 8,757,193 B; v3.1-cpu
  8,679,180 B; GPU variants 32,411,198 B each (~31 MB). NO
  weights file ships in this batch and none is downloaded —
  the operator-supplied pattern (``find_smartturn_weights``)
  is kept exactly — so the weights-provisioning step is still
  owed, not this batch.
* LAZY-imported (function-level static imports, the sympy
  pattern); absence of the engine degrades every call to an
  honest engine_unavailable, nothing else.
"""

import time

__all__ = [
    "engine_available",
    "engine_status",
    "predict",
    "reset",
    "set_backend",
    "reset_backend",
    "TurnPredictor",
    "SAMPLE_RATE",
    "TURN_THRESHOLD",
    "MIN_SILENCE_S",
    "MAX_TURN_S",
]

# -- constants ---------------------------------------------------------
SAMPLE_RATE = 16000
TURN_THRESHOLD = 0.5  # the slot's endpoint threshold (prob >= 0.5)
MIN_SILENCE_S = 0.8  # floor below which a neural endpoint is suppressed
MAX_TURN_S = 30.0  # a turn open longer than this flushes as max_turn

_VAD_STATES = (None, "silence", "speaking", "ended")


class _EngineUnavailable(Exception):
    """The turn-taking engine (core/voice/smartturn.py, or numpy
    under it) is not importable in this interpreter."""


# -- lazy engine import (sympy pattern) ---------------------------------
_SMART = None  # cached dict of engine names, or False when unavailable


def _load_engine():
    """Import the tree's SmartTurn slot once; return the names needed.

    Raises _EngineUnavailable when core/voice/smartturn.py (or
    numpy under it) cannot be imported. onnxruntime is NOT
    required here: the slot imports it only inside
    SmartTurnDetector, so the heuristic fallback loads on
    interpreters without it.
    """
    global _SMART
    if _SMART is None:
        try:
            import numpy as _np
            from ..voice.smartturn import (
                EndOfTurnDetector as _EndOfTurnDetector,
            )
            from ..voice.smartturn import (
                HeuristicTurnDetector as _HeuristicTurnDetector,
            )
            from ..voice.smartturn import (
                SMARTTURN_LICENCE as _Licence,
            )
            from ..voice.smartturn import (
                SMARTTURN_WEIGHTS_NOTE as _WeightsNote,
            )
            from ..voice.smartturn import (
                find_smartturn_weights as _find_weights,
            )
            from ..voice.smartturn import (
                HEURISTIC_MIN_SILENCE_S as _MinSilence,
            )
        except Exception as e:  # noqa: BLE001 — the reason IS the report
            _SMART = False
            raise _EngineUnavailable(
                "the turn-taking engine (core/voice/smartturn.py) "
                f"is not importable in this interpreter ({e}); "
                "turn-taking degrades to engine_unavailable"
            ) from e
        _SMART = {
            "numpy": _np,
            "EndOfTurnDetector": _EndOfTurnDetector,
            "HeuristicTurnDetector": _HeuristicTurnDetector,
            "licence": _Licence,
            "weights_note": _WeightsNote,
            "find_weights": _find_weights,
            "min_silence_s": float(_MinSilence),
        }
    if _SMART is False:
        raise _EngineUnavailable(
            "the turn-taking engine (core/voice/smartturn.py) is "
            "not importable in this interpreter; turn-taking "
            "degrades to engine_unavailable"
        )
    return _SMART


def _onnxruntime_available() -> bool:
    """True when onnxruntime imports in this interpreter."""
    try:
        import onnxruntime  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


# -- real backend (the tree's EndOfTurnDetector facade) -----------------
class _RealBackend:
    """Thin adapter over the tree's turn-taking slot. The facade
    owns the neural-vs-heuristic choice and the silence floor;
    this adapter only routes: audio present -> the facade, audio
    absent -> the heuristic directly (calling the facade with
    pcm=None would hand None to SmartTurnDetector.predict, whose
    failed run would mark the neural session dead — the wrapper
    never lets a missing-audio call kill the model).

    One model run per predict call at most: the facade scores
    the turn audio once. The wrapper never calls predict() AND
    decide() per call.
    """

    def __init__(self, weights=None):
        eng = _load_engine()
        self._eng = eng
        self._facade = eng["EndOfTurnDetector"](weights)
        self._heur = eng["HeuristicTurnDetector"]()

    @property
    def backend(self) -> str:
        return self._facade.backend

    def decide(self, pcm, trailing_silence_s, transcript):
        """Score one turn. Returns the slot's decision dict
        ({decision, probability, backend, reason, features})."""
        if pcm is None:
            return self._heur.decide(
                None, trailing_silence_s=trailing_silence_s,
                transcript=transcript)
        return self._facade.decide(
            pcm, trailing_silence_s=trailing_silence_s,
            transcript=transcript)

    def reset(self) -> None:
        # The slot's detectors are stateless across turns (the
        # ONNX h/c state lives in the VAD, not here); nothing to
        # clear. Kept so the wrapper's reset path is uniform.
        return None

    def status(self) -> dict:
        return self._facade.status()


# -- validation ----------------------------------------------------------
def _refuse(reason):
    return {"ok": False, "reason": reason}


# -- the wrapper ---------------------------------------------------------
class TurnPredictor:
    """One turn predictor. ``backend=None`` builds the real
    engine (lazy import; engine_unavailable when the engine
    cannot import). A test backend may be injected; the
    wrapper's validation, state machine, and reporting rules
    are identical either way.

    A test backend implements ``decide(pcm,
    trailing_silence_s, transcript)`` returning a dict with
    ``decision`` ("endpoint"|"hold"|"unknown"), ``probability``
    (float|None), ``backend`` (str) and ``reason`` (str), plus
    ``reset()`` — the same shape as _RealBackend.
    """

    def __init__(self, backend=None, weights=None,
                 threshold: float = TURN_THRESHOLD,
                 min_silence_s: float = MIN_SILENCE_S,
                 max_turn_s: float = MAX_TURN_S,
                 now_fn=None):
        if isinstance(threshold, bool) or not isinstance(
                threshold, (int, float)):
            raise ValueError("threshold must be a number")
        if not 0.0 < float(threshold) <= 1.0:
            raise ValueError("threshold must be in (0, 1]")
        if isinstance(min_silence_s, bool) or not isinstance(
                min_silence_s, (int, float)):
            raise ValueError("min_silence_s must be a number")
        if float(min_silence_s) < 0.0:
            raise ValueError("min_silence_s must be >= 0")
        if isinstance(max_turn_s, bool) or not isinstance(
                max_turn_s, (int, float)):
            raise ValueError("max_turn_s must be a number")
        if float(max_turn_s) <= 0.0:
            raise ValueError("max_turn_s must be > 0")
        self._backend = backend  # None -> build the real one lazily
        self._weights = weights
        self.threshold = float(threshold)
        self.min_silence_s = float(min_silence_s)
        self.max_turn_s = float(max_turn_s)
        self._now_fn = now_fn or time.monotonic
        self._reset_machine()

    # -- internals ------------------------------------------------------
    def _reset_machine(self) -> None:
        self._state = "listening"  # listening | in_turn
        self._turn_started_at = None  # monotonic seconds, or None

    def _turn_age(self, now: float) -> float:
        if self._turn_started_at is None:
            return 0.0
        return max(0.0, now - self._turn_started_at)

    def _open_turn(self, now: float) -> None:
        if self._turn_started_at is None:
            self._state = "in_turn"
            self._turn_started_at = now

    def _close_turn(self, now: float) -> float:
        """Close the open turn; return its age in seconds at ``now``."""
        age = self._turn_age(now)
        self._reset_machine()
        return age

    def _ensure_backend(self):
        if self._backend is None:
            self._backend = _RealBackend(self._weights)
        return self._backend

    @staticmethod
    def _backend_name(backend) -> str | None:
        # The per-call name from decide() is authoritative;
        # this is only the "current backend" label for reports
        # made before any decision was taken.
        return getattr(backend, "backend", None)

    def _engine_or_none(self):
        """The backend, or None + the engine_unavailable dict
        when the engine cannot load."""
        try:
            return self._ensure_backend(), None
        except _EngineUnavailable as e:
            return None, {"ok": False, "reason": "engine_unavailable",
                          "detail": str(e)}

    def _report(self, ok, state, backend, reason="", **extra):
        out = {
            "ok": ok,
            "state": state,
            "backend": backend,
            "probability": None,
            "turn_open": self._state == "in_turn",
            "turn_s": 0.0,
            "completed": False,
            "timestamp": None,
            "reason": reason,
        }
        out.update(extra)
        return out

    # -- public API ------------------------------------------------------
    def predict(self, pcm=None, *, transcript=None, vad_state=None,
                trailing_silence_s: float = 0.0,
                sample_rate: int = SAMPLE_RATE,
                timestamp: float | None = None) -> dict:
        """Predict whether the user's turn is over, from the turn
        audio + the partial transcript + the VAD state. Returns a
        dict with ok/state/backend/probability/turn age; see the
        module docstring for the state machine and the AND rule.

        A validation refusal never touches the machine. An
        engine error resets the machine to listening and returns
        ok=False — a stale "complete" after an error is
        impossible by construction.
        """
        # 1. Timestamp: plain check, no engine needed.
        if timestamp is None:
            timestamp = self._now_fn()
        try:
            timestamp = float(timestamp)
        except (TypeError, ValueError):
            return self._report(
                False, self._state, None,
                reason="timestamp must be a number (monotonic seconds)")
        if timestamp != timestamp:  # NaN
            return self._report(
                False, self._state, None,
                reason="timestamp must not be NaN")

        # 2. Validation: transcript, VAD state, silence, rate, audio.
        #    None of this touches the machine.
        if transcript is not None and not isinstance(transcript, str):
            return self._report(
                False, self._state, None,
                reason="transcript must be a string (or None) — "
                       "turn-taking reads what was said, not a "
                       "number or an object")
        if vad_state not in _VAD_STATES:
            return self._report(
                False, self._state, None,
                reason=("vad_state must be None or one of "
                        "'silence' / 'speaking' / 'ended' "
                        f"(got {vad_state!r})"))
        if isinstance(trailing_silence_s, bool) or not isinstance(
                trailing_silence_s, (int, float)):
            return self._report(
                False, self._state, None,
                reason="trailing_silence_s must be a number")
        trailing_silence_s = float(trailing_silence_s)
        if trailing_silence_s != trailing_silence_s or \
                trailing_silence_s < 0.0:
            return self._report(
                False, self._state, None,
                reason="trailing_silence_s must be >= 0")
        if isinstance(sample_rate, bool) or not isinstance(
                sample_rate, int):
            return self._report(
                False, self._state, None,
                reason="sample_rate must be an integer (16000)")
        if sample_rate != SAMPLE_RATE:
            return self._report(
                False, self._state, None,
                reason=(f"sample_rate must be {SAMPLE_RATE} (SmartTurn "
                        f"runs at 16 kHz; got {sample_rate}) — "
                        "resampling is the driver's job, never "
                        "silent here"))
        if pcm is not None and not isinstance(pcm, (bytes, bytearray)):
            return self._report(
                False, self._state, None,
                reason="pcm must be bytes of int16 PCM (or None) — "
                       "the turn audio is the model's input")
        if isinstance(pcm, (bytes, bytearray)) and len(pcm) % 2 != 0:
            return self._report(
                False, self._state, None,
                reason="pcm must hold whole int16 samples "
                       "(odd byte length)")

        # 3. Engine (lazy). Without it, nothing can be scored.
        backend, failure = self._engine_or_none()
        if failure is not None:
            failure.update({
                "state": self._state,
                "backend": None,
                "probability": None,
                "turn_open": self._state == "in_turn",
                "turn_s": self._turn_age(timestamp),
                "completed": False,
                "timestamp": timestamp,
            })
            return failure

        # 4. Turn tracking: speaking opens the turn; audio in hand
        #    opens it too (a driver that only calls at utterance
        #    end still gets max-turn tracking from this call on).
        has_audio = isinstance(pcm, (bytes, bytearray)) and len(pcm) > 0
        has_text = isinstance(transcript, str) and bool(transcript.strip())
        if vad_state == "speaking" or (self._state == "listening"
                                        and (has_audio or has_text)):
            self._open_turn(timestamp)

        # 5. Max-turn flush first: a rambling user cannot hold the
        #    floor forever, whatever the model says.
        if self._state == "in_turn" and \
                self._turn_age(timestamp) >= self.max_turn_s:
            age = self._close_turn(timestamp)
            return self._report(
                True, "max_turn", self._backend_name(backend),
                reason=(f"turn open {age:.1f} s (>= {self.max_turn_s:.0f} s "
                        "max) — flushed, never held forever"),
                turn_s=round(age, 3),
                completed=True,
                timestamp=timestamp)

        # 6. Score the turn (one model run at most).
        try:
            result = backend.decide(
                pcm, trailing_silence_s, transcript)
        except Exception as e:  # noqa: BLE001 — engine error: reset, never stale
            self._reset_machine()
            try:
                backend.reset()
            except Exception:  # noqa: BLE001 — teardown must not raise
                pass
            return self._report(
                False, "listening", self._backend_name(backend),
                reason=f"the turn-taking engine failed: {e}",
                reset_on_error=True,
                timestamp=timestamp,
            )
        decision = result.get("decision")
        probability = result.get("probability")
        ran_backend = result.get("backend") or self._backend_name(backend)
        model_reason = result.get("reason") or ""
        age = self._turn_age(timestamp)

        # 7. The AND rule: a turn completes only when the model
        #    says endpoint AND the audio has actually stopped.
        audio_stopped = (vad_state == "ended"
                         or trailing_silence_s >= self.min_silence_s)
        if decision == "endpoint" and vad_state == "speaking":
            # The model alone never ends a turn while speech is
            # still going: a complete sentence spoken over
            # continuing speech is not a turn end.
            return self._report(
                True, "listening", ran_backend,
                reason=("model endpoint suppressed: VAD still "
                        "speaking — the turn stays open"),
                probability=probability,
                turn_s=round(age, 3),
                timestamp=timestamp)
        if decision == "endpoint" and not audio_stopped:
            # The model alone never ends a turn on silence that
            # has not reached the floor: the facade suppresses
            # this itself, but the wrapper enforces the rule too,
            # so no backend can end a turn early.
            return self._report(
                True, "listening", ran_backend,
                reason=("model endpoint suppressed: trailing "
                        f"silence {trailing_silence_s:.2f} s below "
                        f"the {self.min_silence_s:.1f} s floor"),
                probability=probability,
                turn_s=round(age, 3),
                timestamp=timestamp)
        if decision == "endpoint" and audio_stopped:
            self._close_turn(timestamp)
            return self._report(
                True, "turn_complete", ran_backend,
                reason=model_reason,
                probability=probability,
                turn_s=round(age, 3),
                completed=True,
                timestamp=timestamp)
        # "hold" or "unknown": the turn stays open (a VAD-ended
        # utterance below threshold is a pause in the same turn,
        # re-scored with fuller audio next time). An "unknown"
        # (failed neural run) is a hold with the reason recorded
        # — the driver keeps listening, and the max-turn bound
        # still guarantees the turn eventually flushes.
        return self._report(
            True, "listening", ran_backend,
            reason=(model_reason or
                    "turn held: model probability below threshold"),
            probability=probability,
            turn_s=round(age, 3),
            timestamp=timestamp)

    def reset(self) -> dict:
        """Clear the state machine (abandon the open turn). Returns
        a dict; never raises (a reset that cannot reach the
        backend still clears this machine)."""
        self._reset_machine()
        if self._backend is not None:
            try:
                self._backend.reset()
            except Exception:  # noqa: BLE001 — reset must not raise
                pass
        return self._report(True, "listening",
                            self._backend_name(self._backend)
                            if self._backend is not None else None,
                            reason="")

    @property
    def state(self) -> str:
        """The machine state right now ("listening" or "in_turn").
        "turn_complete" / "max_turn" are reports for the predict
        call that closed a turn, not held states."""
        return self._state


# -- module-level default engine (driver surface) -----------------------
_DEFAULT = None
_BACKEND_OVERRIDE = None


def _default_engine():
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = TurnPredictor(backend=_BACKEND_OVERRIDE)
    return _DEFAULT


def engine_available() -> bool:
    """True when the turn-taking engine module imports in this
    interpreter. The backend may still be the heuristic fallback
    (no weights / no onnxruntime) — see engine_status()."""
    try:
        _load_engine()
        return True
    except _EngineUnavailable:
        return False


def engine_status() -> dict:
    """Honest engine report: importability, the backend that
    would actually run, weights path, licence position. Never
    builds an ONNX session (weights presence + onnxruntime
    importability are read, not run)."""
    try:
        eng = _load_engine()
    except _EngineUnavailable as e:
        return {
            "ok": False,
            "available": False,
            "backend": None,
            "smartturn_available": False,
            "weights_path": None,
            "degraded": True,
            "reason": str(e),
            "licence": None,
            "weights_note": None,
        }
    weights = eng["find_weights"]()
    smartturn_available = bool(weights is not None
                               and _onnxruntime_available())
    return {
        "ok": True,
        "available": True,
        "backend": ("smartturn-onnx" if smartturn_available
                    else "heuristic-end-of-turn"),
        "smartturn_available": smartturn_available,
        "weights_path": str(weights) if weights else None,
        "degraded": not smartturn_available,
        "reason": (None if smartturn_available else
                   "no smart-turn.onnx found, or onnxruntime absent "
                   "— the heuristic fallback runs, labelled as such"),
        "licence": eng["licence"],
        "weights_note": eng["weights_note"],
    }


def set_backend(backend):
    """Test seam (clipboard idiom): replace the backend the
    default engine uses. Resets the default engine so the next
    call builds a fresh one on the new backend."""
    global _DEFAULT, _BACKEND_OVERRIDE
    _BACKEND_OVERRIDE = backend
    _DEFAULT = None
    return {"ok": True}


def reset_backend():
    """Undo set_backend(): the default engine builds the real
    engine again on next use."""
    global _DEFAULT, _BACKEND_OVERRIDE
    _BACKEND_OVERRIDE = None
    _DEFAULT = None
    return {"ok": True}


def predict(pcm=None, *, transcript=None, vad_state=None,
            trailing_silence_s: float = 0.0,
            sample_rate: int = SAMPLE_RATE,
            timestamp: float | None = None) -> dict:
    return _default_engine().predict(
        pcm, transcript=transcript, vad_state=vad_state,
        trailing_silence_s=trailing_silence_s,
        sample_rate=sample_rate, timestamp=timestamp)


def reset() -> dict:
    return _default_engine().reset()
