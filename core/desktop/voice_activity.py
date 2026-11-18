"""B-R1-1 (2026-11-09) Silero VAD speech detector — the ONE dict-returning
wrapper a driver calls for voice activity detection (is the user
speaking right now, vs silence, vs background noise).

THE GAP (proved first, file/line evidence)
------------------------------------------
A Silero VAD ALREADY exists in the tree — this batch builds only
the remainder:

* ``core/voice/vad.py`` (v5.58 Wave 3 PR-11, 300 lines) already
  implements the Silero ONNX interface directly: ``SileroVAD``
  (onnxruntime session, h/c state [2, 1, 64], speech probability
  per frame, threshold 0.5), ``EnergyVAD`` (RMS threshold 400,
  the previous VAD), and the ``VoiceActivityDetector`` slot
  (Silero when weights exist, energy otherwise). Weights are
  NEVER downloaded or bundled — operator-supplied only via
  ``find_silero_weights`` (``VYUK_SILERO_VAD_ONNX`` env var,
  bundled assets dir, or an explicit path). No ``.onnx`` file
  exists anywhere in the tree (proved by find, 2026-11-09).
* But the slot is DORMANT in the live path: its only caller is
  ``core/voice/audio_streams_playback.py:134-136``, which reads
  ``status()`` for a report. The live mic loop
  (``core/voice/local_client_mic.py:125``) scores speech with
  bare RMS energy: ``is_speech = rms >=
  _effective_vad_threshold(estimator)``. No neural VAD ever runs
  on the mic. The utterance assembly there (``VAD_MIN_SPEECH_S``
  0.35, ``VAD_TRAILING_SILENCE_S`` 0.8, ``MAX_UTTERANCE_S`` 30.0,
  ``NoiseFloorEstimator``) is wired to the energy score, not to
  Silero.
* And no dict-returning driver wrapper exists: the slot returns
  bare bools/floats/None — no state machine, no frame counts, no
  timestamps, no lazy import / engine_unavailable degradation,
  no fake-backend seam for tests.

What is genuinely missing, and what this module adds, is the
driver surface: feed 16 kHz frames in, get a state machine out
(silence / speaking / ended) with frame counts and timestamps,
N consecutive speech windows to start, M consecutive silence
windows to end, a max-utterance flush, and an error that can
never leave a stale state. The mic loop is NOT rewired here
(it still scores energy — a rewire is a separate decision for
the parent). Boundary: B11's ``run_goal`` persistence is for
GOAL STEPS; this wrapper is for SPEECH DETECTION. The voice
loop is NOT wired (Stage 1 hold stands); Wake stays OFF — the
VAD detects speech, it does not listen for a wake word.

LOPHOLE FAMILY — "speech that is missed, or noise that is
mistaken for speech, or an utterance cut in the wrong place"
---------------------------------------------------------------
* Speech missed because the threshold is too high (a quiet
  speaker never triggers).
* Noise mistaken for speech (a door slam, a fan, music — a
  false trigger that wakes the pipeline).
* An utterance cut too early (a pause mid-sentence ends the
  turn).
* An utterance cut too late (trailing silence kept, delaying
  the response).
* A VAD state that never resets (a stuck "speaking" after an
  error).

ONE design closes the class: every mic frame goes through this
module; the wrapper runs the real Silero model (or the honest
energy fallback, always labelled as such) on 16 kHz mono
512-sample windows; a speech start requires N consecutive
speech windows (not one); a speech end requires M consecutive
silence windows (not one — a pause mid-sentence does not end
the turn); the wrapper reports a state machine (silence /
speaking / ended) with the frame counts, never a bare boolean;
a stuck state is impossible by construction (every window
advances the state, and an engine error resets the machine to
silence and returns an honest failure, never a stale state).

Rejected alternatives (recorded):
* A bare energy threshold (noise triggers it). (What the live
  mic loop does today; the wrapper runs the model first and
  labels the energy path as degraded, never as Silero.)
* A single-frame decision (a door slam triggers it, a pause
  ends it). (N/M consecutive windows, below.)
* A bare boolean return (the driver cannot tell a pause from
  an end). (The state machine + frame counts + ended list.)
* A state that survives an error (a stuck "speaking" after a
  failure). (An engine error resets the machine to silence
  and returns ok=False; a validation refusal never touches
  the machine at all.)
* Calling the engine's probability() AND is_speech() per
  window (two model runs per window — the Silero LSTM state
  would advance twice per window). (One call per window: the
  wrapper calls probability() once on the Silero path and
  compares against the engine's own threshold, and calls
  is_speech() once on the energy path, preserving each
  backend's own threshold semantics.)
* Silent resampling of non-16 kHz audio (a resampled frame is
  different audio; the wrapper must not silently score audio
  the caller did not intend). (Non-16 kHz is refused.)

THE STATE MACHINE
-----------------
Windows are 512 samples at 16 kHz = 32 ms each (the Silero
native window; the mic delivers 1024-sample chunks, so one
mic chunk is two windows). Counts mirror the mic loop's own
tunings, so the wrapper reproduces the mic loop's timing when
driven by the same audio:

* ``N_START_FRAMES = 11`` — 11 x 32 ms = 0.352 s of speech to
  start (mirrors ``VAD_MIN_SPEECH_S`` 0.35 s: a voiced blip
  shorter than this never opens an utterance).
* ``M_END_FRAMES = 25`` — 25 x 32 ms = 0.8 s of silence to end
  (mirrors ``VAD_TRAILING_SILENCE_S`` 0.8 s: a mid-sentence
  pause never ends the turn).
* ``MAX_SPEECH_FRAMES = 938`` — 938 x 32 ms = 30.016 s of
  speech ends the utterance (mirrors ``MAX_UTTERANCE_S`` 30.0 s:
  a noisy room can never hold the state open forever).

States: "silence" (no utterance open), "speaking" (utterance
open), "ended" (an utterance ended during this call and the
machine is now idle — the ended utterance's counts ride in
``ended_utterances``). An utterance that ends mid-call and a
new one that starts in the same call is never lost: every
utterance that ends during the call is listed in
``ended_utterances``, and the state is "speaking" (the current
truth) with ``ended`` True.

SAMPLE-RATE + FRAME RULES
--------------------------
* Sample rate must be 16000 (the tree's ONNX call hardcodes
  sr=16000; the 8 kHz variant needs a different weights file).
  Anything else is refused, never silently resampled.
* A frame's length must be a positive multiple of 512 samples.
  The wrapper splits it into 512-sample windows and scores
  each in order. A partial window is refused (it is not a frame
  the model can score; the driver buffers).
* Frames are float32 mono in [-1, 1] (the engine's form). An
  int16/int32 numpy array is scaled to [-1, 1] (its dtype
  declares the encoding); a list/tuple of numbers is read as
  float values; bytes are refused (ambiguous encoding — the
  driver converts explicitly). Non-finite samples (NaN/inf)
  are refused.
* Every feed carries a timestamp (monotonic seconds,
  caller-supplied or now); the returned dict reports the last
  window's timestamp.

DEPENDENCY (proposed — parent rules)
------------------------------------
* NO new dependency. The engine is the tree's existing direct
  ONNX implementation (``core/voice/vad.py``) over onnxruntime
  1.31.0 — already declared (requirements.txt, THIRD_PARTY
  row 26, lock pin 1.31.0; MIT, licence read from its wheel
  2026-10-24) and already shipped for kokoro-onnx. The VAD
  adds NO new onnxruntime weight. numpy is already declared
  (requirements.txt:31) and present in both interpreters.
* The ``silero_vad`` PyPI package (6.2.3) is NOT declared:
  it hard-requires ``torch>=1.12.0`` (a top-level
  requires_dist, not an extra) — a ~539 MB wheel for a ~2 MB
  model. That is a markitdown-class weight rejection (B-D3):
  the tree's direct-ONNX implementation does the same job
  over the already-declared runtime.
* Silero VAD code licence, re-read 2026-11-09: MIT (repo
  LICENSE file, "MIT License, Copyright (c) 2020-present
  Silero Team"; GitHub API repo licence field MIT, SPDX MIT;
  PyPI classifiers "License :: OSI Approved :: MIT License").
* Silero VAD WEIGHTS licence, re-read 2026-11-09 — PROPOSED,
  parent rules: the README body says "Published under
  permissive license (MIT) Silero VAD has zero strings
  attached"; the header badge IMAGE says "License-MIT" and
  links to the MIT LICENSE file; the PyPI package (which ships
  the weights as package data) is classified MIT. The
  "CC BY-NC 4.0" text appears ONLY as that badge's ALT TEXT —
  a stale copy-paste from silero-models (whose STT models are
  CC BY-NC). The 2026-10-24 record flagged the ambiguity
  correctly from the text then read; the fresh read finds the
  badge image, the LICENSE file, the README body, the API
  field, and the PyPI classifiers all saying MIT. NO weights
  file ships in this batch and none is downloaded — the
  operator-supplied pattern (``find_silero_weights``) is kept
  exactly — so the ruling gates future weights-bundling, not
  this batch.
* LAZY-imported (function-level static imports, the sympy
  pattern); absence of the engine degrades every call to an
  honest engine_unavailable, nothing else.
"""

import time

__all__ = [
    "engine_available",
    "engine_status",
    "feed",
    "reset",
    "set_backend",
    "reset_backend",
    "SpeechDetector",
    "SAMPLE_RATE",
    "WINDOW_SAMPLES",
    "WINDOW_S",
    "N_START_FRAMES",
    "M_END_FRAMES",
    "MAX_SPEECH_FRAMES",
]

# -- constants ---------------------------------------------------------
SAMPLE_RATE = 16000
WINDOW_SAMPLES = 512  # Silero native window: 512 samples @ 16 kHz = 32 ms
WINDOW_S = WINDOW_SAMPLES / SAMPLE_RATE  # 0.032
# Counts mirror the mic loop's tunings (core/voice/local_client_vad.py):
# VAD_MIN_SPEECH_S 0.35 -> 11 windows (0.352 s) to start;
# VAD_TRAILING_SILENCE_S 0.8 -> 25 windows (0.8 s) to end;
# MAX_UTTERANCE_S 30.0 -> 938 windows (30.016 s) hard flush.
N_START_FRAMES = 11
M_END_FRAMES = 25
MAX_SPEECH_FRAMES = 938


class _EngineUnavailable(Exception):
    """The VAD engine (core/voice/vad.py, or numpy under it) is not
    importable in this interpreter."""


# -- lazy engine import (sympy pattern) ---------------------------------
_VAD = None  # cached dict of engine names, or False when unavailable


def _load_engine():
    """Import the tree's VAD engine once; return the names needed.

    Raises _EngineUnavailable when core/voice/vad.py (or numpy
    under it) cannot be imported. onnxruntime is NOT required
    here: the engine module imports it only inside SileroVAD,
    so the energy fallback loads on interpreters without it.
    """
    global _VAD
    if _VAD is None:
        try:
            import numpy as _np
            from ..voice.vad import (
                EnergyVAD as _EnergyVAD,
            )
            from ..voice.vad import (
                SILERO_DEFAULT_THRESHOLD as _Threshold,
            )
            from ..voice.vad import (
                SILERO_LICENCE as _Licence,
            )
            from ..voice.vad import (
                SILERO_WEIGHTS_LICENCE_NOTE as _WeightsNote,
            )
            from ..voice.vad import (
                SileroVAD as _SileroVAD,
            )
            from ..voice.vad import (
                find_silero_weights as _find_weights,
            )
            from ..voice.vad import (
                onnxruntime_available as _ort_available,
            )
        except Exception as e:  # noqa: BLE001 — the reason IS the report
            _VAD = False
            raise _EngineUnavailable(
                "the VAD engine (core/voice/vad.py) is not importable "
                f"in this interpreter ({e}); speech detection degrades "
                "to engine_unavailable"
            ) from e
        _VAD = {
            "numpy": _np,
            "SileroVAD": _SileroVAD,
            "EnergyVAD": _EnergyVAD,
            "threshold": float(_Threshold),
            "licence": _Licence,
            "weights_note": _WeightsNote,
            "find_weights": _find_weights,
            "onnxruntime_available": _ort_available,
        }
    if _VAD is False:
        raise _EngineUnavailable(
            "the VAD engine (core/voice/vad.py) is not importable in "
            "this interpreter; speech detection degrades to "
            "engine_unavailable"
        )
    return _VAD


# -- real backend (the tree's SileroVAD + EnergyVAD) --------------------
class _RealBackend:
    """Thin adapter over the tree's VAD slot, with per-window
    backend tracking. The slot's own fallback logic is mirrored
    here, but the wrapper — not the slot — owns the per-window
    bookkeeping, so the backend that ran on each window is known
    exactly (the slot's is_speech() does not update its backend
    attribute, and calling probability() as well would run the
    model twice per window).

    One model run per window: on the Silero path, probability()
    is called once and compared against the engine's own
    threshold; on the energy path, is_speech() is called once,
    preserving that backend's own threshold semantics. When the
    Silero session dies mid-stream (probability() returns None),
    the backend flips to energy for the rest of this instance's
    life and the flip is reported (never silently relabelled).
    """

    def __init__(self, weights=None):
        eng = _load_engine()
        self._eng = eng
        self._silero = eng["SileroVAD"](weights)
        self._energy = eng["EnergyVAD"]()
        self._use_silero = bool(self._silero.available)
        self._flipped = False

    @property
    def backend(self) -> str:
        return "silero-onnx" if self._use_silero else "energy"

    @property
    def flipped(self) -> bool:
        return self._flipped

    def is_speech(self, window):
        """Score one 512-sample window. Returns
        (is_speech, backend, flipped_this_call)."""
        if self._use_silero:
            prob = self._silero.probability(window)
            if prob is None:
                # Session died mid-stream: flip to energy, and
                # score THIS window on energy too, so the window
                # still gets an honest verdict, labelled energy.
                self._use_silero = False
                self._flipped = True
                return (bool(self._energy.is_speech(window)),
                        "energy", True)
            return (bool(prob >= self._silero.threshold),
                    "silero-onnx", False)
        return (bool(self._energy.is_speech(window)), "energy", False)

    def reset(self) -> None:
        # Clears the Silero h/c state; a dead session stays dead
        # (the flip is permanent for this instance — reset() must
        # never revive a backend that failed).
        self._silero.reset()

    def status(self) -> dict:
        return {
            "backend": self.backend,
            "silero_available": bool(self._silero.available),
            "weights_path": (str(self._silero.weights_path)
                             if self._silero.weights_path else None),
            "flipped_to_energy": self._flipped,
        }


# -- validation ----------------------------------------------------------
def _refuse(reason):
    return {"ok": False, "reason": reason}


def _validate_counts(start_frames, end_frames, max_speech_frames):
    for name, value in (("start_frames", start_frames),
                        ("end_frames", end_frames),
                        ("max_speech_frames", max_speech_frames)):
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{name} must be an integer"
        if value < 1:
            return f"{name} must be >= 1"
    if max_speech_frames < start_frames:
        return ("max_speech_frames must be >= start_frames (an "
                "utterance that cannot start can never end)")
    return None


# -- the wrapper ---------------------------------------------------------
class SpeechDetector:
    """One speech detector. ``backend=None`` builds the real
    engine (lazy import; engine_unavailable when the engine
    cannot import). A test backend may be injected; the
    wrapper's validation, state machine, and reporting rules
    are identical either way.

    A test backend implements ``is_speech(window)`` returning
    ``(is_speech, backend, flipped)`` and ``reset()`` — the
    same shape as _RealBackend.
    """

    def __init__(self, backend=None, weights=None,
                 start_frames: int = N_START_FRAMES,
                 end_frames: int = M_END_FRAMES,
                 max_speech_frames: int = MAX_SPEECH_FRAMES):
        err = _validate_counts(start_frames, end_frames,
                               max_speech_frames)
        if err:
            raise ValueError(err)
        self._backend = backend  # None -> build the real one lazily
        self._weights = weights
        self.start_frames = start_frames
        self.end_frames = end_frames
        self.max_speech_frames = max_speech_frames
        self._reset_machine()

    # -- internals ------------------------------------------------------
    def _reset_machine(self) -> None:
        self._state = "silence"  # silence | speaking (ended is a report, not a state)
        self._speech_frames = 0  # speech windows in the open utterance
        self._silence_frames = 0  # trailing silence windows in the open utterance
        self._speech_run = 0  # consecutive speech windows (build-up included)
        self._silence_run = 0  # consecutive silence windows

    def _ensure_backend(self):
        if self._backend is None:
            self._backend = _RealBackend(self._weights)
        return self._backend

    @staticmethod
    def _backend_name(backend) -> str | None:
        # The per-window name from is_speech() is authoritative;
        # this is only the "current backend" label for reports
        # made before any window was scored.
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
            "backend_changed": False,
            "windows": 0,
            "speech_frames": self._speech_frames,
            "silence_frames": self._silence_frames,
            "speech_run": self._speech_run,
            "silence_run": self._silence_run,
            "ended": False,
            "ended_utterances": [],
            "timestamp": None,
            "reason": reason,
        }
        out.update(extra)
        return out

    # -- public API ------------------------------------------------------
    def feed(self, frame, sample_rate: int = SAMPLE_RATE,
             timestamp: float | None = None) -> dict:
        """Score one frame (a positive multiple of 512 samples at
        16 kHz mono) and advance the state machine. Returns a
        dict with ok/state/backend/frame counts; see the module
        docstring for the state machine and the frame rules.

        A validation refusal never touches the machine. An
        engine error resets the machine to silence and returns
        ok=False — a stuck "speaking" after an error is
        impossible by construction.
        """
        # 1. Sample rate: plain check, no engine needed.
        if isinstance(sample_rate, bool) \
                or not isinstance(sample_rate, int):
            return self._report(
                False, self._state, None,
                reason="sample_rate must be an integer (16000)")
        if sample_rate != SAMPLE_RATE:
            return self._report(
                False, self._state, None,
                reason=(f"sample_rate must be {SAMPLE_RATE} (Silero "
                        f"runs at 16 kHz; got {sample_rate}) — "
                        "resampling is the driver's job, never "
                        "silent here"))
        if timestamp is None:
            timestamp = time.monotonic()
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

        # 2. Engine (lazy). Without it, nothing can be scored.
        backend, failure = self._engine_or_none()
        if failure is not None:
            failure.update({
                "state": self._state,
                "backend": None,
                "backend_changed": False,
                "windows": 0,
                "speech_frames": self._speech_frames,
                "silence_frames": self._silence_frames,
                "speech_run": self._speech_run,
                "silence_run": self._silence_run,
                "ended": False,
                "ended_utterances": [],
                "timestamp": timestamp,
            })
            return failure

        # 3. Frame conversion + validation.
        eng = _load_engine()
        np = eng["numpy"]
        if isinstance(frame, (bytes, str)) or frame is None:
            return self._report(
                False, self._state, self._backend_name(backend),
                reason=("frame must be float32 mono audio (a numpy "
                        "array or a sequence of numbers) — bytes are "
                        "refused: their encoding (int16 vs float32) "
                        "is ambiguous"))
        try:
            arr = np.asarray(frame)
        except Exception as e:  # noqa: BLE001
            return self._report(
                False, self._state, self._backend_name(backend),
                reason=f"frame could not be read as audio: {e}")
        try:
            if np.issubdtype(arr.dtype, np.integer):
                # The dtype declares the encoding: int16/int32
                # samples scale to [-1, 1] by their own range.
                # (Parent fix 2026-11-09: the original divided
                # every integer dtype by 32768.0, so an int32
                # frame was 65536x too loud — a quiet int32
                # frame counted as speech. int16 and int32 now
                # scale by their own ranges; other integer
                # dtypes are refused, not guessed.)
                if arr.dtype == np.int16:
                    arr = arr.astype(np.float32) / 32768.0
                elif arr.dtype == np.int32:
                    arr = arr.astype(np.float32) / 2147483648.0
                else:
                    return self._report(
                        False, self._state, self._backend_name(backend),
                        reason=(f"integer frames must be int16 or "
                                f"int32 (got {arr.dtype}) — the dtype "
                                "declares the encoding, and no other "
                                "integer encoding is supported"))
            else:
                arr = arr.astype(np.float32)
            arr = arr.reshape(-1)
        except Exception as e:  # noqa: BLE001
            return self._report(
                False, self._state, self._backend_name(backend),
                reason=f"frame could not be read as mono audio: {e}")
        if arr.size == 0:
            return self._report(
                False, self._state, self._backend_name(backend),
                reason="frame is empty (a frame must hold audio)")
        if arr.size % WINDOW_SAMPLES != 0:
            return self._report(
                False, self._state, self._backend_name(backend),
                reason=(f"frame length must be a positive multiple "
                        f"of {WINDOW_SAMPLES} samples (got {arr.size}) "
                        "— a partial window is not a frame the model "
                        "can score; the driver buffers"))
        try:
            finite = bool(np.isfinite(arr).all())
        except Exception:  # noqa: BLE001
            finite = False
        if not finite:
            return self._report(
                False, self._state, self._backend_name(backend),
                reason="frame holds non-finite samples (NaN/inf)")

        # 4. Score each 512-sample window in order.
        windows = arr.size // WINDOW_SAMPLES
        ended_utterances: list[dict] = []
        backend_changed = False
        last_backend = backend.backend
        try:
            for i in range(windows):
                window = arr[i * WINDOW_SAMPLES:(i + 1) * WINDOW_SAMPLES]
                is_speech, ran_backend, flipped = backend.is_speech(window)
                last_backend = ran_backend
                if flipped:
                    backend_changed = True
                if is_speech:
                    self._speech_run += 1
                    self._silence_run = 0
                    if self._state == "silence":
                        if self._speech_run >= self.start_frames:
                            self._state = "speaking"
                            # The build-up windows ARE speech: they
                            # count toward the utterance (the mic
                            # path's pre-roll keeps the onset audio).
                            self._speech_frames = self._speech_run
                            self._silence_frames = 0
                    else:  # speaking
                        self._speech_frames += 1
                        self._silence_frames = 0
                        if self._speech_frames >= self.max_speech_frames:
                            ended_utterances.append(
                                self._close_utterance(
                                    "max_utterance", timestamp))
                else:
                    self._silence_run += 1
                    self._speech_run = 0
                    if self._state == "speaking":
                        self._silence_frames = self._silence_run
                        if self._silence_run >= self.end_frames:
                            ended_utterances.append(
                                self._close_utterance("silence", timestamp))
        except Exception as e:  # noqa: BLE001 — engine error: reset, never stale
            self._reset_machine()
            try:
                backend.reset()
            except Exception:  # noqa: BLE001 — teardown must not raise
                pass
            return self._report(
                False, "silence", last_backend,
                reason=f"the VAD engine failed while scoring: {e}",
                reset_on_error=True,
                timestamp=timestamp,
            )

        # "ended" is the event report for a call that closed an
        # utterance and left the machine idle; if a new utterance
        # opened after the close in the same call, the state is
        # "speaking" (the current truth) and `ended` still says
        # an utterance ended (its record rides in
        # ended_utterances).
        state = ("ended" if (ended_utterances
                             and self._state == "silence")
                 else self._state)
        return self._report(
            True, state, last_backend,
            backend_changed=backend_changed,
            windows=windows,
            ended=bool(ended_utterances),
            ended_utterances=ended_utterances,
            timestamp=timestamp,
        )

    def _close_utterance(self, end_reason: str,
                         timestamp: float) -> dict:
        """Close the open utterance and reset the machine. Returns
        the ended utterance's record."""
        record = {
            "speech_frames": self._speech_frames,
            "silence_frames": self._silence_frames,
            "utterance_s": round(self._speech_frames * WINDOW_S, 3),
            "end_reason": end_reason,
            "ended_at": timestamp,
        }
        self._reset_machine()
        return record

    def reset(self) -> dict:
        """Clear the state machine AND the underlying model state
        (Silero h/c). Returns a dict; never raises (a reset that
        cannot reach the backend still clears this machine)."""
        self._reset_machine()
        if self._backend is not None:
            try:
                self._backend.reset()
            except Exception:  # noqa: BLE001 — reset must not raise
                pass
        return self._report(True, "silence",
                            self._backend_name(self._backend)
                            if self._backend is not None else None,
                            reason="")

    @property
    def state(self) -> str:
        """The machine state right now ("silence" or "speaking").
        "ended" is a report for the feed that ended an utterance,
        not a held state."""
        return self._state


# -- module-level default engine (driver surface) -----------------------
_DEFAULT = None
_BACKEND_OVERRIDE = None


def _default_engine():
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = SpeechDetector(backend=_BACKEND_OVERRIDE)
    return _DEFAULT


def engine_available() -> bool:
    """True when the VAD engine module imports in this
    interpreter (numpy present). The backend may still be the
    energy fallback (no weights / no onnxruntime) — see
    engine_status()."""
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
            "silero_available": False,
            "weights_path": None,
            "degraded": True,
            "reason": str(e),
            "licence": None,
            "weights_licence_note": None,
        }
    weights = eng["find_weights"]()
    silero_available = bool(weights is not None
                            and eng["onnxruntime_available"]())
    return {
        "ok": True,
        "available": True,
        "backend": "silero-onnx" if silero_available else "energy",
        "silero_available": silero_available,
        "weights_path": str(weights) if weights else None,
        "degraded": not silero_available,
        "reason": (None if silero_available else
                   "no silero_vad.onnx found, or onnxruntime absent "
                   "— the energy fallback runs, labelled as such"),
        "licence": eng["licence"],
        "weights_licence_note": eng["weights_note"],
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


def feed(frame, sample_rate: int = SAMPLE_RATE,
         timestamp: float | None = None) -> dict:
    return _default_engine().feed(frame, sample_rate, timestamp)


def reset() -> dict:
    return _default_engine().reset()
