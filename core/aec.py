"""Render-reference echo cancellation (v5.58, Wave 3, EA-1 + EA-29).

Design (what ships) vs target (what does not yet):
- SHIPS: a sidecar/shim. Her own TTS render is tapped BEFORE
  the device (``AudioManager.tap_render_reference`` feeds
  ``feed_render``) and used as the far-end reference; the mic
  path is cleaned by a pure-numpy NLMS adaptive canceller in
  ``RenderReferenceAEC``. The shim NEVER runs on the realtime
  callback: ``feed_render`` only buffers + resamples, and
  ``process_mic`` is called by the session thread, never by
  PortAudio.
- DOES NOT SHIP: WebRTC APM itself. No webrtc-audio-processing
  binary is vendored in this tree and no new pip dependency is
  allowed in this wave, so a true APM engine cannot be linked
  in-process here. ``webrtc_apm_available`` is therefore False
  and ``status()["engine"]`` says so in plain words. The
  ``set_engine`` slot accepts any object with
  ``process(mic_frame, ref_frame)`` so a future APM engine drops
  in without an interface change. Closing step: vendor a
  webrtc-audio-processing binary (WebRTC is BSD-3-Clause) or add
  the dependency through the CC-1 licence review, then wire it
  as the engine and re-measure against the metric below.

Honest costing (EA-29), recorded here because the numbers
decide whether APM can ever sit on the callback (it cannot):
- Far end is her 24 kHz speaker path (config.sample_rate_out),
  near end is the 16 kHz mic path: every render sample needs a
  24 -> 16 kHz resample (factor 2/3) before it can serve as a
  reference. ``resample_24k_to_16k`` does it with linear
  interpolation (harness-grade; a production APM wire would use
  a polyphase resampler — the interface, not the interpolator,
  is what this shim pins).
- APM works in 10 ms frames (160 samples at 16 kHz). Her
  playback/mic chunks are 1024 samples: 64 ms at 16 kHz, 42.7 ms
  at 24 kHz — neither is a whole number of APM frames (6.4 and
  4.27 frames). ``frame_10ms`` / ``reframe`` therefore exist:
  any APM wire must accumulate into 160-sample frames and carry
  the remainder, which is a buffering delay the callback cannot
  absorb. Sidecar only.
- Far-end/near-end delay is unknown (device + OS buffering):
  ``estimate_delay_samples`` aligns by cross-correlation over
  a bounded lag window before cancellation; without alignment
  the canceller chases its own tail.

Acceptance metric (§10): SELF-INTERRUPTION RATE — interruptions
fired while she is speaking, divided by speaking turns.
``SelfInterruptionMeter`` counts exactly that; with zero
speaking turns it reports None (a 0% claim on no data would be
a fabrication). The §10 cut-off bar (<=5%) is measured by the
field harness, not asserted here.
"""

from __future__ import annotations

import numpy as np

RENDER_RATE = 24000
MIC_RATE = 16000
APM_FRAME_MS = 10
APM_FRAME_SAMPLES_16K = 160
MAX_DELAY_SAMPLES = 8000  # 500 ms at 16 kHz — beyond this, alignment is hopeless
NLMS_FILTER_LEN = 256
NLMS_STEP = 0.5
REFERENCE_RING_SECONDS = 10.0


def resample_linear(samples: np.ndarray, src_rate: int,
                    dst_rate: int) -> np.ndarray:
    """Linear-interpolation resample (harness-grade, numpy-only).

    Returns float32. Length is round(n * dst/src); empty input
    stays empty. This is NOT a production anti-aliased resampler
    — it pins the rate-conversion interface and the cost shape
    (one multiply per output sample), not the filter quality.
    """
    arr = np.asarray(samples, dtype=np.float32).reshape(-1)
    if arr.size == 0 or int(src_rate) == int(dst_rate):
        return arr.astype(np.float32)
    n_out = max(1, int(round(arr.size * int(dst_rate) / int(src_rate))))
    if n_out == 1:
        return arr[:1].astype(np.float32)
    src_idx = np.linspace(0, arr.size - 1, n_out)
    return np.interp(src_idx, np.arange(arr.size), arr).astype(np.float32)


def resample_24k_to_16k(samples: np.ndarray) -> np.ndarray:
    """Her 24 kHz speaker render -> 16 kHz far-end reference."""
    return resample_linear(samples, RENDER_RATE, MIC_RATE)


def resample_16k_to_24k(samples: np.ndarray) -> np.ndarray:
    """Inverse direction, for completeness of the cost ledger."""
    return resample_linear(samples, MIC_RATE, RENDER_RATE)


def frame_10ms(samples: np.ndarray,
               sample_rate: int = MIC_RATE) -> list[np.ndarray]:
    """Split into APM 10 ms frames; a short tail is dropped.

    Dropping the tail is the honest choice for an APM wire: APM
    cannot consume a partial frame, and padding it would invent
    audio. Callers needing the tail must buffer it themselves
    (``reframe`` keeps the remainder).
    """
    arr = np.asarray(samples, dtype=np.float32).reshape(-1)
    frame = max(1, int(round(sample_rate * APM_FRAME_MS / 1000.0)))
    n = arr.size // frame
    return [arr[i * frame:(i + 1) * frame] for i in range(n)]


def reframe(buffer: np.ndarray, frame_samples: int = APM_FRAME_SAMPLES_16K):
    """Split off whole frames, returning (frames, remainder).

    The remainder is carried to the next call — this is the
    buffering the 1024-sample chunks force on any APM wire.
    """
    arr = np.asarray(buffer, dtype=np.float32).reshape(-1)
    n = arr.size // int(frame_samples)
    frames = [arr[i * frame_samples:(i + 1) * frame_samples]
              for i in range(n)]
    return frames, arr[n * frame_samples:]


def estimate_delay_samples(mic: np.ndarray, ref: np.ndarray,
                           max_delay: int = MAX_DELAY_SAMPLES) -> int:
    """Delay (samples) by which ref lags mic, via correlation.

    Returns 0 when either signal is empty/silent or the best lag
    is outside the window. Positive means the reference must be
    shifted forward by that many samples to align with the mic.
    Bounded: the correlation window is capped at ``max_delay``.
    """
    m = np.asarray(mic, dtype=np.float32).reshape(-1)
    r = np.asarray(ref, dtype=np.float32).reshape(-1)
    if m.size == 0 or r.size == 0:
        return 0
    if float(np.dot(m, m)) == 0.0 or float(np.dot(r, r)) == 0.0:
        return 0
    # Full correlation: index centre+lag holds sum_t mic[t]*ref[t-lag],
    # so the argmax lag over 0..max_delay is the echo delay: the
    # reference sample at t-delay is the one sounding in the mic
    # at t. Only non-negative lags are searched (the reference
    # cannot sound before it is rendered).
    full = np.correlate(m, r, mode="full")
    centre = r.size - 1
    window = full[centre:centre + int(max_delay) + 1]
    if window.size == 0:
        return 0
    return int(np.argmax(np.abs(window)))


class NLMSEchoCanceller:
    """Reference NLMS canceller. NOT WebRTC APM — labelled as such.

    Time-domain normalised LMS, 256 taps, step 0.5, frame-based
    (any frame length; APM-shaped 160-sample frames are the
    intended call shape). ``process`` returns the error signal
    (mic minus estimated echo). State resets on ``reset()``.
    """

    engine = "nlms-reference (NOT WebRTC APM)"

    def __init__(self, filter_len: int = NLMS_FILTER_LEN,
                 step: float = NLMS_STEP):
        self.filter_len = int(filter_len)
        self.step = float(step)
        self._w = np.zeros(self.filter_len, dtype=np.float32)
        self._ref_hist = np.zeros(self.filter_len, dtype=np.float32)

    def reset(self) -> None:
        self._w = np.zeros(self.filter_len, dtype=np.float32)
        self._ref_hist = np.zeros(self.filter_len, dtype=np.float32)

    def process(self, mic_frame: np.ndarray,
                ref_frame: np.ndarray) -> np.ndarray:
        """Cancel echo sample-by-sample over one frame."""
        mic = np.asarray(mic_frame, dtype=np.float32).reshape(-1)
        ref = np.asarray(ref_frame, dtype=np.float32).reshape(-1)
        out = np.zeros_like(mic)
        for i in range(mic.size):
            x = float(ref[i]) if i < ref.size else 0.0
            self._ref_hist = np.roll(self._ref_hist, 1)
            self._ref_hist[0] = x
            echo = float(np.dot(self._w, self._ref_hist))
            err = float(mic[i]) - echo
            out[i] = err
            norm = float(np.dot(self._ref_hist, self._ref_hist)) + 1e-6
            self._w += (self.step * err / norm) * self._ref_hist
        return out


class RenderReferenceAEC:
    """Sidecar render-reference AEC shim (EA-1 + EA-29).

    Usage (session thread, never the PortAudio callback):
      aec = RenderReferenceAEC()
      aec.feed_render(tts_pcm_24k)   # pre-device tap, per enqueue
      clean, report = aec.process_mic(mic_pcm_16k)
    ``feed_render`` resamples 24 -> 16 kHz and keeps a capped
    ring (default 10 s) of reference audio. ``process_mic``
    aligns the reference to the mic by the last estimated delay
    (re-estimated when the caller asks, or every call when the
    ring is fresh — cheap at these sizes) and runs the engine.
    ``set_engine`` swaps the canceller for a future APM engine
    without changing this interface.
    """

    def __init__(self, engine: Any | None = None):
        self.engine = engine if engine is not None else NLMSEchoCanceller()
        self._ref_ring = np.zeros(0, dtype=np.float32)
        self._delay = 0
        self._fed_samples = 0
        self._processed_samples = 0

    def feed_render(self, pcm_bytes: bytes) -> bool:
        """Tap pre-device render (24 kHz int16 PCM). Never raises.

        Returns False (tap dropped, reason in status) when the
        input is malformed — a dropped tap degrades echo
        cancellation, it never breaks playback.
        """
        try:
            if not isinstance(pcm_bytes, (bytes, bytearray)):
                return False
            raw = bytes(pcm_bytes)
            if len(raw) % 2 != 0 or len(raw) == 0:
                return False
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            ref = resample_24k_to_16k(pcm)
            cap = int(REFERENCE_RING_SECONDS * MIC_RATE)
            self._ref_ring = np.concatenate([self._ref_ring, ref])[-cap:]
            self._fed_samples += int(ref.size)
            return True
        except Exception:  # noqa: BLE001
            return False

    def process_mic(self, pcm_bytes: bytes) -> tuple[np.ndarray, dict]:
        """Clean one mic chunk (16 kHz int16 PCM). Never raises.

        Returns (cleaned float32 array, report dict). On malformed
        input the report says so and the cleaned array is empty —
        the caller must not mistake that for silence.
        """
        report: dict[str, Any] = {
            "engine": getattr(self.engine, "engine", type(self.engine).__name__),
            "webrtc_apm": False,
            "delay_samples": self._delay,
            "samples_in": 0,
            "samples_out": 0,
            "error": None,
        }
        try:
            if not isinstance(pcm_bytes, (bytes, bytearray)):
                report["error"] = "mic input is not bytes"
                return np.zeros(0, dtype=np.float32), report
            raw = bytes(pcm_bytes)
            if len(raw) % 2 != 0 or len(raw) == 0:
                report["error"] = "mic input empty or odd-length"
                return np.zeros(0, dtype=np.float32), report
            mic = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            report["samples_in"] = int(mic.size)
            if self._ref_ring.size >= mic.size:
                ref_seg = self._ref_ring[-mic.size:]
                delay = estimate_delay_samples(mic, ref_seg)
                if delay:
                    self._delay = delay
                ref_aligned = np.concatenate([
                    np.zeros(self._delay, dtype=np.float32),
                    ref_seg])[:mic.size] if self._delay else ref_seg
            else:
                ref_aligned = np.zeros_like(mic)
            frames_mic, _ = reframe(mic)
            frames_ref, _ = reframe(ref_aligned)
            # Any remainder shorter than a frame passes through
            # uncancelled rather than padded into invented audio.
            tail_start = len(frames_mic) * APM_FRAME_SAMPLES_16K
            out_parts = []
            for fm, fr in zip(frames_mic, frames_ref):
                out_parts.append(self.engine.process(fm, fr))
            if tail_start < mic.size:
                out_parts.append(mic[tail_start:])
            clean = (np.concatenate(out_parts) if out_parts
                     else np.zeros(0, dtype=np.float32))
            self._processed_samples += int(mic.size)
            report["samples_out"] = int(clean.size)
            report["delay_samples"] = self._delay
            return clean, report
        except Exception as e:  # noqa: BLE001
            report["error"] = f"aec failed: {e}"
            return np.zeros(0, dtype=np.float32), report

    def set_engine(self, engine: Any) -> None:
        """Swap the canceller (future APM engine slot)."""
        self.engine = engine

    def status(self) -> dict[str, Any]:
        # Field-check fix #12 (M11c): the reference is collected
        # (fed_samples grows with every TTS turn) but the mic
        # audio is NEVER cleaned against it — process_mic() has
        # no caller on the mic path — so the status says so in
        # plain words instead of letting an engine name plus a
        # fed-samples counter read as "echo cancellation is
        # running". processed_samples stays 0 until a caller
        # actually runs mic audio through process_mic().
        return {
            "engine": getattr(self.engine, "engine",
                              type(self.engine).__name__),
            "webrtc_apm_available": False,
            "webrtc_apm_note": (
                "WebRTC APM NOT bundled: no webrtc-audio-processing "
                "binary is vendored and no new pip dependency is "
                "allowed in this wave. NLMS reference runs instead; "
                "APM wiring is the set_engine slot + CC-1 review."),
            "on_realtime_callback": False,
            "applied_to_mic": False,
            "note": (
                "Echo cancellation is not running: the render "
                "reference is collected, but the microphone audio "
                "is never cleaned against it."),
            "render_rate": RENDER_RATE,
            "mic_rate": MIC_RATE,
            "apm_frame_samples_16k": APM_FRAME_SAMPLES_16K,
            "chunk_samples": 1024,
            "reference_ring_samples": int(self._ref_ring.size),
            "fed_samples_16k": self._fed_samples,
            "processed_samples": self._processed_samples,
            "delay_samples": self._delay,
        }


class SelfInterruptionMeter:
    """§10 self-interruption rate: interruptions while she speaks.

    ``note_turn(speaking, interrupted)`` records one assistant
    speaking turn. ``rate()`` is None until at least one speaking
    turn exists — no data, no rate. The <=5% cut-off is a field
    measurement over this meter, not a claim this class makes.
    """

    def __init__(self):
        self.speaking_turns = 0
        self.interrupted_while_speaking = 0

    def note_turn(self, speaking: bool, interrupted: bool) -> None:
        if speaking:
            self.speaking_turns += 1
            if interrupted:
                self.interrupted_while_speaking += 1

    def rate(self) -> float | None:
        if self.speaking_turns == 0:
            return None
        return self.interrupted_while_speaking / self.speaking_turns

    def status(self) -> dict[str, Any]:
        return {
            "speaking_turns": self.speaking_turns,
            "interrupted_while_speaking": self.interrupted_while_speaking,
            "self_interruption_rate": self.rate(),
        }
