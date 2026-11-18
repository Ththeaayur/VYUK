"""The microphone doctor's deep probes (v5.54, Wave 0 — wave 9
EA-11, with the EA-7 SoundCard rung).

WHY: the field failure this answers is a stream that opens fine
and hears only silence, with three different possible causes
that look identical from inside the app. This module separates
them by asking WINDOWS, not the audio backend:

  1. endpoint state — is the console-role default capture
     endpoint active / disabled / unplugged / not present
     (core/mmdevice.py, her own MMDevice layer);
  2. endpoint volume — is it OS-muted, or is its level at zero
     (IAudioEndpointVolume);
  3. endpoint meter — does sound reach WINDOWS during a short
     prompted test (IAudioMeterInformation.GetPeakValue polled
     WITHOUT opening her stream, so the probe can't be the
     thing that's broken);
  4. stream flags — when her own stream IS running, what its
     callback status says (overflow / discontinuity / silent
     buffers), classified by classify_stream_flags().

The "three silences" verdict:
  · meter moves + her stream silent  → an APP-SIDE cause
    (host-API/default mismatch, exclusive-mode takeover, or
    Windows mic-privacy denial);
  · meter flat + a settings cause     → the named Windows /
    device cause (muted, level zero, disabled, unplugged);
  · meter flat + settings clean       → hardware or driver.

Plus the EA-7 rung: SoundCard (BSD 3-Clause, licence verified
from its PyPI wheel) names the default microphone through an
independent, WASAPI-native enumeration — a cross-check on the
name her own backend resolved. SoundCard's default is the
eConsole role (read from its mediafoundation.py source,
2026-10-24), the same role rung 1 reads.

Plus the §10 rung (voice-control batch, 2026-11-16): the
Windows mic-privacy state (core/platform_permission_probe —
the "Let desktop apps access your microphone" toggle) is
judged BEFORE the three-silence matrix. A denial explains
every downstream reading, and without this rung the doctor
blamed hardware or the driver for a Settings toggle.

Everything here is lazy and guarded (INV-12): off Windows, or
with any piece missing, the probes return None and the caller
falls back to the plain device-list diagnosis. diagnose() is a
PURE function over probe results so the verdict matrix is
testable without a microphone.
"""
from __future__ import annotations

import time

# A meter peak at/above this during the prompted test counts as
# "sound is reaching Windows"; at/below the flat bound, with
# clean settings, the device itself is the suspect.
METER_MOVES = 0.02
METER_FLAT = 0.002
LEVEL_NEAR_ZERO = 0.02


def classify_stream_flags(flags: dict | None) -> str | None:
    """Rung 4: classify a capture stream's status flags into a
    plain-words note, or None when nothing is wrong. Accepts
    the WASAPI-shaped names (silent_buffer / discontinuity /
    timestamp_error) and the PortAudio-shaped pair her
    sounddevice callback actually surfaces (input_overflow /
    input_underflow) — the mapping is stated, not hidden."""
    if not flags:
        return None
    if flags.get("silent_buffer"):
        return ("the stream is being handed silent buffers by "
                "the host (a host-side silence, not a quiet room)")
    if flags.get("timestamp_error"):
        return ("the stream's timestamps are erroring "
                "(a driver / host-API fault)")
    if flags.get("discontinuity"):
        return ("the stream reports discontinuities "
                "(buffers are being dropped or the device "
                "glitched)")
    if flags.get("input_overflow"):
        return ("the stream reports input overflow "
                "(the PC is not draining the microphone fast "
                "enough — buffers are being lost)")
    if flags.get("input_underflow"):
        return ("the stream reports input underflow "
                "(the device is not delivering buffers on time)")
    return None


def soundcard_default_microphone() -> dict | None:
    """The EA-7 rung: SoundCard's view of the default
    microphone — {"name": str, "choices": [names]} — or None
    when SoundCard is missing or its backend can't enumerate
    (a headless Linux box, say). Never raises."""
    try:
        import soundcard  # lazy (INV-12)

        default = soundcard.default_microphone()
        choices = [str(getattr(m, "name", m))
                   for m in soundcard.all_microphones()]
        return {"name": str(getattr(default, "name", default)),
                "choices": choices}
    except Exception:  # noqa: BLE001 — the rung is a cross-check
        return None


def diagnose(*, endpoint: dict | None = None,
             volume: dict | None = None,
             meter_peak: float | None = None,
             stream_hearing: bool | None = None,
             stream_note: str | None = None,
             os_state=None) -> dict:
    """The verdict for one set of probe results (PURE):
    {"verdict": str, "sentence": str}. The sentence is written
    for the doctor's detail line — one cause, one fix.
    ``os_state`` is the Windows mic-privacy state for the
    microphone — a PlatformPermissionState, a dict with a
    "state" key, or a plain "allowed"/"denied"/"unknown" string
    (None = unprobed, the old behaviour). It is judged FIRST:
    a Windows denial explains every downstream reading (the
    endpoint exists and reads clean, the meter may even move —
    privacy blocks the app's capture, not the endpoint), and
    without this branch the doctor blamed hardware or the
    driver for a Settings toggle (voice-control batch,
    2026-11-16, §10 P1). "unknown" never changes the verdict.
    """
    if os_state is not None:
        if isinstance(os_state, dict):
            os_s = str(os_state.get("state", "") or "").strip().lower()
        elif isinstance(os_state, str):
            os_s = os_state.strip().lower()
        else:
            os_s = str(getattr(os_state, "state", "") or "").strip().lower()
        if os_s == "denied":
            return {"verdict": "os_denied", "sentence":
                    "Windows has the microphone off for desktop "
                    "apps, so VYUK's stream hears nothing whatever "
                    "the endpoint says. Fix: Settings > Privacy > "
                    "Microphone — turn on 'Let desktop apps access "
                    "your microphone' (the shared desktop-apps "
                    "toggle; it gates every desktop app, not just "
                    "VYUK)."}
    if endpoint is not None:
        state = endpoint.get("state_name", "")
        if state == "disabled":
            return {"verdict": "endpoint_disabled", "sentence":
                    "Windows says the default microphone is "
                    "disabled. Fix: Settings > Sound > Input — "
                    "enable it (or Device Manager > Audio inputs "
                    "and outputs)."}
        if state == "not present":
            return {"verdict": "endpoint_not_present", "sentence":
                    "Windows says the default microphone is not "
                    "present — it was removed or its driver is "
                    "gone. Fix: reconnect it, or pick another "
                    "default in Settings > Sound > Input."}
        if state == "unplugged":
            return {"verdict": "endpoint_unplugged", "sentence":
                    "Windows says the default microphone is "
                    "unplugged. Fix: check its cable / jack, or "
                    "pick another default in Settings > Sound > "
                    "Input."}
    if volume is not None and volume.get("mute"):
        return {"verdict": "os_muted", "sentence":
                "The default microphone is muted at the Windows "
                "level. Fix: Settings > Sound > Input — unmute "
                "it (some headsets also have a physical mute "
                "switch on the cable or earcup)."}
    if (volume is not None
            and volume.get("level") is not None
            and float(volume["level"]) < LEVEL_NEAR_ZERO):
        return {"verdict": "level_zero", "sentence":
                "The default microphone's Windows volume is at "
                "zero. Fix: Settings > Sound > Input — raise "
                "the input volume slider."}
    if meter_peak is not None:
        if meter_peak >= METER_MOVES and stream_hearing is False:
            extra = f" Her stream reports {stream_note}." \
                if stream_note else ""
            return {"verdict": "app_side", "sentence":
                    "Sound IS reaching Windows — its own meter "
                    "moved during the test — but VYUK's stream "
                    "hears nothing, so the cause is on the app "
                    "side: a host-API/default mismatch, another "
                    "app holding the microphone in exclusive "
                    "mode, or Windows mic privacy blocking "
                    "desktop apps (Settings > Privacy > "
                    "Microphone)." + extra}
        if meter_peak <= METER_FLAT:
            return {"verdict": "hardware_or_driver", "sentence":
                    "Windows hears nothing at its own meter "
                    "either, and the endpoint settings are clean "
                    "(present, unmuted, volume up) — that "
                    "points at the hardware or its driver: "
                    "check the cable and any headset mute "
                    "switch, try another jack / USB port, or "
                    "reinstall the audio driver."}
        if meter_peak < METER_MOVES:
            # Mic remake (v6.3.x): the dead band. A meter peak
            # between FLAT and MOVES is too little to tell
            # quiet speech from a fault — the old code fell
            # through to "ok" here, so a quiet-but-present
            # meter was reported as healthy while VYUK heard
            # nothing. When the stream DID hear something the
            # verdict is still ok (quiet voice, not a fault);
            # when it heard nothing the verdict is honestly
            # inconclusive, not healthy.
            extra = f" Her stream reports {stream_note}." \
                if stream_note else ""
            if stream_hearing:
                return {"verdict": "ok", "sentence":
                        "Sound is reaching Windows and the endpoint "
                        "settings are clean — the meter moved only "
                        "a little, so the voice was quiet; speak "
                        "closer or raise the input volume if she "
                        "still misses soft speech." + extra}
            return {"verdict": "inconclusive", "sentence":
                    "Windows' meter moved only a little during "
                    "the test — too little to tell quiet speech "
                    "from a fault. Speak louder and closer to "
                    "the microphone and re-run the doctor; if it "
                    "stays inconclusive, check the input volume "
                    "in Settings > Sound > Input." + extra}
        return {"verdict": "ok", "sentence":
                "Sound is reaching Windows and the endpoint "
                "settings are clean."}
    if stream_note:
        return {"verdict": "stream_fault", "sentence":
                f"Her stream reports a fault: {stream_note}. "
                "The endpoint settings themselves read clean."}
    if endpoint is not None or volume is not None:
        return {"verdict": "settings_ok_unprobed", "sentence":
                "The endpoint settings read clean (present, "
                "unmuted, volume up). The meter leg did not "
                "run, so an app-side fault and a dead device "
                "can't be separated from here."}
    return {"verdict": "unprobed", "sentence":
            "The deep microphone probes aren't available on "
            "this system, so only the device list was checked."}


def poll_meter_peak(seconds: float = 3.0,
                    interval: float = 0.1) -> float | None:
    """Rung 3's engine: poll the default endpoint's own meter
    for `seconds` WITHOUT opening her stream; returns the peak
    seen, or None when the meter can't be read."""
    from .. import mmdevice  # Phase 3 V1a-1: audio chain moved to core/voice/

    device = mmdevice.default_capture_endpoint()
    if device is None:
        return None
    meter = mmdevice.endpoint_meter(device)
    if meter is None:
        return None
    peak = 0.0
    deadline = time.monotonic() + max(0.1, float(seconds))
    while True:
        value = mmdevice.meter_peak(meter)
        if value is not None and value > peak:
            peak = value
        if time.monotonic() >= deadline:
            return peak
        time.sleep(max(0.01, float(interval)))


def run_diagnosis(meter_seconds: float = 0.0,
                  stream_hearing: bool | None = None,
                  stream_note: str | None = None,
                  os_state=None) -> dict:
    """Gather the live probes and return diagnose()'s verdict
    plus the raw probe results:
    {"verdict", "sentence", "endpoint", "volume", "meter_peak",
     "soundcard", "os_microphone"}. meter_seconds > 0 runs the
    prompted meter leg (the caller owns the prompt). ``os_state``
    is the Windows mic-privacy state in diagnose()'s shape; when
    None it is read live here via probe_platform_permissions()
    (guarded — a failed probe reads as unprobed, never as a
    denial). Never raises."""
    from .. import mmdevice  # Phase 3 V1a-1: audio chain moved to core/voice/

    endpoint = volume = None
    meter_peak = None
    os_microphone = None
    if os_state is None:
        # Voice-control batch (2026-11-16, §10 P1): the doctor
        # never consulted the OS privacy state, so a Windows
        # denial was diagnosed as hardware/driver. Read it here
        # (read-only registry probe); any failure leaves it
        # unprobed and the verdict unchanged.
        try:
            from .. import platform_permission_probe as _ppp
            states = _ppp.probe_platform_permissions()
            mic = (states or {}).get("microphone")
            if mic is not None:
                os_microphone = str(
                    getattr(mic, "state", "") or "").strip().lower() \
                    or None
                os_state = os_microphone
        except Exception:  # noqa: BLE001 — probes never break a caller
            os_microphone = None
            os_state = None
    elif isinstance(os_state, dict):
        os_microphone = str(
            os_state.get("state", "") or "").strip().lower() or None
    elif isinstance(os_state, str):
        os_microphone = os_state.strip().lower() or None
    else:
        os_microphone = str(
            getattr(os_state, "state", "") or "").strip().lower() or None
    try:
        device = mmdevice.default_capture_endpoint()
        if device is not None:
            endpoint = mmdevice.endpoint_state_and_id(device)
            volume = mmdevice.endpoint_volume(device)
    except Exception:  # noqa: BLE001 — probes never break a caller
        endpoint = volume = None
    if meter_seconds and meter_seconds > 0:
        try:
            meter_peak = poll_meter_peak(meter_seconds)
        except Exception:  # noqa: BLE001
            meter_peak = None
    verdict = diagnose(endpoint=endpoint, volume=volume,
                       meter_peak=meter_peak,
                       stream_hearing=stream_hearing,
                       stream_note=stream_note, os_state=os_state)
    verdict.update({"endpoint": endpoint, "volume": volume,
                    "meter_peak": meter_peak,
                    "soundcard": soundcard_default_microphone(),
                    "os_microphone": os_microphone})
    return verdict
