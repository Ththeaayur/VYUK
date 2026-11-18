"""Module-level audio helpers, split out of core/voice/audio.py
(B32 part 1 of 2): chat-announcement hook, device resolution,
watchdog constants, system-audio probe. Pure functions plus the
hook state; AudioManager itself stays in the facade until B33."""

import sys

import sounddevice as sd




# The chat-announcement hook (fn(text) | None), registered by
# the GUI at startup — the watcher-event seam, the house road
# for proactive lines (the same pattern core/music_player
# uses). None in headless/test use: _announce then simply has
# nowhere to go and drops the line; the mic_status surfaces
# and the console log still carry it.
_announce_hook = None




def set_announce_hook(fn) -> None:
    global _announce_hook
    _announce_hook = fn




def _announce(text: str) -> None:
    """One proactive chat line, through the registered hook.
    Never raises — an announcement must not break audio."""
    hook = _announce_hook
    if hook is None:
        return
    try:
        hook(text)
    except Exception:  # noqa: BLE001
        pass




# The opens-but-silent explanation, in the DOCTOR's own words
# (doctor.check_microphone's WARN line) so the app and the
# doctor tell the user the same thing (v5.53, F6).
_MIC_SILENCE_EXPLANATION = (
    "on Windows this is usually Settings > Privacy > "
    "Microphone (let apps access your microphone), an "
    "OS-level mute on the device itself, or the wrong device"
)




def _device_fingerprint(devices) -> tuple:
    """The device SET as a tuple of names (v5.53, F6): the
    silence escalation may fall through to another device
    once per set — a changed set (a mic plugged in, one
    removed) is what re-arms it. Never raises."""
    try:
        return tuple(
            str(d.get("name", "")) for d in devices
            if isinstance(d, dict))
    except Exception:  # noqa: BLE001
        return ()




def resolve_input_device(devices, saved_index, saved_name,
                         default_index=None):
    """Resolve which input device to open (v5.37) — pure, no
    sounddevice calls, never raises.

    `devices` is a device enumeration in sounddevice order (dicts
    with at least "name" and "max_input_channels"); `saved_index` /
    `saved_name` are the user's stored pick (either may be None);
    `default_index` is the enumeration index of the system default
    input, when the caller knows it.

    Returns (index | None, name | None, note):
      * the saved pick, when its index still exists, is
        input-capable, and the name there matches the saved name
        (or no name was saved);
      * the saved name's new index, when Windows re-indexed the
        devices and the name now lives elsewhere (note says so);
      * the system default input, or the first input-capable
        device, when the pick is unresolvable (note says so when
        a pick had to be dropped);
      * (None, None, note) when nothing is input-capable at all.
    """
    try:
        devs = list(devices) if devices else []
    except Exception:  # noqa: BLE001 — a resolver must never raise
        devs = []

    def _capable(i) -> bool:
        try:
            return (
                isinstance(i, int) and not isinstance(i, bool)
                and 0 <= i < len(devs)
                and isinstance(devs[i], dict)
                and int(devs[i].get("max_input_channels", 0) or 0) > 0
            )
        except Exception:  # noqa: BLE001
            return False

    def _name_at(i):
        try:
            name = devs[i].get("name")
            return str(name) if name else None
        except Exception:  # noqa: BLE001
            return None

    try:
        wanted = str(saved_name).strip() if saved_name else ""
        have_pick = (
            isinstance(saved_index, int)
            and not isinstance(saved_index, bool)
        ) or bool(wanted)

        # 1. The saved pick is intact.
        if _capable(saved_index):
            actual = _name_at(saved_index)
            if not wanted or (actual or "") == wanted:
                return saved_index, actual, ""

        # 2. The name moved to another input-capable index.
        if wanted:
            for i in range(len(devs)):
                if _capable(i) and (_name_at(i) or "") == wanted:
                    return i, _name_at(i), (
                        f"microphone '{wanted}' moved to a new device "
                        f"index ({saved_index} -> {i}); using it there")

        # 3. Fall back: the system default input, then the first
        # input-capable device.
        dropped = (
            f"saved microphone (index {saved_index}"
            + (f", '{wanted}'" if wanted else "")
            + ") is not available"
        ) if have_pick else ""
        if _capable(default_index):
            note = (f"{dropped}; using the system default input "
                    f"'{_name_at(default_index)}'") if dropped else ""
            return default_index, _name_at(default_index), note
        for i in range(len(devs)):
            if _capable(i):
                note = (f"{dropped}; using '{_name_at(i)}' instead"
                        ) if dropped else ""
                return i, _name_at(i), note
        return None, None, "no input-capable audio device found"
    except Exception:  # noqa: BLE001 — a resolver must never raise
        return None, None, ""




def effective_input(devices, mode, saved_index, saved_name,
                    default_index=None):
    """The v5.52 (item 3) mode gate, pure: WHICH input opens,
    given the stored mode.

    The user's rule (2026-10-23 22:54): "laptop by laptop, the
    default microphone is always chosen as is in the computer."
    So AUTOMATIC is the standing behaviour — the input is the
    SYSTEM DEFAULT, resolved fresh at every stream start, and
    a stored pick does NOT outrank it. A pick governs only in
    MANUAL mode (a deliberate Settings selection), where the
    v5.37 rules apply (intact -> moved-name -> fallback).

    Returns (index | None, name | None, note, source) with
    source "automatic" or "manual". Never raises.
    """
    if mode == "manual":
        index, name, note = resolve_input_device(
            devices, saved_index, saved_name, default_index)
        return index, name, note, "manual"
    index, name, note = resolve_input_device(
        devices, None, None, default_index)
    if not note and index is not None:
        # v5.53 (F6): the note used to be DISCARDED in auto
        # mode, so when Automatic could not land on the PC's
        # default — the default names no input-capable device,
        # or could not be identified at all — and quietly
        # opened something else, nobody downstream ever knew.
        # Say so; start() surfaces it via mic_status() and a
        # one-time chat note.
        if default_index is None:
            note = (
                "no system default input could be identified; "
                f"using '{name}' instead")
        elif index != default_index:
            default_name = None
            try:
                if 0 <= default_index < len(devices):
                    default_name = devices[default_index].get("name")
            except Exception:  # noqa: BLE001
                default_name = None
            note = (
                f"this PC's default input "
                f"('{default_name or 'system default'}') is not "
                f"usable as a microphone; using '{name}' instead")
    return index, name, note, "automatic"




def system_default_input_index(devices, sd_module=None):
    """The enumeration index of the system default INPUT device
    (v5.37), or None — via PortAudio's default pair first, then by
    matching the name `query_devices(kind="input")` reports.
    Guarded throughout; never raises."""
    sd_mod = sd_module if sd_module is not None else sd
    try:
        devs = list(devices) if devices else []
    except Exception:  # noqa: BLE001
        devs = []

    def _capable(i) -> bool:
        try:
            return (
                isinstance(i, int) and not isinstance(i, bool)
                and 0 <= i < len(devs)
                and isinstance(devs[i], dict)
                and int(devs[i].get("max_input_channels", 0) or 0) > 0
            )
        except Exception:  # noqa: BLE001
            return False

    try:
        pair = sd_mod.default.device
        idx = int(pair[0])
        if _capable(idx):
            return idx
    except Exception:  # noqa: BLE001
        pass
    try:
        default_dev = sd_mod.query_devices(kind="input")
        name = default_dev.get("name") if isinstance(default_dev, dict) else None
        if name:
            for i in range(len(devs)):
                if _capable(i) and devs[i].get("name") == name:
                    return i
    except Exception:  # noqa: BLE001
        pass
    return None


def resolve_output_device(devices, saved_index, saved_name,
                          default_index=None):
    """Resolve which output device to open (field-check fix #12,
    M11a) — the output mirror of resolve_input_device: pure, no
    sounddevice calls, never raises.

    `devices` is a device enumeration in sounddevice order (dicts
    with at least "name" and "max_output_channels"); `saved_index` /
    `saved_name` are the user's stored pick (either may be None);
    `default_index` is the enumeration index of the system default
    output, when the caller knows it.

    Returns (index | None, name | None, note):
      * the saved pick, when its index still exists, is
        output-capable, and the name there matches the saved name
        (or no name was saved);
      * the saved name's new index, when Windows re-indexed the
        devices and the name now lives elsewhere (note says so);
      * the system default output, or the first output-capable
        device, when the pick is unresolvable (note says so when
        a pick had to be dropped);
      * (None, None, note) when no pick was stored at all — None
        is PortAudio's own default, which is what an unpicked
        output always meant; a stored pick that is gone falls
        back to the system default output instead, so a pick is
        never silently replaced by the OS default without a note.
    """
    try:
        devs = list(devices) if devices else []
    except Exception:  # noqa: BLE001 — a resolver must never raise
        devs = []

    def _capable(i) -> bool:
        try:
            return (
                isinstance(i, int) and not isinstance(i, bool)
                and 0 <= i < len(devs)
                and isinstance(devs[i], dict)
                and int(devs[i].get("max_output_channels", 0) or 0) > 0
            )
        except Exception:  # noqa: BLE001
            return False

    def _name_at(i):
        try:
            name = devs[i].get("name")
            return str(name) if name else None
        except Exception:  # noqa: BLE001
            return None

    try:
        wanted = str(saved_name).strip() if saved_name else ""
        have_pick = (
            isinstance(saved_index, int)
            and not isinstance(saved_index, bool)
        ) or bool(wanted)

        # 1. The saved pick is intact.
        if _capable(saved_index):
            actual = _name_at(saved_index)
            if not wanted or (actual or "") == wanted:
                return saved_index, actual, ""

        # 2. The name moved to another output-capable index.
        if wanted:
            for i in range(len(devs)):
                if _capable(i) and (_name_at(i) or "") == wanted:
                    return i, _name_at(i), (
                        f"speaker '{wanted}' moved to a new device "
                        f"index ({saved_index} -> {i}); using it there")

        # 3. No pick stored: PortAudio's own default (None).
        if not have_pick:
            return None, None, ""
        # A stored pick that is gone: the system default output,
        # then the first output-capable device.
        dropped = (
            f"saved speaker (index {saved_index}"
            + (f", '{wanted}'" if wanted else "")
            + ") is not available"
        )
        if _capable(default_index):
            return default_index, _name_at(default_index), (
                f"{dropped}; using the system default output "
                f"'{_name_at(default_index)}'")
        for i in range(len(devs)):
            if _capable(i):
                return i, _name_at(i), (
                    f"{dropped}; using '{_name_at(i)}' instead")
        return None, None, "no output-capable audio device found"
    except Exception:  # noqa: BLE001 — a resolver must never raise
        return None, None, ""


def system_default_output_index(devices, sd_module=None):
    """The enumeration index of the system default OUTPUT device
    (field-check fix #12, M11a), or None — the output mirror of
    system_default_input_index: via PortAudio's default pair
    first, then by matching the name `query_devices(kind="output")`
    reports. Guarded throughout; never raises."""
    sd_mod = sd_module if sd_module is not None else sd
    try:
        devs = list(devices) if devices else []
    except Exception:  # noqa: BLE001
        devs = []

    def _capable(i) -> bool:
        try:
            return (
                isinstance(i, int) and not isinstance(i, bool)
                and 0 <= i < len(devs)
                and isinstance(devs[i], dict)
                and int(devs[i].get("max_output_channels", 0) or 0) > 0
            )
        except Exception:  # noqa: BLE001
            return False

    try:
        pair = sd_mod.default.device
        idx = int(pair[1])
        if _capable(idx):
            return idx
    except Exception:  # noqa: BLE001
        pass
    try:
        default_dev = sd_mod.query_devices(kind="output")
        name = default_dev.get("name") if isinstance(default_dev, dict) else None
        if name:
            for i in range(len(devs)):
                if _capable(i) and devs[i].get("name") == name:
                    return i
    except Exception:  # noqa: BLE001
        pass
    return None




# v5.95 (micfix): input-callback liveness bounds for the
# stream watchdog below. A stream whose callback stopped
# firing is dead in fact — no frames, no voice, no error —
# and the 6.5 s silence guard only fires once per start, so
# without this nothing ever reopens it.
_MIC_CALLBACK_STALL_S = 5.0

# Mic remake (v6.3.x): throttle for the watchdog's open
# retry — a failed open is retried no more often than once
# per 5 s while the session is live.
_MIC_OPEN_RETRY_S = 5.0


_MIC_WATCHDOG_TICK_S = 2.0




def system_audio_active(threshold: float = 0.01) -> bool | None:
    """Is the PC's default output device producing sound right now?

    v5.17 (Ears A18). Windows-only: reads the Core Audio peak meter
    (IMMDeviceEnumerator → IAudioMeterInformation) via ctypes. Any
    other platform, and any failure along the way, returns None —
    "unknown" — and callers must treat None as "no information",
    never as silence. The peak is an instantaneous sample, so a
    quiet passage in a film can read False; the wake gate using
    this treats a single False as permission, which is the honest
    bias (a missed pause is a nuisance; a blocked wake word is a
    dead assistant)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import POINTER, byref, c_float, c_long, c_void_p

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

            def __init__(self, text: str):
                import uuid
                u = uuid.UUID(text)
                super().__init__(u.time_low, u.time_mid, u.time_hi_version,
                                 (ctypes.c_ubyte * 8)(*u.bytes[8:]))

        ole32 = ctypes.windll.ole32
        # COINIT_APARTMENTTHREADED; RPC_E_CHANGED_MODE (-2147417850)
        # just means the apartment already exists — equally usable.
        hr = ole32.CoInitializeEx(None, 0x2)
        if hr not in (0, 1, -2147417850):
            return None
        enumerator = c_void_p()
        hr = ole32.CoCreateInstance(
            byref(GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")), None, 0x17,
            byref(GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")),
            byref(enumerator))
        if hr != 0 or not enumerator:
            return None

        def _call(obj, index, restype, *argtypes_and_values):
            vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
            fn = ctypes.WINFUNCTYPE(restype, c_void_p, *[
                t for t, _ in argtypes_and_values])(
                vtbl[index])
            return fn(obj, *[v for _, v in argtypes_and_values])

        device = c_void_p()
        # IMMDeviceEnumerator::GetDefaultAudioEndpoint (vtable 4):
        # eRender = 0, eConsole = 0.
        hr = _call(enumerator, 4, c_long,
                   (ctypes.c_int, 0), (ctypes.c_int, 0),
                   (POINTER(c_void_p), byref(device)))
        if hr != 0 or not device:
            return None
        meter = c_void_p()
        iid_meter = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
        # IMMDevice::Activate (vtable 3).
        hr = _call(device, 3, c_long,
                   (POINTER(GUID), byref(iid_meter)),
                   (ctypes.c_ulong, 0x17), (c_void_p, None),
                   (POINTER(c_void_p), byref(meter)))
        if hr != 0 or not meter:
            return None
        peak = c_float(0.0)
        # IAudioMeterInformation::GetPeakValue (vtable 3).
        hr = _call(meter, 3, c_long, (POINTER(c_float), byref(peak)))
        if hr != 0:
            return None
        return bool(peak.value > threshold)
    except Exception:  # noqa: BLE001 — a probe must never break audio
        return None
