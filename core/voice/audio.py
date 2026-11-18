"""
Audio I/O Manager for Voice AI Assistant.
Provides low-latency recording, playback, and real-time FFT spectrum analysis.
"""


import collections
import queue
import sys
import threading
import time
from typing import Any

import numpy as np
import sounddevice as sd

from ..config import DATA_DIR, config  # Phase 3 V1a-1: audio chain moved to core/voice/

# B32 INTENTIONAL: helpers moved to core/voice/audio_helpers.py;
# the facade re-exports every moved name so importers and
# test patches through this module object keep working.
from .audio_helpers import (
    _announce_hook,
    set_announce_hook,
    _announce,
    _MIC_SILENCE_EXPLANATION,
    _device_fingerprint,
    resolve_input_device,
    resolve_output_device,
    effective_input,
    system_default_input_index,
    system_default_output_index,
    _MIC_CALLBACK_STALL_S,
    _MIC_WATCHDOG_TICK_S,
    system_audio_active,
)


# B33 INTENTIONAL: AudioManager's methods moved to two mixins;
# the facade composes them so importers and the module-level
# audio_manager instance keep working unchanged.
from .audio_devices_mic import AudioDevicesMicMixin
from .audio_streams_playback import AudioStreamsPlaybackMixin



class AudioManager(AudioDevicesMicMixin, AudioStreamsPlaybackMixin):
    def __init__(self):
        self.sample_rate_in = config.sample_rate_in
        self.sample_rate_out = config.sample_rate_out
        self.channels = config.channels
        self.chunk_size = config.chunk_size

        # Device selection (None = default)
        self.input_device: int | None = None
        self.output_device: int | None = None
        # The NAME paired with the input pick (v5.37): Windows
        # re-indexes devices across reboots/plug changes, so an
        # index alone can silently land on a different device —
        # resolve_input_device() re-finds the pick by name.
        self.input_device_name: str | None = None
        # Field-check fix #12 (M11a): the NAME paired with the
        # output pick — the output mirror of input_device_name
        # (same Windows re-indexing reason; resolve_saved_output
        # re-finds the pick by name).
        self.output_device_name: str | None = None

        # Mic health tracking (v5.37): post-gain RMS arrives from
        # _mic_callback — a rolling ~3 s window for the peak, and
        # the last time the level crossed the voice floor. This is
        # what makes an open-but-silent stream (Windows mic
        # privacy, an OS mute, the wrong device) VISIBLE instead
        # of a session that simply never hears anything.
        self._rms_window: collections.deque = collections.deque()
        self._last_voice_at: float | None = None
        self._input_opened_at: float | None = None
        self._opened_device_name: str | None = None
        # Field-check fix #12 (M11a): the output device actually
        # opened, mirroring _opened_device_name for the label.
        self._opened_output_device_name: str | None = None
        # v5.52 (item 3): the mic mode gate. "auto" (the standing
        # behaviour) = the PC's system default input, resolved
        # fresh at every start; "manual" = the user's deliberate
        # Settings pin, resolved by the v5.37 rules. The GUI owns
        # the stored mode (QSettings audio/input_mode) and writes
        # this attribute; core defaults to auto so a stored pick
        # can never outrank the machine's default by accident.
        self.input_mode: str = "auto"
        # P2 speed pass (2026-11-16): which PortAudio latency
        # each stream won ("low" or "default"), None before any
        # stream has opened. Set by the _open_*_stream
        # helpers; read by mic_status().
        self._input_latency_mode: str | None = None
        self._output_latency_mode: str | None = None
        self._opened_device_index: int | None = None
        self._force_default_once: bool = False
        self._silence_fallback_note: str = ""
        # v5.53 (F6), remade v6.3.x: when the opened device IS
        # the resolved default and hears only silence, the
        # guard now DIAGNOSES and stays — the old fall-through
        # to the next enumerated input (the forced pick below,
        # consumed by resolve_saved_input) is removed: it
        # swapped a correct mic for whatever was listed next.
        # _force_device_once is kept as a seam (write-never
        # for now) so the resolver's forced-pick branch stays
        # intact. The resolve note (effective_input's, no
        # longer discarded in auto mode) is remembered for
        # mic_status() and announced once per distinct text.
        self._force_device_once: tuple | None = None
        self._used_forced_device: bool = False
        self._opened_via_silent_fallback: bool = False
        self._silent_default_announced: bool = False
        self._silent_fallback_failed_announced: bool = False
        self._resolve_note: str = ""
        self._resolve_note_announced: str = ""
        # v5.54 (Wave 0, EA-10/EA-11): the default-device
        # watcher (core/device_watch.py; None until the first
        # start() arms it, and forever None off Windows) and the
        # input stream's last classified status note (EA-11
        # rung 4, core/mic_doctor.classify_stream_flags).
        self._device_watcher = None
        self._device_watch_armed: bool = False
        self._input_status_note: str = ""
        # v5.95 (micfix): callback-liveness tracking for the
        # input-stream watchdog (see _mic_watchdog_loop). The
        # callback stamps _last_callback_at on EVERY invocation
        # (muted or not — mute gates the queue, never the
        # callback), so a stream whose callback stopped firing
        # is distinguishable from a stream delivering silence.
        # _mic_callback_errors counts frames the callback
        # failed to process (guarded, never fatal).
        self._last_callback_at: float | None = None
        self._mic_callback_errors: int = 0
        self._mic_watchdog_thread: threading.Thread | None = None
        self._mic_watchdog_stop = threading.Event()
        self._mic_recovering: bool = False
        # Voice-control batch (2026-11-16, W1): the watchdog
        # generation — stop() retires the old generation, so a
        # cleared stop event can never resurrect a sleeping old
        # thread alongside the new one.
        self._mic_watchdog_gen: int = 0

        # Mic remake (v6.3.x): frame-arrival tracking for the
        # silence guard + mic_status() (_last_nonzero_at is
        # stamped on every nonzero frame; _frames_received
        # counts every callback frame, reset at each input
        # start), and the last open failure (_open_error /
        # _open_error_at / _open_retries — set by start() when
        # the input stream would not open, cleared on success,
        # on denial, and on stop(); the watchdog retries the
        # open while it is set).
        self._last_nonzero_at: float | None = None
        self._frames_received: int = 0
        self._open_error: str | None = None
        self._open_error_at: float | None = None
        self._open_retries: int = 0
        # Voice-control batch (2026-11-16, O1): the last OUTPUT
        # open failure — the mirror of _open_error above. Set
        # by _try_open_output() when the speaker would not open,
        # cleared on success and on stop(); the mic watchdog
        # retries the open while it is set.
        self._output_error: str | None = None
        self._output_error_at: float | None = None

        # Mic queue-drop telemetry (v5.43, EARS A9): chunks the
        # callback could not hand to mic_queue because it was full
        # used to vanish silently (except: pass) — a session could
        # lose the start of every utterance and nobody could see
        # why. Counted here, reset at every input-stream start,
        # logged to the session log (data/activity.log) at stop(),
        # and readable any time via get_mic_drop_count() (the
        # accessor the doctor wires to).
        self.mic_drop_count: int = 0

        # Input gain (v5.17): applied in _mic_callback before
        # levels, FFT, the ring buffer and the queue, so the HUD,
        # the VAD and the wake spotter all hear the gained signal.
        # A plain attribute (not a property) so the GUI's gain
        # slider can write it live; config only seeds the start.
        try:
            self.mic_gain: float = float(config.mic_gain)
        except (TypeError, ValueError):
            self.mic_gain = 1.0

        # Playback transport (v5.17): pause holds the queue in
        # place — the callback emits silence and consumes nothing —
        # and a per-sentence FIFO records which sentence's blocks
        # are leaving the queue, powering karaoke state, a
        # sentences-left count and skip-to-next-sentence. Records
        # are dicts {remaining, index, total, text}; only the
        # playback callback mutates the head's `remaining`.
        self.playback_paused = False
        self._sentence_fifo: collections.deque = collections.deque()

        # Queues
        self.mic_queue: queue.Queue[bytes] = queue.Queue(maxsize=100)
        # Voice-control batch (2026-11-16, O4): the playback queue
        # used to be unbounded — a stalled output stream let a
        # long Live reply pile blocks forever (the mic queue beside
        # it is capped at 100). ~15 s of audio at the configured
        # block size; enqueue drops the OLDEST block on a full
        # queue and counts it (playback_dropped_blocks), never
        # blocks the producer — a plain put() on a bounded queue
        # would stall the Live receive loop the same way the
        # unbounded queue stalled memory. Odd-length chunks are
        # counted separately (see enqueue_playback).
        _playback_blocks = max(
            64, int(15.0 * self.sample_rate_out / self.chunk_size))
        self.playback_queue: queue.Queue[np.ndarray] = queue.Queue(
            maxsize=_playback_blocks)
        self.playback_dropped_blocks: int = 0
        self._playback_odd_chunks: int = 0
        self._mic_ring_buffer: collections.deque = collections.deque(maxlen=40)  # ~2.5s rolling audio

        # Streams
        self._in_stream: sd.InputStream | None = None
        self._out_stream: sd.OutputStream | None = None

        # Flags & State
        self.is_recording = False
        self.is_playing = False
        self.is_muted = False
        # (push_to_talk_active removed in the v6.3.x mic
        # remake: it was set True at construction and read
        # nowhere in core/ or gui/ — the live mute state is
        # is_muted, driven by PTT / the mute button / wake.)

        # Real-time visualization metrics (0.0 to 1.0)
        self.mic_level: float = 0.0
        self.speaker_level: float = 0.0
        self.spectrum_bands: list[float] = [0.0] * 16
        # Playback spectrum (v5.42): the same 16-band analysis,
        # computed in _playback_callback from the samples actually
        # written to outdata — so while she speaks the orb performs
        # HER signal, not the microphone's (room / echo) signal.
        self.playback_bands: list[float] = [0.0] * 16

        # Interruption signal
        self._interrupt_event = threading.Event()
        # Field-check fix #13 (M13c): snapshot taken by
        # interrupt_playback() — how many queued blocks were stale
        # at the interrupt, and the drop counter then. Read by the
        # callback's interrupt branch; 0 means "no interrupt
        # pending" (the event is only ever set by
        # interrupt_playback(), which always re-snapshots first).
        self._interrupt_drain_blocks: int = 0
        self._interrupt_dropped_at: int = 0

        # v5.58 (Wave 3, EA-1/EA-29): the render-reference AEC
        # sidecar (core/aec.py). Lazily built on first tap, never
        # on the realtime callback, never imported at module top
        # (so a missing/broken front-end module cannot break
        # audio import). None until the first TTS enqueue taps
        # it; front_end_status() reports it either way.
        self._aec = None

        # Mic remake (v6.3.x): register this manager's stop()
        # as a mic teardown hook HERE, not only in
        # apply_permission_changes() — a mic→denied flip that
        # reaches the store without that registration (or
        # whose registration failed) used to leave the open
        # stream transmitting until the next reopen/stop.
        # The registry dedupes by identity, so a second
        # registration of the same bound method is a no-op;
        # a lazy import keeps permission_grants stdlib-only
        # (core -> core, no GUI dependency).
        try:
            from .. import permission_grants as _pg
            _pg.register_mic_teardown(self.stop)
        except Exception:  # noqa: BLE001 — teardown is a
            # belt-and-braces hook, never a construction failure
            pass


audio_manager = AudioManager()
