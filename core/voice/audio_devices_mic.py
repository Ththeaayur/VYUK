"""Devices-mic mixin, split out of core/voice/audio.py
(B33 part 2 of 2): device resolution, mic health, watchdog,
silence guard, lifecycle spine, and the shared spectrum helper.
Composed into AudioManager by the facade. Methods resolve bare
names in this module's globals."""

import queue
import threading
import time
from typing import Any

import numpy as np
import sounddevice as sd

from ..config import DATA_DIR  # INTENTIONAL (B33: _log_mic_drops only)

# B33 INTENTIONAL: helpers moved in B32; the mic methods below
# resolve these by bare name, so they are imported here.
from .audio_helpers import (
    _MIC_CALLBACK_STALL_S,
    _MIC_OPEN_RETRY_S,
    _MIC_SILENCE_EXPLANATION,
    _MIC_WATCHDOG_TICK_S,
    _announce,
    effective_input,
    resolve_output_device,
    system_default_input_index,
    system_default_output_index,
)


class AudioDevicesMicMixin:
    @staticmethod
    def get_devices() -> dict[str, list[dict[str, Any]]]:
        """Returns lists of available input and output audio devices."""
        devices = sd.query_devices()
        inputs = []
        outputs = []
        for i, dev in enumerate(devices):
            if dev.get("max_input_channels", 0) > 0:
                inputs.append({"index": i, "name": dev["name"], "channels": dev["max_input_channels"]})
            if dev.get("max_output_channels", 0) > 0:
                outputs.append({"index": i, "name": dev["name"], "channels": dev["max_output_channels"]})
        return {"inputs": inputs, "outputs": outputs}

    @staticmethod
    def default_device_names() -> tuple[str, str]:
        """(input_name, output_name) of the OS-default audio
        devices, for display in Settings (v5.17). Any failure —
        no devices, a broken backend — reads as "Unknown device"
        rather than raising: this feeds labels, never control flow."""
        def _name(kind: str) -> str:
            try:
                dev = sd.query_devices(kind=kind)
                name = dev.get("name") if isinstance(dev, dict) else None
                return str(name) if name else "Unknown device"
            except Exception:  # noqa: BLE001
                return "Unknown device"
        return _name("input"), _name("output")

    def resolve_saved_input(self, saved_index, saved_name):
        """Resolve the microphone to open against the LIVE device
        list: returns (index | None, name | None, note). The
        v5.52 (item 3) mode gate lives here — in AUTOMATIC mode
        (the standing behaviour) the system default wins and
        the saved pick is ignored; in MANUAL mode the v5.37
        rules resolve the pick. Never raises — when the device
        list can't even be read, the pick passes through
        untouched, which is exactly the pre-v5.37 behaviour."""
        self._used_forced_device = False
        try:
            devices = [dict(d) for d in sd.query_devices()]
        except Exception:  # noqa: BLE001
            return saved_index, saved_name, ""
        default_index = system_default_input_index(devices)
        # v5.53 (F6): the silent-default escalation's forced
        # pick — the next capable input after a default that
        # opened but captured only silence. Validated against
        # THIS enumeration (index + name; by name alone when
        # the indices moved); a pick that no longer exists
        # falls through to the normal resolution.
        forced = getattr(self, "_force_device_once", None)
        if forced is not None:
            self._force_device_once = None
            try:
                f_index, f_name = forced
            except (TypeError, ValueError):
                f_index, f_name = None, None

            def _capable_at(i) -> bool:
                try:
                    return (
                        isinstance(i, int)
                        and 0 <= i < len(devices)
                        and int(devices[i].get(
                            "max_input_channels", 0) or 0) > 0)
                except Exception:  # noqa: BLE001
                    return False

            chosen = None
            if _capable_at(f_index) and (
                    not f_name
                    or devices[f_index].get("name") == f_name):
                chosen = f_index
            elif f_name:
                for i in range(len(devices)):
                    if _capable_at(i) and \
                            devices[i].get("name") == f_name:
                        chosen = i
                        break
            if chosen is not None:
                self._used_forced_device = True
                return chosen, devices[chosen].get("name"), ""
        mode = getattr(self, "input_mode", "auto")
        if getattr(self, "_force_default_once", False):
            # The silence guard's one fallback: this start opens
            # the system default whatever the mode says.
            self._force_default_once = False
            mode = "auto"
        index, name, note, _source = effective_input(
            devices, mode, saved_index, saved_name, default_index)
        return index, name, note

    def _resolve_live_input(self):
        """resolve_saved_input() for this manager's own pick."""
        return self.resolve_saved_input(
            self.input_device, self.input_device_name)

    def resolve_saved_output(self, saved_index, saved_name):
        """Resolve the speaker to open against the LIVE device
        list: returns (index | None, name | None, note). The
        output mirror of resolve_saved_input (field-check fix
        #12, M11a) — no mode gate (output has no auto/manual
        rule: no pick means PortAudio's own default) and no
        forced-pick machinery, just the v5.37 rules: intact ->
        moved-name -> system-default-output fallback. Never
        raises — when the device list can't even be read, the
        pick passes through untouched."""
        try:
            devices = [dict(d) for d in sd.query_devices()]
        except Exception:  # noqa: BLE001
            return saved_index, saved_name, ""
        default_index = system_default_output_index(devices)
        index, name, note = resolve_output_device(
            devices, saved_index, saved_name, default_index)
        return index, name, note

    def _resolve_live_output(self):
        """resolve_saved_output() for this manager's own pick."""
        return self.resolve_saved_output(
            self.output_device, self.output_device_name)

    def mic_status(self) -> dict[str, Any]:
        """Microphone health (v5.37), for the GUI's deaf-session
        hint and any future diagnostics:

          state   "no_stream" — no input stream is open;
                  "silent" — a stream is open but no frame has
                    crossed the voice floor (RMS > 0.008) in over
                    6 s since it opened, or in over 20 s at all —
                    the signature of a privacy-blocked, muted or
                    wrong device; ALSO "silent" when callbacks
                    have stalled (>5 s since the last one) or no
                    frame has arrived at all within 1 s of the
                    open (mic remake: a dead stream is silent,
                    not "quiet" and never "ok");
                  "quiet" — voice has crossed the floor, but the
                    rolling 3 s peak is still under 0.02;
                  "ok" — voice-level audio is arriving.
          device  the name of the device actually opened.
          level   the rolling ~3 s peak RMS (post-gain).
          muted   whether the mic is muted (the mute flag gates
                  the queue, not the RMS stamps — a muted stream
                  can still read "ok" on energy, so callers must
                  read this field too).
          open_error  the last open failure, if the input stream
                  could not be opened (None while open or denied).
          mic_starved_seconds  seconds the send loop has seen an
                  empty mic queue (0.0 while frames arrive) — the
                  send loop mirrors it here (issue-hunt #5), so
                  the deaf hint can fire on a starving session
                  even when the state reads "ok" (muted) or
                  "no_stream" (never opened).
        """
        now = time.monotonic()
        peak = 0.0
        for ts, rms in self._rms_window:
            if now - ts <= 3.5 and rms > peak:
                peak = rms
        if self._in_stream is None or self._input_opened_at is None:
            out = {"state": "no_stream", "device": None, "level": 0.0,
                   "muted": bool(self.is_muted),
                   "open_error": getattr(self, "_open_error", None),
                   "output_error": getattr(self, "_output_error", None),
                   # Issue-hunt #5: the send loop's starvation
                   # signal, mirrored here — a starving session
                   # must not look connected.
                   "mic_starved_seconds": float(getattr(
                       self, "_mic_starved_seconds", 0.0)),
                   # P2 speed pass (2026-11-16): which PortAudio
                   # latency each stream won ("low" or "default").
                   "input_latency": getattr(
                       self, "_input_latency_mode", None),
                   "output_latency": getattr(
                       self, "_output_latency_mode", None)}
            # v5.54 (EA-11 rung 4): a stream status note outlives
            # the stream that reported it — it is diagnosis gold
            # exactly when the stream has since stopped.
            stream_note = getattr(self, "_input_status_note", "")
            if stream_note:
                out["stream_note"] = stream_note
            out["callback_errors"] = int(getattr(
                self, "_mic_callback_errors", 0))
            return out
        last_voice = self._last_voice_at
        # Mic remake (v6.3.x): a stalled callback is a dead
        # stream, not a quiet room — the old code reported
        # "quiet" for up to ~20 s after the last voice while no
        # frames arrived at all, so the deaf hint (which keys on
        # "silent") never fired and the boot check read "quiet"
        # as ready. A stall is silent, first-class. Likewise a
        # stream that has delivered no frame at all reads
        # "silent" once it is a second old — the old code read
        # "ok" for its first 6 s, which is exactly the window a
        # fresh deaf session hides in.
        _last_cb = getattr(self, "_last_callback_at", None)
        callback_stalled = bool(
            _last_cb is not None
            and now - _last_cb > _MIC_CALLBACK_STALL_S)
        _no_frames_yet = (
            getattr(self, "_frames_received", 0) == 0
            and (now - self._input_opened_at) > 1.0)
        if last_voice is None:
            silent = (now - self._input_opened_at) > 6.0
        else:
            silent = (now - last_voice) > 20.0
        if callback_stalled or _no_frames_yet or silent:
            state = "silent"
        elif last_voice is not None and peak < 0.02:
            state = "quiet"
        else:
            state = "ok"
        out = {"state": state, "device": self._opened_device_name,
               "level": peak, "muted": bool(self.is_muted),
               "open_error": getattr(self, "_open_error", None),
               "output_error": getattr(self, "_output_error", None),
               # Issue-hunt #5: the send loop's starvation
               # signal, mirrored here — a starving session must
               # not look connected.
               "mic_starved_seconds": float(getattr(
                   self, "_mic_starved_seconds", 0.0)),
               # P2 speed pass (2026-11-16): which PortAudio
               # latency each stream won ("low" or "default").
               "input_latency": getattr(
                   self, "_input_latency_mode", None),
               "output_latency": getattr(
                   self, "_output_latency_mode", None)}
        # v5.95 (micfix): callback liveness — a stalled stream
        # (callbacks stopped) reads "silent" (mic remake: the
        # state now keys on the stall, it is not just an extra
        # field), so report the distinguishing facts alongside
        # it. The deaf hint keys on "silent".
        last_cb = _last_cb
        out["callback_errors"] = int(getattr(
            self, "_mic_callback_errors", 0))
        out["callback_age_s"] = (
            None if last_cb is None else now - last_cb)
        out["callback_stalled"] = callback_stalled
        # v5.52 (item 3): the silence guard's fallback, surfaced
        # wherever mic health is read (GUI hint / doctor).
        note = getattr(self, "_silence_fallback_note", "")
        if note:
            out["fallback_note"] = note
        # v5.53 (F6): the resolver's note — why this device is
        # the effective one when it is not the plain default —
        # rides the same surface instead of being discarded
        # (in auto mode it was, before F6).
        resolve_note = getattr(self, "_resolve_note", "")
        if resolve_note:
            out["resolve_note"] = resolve_note
        # v5.54 (EA-11 rung 4): the input stream's own status
        # classification (overflow / discontinuity / ...), when
        # the stream has reported one.
        stream_note = getattr(self, "_input_status_note", "")
        if stream_note:
            out["stream_note"] = stream_note
        return out

    def start(self):
        """Starts input and output streams."""
        # PF-18 batch 1: a denied microphone never opens INPUT —
        # this one check at start() entry neutralises BOTH revival
        # paths, because both funnel through start(): the mic
        # watchdog's stall recovery (_stop_streams()+start())
        # and the default-device watcher's reopen (stop()+start()).
        # A refused input leaves _in_stream None and is_recording
        # False, so the watchdog loop skips (not recording) and
        # the device-event handler returns early (no stream) —
        # nothing auto-restarts a denied mic. OUTPUT still opens:
        # speakers are not microphone input, and the assistant
        # must still be able to speak the refusal. A consult
        # failure fails closed (refuses input), matching the
        # grants store's own fail-closed read; the grants module
        # is stdlib-only, so a consult failure means the tree
        # itself is broken.
        # NOTE (ruled behaviour, not a bug): with no store file
        # yet (fresh install) microphone reads denied, so voice
        # input refuses until the user allows it — either through
        # the first-use mic ask at the first human Connect (D3:
        # "Allow once" records a session-only grant, which this
        # check honours) or by flipping microphone on in Settings
        # (the only permanent path).
        try:
            from .. import permission_grants as _pg
            _mic_denied = (_pg.is_mic_denied()
                           and not _pg.has_session_grant("microphone"))
        except Exception:  # noqa: BLE001 — fail closed, never open
            _mic_denied = True
        if _mic_denied:
            # A denial is a state, not a retryable open error:
            # clear any stale failure so the watchdog does not
            # keep retrying a mic the user switched off.
            self._open_error = None
            self._open_error_at = None
            self._open_retries = 0
            print("[Audio] Microphone is switched off in Settings — "
                  "input stream not opened.")
        if self._in_stream is None and not _mic_denied:
            # v5.37: resolve the input device EXPLICITLY instead
            # of handing PortAudio a raw saved index (or None and
            # a prayer): a stale pick that now names a silent or
            # output-only device is re-found by name, or dropped
            # to the system default — and the device actually
            # opened is printed and remembered for mic_status().
            device_index = self.input_device
            device_name = self.input_device_name
            note = ""
            try:
                device_index, device_name, note = self._resolve_live_input()
                if note:
                    print(f"[Audio] {note}")
            except Exception:  # noqa: BLE001 — resolution must never block audio
                device_index, device_name = self.input_device, self.input_device_name
                note = ""
            # v5.53 (F6): remember the resolve note for
            # mic_status(), whether THIS start opened via the
            # silence escalation's forced pick, and announce a
            # NEW resolve note in chat once per distinct text —
            # a changed effective device is never silent news.
            self._resolve_note = note
            self._opened_via_silent_fallback = bool(
                getattr(self, "_used_forced_device", False))
            if note and note != getattr(
                    self, "_resolve_note_announced", ""):
                self._resolve_note_announced = note
                _announce(note)
            print(f"[Audio] Input device: {device_name or 'system default'}")
            # Mic remake (v6.3.x): the open is guarded and
            # retried — the old code let sd.InputStream /
            # .start() raise out of start(), and every caller
            # swallowed it, so a failed open left a permanently
            # deaf session (is_recording False, _in_stream
            # None, watchdog skipping forever). An empty device
            # name is coerced to None (the 'Input ()' ghost: a
            # nameless enumeration entry must read as the
            # system default, never as a named device).
            if not device_name:
                device_name = None
            stream, open_error = self._open_input_stream(
                device_index, device_name)
            if stream is None:
                # Fallback chain: the system default, then
                # PortAudio's own default (device=None). A
                # -9996 on the resolved pick, or a device that
                # rejects 16 kHz, must not end the session.
                for fb_index, fb_name in self._open_fallbacks(
                        device_index):
                    stream, open_error = self._open_input_stream(
                        fb_index, fb_name)
                    if stream is not None:
                        device_index, device_name = fb_index, fb_name
                        print(f"[Audio] Opened the fallback input "
                              f"{fb_name or 'system default'} after "
                              f"the resolved device failed.")
                        break
            if stream is None:
                # Record the failure instead of raising: the mic
                # watchdog retries the open while the session is
                # live (see _mic_watchdog_loop), and mic_status()
                # surfaces it as open_error.
                self._open_error = (
                    open_error or "microphone would not open")
                self._open_error_at = time.monotonic()
                self._open_retries = int(getattr(
                    self, "_open_retries", 0)) + 1
                if (self._open_retries == 1
                        or self._open_retries % 6 == 0):
                    print(f"[Audio] Microphone would not open: "
                          f"{self._open_error} — will retry while "
                          f"the session is live.")
            else:
                self._in_stream = stream
                self.mic_drop_count = 0  # A9: drops are per-stream
                self.is_recording = True
                self._opened_device_name = device_name
                self._opened_device_index = device_index
                self._input_opened_at = time.monotonic()
                self._last_voice_at = None
                self._last_nonzero_at = None
                self._frames_received = 0
                self._rms_window.clear()
                self._open_error = None
                self._open_error_at = None
                self._open_retries = 0
                self._arm_silence_guard(self._in_stream, device_index)
                self._ensure_device_watch()

        if self._out_stream is None:
            # Voice-control batch (2026-11-16, O1): the output
            # open is guarded like the input open — the old code
            # let sd.OutputStream/.start() raise out of start(),
            # and _start_connection swallowed it, leaving a
            # "connected" session with a live mic and no speaker
            # at all. A failure records _output_error (surfaced
            # in mic_status(), retried by the mic watchdog while
            # the session is live) instead of raising.
            if not self._try_open_output():
                print(f"[Audio] Speaker would not open: "
                      f"{self._output_error} — will retry while "
                      f"the session is live.")
        # v5.95 (micfix): a fresh input stream owes its first
        # callback within the stall window; the watchdog
        # (same thread across reopens) watches from here.
        # Mic remake: the watchdog is armed whenever input was
        # wanted — a FAILED open arms it too, so the retry loop
        # (see _mic_watchdog_loop) can reopen while the session
        # is live instead of leaving the session deaf.
        if self._in_stream is not None:
            self._last_callback_at = time.monotonic()
        if not _mic_denied:
            self._ensure_mic_watchdog()

    def _open_input_stream(self, device_index, device_name):
        """Open one input stream, guarded end to end. Returns
        (stream, None) on success, (None, error) on failure —
        NEVER raises, and never leaves a half-open stream
        behind: if .start() raises after construction, the
        stream is closed and forgotten (the old code assigned
        _in_stream before .start(), so a half-open stream
        poisoned every later start()).

        P2 speed pass (2026-11-16): the stream is asked for
        PortAudio's LOW latency first — the default is the
        high-latency shared-mode buffer, which is pure delay
        on the user's words reaching the server. The retry is
        CONSTRUCTION-only: the latency hint is negotiated at
        open time, so a host that refuses 'low' refuses it
        there. A .start() failure is the DEVICE, not the
        buffer — the mic remake's rule holds, the half-open
        stream is closed and forgotten, and no second stream
        is constructed (a second construction would double
        the instance churn the remake deliberately removed).
        The mode that won is recorded on _input_latency_mode
        for mic_status()."""
        stream = None
        for _latency in ("low", None):
            try:
                stream = sd.InputStream(
                    samplerate=self.sample_rate_in,
                    channels=self.channels,
                    dtype="int16",
                    blocksize=self.chunk_size,
                    device=device_index,
                    callback=self._mic_callback,
                    **({"latency": _latency} if _latency else {}),
                )
            except Exception as e:  # noqa: BLE001
                if _latency is None:
                    return None, f"{type(e).__name__}: {e}"
                continue
            break
        try:
            stream.start()
        except Exception as e:  # noqa: BLE001
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
            return None, f"{type(e).__name__}: {e}"
        self._input_latency_mode = _latency or "default"
        return stream, None

    def _open_output_stream(self, device):
        """Open one output stream, guarded end to end — the
        mirror of _open_input_stream (voice-control batch,
        2026-11-16, O1). Returns (stream, None) on success,
        (None, error) on failure — NEVER raises, and never
        leaves a half-open stream behind: if .start() raises
        after construction, the stream is closed and forgotten
        (the old code assigned _out_stream before .start(), so
        a half-open output poisoned every later start()).

        P2 speed pass (2026-11-16): low latency first, same
        as the input stream — the output host buffer is pure
        delay on the first sound reaching the speakers, and
        the callback itself costs ~0.15 ms per 42.7 ms block,
        so a shallow buffer cannot starve it. The retry is
        construction-only, same as the input stream (a
        .start() failure is the device, not the buffer — no
        second construction). Falls back to the default
        exactly as before; the winning mode is recorded on
        _output_latency_mode for mic_status()."""
        stream = None
        for _latency in ("low", None):
            try:
                stream = sd.OutputStream(
                    samplerate=self.sample_rate_out,
                    channels=self.channels,
                    dtype="int16",
                    blocksize=self.chunk_size,
                    device=device,
                    callback=self._playback_callback,
                    **({"latency": _latency} if _latency else {}),
                )
            except Exception as e:  # noqa: BLE001
                if _latency is None:
                    return None, f"{type(e).__name__}: {e}"
                continue
            break
        try:
            stream.start()
        except Exception as e:  # noqa: BLE001
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
            return None, f"{type(e).__name__}: {e}"
        self._output_latency_mode = _latency or "default"
        return stream, None

    def _try_open_output(self) -> bool:
        """Open the output stream (configured device, then
        PortAudio's own default), recording _output_error on
        failure. Returns True when a stream is open. Never
        raises. Used by start() and by the mic watchdog's
        output retry, so both share one fallback chain.

        Field-check fix #12 (M11a): the pick is re-resolved
        against the LIVE device list first, like start() does
        for the input — a speaker Windows re-indexed is
        re-found by name instead of opening the stale index
        (which could now be a different device, or a mic).
        A pick that is gone falls back to the system default
        output with a note; the stored pick itself is left
        alone (the device may simply be unplugged today)."""
        if self._out_stream is not None:
            return True
        device = self.output_device
        try:
            resolved, resolved_name, note = self._resolve_live_output()
            if note:
                print(f"[Audio] {note}")
            device = resolved
            self.output_device = resolved
            self.output_device_name = resolved_name
            self._opened_output_device_name = resolved_name
        except Exception:  # noqa: BLE001 — resolution must never block audio
            pass
        stream, _err = self._open_output_stream(device)
        if stream is None and device is not None:
            stream, _err = self._open_output_stream(None)
            if stream is not None:
                print("[Audio] Opened the fallback output "
                      "(PortAudio default) after the configured "
                      "output device failed.")
        if stream is None:
            self._output_error = _err or "speaker would not open"
            self._output_error_at = time.monotonic()
            return False
        self._out_stream = stream
        self._output_error = None
        self._output_error_at = None
        return True

    def _open_fallbacks(self, resolved_index):
        """Yield (index, name) fallbacks for a failed open, in
        order: the system default (if it is not the resolved
        device), then PortAudio's own default (device=None).
        Never raises."""
        try:
            devices = sd.query_devices()
        except Exception:  # noqa: BLE001
            devices = []
        try:
            default_index = system_default_input_index(devices)
        except Exception:  # noqa: BLE001
            default_index = None
        if (default_index is not None
                and default_index != resolved_index):
            try:
                default_name = (
                    devices[default_index].get("name") or None)
            except Exception:  # noqa: BLE001
                default_name = None
            yield default_index, default_name
        # PortAudio's own default (device=None): the last
        # resort — distinct from the resolved index by
        # construction, since None lets PortAudio choose.
        if resolved_index is not None:
            yield None, None

    def _arm_silence_guard(self, stream, opened_index) -> None:
        """v5.52 (item 3) silence guard, remade v6.3.x: 20 s
        after this start, if the SAME stream is still open
        and has delivered ONLY exact-zero frames since it
        opened — a non-default device reopens ONCE on the
        system default (the original branch); the system
        default ITSELF is only diagnosed, never hopped away
        from (v5.53 F6's fall-through to the next enumerated
        input is removed: it swapped a correct mic for a
        webcam mic / virtual cable / dead device on the
        strength of a quiet room). The old guard fired at
        6.5 s on mic_status() == "silent", which is true
        whenever the user simply has not spoken yet — a quiet
        user is not a dead device. Notes land in
        _silence_fallback_note (the doctor/status surface),
        the console log, and — for the diagnosis lines —
        chat, via the announce hook. Never raises."""
        def _check():
            try:
                self._silence_guard_fired(stream, opened_index)
            except Exception:  # noqa: BLE001 — a guard must not break audio
                pass

        try:
            timer = threading.Timer(20.0, _check)
            timer.daemon = True
            timer.start()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------
    # v5.54 (Wave 0, EA-10): default-device switch watching
    # ------------------------------------------------------
    def _ensure_device_watch(self) -> None:
        """Arm the default-device watcher once per session
        (core/device_watch.py). When Windows moves the default
        microphone, the watcher calls _on_default_device_event
        so the input stream reopens on the NEW default instead
        of listening to a device that is no longer the default
        (or no longer exists). Silent no-op off Windows / when
        COM is unavailable; never raises."""
        if getattr(self, "_device_watch_armed", False):
            return
        try:
            from ..device_watch import DefaultDeviceWatcher  # Phase 3 V1a-1: audio chain moved to core/voice/

            watcher = DefaultDeviceWatcher(
                self._on_default_device_event)
            if watcher.start():
                self._device_watcher = watcher
                self._device_watch_armed = True
                print("[Audio] Watching for default-microphone "
                      "changes.")
            # Voice-control batch (2026-11-16, W4): a failed arm
            # used to set _device_watch_armed True anyway, so the
            # watcher was never retried even if COM became
            # available later — leave it False on failure so a
            # later start() retries the arm.
        except Exception:  # noqa: BLE001 — a watcher must not
            pass  # break audio; the silence guard is the backstop

    def _ensure_mic_watchdog(self) -> None:
        """Start the input-stream liveness watchdog once
        (v5.95 micfix). The thread survives stream reopens
        (recovery reuses it); a user stop() sets its stop
        event and the next start() replaces it. Never raises.
        Voice-control batch (2026-11-16, W1): the loop runs
        under a generation — stop() retires the old generation,
        so a cleared stop event can never resurrect a sleeping
        old thread alongside the new one (the two-watchdog
        race: stop() set the event, start() cleared it and
        spawned a second thread while the old one still slept)."""
        try:
            thread = self._mic_watchdog_thread
            if thread is not None and thread.is_alive() \
                    and not self._mic_watchdog_stop.is_set():
                return
            self._mic_watchdog_gen = int(getattr(
                self, "_mic_watchdog_gen", 0)) + 1
            _gen = self._mic_watchdog_gen
            self._mic_watchdog_stop.clear()
            thread = threading.Thread(
                target=self._mic_watchdog_loop, daemon=True,
                name="MicWatchdog", args=(_gen,))
            thread.start()
            self._mic_watchdog_thread = thread
        except Exception:  # noqa: BLE001 — a watchdog must not break audio
            pass

    def _mic_watchdog_loop(self, gen=None) -> None:
        """Daemon loop (v5.95 micfix): while a stream is open
        and recording, if no callback has fired for longer
        than _MIC_CALLBACK_STALL_S, the input stream is dead
        in fact (device glitch, exclusive-mode grab, callback
        failure) — reopen it via _stop_streams()+start(),
        exactly the device-switch pattern. A muted-but-healthy
        stream still fires callbacks, so mute never trips
        this. A fresh stream gets its stall window from its
        birth stamp in start(). Mic remake (v6.3.x): when NO
        stream is open because start() failed to open one,
        the loop retries the open (throttled to
        _MIC_OPEN_RETRY_S) while the session is live — the
        old loop skipped forever on _in_stream None, so a
        single -9996 at Connect deafened the whole session.
        Never raises; never joins (recovery runs on this
        thread). Voice-control batch (2026-11-16, W1/O1): the
        loop exits when its generation is retired, and a failed
        OUTPUT open is retried here too, throttled like the
        input retry — a speaker busy at Connect recovers
        without a reconnect."""
        while not self._mic_watchdog_stop.is_set():
            try:
                time.sleep(_MIC_WATCHDOG_TICK_S)
                if gen is not None and gen != getattr(
                        self, "_mic_watchdog_gen", None):
                    return
                if self._mic_watchdog_stop.is_set():
                    break
                if self._in_stream is None:
                    # A start() that failed to open records
                    # _open_error instead of raising — retry
                    # the open while the session is live. A
                    # denial clears _open_error, so a denied
                    # mic is never retried; a user stop()
                    # exits this loop outright.
                    if getattr(self, "_open_error", None) is not None:
                        _last_try = getattr(
                            self, "_open_error_at", 0) or 0
                        if (time.monotonic() - _last_try
                                >= _MIC_OPEN_RETRY_S):
                            try:
                                self.start()
                            except Exception as e:  # noqa: BLE001
                                print(f"[Audio] Mic open retry "
                                      f"failed: {e}")
                                self._open_error_at = time.monotonic()
                    continue
                # Voice-control batch (2026-11-16, O1): a failed
                # output open is retried while the session is
                # live, throttled to the same interval — the
                # input retry above only runs when no input
                # stream is open, so the output retry lives here.
                if getattr(self, "_output_error", None) is not None:
                    _last_out_try = getattr(
                        self, "_output_error_at", 0) or 0
                    if (time.monotonic() - _last_out_try
                            >= _MIC_OPEN_RETRY_S):
                        try:
                            self._try_open_output()
                        except Exception as e:  # noqa: BLE001
                            print(f"[Audio] Speaker open retry "
                                  f"failed: {e}")
                            self._output_error_at = time.monotonic()
                if not self.is_recording:
                    continue
                last = self._last_callback_at
                if last is None or self._mic_recovering:
                    continue
                if time.monotonic() - last <= _MIC_CALLBACK_STALL_S:
                    continue
                self._mic_recovering = True
                try:
                    print("[Audio] Input stream stalled (no mic "
                          "callbacks) — reopening the microphone.")
                    try:
                        self._log_mic_drops()
                    except Exception:  # noqa: BLE001
                        pass
                    self._stop_streams()
                    self.start()
                    if self._mic_watchdog_stop.is_set():
                        # A user stop() landed mid-recovery:
                        # stay stopped, never resurrect.
                        self._stop_streams()
                except Exception as e:  # noqa: BLE001
                    print(f"[Audio] Mic reopen failed: {e}")
                finally:
                    self._mic_recovering = False
            except Exception:  # noqa: BLE001 — the watchdog never dies
                pass

    def _on_default_device_event(self, kind: str, role: int,
                                 device_id: str) -> None:
        """The watcher's coalesced report (its timer thread).
        Only the CONSOLE role steers her stream (it is the role
        her backend resolves). With no stream open there is
        nothing to reopen — the next start() resolves fresh.
        A default change always reopens; a removal / state
        change reopens only when the open stream hears nothing
        (the removal that matters is the opened device's)."""
        try:
            if role != 0:  # ERole.eConsole
                return
            if self._in_stream is None:
                return
            if kind == "default_changed":
                self._reopen_for_device_switch()
            elif kind in ("removed", "state_changed"):
                if self.mic_status().get("state") == "silent":
                    self._reopen_for_device_switch()
        except Exception:  # noqa: BLE001 — never break audio
            pass

    def _reopen_for_device_switch(self) -> None:
        """Reopen on the (new) default via the silence guard's
        own stop()+start() pattern, and ANNOUNCE the switch in
        plain words — a changed effective microphone is never
        silent news."""
        old_name = self._opened_device_name
        try:
            self.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.start()
        except Exception:  # noqa: BLE001
            pass
        if self._in_stream is not None and self.is_recording:
            new_name = self._opened_device_name
            if new_name and new_name != old_name:
                msg = (f"Your default microphone changed, so "
                       f"I've switched from '{old_name}' to "
                       f"'{new_name}' to keep hearing you.")
            elif new_name:
                msg = (f"Your default microphone changed — I've "
                       f"reopened '{new_name}' so I can still "
                       "hear you.")
            else:
                msg = ("Your default microphone changed — I've "
                       "reopened the new default so I can still "
                       "hear you.")
            _announce(msg)
            print(f"[Audio] {msg}")
        else:
            msg = ("Your default microphone changed and I "
                   "couldn't open the new one — the doctor's "
                   "microphone check can diagnose it.")
            _announce(msg)
            print(f"[Audio] {msg}")

    def _note_input_status(self, status) -> None:
        """EA-11 rung 4 (v5.54): classify the input callback's
        status flags (core/mic_doctor.classify_stream_flags)
        and remember the note for mic_status() / the doctor.
        Called only when a status is present; cheap."""
        try:
            from . import mic_doctor

            note = mic_doctor.classify_stream_flags({
                "input_overflow": bool(
                    getattr(status, "input_overflow", False)),
                "input_underflow": bool(
                    getattr(status, "input_underflow", False)),
            })
            if note and note != self._input_status_note:
                self._input_status_note = note
                print(f"[Audio] Input stream status: {note}")
        except Exception:  # noqa: BLE001 — classification is
            pass  # diagnostic only

    def _silence_guard_fired(self, stream, opened_index) -> None:
        if self._in_stream is not stream:
            return  # restarted/stopped since: not our stream
        # Mic remake (v6.3.x): the guard fires ONLY on
        # exact-zero digital silence — no nonzero frame in
        # the 20 s since this stream opened. The old guard
        # fired on mic_status() == "silent", which is true
        # whenever the user simply has not spoken yet (no
        # frame crossed the 0.008 voice floor in 6.5 s) — so
        # a quiet user got their correct mic swapped for
        # the next enumerated input. A merely-quiet user
        # (room noise arriving, voice below the floor) is
        # NOT a dead device and must never trigger a switch.
        _now = time.monotonic()
        if getattr(self, "_last_nonzero_at", None) is not None:
            return  # nonzero frames are arriving: alive
        _opened_at = getattr(self, "_input_opened_at", None)
        if _opened_at is None or _now - _opened_at < 20.0:
            return  # the timer fired early: wait
        if getattr(self, "_opened_via_silent_fallback", False):
            # v5.53 (F6): this stream IS the silent-default
            # escalation's fallback, and it is silent too —
            # say so once, and stop. Falling back again from
            # here would ping-pong between two dead devices
            # (the "next" candidate IS the silent default).
            if not self._silent_fallback_failed_announced:
                self._silent_fallback_failed_announced = True
                msg = (
                    "The next microphone "
                    f"('{self._opened_device_name}') is also "
                    "silent, so I have stopped switching. "
                    "On Windows this is usually Settings > "
                    "Privacy > Microphone (let apps access "
                    "your microphone), an OS-level mute on "
                    "the device itself, or the wrong device.")
                self._silence_fallback_note = msg
                _announce(msg)
                print(f"[Audio] {msg}")
            return
        try:
            devices = [dict(d) for d in sd.query_devices()]
        except Exception:  # noqa: BLE001
            return
        default_index = system_default_input_index(devices)
        if default_index is None or default_index == opened_index:
            # v5.53 (F6), remade: the opened device IS the
            # resolved default (or no default could be
            # identified) — "switch to the default" has
            # nowhere to go, and falling through to the next
            # enumerated input swapped a correct mic for
            # whatever happened to be listed next. Diagnose
            # instead of hopping; the stream stays open so
            # the user can speak and be heard the moment
            # the device recovers.
            self._diagnose_silent_default(
                opened_index, default_index)
            return
        name = None
        try:
            name = devices[default_index].get("name")
        except Exception:  # noqa: BLE001
            pass
        self._force_default_once = True
        self._silence_fallback_note = (
            "The opened microphone "
            f"('{self._opened_device_name}') was silent, so I "
            f"switched to this PC's default input "
            f"('{name or 'system default'}').")
        print(f"[Audio] {self._silence_fallback_note}")
        # Reopen once on the default: stop() + start() with the
        # force-default flag consumed by resolve_saved_input.
        try:
            self.stop()
        except Exception:  # noqa: BLE001
            pass
        self.start()

    def _diagnose_silent_default(self, opened_index,
                                 default_index) -> None:
        """Mic remake (v6.3.x), replacing v5.53 (F6)
        _escalate_silent_default: the opened device IS the
        resolved default (or no default could be identified)
        and it has delivered only exact-zero frames for
        20 s — the case the v5.52 guard returned silently
        on, leaving a deaf session with no clue why.

        The old escalation then fell through ONCE per
        device set to the next input-capable device in
        enumeration order — which swapped a correct mic
        for a webcam mic, a virtual cable, or a dead
        device on the strength of a quiet room, and the
        wrong device persisted until reconnect. The remake
        diagnoses and STAYS: the diagnosis is announced
        in chat ONCE per app session, in the doctor's own
        words for an opens-but-silent device (the app and
        the doctor tell the user the same thing, including
        the Windows privacy check), and the stream stays
        open on the default so the user is heard the
        moment the device recovers. Never raises."""
        opened_name = self._opened_device_name or "system default"
        # The diagnosis — once per app session.
        if not self._silent_default_announced:
            self._silent_default_announced = True
            if default_index is not None:
                msg = (
                    f"'{opened_name}' (this PC's default "
                    f"(Automatic)) opens but delivers silence "
                    f"— {_MIC_SILENCE_EXPLANATION}.")
            else:
                msg = (
                    f"'{opened_name}' opens but delivers "
                    f"silence — {_MIC_SILENCE_EXPLANATION}.")
            self._silence_fallback_note = msg
            _announce(msg)
            print(f"[Audio] {msg}")


    def get_mic_drop_count(self) -> int:
        """Mic chunks dropped on a full mic_queue since the input
        stream last started (v5.43, A9) — the doctor's accessor;
        mic_drop_count is the same number as a plain attribute."""
        return int(self.mic_drop_count)

    def _log_mic_drops(self):
        """Surfaces the drop count in the session log (A9): one
        line in data/activity.log — the file every session's
        events land in — plus the console. Called from stop();
        silent when nothing was dropped. Never raises."""
        count = int(getattr(self, "mic_drop_count", 0) or 0)
        if count <= 0:
            return
        line = (f"Mic queue full: dropped {count} mic chunk(s) "
                f"this session — speech may have been clipped.")
        print(f"[Audio] {line}")
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(DATA_DIR / "activity.log", "a",
                      encoding="utf-8") as fh:
                fh.write(f"[Audio] {line}\n")
        except Exception:  # noqa: BLE001 — telemetry must not break stop
            pass

    def _stop_streams(self):
        """Close both streams without touching the watchdog
        (v5.95 micfix): the watchdog thread itself recovers by
        _stop_streams()+start(), so it must not set its own
        stop event; user-facing stop() does that below.
        Voice-control batch (2026-11-16, O10): each close is
        guarded individually — an output close that raises
        (device removed mid-session) used to skip the rest of
        stop(), leaving the watchdog thread alive and, in the
        worst case, resurrecting the mic after disconnect."""
        self.is_recording = False
        if self._in_stream:
            try:
                self._in_stream.stop()
            except Exception:  # noqa: BLE001 — a close must not break stop
                pass
            try:
                self._in_stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._in_stream = None
            self._input_opened_at = None
            self._last_callback_at = None
        if self._out_stream:
            try:
                self._out_stream.stop()
            except Exception:  # noqa: BLE001 — a close must not break stop
                pass
            try:
                self._out_stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._out_stream = None

    def stop(self):
        """Stops all streams."""
        try:
            self._log_mic_drops()
        except Exception:  # noqa: BLE001 — telemetry must not break stop
            pass
        try:
            self._stop_streams()
        finally:
            # Voice-control batch (2026-11-16, O10/W1): the
            # watchdog stop + generation retire + error cleanup
            # live in a finally, so a close failure can never
            # skip them and leave a zombie watchdog behind.
            self._mic_watchdog_gen = int(getattr(
                self, "_mic_watchdog_gen", 0)) + 1
            self._mic_watchdog_stop.set()
            # Mic remake: a user stop ends the session — a
            # recorded open failure must not survive it (the
            # watchdog loop exits on the stop event, and the
            # next start() begins from a clean slate).
            self._open_error = None
            self._open_error_at = None
            self._open_retries = 0
            self._output_error = None
            self._output_error_at = None

    @staticmethod
    def _spectrum_bands_from_mono(raw_float_mono: np.ndarray) -> list[float]:
        """16-band FFT analysis of one mono float block (v5.42).

        The single binning/scaling shared by the mic and playback
        paths (bins of len(rfft)//16, energy * 8.0 clamped to 1.0),
        so the two spectra are visually comparable."""
        fft_vals = np.abs(np.fft.rfft(raw_float_mono))
        band_size = max(1, len(fft_vals) // 16)
        bands = []
        for b in range(16):
            start = b * band_size
            end = min(len(fft_vals), (b + 1) * band_size)
            energy = float(np.mean(fft_vals[start:end])) if end > start else 0.0
            bands.append(min(1.0, energy * 8.0))
        return bands

    def _mic_callback(self, indata, frames, time_info, status):
        """PortAudio callback entry (v5.95 micfix): stamps
        liveness on EVERY invocation (muted or not — mute
        gates the queue, never the callback) and guards the
        processing body so one bad frame can never kill the
        stream silently. A raising body used to abort the
        stream with no error, no reopen, and no UI state —
        the session stayed "connected" and deaf while typed
        chat kept working. Errors are counted (mic_status)
        and printed throttled (first + every 100th)."""
        self._last_callback_at = time.monotonic()
        try:
            self._mic_callback_impl(indata, frames, time_info, status)
        except Exception as e:  # noqa: BLE001 — a callback must never die
            self._mic_callback_errors += 1
            if self._mic_callback_errors == 1 or \
                    self._mic_callback_errors % 100 == 0:
                print(f"[Audio] Mic callback error "
                      f"(#{self._mic_callback_errors}): {e}")

    def _mic_callback_impl(self, indata: np.ndarray, frames: int, time_info, status):
        """Processes microphone input frames."""
        if status:
            # v5.54 (EA-11 rung 4): classify + remember the
            # stream's own status instead of dropping it.
            self._note_input_status(status)

        # Input gain (v5.17) FIRST: everything below — the level
        # meter, the FFT, the ring buffer, the transmission queue —
        # works on the gained signal, so turning her ears up helps
        # every downstream listener alike. float32 represents every
        # int16 exactly, so gain 1.0 is a bit-exact pass-through.
        gained = np.clip(
            indata.astype(np.float32) * float(self.mic_gain),
            -32768.0, 32767.0,
        ).astype(np.int16)

        # Compute volume level & FFT spectrum for HUD
        raw_float = gained.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(raw_float ** 2)))
        self.mic_level = min(1.0, rms * 5.0)

        # Mic health (v5.37): keep the rolling ~3 s of post-gain
        # RMS for mic_status(), and stamp the last voice-floor
        # crossing. Cheap and allocation-light — one deque append
        # per PortAudio block.
        _now = time.monotonic()
        self._rms_window.append((_now, rms))
        while self._rms_window and _now - self._rms_window[0][0] > 3.5:
            self._rms_window.popleft()
        if rms > 0.008:
            self._last_voice_at = _now

        # Mic remake: track frame arrival for the silence
        # guard + mic_status() — the guard must tell a quiet
        # user (frames arriving, voice below the floor) from
        # a dead device (exact-zero frames), and mic_status()
        # must not read "ok" for a stream that never delivered
        # a frame. np.any over 1024 int16s costs ~µs.
        self._frames_received = int(getattr(
            self, "_frames_received", 0)) + 1
        if np.any(gained != 0):
            self._last_nonzero_at = _now

        # FFT Analysis into 16 logarithmically spaced frequency bins
        if rms > 0.001:
            self.spectrum_bands = self._spectrum_bands_from_mono(raw_float[:, 0])
        else:
            self.spectrum_bands = [b * 0.8 for b in self.spectrum_bands]

        # Keep rolling audio in ring buffer for wake-word detection
        self._mic_ring_buffer.append(gained.copy())

        # Put audio to transmission queue if not muted
        if not self.is_muted:
            pcm_bytes = gained.tobytes()
            try:
                self.mic_queue.put_nowait(pcm_bytes)
            except queue.Full:
                # A9: a dropped chunk is lost speech — count it
                # (surfaced at stop() + via get_mic_drop_count())
                # instead of swallowing it silently.
                self.mic_drop_count += 1

    def get_recent_mic_audio(self, seconds: float = 2.0) -> list[np.ndarray]:
        """Returns the most recent audio chunks up to the requested duration."""
        chunks_needed = int((seconds * self.sample_rate_in) / self.chunk_size)
        buffer_copy = list(self._mic_ring_buffer)
        return buffer_copy[-chunks_needed:] if buffer_copy else []
