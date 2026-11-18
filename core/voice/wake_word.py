"""
Wake Word Detection Engine for Voice AI Assistant.
Provides hands-free voice activation ("Hey VYUK" — the Vosk
spotter hears it as the phonetic pair "via oak" / "hey via oak";
see WakeWordDetector. The pre-rename wake alias is gone —
v5.50, programme item 44).

ENGINE ORDER (v5.2): a grammar-constrained Vosk spotter is the
PRIMARY engine whenever its model is on disk (core/voice/local_assets.py
fetches it with the voice stage). The recognizer's grammar admits
only the wake phrases plus the "[unk]" reject token, and a wake
fires only on an endpointed (final) result — never on a partial,
and never when the phrase is glued to a preceding "[unk]" (under
the pre-rename grammar the decoder split "supernova" and
"casanova" into "[unk] nova"; that tail is somebody else's word,
not a wake). Random speech,
TV noise and loud sounds therefore cannot fire it the way the old
energy trigger could. When Vosk or its model is unavailable the
detector falls back to the previous chain — SpeechRecognition
(pocketsphinx / Google) on voice bursts, then the offline cadence
trigger, which remains exactly the last resort it always was.
Which engine armed is PRINTED at loop start: silent degradation
is how the energy trigger once became the product.

SENSITIVITY + CUSTOM PHRASE (v5.17): WAKE_PHRASE_ALT adds one
user-chosen phrase to the DEFAULT word list (callers passing an
explicit list get exactly that list). WAKE_SENSITIVITY scales
only the legacy chain's thresholds — the SpeechRecognition
energy threshold and the cadence trigger — inversely: keener
ears, lower bars. It deliberately does NOT touch the Vosk
spotter: grammar-constrained matching and the "[unk]" rule are
correctness walls, not tuning knobs.

CONFIDENCES + STABLE-PARTIAL EXPERIMENT (v5.45, EARS A6): the
spotter now enables Vosk word confidences (SetWords) and logs
every final result's per-word confidences to the senses
timeline, so one release of real data can size the wake-latency
problem before any firing policy changes. The firing policy
itself is unchanged by default: endpointed finals only. Behind
config.wake_partial_fire (env WAKE_PARTIAL_FIRE, default OFF) a
STRICT stable-partial experiment may fire early: a partial fires
only when its text is exactly the full wake phrase, byte-
identical across two consecutive polls, with no "[unk]"
anywhere. With the flag OFF the partial stream is never even
read — the code path is behaviour-identical to pre-v5.45.
"""

import json
import threading
import time
from collections.abc import Callable

import numpy as np

from ..config import config  # Phase 3 V1a-2: config stays flat

# Post-fire refractory for the Vosk path (the legacy paths keep
# their own longer cooldowns below).
_WAKE_REFRACTORY_S = 2.5


def _match_wake_phrase(text: str, wake_words: list[str]) -> str | None:
    """The wake phrase in a transcript, or None.

    Whole-phrase matching on word boundaries: "via oak" matches
    the transcripts "via oak" and "hey via oak", never as part of
    a longer word it happens to sit inside — the embedded-word
    trap is rejected here in text space, whatever produced the
    transcript."""
    if not text:
        return None
    cleaned = " ".join(text.replace("[unk]", " ").split())
    if not cleaned:
        return None
    padded = f" {cleaned} "
    for phrase in wake_words:
        if f" {phrase} " in padded:
            return phrase
    return None


def _match_from_result(result: dict, wake_words: list[str]) -> str | None:
    """The wake phrase in a Vosk final-result JSON dict, or None.

    Token-span matching over the result text, longest phrase first,
    with one hard rule on top of _match_wake_phrase's boundaries: a
    span immediately preceded by an "[unk]" token does NOT count.
    Grammar decoding splits words it does not know around the one
    syllable it does — under the pre-rename grammar "supernova"
    and "casanova" both decoded as "[unk] nova" — and that trailing
    token is the tail of a longer word, not somebody calling her
    name. (A genuine phrase standing after a mumbled filler still
    matches through its later span, when that span is not itself
    glued to an "[unk]".)"""
    text = (result.get("text") or "").lower()
    tokens = text.split()
    if not tokens:
        return None
    for phrase in sorted(wake_words, key=len, reverse=True):
        phrase_tokens = phrase.split()
        span = len(phrase_tokens)
        for i in range(len(tokens) - span + 1):
            if tokens[i:i + span] == phrase_tokens:
                if i > 0 and tokens[i - 1] == "[unk]":
                    continue  # glued reject-token — a longer word's tail
                return phrase
    return None


def _result_word_confidences(result: dict) -> list[tuple[str, float | None]]:
    """(word, confidence) pairs from a Vosk result dict's "result"
    array (present because the spotter enables SetWords). A word
    with no recorded confidence pairs with None; a result with no
    "result" array (older decoders, "[unk]"-only finals) yields
    an empty list. Pure — never raises on odd shapes."""
    pairs: list[tuple[str, float | None]] = []
    try:
        entries = result.get("result") or []
    except AttributeError:
        return pairs
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        word = str(entry.get("word") or "")
        if not word:
            continue
        conf = entry.get("conf")
        try:
            conf = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf = None
        pairs.append((word, conf))
    return pairs


def _emit_wake_confidence_log(message: str) -> None:
    """The established channel for wake-confidence lines: the
    senses timeline (core/senses_log.py — the same append-only
    store the watchers feed), kind "wake_word", plus a console
    line so a live session shows it too. Never raises: senses_log
    swallows its own failures by contract, and the import + print
    are guarded here so logging can never break spotting. Tests
    stub this seam to capture the lines."""
    try:
        print(f"[WakeWord] {message}")
    except Exception:  # noqa: BLE001 — logging must never break spotting
        pass
    try:
        from .. import senses_log  # Phase 3 V1a-2: senses_log stays flat

        senses_log.append_event("wake_word", message)
    except Exception:  # noqa: BLE001 — ditto
        pass


def _log_wake_confidences(result: dict, source: str = "final") -> None:
    """Logs one decoded result's per-word confidences (v5.45, A6):
    "wake confidences (final): via=0.93 oak=0.87" — the evidence
    base for sizing any future partial-firing policy. A result
    carrying no word array still logs its text, so an empty line
    is never mistaken for missing data."""
    text = str((result or {}).get("text") or "").strip()
    pairs = _result_word_confidences(result or {})
    if pairs:
        detail = " ".join(
            f"{word}={conf:.2f}" if conf is not None else f"{word}=?"
            for word, conf in pairs)
        _emit_wake_confidence_log(
            f"wake confidences ({source}): {detail}")
    elif text:
        _emit_wake_confidence_log(
            f"wake confidences ({source}): text={text!r} (no word data)")


def _match_partial_text(partial: str, wake_words: list[str]) -> str | None:
    """The STRICT stable-partial rule's match half (v5.45, A6):
    the wake phrase when `partial` IS the wake phrase — the whole
    partial, exactly, nothing before or after it — else None.
    Any "[unk]" token anywhere rejects outright (a glued reject
    token means the decoder is mid-way through somebody else's
    word — the supernova trap), and a mere prefix ("via", "hey
    via") or a phrase embedded in more speech never matches:
    unlike finals, a partial gets no span search at all."""
    text = (partial or "").strip().lower()
    if not text:
        return None
    if "[unk]" in text.split():
        return None
    for phrase in wake_words:
        if text == phrase:
            return phrase
    return None


def _resample_int16(samples: np.ndarray, src_rate: int,
                    dst_rate: int) -> np.ndarray:
    """Linear-interp resample of an int16 mono buffer. The mic runs
    at 16 kHz by default — exactly the model's rate — so this is a
    pass-through unless SAMPLE_RATE_IN was changed."""
    if src_rate == dst_rate or samples.size == 0:
        return samples
    n_out = int(round(samples.size * dst_rate / src_rate))
    if n_out <= 0:
        return samples[:0]
    x_old = np.linspace(0.0, 1.0, num=samples.size)
    x_new = np.linspace(0.0, 1.0, num=n_out)
    return np.interp(x_new, x_old,
                     samples.astype(np.float64)).astype(np.int16)


class _VoskSpotter:
    """Grammar-constrained keyword spotter over the Vosk small
    en-us model. The recognizer's grammar admits ONLY the wake
    phrases and "[unk]", so its output vocabulary is the wake set
    by construction.

    This is the detector's recognizer seam: it (and the tests) feed
    raw int16 PCM at the model's sample rate and get back the
    matched wake phrase or None.

    FIRING POLICY (measured against the real decoder, v5.2):
    endpointed final results only. Partials NEVER fire — the
    decoder's partial stream cheerfully reports the wake phrase
    in the middle of unrelated sentences while it is still
    deciding, and an exact-phrase partial is no evidence at all. Finals are
    matched by _match_from_result, whose "[unk]"-gluing rule is
    what keeps "supernova" silent.

    v5.45 (A6): SetWords is ON so final results carry per-word
    confidences, which accept()/flush() log (see
    _log_wake_confidences) — evidence only; matching is unchanged.
    The ONE exception to "partials never fire" is the flagged
    experiment (config.wake_partial_fire, default OFF): see
    _accept_partial. With the flag off, accept() never reads the
    partial stream at all."""

    SAMPLE_RATE = 16000

    def __init__(self, model_path, wake_words: list[str],
                 partial_fire: bool | None = None):
        import vosk  # heavy + optional — the caller guards this

        vosk.SetLogLevel(-1)
        self.wake_words = [w.lower() for w in wake_words]
        grammar = json.dumps([*self.wake_words, "[unk]"])
        self._model = vosk.Model(str(model_path))
        self._recognizer = vosk.KaldiRecognizer(
            self._model, float(self.SAMPLE_RATE), grammar)
        # v5.45 (A6): word confidences ON — final results now
        # carry the "result" array _log_wake_confidences reads.
        self._recognizer.SetWords(True)
        if partial_fire is None:
            partial_fire = bool(
                getattr(config, "wake_partial_fire", False))
        self._partial_fire = bool(partial_fire)
        self._last_partial: str | None = None

    def reset(self) -> None:
        """Drops any half-decoded utterance (used after a fire, and
        whenever the detector skips buffered backlog)."""
        self._last_partial = None
        self._recognizer.Reset()

    def _partial_result_text(self) -> str | None:
        """The recognizer's current partial text, or None when it
        cannot be read. The experiment's read seam — tests script
        it via a fake recognizer; production reads Vosk's
        PartialResult() JSON."""
        try:
            data = json.loads(self._recognizer.PartialResult())
        except Exception:  # noqa: BLE001 — a bad frame is a miss
            return None
        if not isinstance(data, dict):
            return None
        partial = data.get("partial")
        return partial if isinstance(partial, str) else None

    def _accept_partial(self) -> str | None:
        """The stable-partial experiment (v5.45, A6) — called ONLY
        when the flag is on and no endpoint has arrived. A partial
        fires only when ALL of these hold: the partial text is
        exactly the full wake phrase (the strict rule in
        _match_partial_text — no "[unk]", no extra words, no
        prefix), AND it is byte-identical to the previous poll's
        partial (the same phrase, seen stable twice — a decoder
        still changing its mind never fires). Anything else just
        updates the stability tracker. The detector loop's 2.5 s
        refractory applies to a partial fire exactly as to a
        final fire — both return through accept()."""
        text = self._partial_result_text()
        if text is None:
            return None
        previous = self._last_partial
        self._last_partial = text
        if previous is None or text != previous:
            return None  # first sighting, or still changing
        phrase = _match_partial_text(text, self.wake_words)
        if phrase:
            self._last_partial = None
        return phrase

    def accept(self, pcm: bytes) -> str | None:
        """Feeds one int16 PCM chunk. Returns the wake phrase when
        an endpointed utterance matches, else None — plus, only
        with the A6 experiment flag on, when a stable partial
        matches the strict rule (see _accept_partial)."""
        if not pcm:
            return None
        if not self._recognizer.AcceptWaveform(pcm):
            if not self._partial_fire:
                return None  # no endpoint yet — partials never fire
            return self._accept_partial()
        self._last_partial = None
        try:
            result = json.loads(self._recognizer.Result())
        except Exception:  # noqa: BLE001 — a bad frame is a miss, not a crash
            return None
        _log_wake_confidences(result, "final")
        return _match_from_result(result, self.wake_words)

    def flush(self) -> str | None:
        """Matches whatever utterance is still buffered (stream
        end). The live detector relies on natural endpoints; tests
        and shutdown paths drain with this."""
        try:
            result = json.loads(self._recognizer.FinalResult())
        except Exception:  # noqa: BLE001
            return None
        _log_wake_confidences(result, "flush")
        return _match_from_result(result, self.wake_words)


class WakeWordDetector:
    def __init__(self, wake_words: list[str] | None = None,
                 sensitivity: float | None = None):
        if wake_words is None:
            # What she answers to is "hey vyuk" (VYUK rename,
            # v5.36); the grammar words are the phonetic pair
            # the Vosk model actually hears for it — "vyuk"
            # itself is out-of-vocabulary and can never match.
            words = ["via oak", "hey via oak"]
            # Custom wake phrase (v5.17, A14): joins the DEFAULT
            # list only. Callers passing an explicit list get
            # exactly that list — a hidden extra phrase would be
            # a nasty surprise wherever a list is pinned.
            alt = (config.wake_phrase_alt or "").strip().lower()
            if alt and alt not in words:
                words.append(alt)
        else:
            words = list(wake_words)
        self.wake_words = [w.lower() for w in words]
        # Sensitivity (v5.17, A6) — legacy chain only. The base
        # values are the pre-v5.17 literals (SR energy 300,
        # cadence 0.18 burst / 0.08 quiet-ceiling / 0.22 recent);
        # thresholds a voice must EXCEED divide by sensitivity,
        # the quiet ceiling scales with it. At 1.0 every attribute
        # equals the old literal exactly.
        if sensitivity is None:
            sensitivity = config.wake_sensitivity
        try:
            sensitivity = float(sensitivity)
        except (TypeError, ValueError):
            sensitivity = 1.0
        if not sensitivity > 0.0:  # also catches NaN
            sensitivity = 1.0
        self.sensitivity = min(4.0, max(0.25, sensitivity))
        self.sr_energy_threshold = 300.0 / self.sensitivity
        self.cadence_burst_level = 0.18 / self.sensitivity
        self.cadence_recent_min = 0.22 / self.sensitivity
        self.cadence_prior_max = 0.08 * self.sensitivity
        self._running = False
        self._thread: threading.Thread | None = None
        # Voice-control batch (2026-11-16, W1-wake): the
        # generation retires a stopped detector thread even if
        # a fast start() sets _running True again before it
        # observes the stop — without it, stop→start can run
        # two detectors. _cooldown_until is instance state (not
        # a loop-local) so the refractory is shared, never
        # per-thread.
        self._wake_gen = 0
        self._cooldown_until = 0.0
        self._arm_spotter_state: str | None = None
        self.enabled = False
        self.on_wake: Callable[[], None] | None = None
        # R7-6 micro-wake-word: a trained custom model, armed
        # separately from wake itself. None until set_custom_model
        # loads one; wake stays OFF regardless of arming.
        self._custom_scorer = None
        self._custom_model_slug: str | None = None

    def set_sensitivity(self, sensitivity: float):
        """Re-aims the legacy-chain thresholds live (v5.17, Ears
        A6) — the same sanitising and formulas as __init__, so the
        Settings slider takes effect without restarting the
        detector. The Vosk grammar path is untouched, as always:
        sensitivity never weakens matching correctness."""
        try:
            sensitivity = float(sensitivity)
        except (TypeError, ValueError):
            sensitivity = 1.0
        if not sensitivity > 0.0:  # also catches NaN
            sensitivity = 1.0
        self.sensitivity = min(4.0, max(0.25, sensitivity))
        self.sr_energy_threshold = 300.0 / self.sensitivity
        self.cadence_burst_level = 0.18 / self.sensitivity
        self.cadence_recent_min = 0.22 / self.sensitivity
        self.cadence_prior_max = 0.08 * self.sensitivity

    def set_custom_model(self, slug=None, threshold=None):
        """Arm (or disarm, with slug=None) a trained custom wake
        model. Loads the model from the store, verifies its card
        sha256 + feature shape, and builds the streaming scorer.
        Returns (ok, detail) and never raises — a bad model is a
        refusal, and arming a model does NOT enable wake."""
        if slug is None:
            self._custom_scorer = None
            self._custom_model_slug = None
            return True, "custom wake model disarmed"
        try:
            from .wake_model import WakeModel, models_dir
        except Exception as exc:  # noqa: BLE001
            return False, f"custom wake model unavailable: {exc}"
        try:
            model_path = models_dir() / (str(slug) + ".npz")
            model = WakeModel.load(model_path)
        except ValueError as exc:
            return False, str(exc)
        try:
            from .wake_model import StreamingWakeScorer

            self._custom_scorer = StreamingWakeScorer(
                model, threshold=threshold)
            self._custom_model_slug = str(slug)
        except Exception as exc:  # noqa: BLE001
            self._custom_scorer = None
            self._custom_model_slug = None
            return False, f"could not arm custom wake model: {exc}"
        return True, (f"custom wake model armed: "
                      f"{model.card.phrase!r} (threshold "
                      f"{self._custom_scorer.threshold:.2f})")

    def _arm_custom_model(self):
        """Arm the ACTIVE model at listener start (mirrors
        _arm_spotter): the outcome is printed either way, never
        raised. No pointer set means no model — the Vosk spotter
        carries wake alone, exactly as before R7-6."""
        try:
            from .wake_training import attach_active_model
        except Exception as exc:  # noqa: BLE001
            print(f"[WakeWord] Custom wake model unavailable ({exc})")
            return
        ok, detail = attach_active_model(self)
        if ok:
            print(f"[WakeWord] {detail}")
        else:
            print(f"[WakeWord] {detail}")

    def start(self, audio_manager):
        """Starts background wake word listener using audio_manager's mic stream."""
        # Voice-control batch (2026-11-16, W1-wake): a stopped
        # thread that has not yet exited must never be joined by
        # a second detector — wait for it (bounded), and retire
        # it by generation even if the wait times out.
        _old = getattr(self, "_thread", None)
        if _old is not None and _old.is_alive():
            if self._running:
                return
            _old.join(timeout=1.0)
        if self._running:
            return
        self._running = True
        self._audio_manager = audio_manager
        self._arm_custom_model()
        self._wake_gen = int(getattr(self, "_wake_gen", 0)) + 1
        _gen = self._wake_gen
        self._thread = threading.Thread(
            target=self._detection_loop, daemon=True,
            name="WakeWordDetector", args=(_gen,),
        )
        self._thread.start()
        print(f"[WakeWord] Detector started. Wake words: {self.wake_words}")

    def stop(self):
        self._running = False
        # Voice-control batch (2026-11-16, W1-wake): bump the
        # generation so a stopped thread can never be
        # resurrected by a fast start() setting _running True
        # again before it observes the stop.
        self._wake_gen = int(getattr(self, "_wake_gen", 0)) + 1
        _old = getattr(self, "_thread", None)
        if _old is not None and _old.is_alive() \
                and _old is not threading.current_thread():
            _old.join(timeout=1.0)
        self._thread = None

    def trigger_now(self):
        """Manually trigger wake-word activation."""
        if self.on_wake:
            self.on_wake()

    def _make_spotter(self):
        """(spotter, detail): builds the Vosk spotter, or (None,
        why-not). Detail names the model path on success. Never
        raises — every failure is a fallback, not a crash."""
        try:
            from . import local_assets  # Phase 3 V2: local_assets now in core/voice/; V1a-2 interim closed
        except Exception as e:  # noqa: BLE001
            return None, f"local_assets import failed: {e}"
        try:
            if not local_assets.wake_model_ready():
                return None, "wake model not downloaded yet"
            path = local_assets.wake_model_path()
        except Exception as e:  # noqa: BLE001
            return None, f"wake model check failed: {e}"
        try:
            spotter = _VoskSpotter(path, self.wake_words)
        except Exception as e:  # noqa: BLE001 — vosk missing / broken model
            return None, f"vosk unavailable: {e}"
        return spotter, str(path)

    def _arm_spotter(self):
        """Arms the Vosk grammar spotter when possible and LOGS the
        outcome either way — which engine is live must never be a
        mystery again. Voice-control batch (2026-11-16, W9): the
        re-arm retry used to print the fallback line every 5 s
        for the life of the process when the model was
        permanently unavailable — now it logs only on a state
        change."""
        spotter, detail = self._make_spotter()
        _state = "armed" if spotter is not None else "unavailable"
        if getattr(self, "_arm_spotter_state", None) != _state:
            self._arm_spotter_state = _state
            if spotter is not None:
                print(f"[WakeWord] Engine armed: vosk grammar spotter ({detail})")
            else:
                print(f"[WakeWord] Vosk spotter unavailable ({detail}); "
                      "falling back to the speech-recognition / cadence chain")
        return spotter

    def _detection_loop(self, gen=None):
        # Attempt to load local speech recognizer
        recognizer = None
        sr = None
        try:
            import speech_recognition as sr
            recognizer = sr.Recognizer()
            recognizer.energy_threshold = self.sr_energy_threshold
            recognizer.dynamic_energy_threshold = True
        except ImportError:
            pass

        # Primary engine (v5.2): the Vosk grammar spotter, when its
        # model is on disk. Re-armed periodically while unavailable —
        # the model may land mid-run (a first-run download finishing
        # while this detector idles).
        spotter = self._arm_spotter()
        last_arm_retry = time.time()
        fed_last = None  # newest ring-buffer chunk already fed to the spotter

        # Rolling history of energy levels to detect voice cadence
        energy_history: list[float] = []
        is_recognizing = False

        def _recognize_async(chunks_copy):
            nonlocal is_recognizing
            try:
                if not chunks_copy:
                    return
                raw_data = np.concatenate(chunks_copy, axis=0).tobytes()
                audio_data = sr.AudioData(raw_data, self._audio_manager.sample_rate_in, 2)
                text = ""
                try:
                    text = recognizer.recognize_sphinx(audio_data).lower()
                except Exception:  # noqa: BLE001
                    # Voice-control batch (2026-11-16, W7): the
                    # Google fallback is gone — recognize_google
                    # takes no timeout, so one slow network call
                    # could hang this thread and disable fallback
                    # wake for the rest of the process, and a cloud
                    # call in a wake path contradicts the local-first
                    # posture. Sphinx is local; its failure is the
                    # end of this attempt, not a cloud round-trip.
                    text = ""

                # Voice-control batch (2026-11-16, W7): whole-phrase
                # matching, never substring — the old `any(w in
                # text)` fired when the phrase sat inside a longer
                # decoded word, the exact trap the Vosk grammar
                # was built to close.
                if _match_wake_phrase(text, self.wake_words):
                    print(f"[WakeWord] Keyword detected: '{text}'")
                    self._cooldown_until = time.time() + 3.5
                    if self.on_wake:
                        self.on_wake()
            finally:
                is_recognizing = False

        # Voice-control batch (2026-11-16, W1-wake): the loop
        # exits when its generation is retired (stop() or a
        # newer start()), not merely when _running flips — a
        # fast stop→start can otherwise resurrect the old thread.
        while self._running and (
                gen is None or gen == getattr(self, "_wake_gen", None)):
            if not self.enabled:
                time.sleep(0.2)
                continue

            now = time.time()
            if now < self._cooldown_until:
                time.sleep(0.1)
                continue

            if spotter is None and now - last_arm_retry > 5.0:
                last_arm_retry = now
                spotter = self._arm_spotter()
                fed_last = None

            if spotter is not None:
                # --- Vosk grammar spotting (primary) -------------
                # Feed only the ring-buffer chunks not yet fed
                # (tracked by identity — the buffer hands out the
                # same array objects). Whenever the watermark is
                # lost — just armed, just fired, re-enabled, buffer
                # rolled over — the backlog is SKIPPED, never fed:
                # only audio from this moment on may wake her, so a
                # phrase spoken while she was disabled or cooling
                # down can't fire late.
                chunks = self._audio_manager.get_recent_mic_audio(seconds=2.5)
                fresh: list = []
                if fed_last is not None and chunks:
                    idx = next(
                        (i for i, c in enumerate(chunks) if c is fed_last),
                        None,
                    )
                    if idx is not None:
                        fresh = chunks[idx + 1:]
                    else:
                        spotter.reset()
                if chunks:
                    fed_last = chunks[-1]
                phrase = None
                model_fired = False
                for chunk in fresh:
                    try:
                        pcm = _resample_int16(
                            np.asarray(chunk),
                            self._audio_manager.sample_rate_in,
                            _VoskSpotter.SAMPLE_RATE,
                        ).tobytes()
                        phrase = spotter.accept(pcm)
                    except Exception as e:  # noqa: BLE001
                        # Voice-control batch (2026-11-16, W2): one
                        # Vosk exception used to kill this thread
                        # silently while the UI still said wake was
                        # On — log it, reset the spotter, keep
                        # listening. Never let one bad chunk end
                        # wake for the session.
                        print(f"[WakeWord] Spotter error (ignored): {e}")
                        try:
                            spotter.reset()
                        except Exception:  # noqa: BLE001
                            pass
                        phrase = None
                        break
                    if phrase:
                        break
                    # R7-6 micro-wake-word: the same resampled PCM
                    # feeds the armed custom model. A scorer failure
                    # never takes the Vosk path down, and a model
                    # fires only while wake is enabled (this loop
                    # sleeps while disabled, above).
                    if self._custom_scorer is not None:
                        try:
                            if self._custom_scorer.feed(pcm):
                                model_fired = True
                                break
                        except Exception:  # noqa: BLE001
                            pass
                if phrase:
                    print(f"[WakeWord] Keyword detected (vosk): '{phrase}'")
                    self._cooldown_until = time.time() + _WAKE_REFRACTORY_S
                    spotter.reset()
                    fed_last = None
                    # Voice-control batch (2026-11-16, W8): the
                    # custom branch reset the scorer on fire but
                    # the Vosk branch did not — a stale scorer
                    # could fire on pre-fire context.
                    if self._custom_scorer is not None:
                        try:
                            self._custom_scorer.reset()
                        except Exception:  # noqa: BLE001
                            pass
                    if self.on_wake:
                        self.on_wake()
                elif model_fired:
                    print("[WakeWord] Keyword detected (custom model): "
                          f"'{self._custom_model_slug}'")
                    self._cooldown_until = time.time() + _WAKE_REFRACTORY_S
                    spotter.reset()
                    fed_last = None
                    self._custom_scorer.reset()
                    if self.on_wake:
                        self.on_wake()
                time.sleep(0.06)
                continue

            level = self._audio_manager.mic_level
            energy_history.append(level)
            if len(energy_history) > 30:
                energy_history.pop(0)

            # Check for voice activity burst
            if level > self.cadence_burst_level:
                # If speech recognition is available, run keyword recognizer in background
                if recognizer and sr and not is_recognizing:
                    is_recognizing = True
                    chunks = self._audio_manager.get_recent_mic_audio(seconds=2.0)
                    t = threading.Thread(target=_recognize_async, args=(chunks,), daemon=True)
                    t.start()
                elif not (recognizer and sr):
                    # Offline cadence trigger: quiet background followed by vocal burst
                    if len(energy_history) >= 15:
                        prior_avg = sum(energy_history[:8]) / 8.0
                        recent_avg = sum(energy_history[-6:]) / 6.0
                        if prior_avg < self.cadence_prior_max and recent_avg > self.cadence_recent_min:
                            # Distinct vocal onset detected
                            print(f"[WakeWord] Vocal cadence trigger detected (prior: {prior_avg:.2f}, burst: {recent_avg:.2f})")
                            self._cooldown_until = time.time() + 4.0
                            if self.on_wake:
                                self.on_wake()

            time.sleep(0.06)


wake_word_detector = WakeWordDetector()


# --- Wave 3 (v5.58) wake additions: ADDITIVE ONLY -------------------
# Nothing above this line changed behaviour. The names below are
# the v5.58 wake surface for the coordinator's pre-pin record:
# section-10 targets, the wake default, and the custom-phrase
# validator (full enrolment lives in core/wake_enrolment.py).
# Wake ships OFF by default — UNCHANGED by Wave 3. The detector's
# `enabled` flag starts False and only the app (never this
# module) turns it on; the ON/OFF ruling stays the owner's.
WAKE_SHIPS_ENABLED_BY_DEFAULT = False
WAKE_SECTION10_FALSE_ACCEPTS_PER_HOUR_MAX = 0.5
WAKE_SECTION10_TRUE_ACCEPT_MIN = 0.95
WAKE_SECTION10_TRUE_ACCEPT_DISTANCE_M = 1.5


def validate_custom_wake_phrase(phrase: str) -> list[str]:
    """Validation errors for a custom wake phrase, [] when valid.

    Thin wrapper over core/wake_enrolment.py so callers of this
    module get the same rule without importing a second module.
    The import is lazy so this module's import cost is unchanged
    for callers that never enrol a phrase.
    """
    from ..wake_enrolment import validate_custom_wake_phrase as _validate  # Phase 3 V1a-2: wake_enrolment stays flat; alias _validate RESTORED (V1a-2 dropped it, breaking custom-wake validation with NameError)

    return _validate(phrase)
