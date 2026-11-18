"""Wave 3 (v5.58) EA-20 + EA-26 sound-event detection — SIDECAR.

What this is, stated plainly first, because the plan itself flags
it: this sidecar taps the microphone BEFORE VAD/ASR gating. While
it is started it listens continuously — not only while the user is
speaking, and not only after a wake word. That is an always-on tap
for as long as it runs, so it ships ONLY behind a per-session
opt-in, with an indicator, and it stops the moment the opt-in
lapses. If that framing is not accepted, this item does not ship:
nothing here runs without the opt-in, and nothing here runs by
default (see SoundEventOptIn and SoundEventSidecar.start).

Frigate's discipline, applied to audio:
- RMS gate FIRST: quiet frames never reach the classifier, so
  room noise costs no inference and fires no event.
- Tiny default label set (5 labels only): doorbell, alarm,
  glass breaking, dog bark, baby cry. The 521-class YAMNet
  output is reduced to this set and nothing else is ever
  emitted — a "speech" or "music" score cannot become an event.
- Per-class thresholds (not one global threshold): a glass
  break needs a higher score than a doorbell, because a false
  glass alarm is costlier than a false doorbell.
- Per-class cooldowns: one label fires at most once per
  cooldown window, so a ringing phone cannot spam the chat.

Sidecar, not a patch to the audio path:
- core/voice/audio.py is NEVER edited, imported for its stream, or
  reused here. The sidecar opens its OWN PortAudio input stream
  (the OS default sharing mode) through an injectable factory,
  so it coexists with the resident mic stream instead of
  hijacking it. The default factory lazily imports sounddevice;
  with no factory and no sounddevice available, start() refuses
  instead of crashing.
- NEVER pip-install tensorflow: there is no TensorFlow import
  anywhere in this module. The classifier is an injected
  callable, `classifier(samples_16k_float, sample_rate) ->
  {label: score}`. With no classifier injected the sidecar
  fires NOTHING — it never guesses, and a missing model is
  reported as unavailable, not papered over.
- Telemetry OFF by shape: no network, no storage, no transcript.
  An emitted event carries only (label, score, time) — never
  audio, never a transcript, never a recording.
- Denylist-compatible: before every chunk, the foreground
  denylist from core/capture_guard is checked (default probe;
  injectable for tests). While a denylisted window (password
  manager, banking surface, ...) holds the foreground, frames
  are dropped and no event fires; listening resumes only when
  the foreground clears. The denylist is checked first, before
  any opt-in logic, exactly as capture_guard orders it.

Upgrade seats, NOT shipped here:
- EA-21 CED and EA-22 PANNs are upgrade seats behind YAMNet.
  EA-21's weights licence is owed, so no CED code exists here.
- EA-25 CLAP is an opt-in SECOND stage only: it can confirm or
  veto a classifier event, but it can never originate one. A
  CLAP-only event is impossible by construction — with no
  classifier, `process_pcm` returns no events whatever the
  second stage says.

Standard library only (plus the lazily-imported sounddevice in
the default stream factory); no Qt, no new dependencies.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Constants — NEW in v5.58 (nothing existing was moved; see handoff).
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000  # YAMNet's native rate; the sidecar resamples nothing itself — the stream runs at this rate.

# RMS gate (0.0–1.0 float scale): frames at or below this never
# reach the classifier. Above the house voice floor (0.008) so
# ordinary room noise stays gated, below a normal doorbell/alarm
# level so real events pass. Tunable per sidecar instance.
RMS_GATE = 0.02

# The tiny default label set. Keys are this module's labels; values
# are the YAMNet AudioSet display names a real backend maps from.
# Anything outside this map can never become an event.
LABEL_MAP: dict[str, str] = {
    "doorbell": "Doorbell",
    "alarm": "Alarm",
    "glass_breaking": "Glass",
    "dog_bark": "Dog",
    "baby_cry": "Baby cry, infant cry",
}

# Per-class score thresholds (classifier scores are 0.0–1.0).
DEFAULT_THRESHOLDS: dict[str, float] = {
    "doorbell": 0.60,
    "alarm": 0.65,
    "glass_breaking": 0.70,
    "dog_bark": 0.60,
    "baby_cry": 0.65,
}

# Per-class cooldown: seconds before the same label may fire again.
DEFAULT_COOLDOWNS_S: dict[str, float] = {
    "doorbell": 30.0,
    "alarm": 30.0,
    "glass_breaking": 30.0,
    "dog_bark": 30.0,
    "baby_cry": 30.0,
}

# Plain, speakable sentences — the watcher-bus message for each
# label. Scores never reach speech; they are timeline detail only.
SENTENCES: dict[str, str] = {
    "doorbell": "I heard the doorbell.",
    "alarm": "I heard an alarm.",
    "glass_breaking": "I heard glass breaking.",
    "dog_bark": "I heard a dog barking.",
    "baby_cry": "I heard a baby crying.",
}

# Upgrade seats behind YAMNet — named only to state they are NOT
# implemented here. EA-21's weights licence is owed: no CED code.
UPGRADE_ONLY_SEATS: tuple[str, ...] = ("EA-21 CED", "EA-22 PANNs")

# The capture-indicator kind this sidecar reports through
# core/capture_guard (note_capture_started/ended). It is its own
# kind, not "microphone": the indicator must be able to say
# SOUND listening is live, distinctly from voice capture.
INDICATOR_KIND = "sound_events"


def chunk_rms(samples) -> float:
    """RMS of one PCM chunk on the 0.0–1.0 scale. Accepts float
    samples in [-1, 1] or int16 samples (scaled by 32768). Empty
    input is 0.0. Never raises on odd input — bad input is
    silence, not a crash."""
    try:
        values = list(samples)
    except Exception:  # noqa: BLE001 — a bad chunk is silence
        return 0.0
    if not values:
        return 0.0
    total = 0.0
    count = 0
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if abs(number) > 1.5:  # int16 range, not float
            number = number / 32768.0
        total += number * number
        count += 1
    if count == 0:
        return 0.0
    return math.sqrt(total / count)


@dataclass
class SoundEvent:
    """One emitted sound event: label + score + time only. No
    audio, no transcript, no recording is ever attached."""

    label: str
    score: float
    monotonic_time: float

    @property
    def message(self) -> str:
        return SENTENCES.get(self.label, f"I heard a {self.label}.")


class SoundEventOptIn:
    """The per-session opt-in for the continuous mic tap.

    Two approvals must BOTH hold, and both are per-session:
      (a) this module's own grant — `grant()` records it for the
          current capture session only. The session token is
          read from core/capture_guard on every check, so a
          session rotation (note_new_session / reset_session)
          silently lapses the grant: the next start asks afresh.
      (b) the shared per-session microphone approval — the SAME
          opt-in every other capture uses
          (capture_guard.is_capture_approved("microphone")).
          A caller may inject `mic_approval_fn` (tests, or a
          coordinator-owned wiring that already holds the
          approval); without an injection, the real guard is
          consulted and nothing is assumed.

    A denial is not remembered (capture_guard's own policy): a
    refused start simply does not start, and the next start
    asks again. `revoke()` lapses the grant immediately, and a
    running sidecar that sees a lapsed grant stops feeding.
    """

    def __init__(self, mic_approval_fn=None, session_token_fn=None):
        self._mic_approval_fn = mic_approval_fn
        self._session_token_fn = session_token_fn
        self._lock = threading.Lock()
        self._granted_token = None

    def _current_token(self):
        if self._session_token_fn is not None:
            try:
                return self._session_token_fn()
            except Exception:  # noqa: BLE001 — unknown session lapses
                return None
        try:
            from . import capture_guard

            return capture_guard.current_session_token()
        except Exception:  # noqa: BLE001 — unknown session lapses
            return None

    def _mic_approved(self) -> bool:
        if self._mic_approval_fn is not None:
            try:
                return bool(self._mic_approval_fn("microphone"))
            except Exception:  # noqa: BLE001 — a broken probe denies
                return False
        try:
            from . import capture_guard

            return bool(capture_guard.is_capture_approved("microphone"))
        except Exception:  # noqa: BLE001 — a broken guard denies
            return False

    def grant(self) -> None:
        """Record this session's opt-in grant."""
        with self._lock:
            self._granted_token = self._current_token()

    def revoke(self) -> None:
        """Lapse the grant immediately (session end, user off)."""
        with self._lock:
            self._granted_token = None

    def is_granted(self) -> bool:
        """True only when this session's grant is live."""
        with self._lock:
            token = self._granted_token
        if token is None:
            return False
        current = self._current_token()
        if current is None or current != token:
            return False  # session rotated (or unknown): lapsed
        return True

    def satisfied(self) -> bool:
        """Both halves of the opt-in hold."""
        return self.is_granted() and self._mic_approved()


def _default_foreground_block():
    """The capture_guard denylist check, or None when clear."""
    try:
        from . import capture_guard

        return capture_guard.foreground_capture_block()
    except Exception:  # noqa: BLE001 — a broken probe must not block
        return None


def _default_stream_factory(callback):
    """The sidecar's own PortAudio input stream (OS default
    sharing mode), at YAMNet's 16 kHz, mono, int16. Lazily
    imports sounddevice so this module imports cleanly without
    it; raises a plain RuntimeError when it is unavailable."""
    try:
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "sounddevice is unavailable, so the sound-event "
            "sidecar cannot open its own mic stream.") from exc

    def _callback(indata, _frames, _time_info, _status):
        try:
            callback([float(v) for row in indata for v in row])
        except Exception:  # noqa: BLE001 — a bad callback frame drops
            pass

    return sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16",
        callback=_callback)


@dataclass
class SoundEventSidecar:
    """The sidecar itself. Constructed OFF and silent; `start()`
    is the only path to live listening, and it refuses without
    the opt-in (see SoundEventOptIn). `classifier` and
    `second_stage` are injected callables (see module docstring);
    with no classifier, no event ever fires."""

    classifier: object = None  # fn(float samples, sr) -> {label: score}
    second_stage: object = None  # fn(SoundEvent) -> bool; confirm/veto only
    opt_in: SoundEventOptIn | None = None
    thresholds: dict = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    cooldowns_s: dict = field(default_factory=lambda: dict(DEFAULT_COOLDOWNS_S))
    rms_gate: float = RMS_GATE
    foreground_block_fn: object = None  # fn() -> reason str | None
    on_event: object = None  # fn(SoundEvent) — the watcher-bus seam
    _lock: threading.Lock = field(default_factory=threading.Lock,
                                  repr=False)
    _running: bool = field(default=False, init=False)
    _paused_denylisted: bool = field(default=False, init=False)
    _last_fired: dict = field(default_factory=dict, repr=False)
    _stream: object = field(default=None, repr=False)

    def __post_init__(self):
        if self.opt_in is None:
            self.opt_in = SoundEventOptIn()
        if self.foreground_block_fn is None:
            self.foreground_block_fn = _default_foreground_block

    # -- pure detection (no opt-in, no I/O: the testable core) ----
    def process_pcm(self, samples, now: float | None = None) -> list[SoundEvent]:
        """Run one chunk through RMS gate -> classifier ->
        tiny label set -> per-class thresholds -> cooldowns ->
        optional CLAP second-stage confirmation. Returns the
        events that survive every stage (usually zero or one).
        This is deliberately opt-in-free: the gate lives in
        feed_pcm/start, so tests can drive detection without
        pretending to be a user, and production can never reach
        here without passing the gate first."""
        if now is None:
            now = time.monotonic()
        if chunk_rms(samples) <= self.rms_gate:
            return []
        if self.classifier is None:
            return []  # no model injected: no guesses, ever
        try:
            scores = self.classifier(list(samples), SAMPLE_RATE)
        except Exception:  # noqa: BLE001 — a broken model fires nothing
            return []
        if not isinstance(scores, dict):
            return []
        events: list[SoundEvent] = []
        with self._lock:
            for label in LABEL_MAP:
                score = scores.get(label)
                if score is None:
                    continue
                try:
                    score = float(score)
                except (TypeError, ValueError):
                    continue
                if score < float(self.thresholds.get(label, 1.0)):
                    continue
                last = self._last_fired.get(label)
                cooldown = float(self.cooldowns_s.get(label, 30.0))
                if last is not None and (now - last) < cooldown:
                    continue
                event = SoundEvent(label=label, score=score,
                                   monotonic_time=float(now))
                if self.second_stage is not None:
                    # CLAP confirms or vetoes ONLY: it can never
                    # originate an event (see module docstring).
                    try:
                        confirmed = bool(self.second_stage(event))
                    except Exception:  # noqa: BLE001 — broken stage vetoes
                        confirmed = False
                    if not confirmed:
                        continue
                self._last_fired[label] = float(now)
                events.append(event)
        return events

    # -- gated live path -------------------------------------------
    def feed_pcm(self, samples, now: float | None = None) -> list[SoundEvent]:
        """The live entry point: denylist first, then opt-in,
        then detection. Returns events delivered to on_event
        (or [] when gated, paused, or silent). Never raises."""
        try:
            blocked = self.foreground_block_fn()
        except Exception:  # noqa: BLE001 — a broken probe pauses, safely
            blocked = "foreground probe failed"
        if blocked is not None:
            with self._lock:
                self._paused_denylisted = True
            return []
        with self._lock:
            self._paused_denylisted = False
        if not self._running or not self.opt_in.satisfied():
            return []
        try:
            events = self.process_pcm(samples, now=now)
        except Exception:  # noqa: BLE001 — detection must not raise
            return []
        if self.on_event is not None:
            for event in events:
                try:
                    self.on_event(event)
                except Exception:  # noqa: BLE001 — a listener never kills listening
                    pass
        return events

    def start(self, stream_factory=None) -> tuple[bool, str]:
        """Start live listening. Refuses (False, reason) when:
        the opt-in is not satisfied — including asking the shared
        mic opt-in once via capture_guard when it is not already
        held; no stream can be opened; or already running. On
        success the capture indicator reports kind "sound_events"
        live. This is a CONTINUOUS mic tap while started (see
        module docstring): it is not VAD- or wake-gated."""
        with self._lock:
            if self._running:
                return False, "Sound-event listening is already running."
        if not self.opt_in.is_granted():
            return False, (
                "Sound-event listening needs its per-session "
                "opt-in first: it listens continuously while "
                "running (before voice detection), so it never "
                "starts without your explicit approval for this "
                "session.")
        if not self.opt_in._mic_approved():
            # The SAME per-session mic opt-in every other capture
            # uses — ask once through the shared guard, unless a
            # test/coordinator injection already owns approval.
            if self.opt_in._mic_approval_fn is None:
                try:
                    from . import capture_guard

                    allowed, _msg = capture_guard.ensure_capture_allowed(
                        "microphone")
                except Exception:  # noqa: BLE001 — a broken guard denies
                    allowed = False
                if not allowed:
                    return False, (
                        "Microphone capture was not confirmed for "
                        "this session, so sound-event listening "
                        "did not start.")
            else:
                return False, (
                    "Microphone capture is not approved for this "
                    "session, so sound-event listening did not "
                    "start.")
        factory = stream_factory or _default_stream_factory
        try:
            stream = factory(self.feed_pcm)
            stream.start()
        except Exception as exc:  # noqa: BLE001 — a failed stream refuses
            return False, f"Sound-event mic stream could not start: {exc}"
        with self._lock:
            self._stream = stream
            self._running = True
        # Fix 7 (D4, fail-open audit follow-up): the indicator note
        # must not sink start — but the message must not claim it
        # landed when it did not. Track it and name the failure.
        _indicator_ok = False
        try:
            from . import capture_guard

            capture_guard.note_capture_started(INDICATOR_KIND)
            _indicator_ok = True
        except Exception:  # noqa: BLE001 — the indicator must not sink start
            _indicator_ok = False
        if _indicator_ok:
            return True, (
                "Sound-event listening is on for this session: "
                "doorbell, alarm, glass breaking, dog bark and baby "
                "cry only, continuous mic tap, indicator live.")
        return True, (
            "Sound-event listening is on for this session: "
            "doorbell, alarm, glass breaking, dog bark and baby "
            "cry only, continuous mic tap — but the on-screen "
            "indicator note failed, so the indicator may not show "
            "even though listening is on.")

    def stop(self) -> None:
        """Stop live listening and clear the indicator. Safe to
        call when not running; never raises."""
        with self._lock:
            stream, self._stream = self._stream, None
            was_running = self._running
            self._running = False
        if stream is not None:
            try:
                stream.stop()
            except Exception:  # noqa: BLE001 — teardown never raises
                pass
        if was_running:
            try:
                from . import capture_guard

                capture_guard.note_capture_ended(INDICATOR_KIND)
            except Exception:  # noqa: BLE001
                pass

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def is_paused_denylisted(self) -> bool:
        with self._lock:
            return self._paused_denylisted
