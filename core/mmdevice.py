"""Minimal Windows MMDevice plumbing (v5.54, Wave 0).

Her OWN thin layer over the Windows multimedia device API
(MMDevice), written on comtypes — the COM plumbing
core/voice/mic_doctor.py (endpoint state / volume / meter probes) and
core/device_watch.py (default-device change notifications) are
built on. It exists so the microphone diagnosis reads the SAME
endpoint Windows itself reports (the console-role capture
default) instead of trusting any one audio backend's view.

Discipline (INV-12): comtypes is imported lazily, inside the
calls, and every public function returns None / False off
Windows or on any COM failure — a probe must never take the app
down. Nothing in this module is imported by the audio hot path.

The interface definitions below are the standard MMDevice
shapes (the same vtbl layouts the SDK publishes); only the
methods VYUK calls carry full signatures — unused slots keep
their position with an empty signature, which is all the vtbl
arithmetic needs.
"""
from __future__ import annotations

import sys

CLSCTX_ALL = 23
COINIT_MULTITHREADED = 0x0

# EDataFlow
E_RENDER = 0
E_CAPTURE = 1
# ERole
E_CONSOLE = 0
E_MULTIMEDIA = 1
E_COMMUNICATIONS = 2

# DEVICE_STATE_* (mmdeviceapi.h)
DEVICE_STATE_ACTIVE = 0x00000001
DEVICE_STATE_DISABLED = 0x00000002
DEVICE_STATE_NOTPRESENT = 0x00000004
DEVICE_STATE_UNPLUGGED = 0x00000008

_STATE_NAMES = {
    DEVICE_STATE_ACTIVE: "active",
    DEVICE_STATE_DISABLED: "disabled",
    DEVICE_STATE_NOTPRESENT: "not present",
    DEVICE_STATE_UNPLUGGED: "unplugged",
}

_cache: dict = {}


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def state_name(state: int | None) -> str:
    if state is None:
        return "unknown"
    return _STATE_NAMES.get(int(state), f"state {state}")


def _interfaces() -> dict | None:
    """The comtypes interface definitions, built once per
    process. None when comtypes is unavailable (non-Windows
    installs never reach here — callers check _is_windows
    first)."""
    if "ifaces" in _cache:
        return _cache["ifaces"]
    try:
        import ctypes

        from comtypes import GUID, HRESULT, IUnknown, STDMETHOD

        c_float = ctypes.c_float
        c_int = ctypes.c_int
        c_uint = ctypes.c_uint
        c_wchar_p = ctypes.c_wchar_p
        c_void_p = ctypes.c_void_p
        POINTER = ctypes.POINTER

        class PROPERTYKEY(ctypes.Structure):
            _fields_ = [("fmtid", GUID), ("pid", c_uint)]

        class IMMNotificationClient(IUnknown):
            _iid_ = GUID("{7991EEC9-7E89-4D85-8390-6C703CEC60C0}")
            _methods_ = [
                STDMETHOD(HRESULT, "OnDeviceStateChanged",
                          [c_wchar_p, c_uint]),
                STDMETHOD(HRESULT, "OnDeviceAdded", [c_wchar_p]),
                STDMETHOD(HRESULT, "OnDeviceRemoved", [c_wchar_p]),
                STDMETHOD(HRESULT, "OnDefaultDeviceChanged",
                          [c_int, c_int, c_wchar_p]),
                STDMETHOD(HRESULT, "OnPropertyValueChanged",
                          [c_wchar_p, PROPERTYKEY]),
            ]

        class IMMDevice(IUnknown):
            _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
            _methods_ = [
                STDMETHOD(HRESULT, "Activate",
                          [POINTER(GUID), c_uint, c_void_p,
                           POINTER(POINTER(IUnknown))]),
                STDMETHOD(HRESULT, "OpenPropertyStore",
                          [c_uint, c_void_p]),
                STDMETHOD(HRESULT, "GetId", [POINTER(c_wchar_p)]),
                STDMETHOD(HRESULT, "GetState", [POINTER(c_uint)]),
            ]

        class IMMDeviceEnumerator(IUnknown):
            _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
            _methods_ = [
                STDMETHOD(HRESULT, "EnumAudioEndpoints",
                          [c_int, c_uint, c_void_p]),
                STDMETHOD(HRESULT, "GetDefaultAudioEndpoint",
                          [c_int, c_int, POINTER(POINTER(IMMDevice))]),
                STDMETHOD(HRESULT, "GetDevice",
                          [c_wchar_p, POINTER(POINTER(IMMDevice))]),
                STDMETHOD(HRESULT, "RegisterEndpointNotificationCallback",
                          [POINTER(IMMNotificationClient)]),
                STDMETHOD(HRESULT,
                          "UnregisterEndpointNotificationCallback",
                          [POINTER(IMMNotificationClient)]),
            ]

        # IAudioEndpointVolume vtbl order (mmdeviceapi /
        # endpointvolume.h): only GetMasterVolumeLevelScalar (6)
        # and GetMute (12) are called; earlier/later slots keep
        # their positions with empty signatures.
        class IAudioEndpointVolume(IUnknown):
            _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
            _methods_ = [
                STDMETHOD(HRESULT, "RegisterControlChangeNotify", []),
                STDMETHOD(HRESULT, "UnregisterControlChangeNotify", []),
                STDMETHOD(HRESULT, "GetChannelCount", []),
                STDMETHOD(HRESULT, "SetMasterVolumeLevel", []),
                STDMETHOD(HRESULT, "SetMasterVolumeLevelScalar", []),
                STDMETHOD(HRESULT, "GetMasterVolumeLevel", []),
                STDMETHOD(HRESULT, "GetMasterVolumeLevelScalar",
                          [POINTER(c_float)]),
                STDMETHOD(HRESULT, "SetChannelVolumeLevel", []),
                STDMETHOD(HRESULT, "SetChannelVolumeLevelScalar", []),
                STDMETHOD(HRESULT, "GetChannelVolumeLevel", []),
                STDMETHOD(HRESULT, "GetChannelVolumeLevelScalar", []),
                STDMETHOD(HRESULT, "SetMute", []),
                STDMETHOD(HRESULT, "GetMute", [POINTER(c_int)]),
            ]

        class IAudioMeterInformation(IUnknown):
            _iid_ = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
            _methods_ = [
                STDMETHOD(HRESULT, "GetPeakValue", [POINTER(c_float)]),
                STDMETHOD(HRESULT, "GetMeteringChannelCount", []),
                STDMETHOD(HRESULT, "GetChannelsPeakValues", []),
                STDMETHOD(HRESULT, "QueryHardwareSupport", []),
            ]

        ifaces = {
            "IMMNotificationClient": IMMNotificationClient,
            "IMMDevice": IMMDevice,
            "IMMDeviceEnumerator": IMMDeviceEnumerator,
            "IAudioEndpointVolume": IAudioEndpointVolume,
            "IAudioMeterInformation": IAudioMeterInformation,
            "PROPERTYKEY": PROPERTYKEY,
            "enumerator_clsid": GUID(
                "{BCDE0395-E52F-467C-8E3D-C4579291692E}"),
        }
    except Exception:  # noqa: BLE001 — no comtypes, no probes
        ifaces = None
    _cache["ifaces"] = ifaces
    return ifaces


def _co_initialize() -> None:
    """Best-effort COM apartment init for the calling thread
    (S_OK / S_FALSE / already-initialised are all fine)."""
    try:
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    except Exception:  # noqa: BLE001
        pass


def create_enumerator():
    """A live IMMDeviceEnumerator, or None. The caller owns the
    reference; on an MTA worker thread (device_watch) COM must
    be initialised there first — done inside."""
    if not _is_windows():
        return None
    ifaces = _interfaces()
    if ifaces is None:
        return None
    try:
        from comtypes.client import CreateObject

        _co_initialize()
        return CreateObject(ifaces["enumerator_clsid"],
                            interface=ifaces["IMMDeviceEnumerator"])
    except Exception:  # noqa: BLE001
        return None


def default_capture_endpoint(enumerator=None, role: int = E_CONSOLE):
    """The console-role default CAPTURE device (IMMDevice), or
    None. This is the endpoint Windows Sound settings calls the
    default microphone — the one her PortAudio stream resolves
    to as well."""
    if not _is_windows():
        return None
    try:
        enum = enumerator or create_enumerator()
        if enum is None:
            return None
        return enum.GetDefaultAudioEndpoint(E_CAPTURE, role)
    except Exception:  # noqa: BLE001
        return None


def default_capture_endpoint_detail(enumerator=None,
                                    role: int = E_CONSOLE):
    """(endpoint_or_None, detail) for the console-role default
    CAPTURE device. detail is "ok" (an endpoint was returned),
    "no_default" (Windows reports E_NOTFOUND — no default
    microphone is configured at all), "enumeration_failed"
    (the enumerator or the query itself failed), or
    "unavailable" (not Windows / no MMDevice layer).
    Voice-control batch (2026-11-16, §10 P2):
    default_capture_endpoint() collapses all four to None, so
    the permission probe could not tell "no microphone
    configured" from "enumeration broken" — different fixes
    (pick a default in Settings vs repair the audio service /
    driver). This function separates them; the older function
    keeps its contract for its other callers. An hresult that
    cannot be read degrades to "enumeration_failed", never to
    a guessed "no_default"."""
    if not _is_windows():
        return None, "unavailable"
    try:
        enum = enumerator or create_enumerator()
        if enum is None:
            return None, "enumeration_failed"
        return enum.GetDefaultAudioEndpoint(E_CAPTURE, role), "ok"
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "hresult", None) == 0x80070490:  # E_NOTFOUND
            return None, "no_default"
        return None, "enumeration_failed"


def endpoint_state_and_id(device) -> dict | None:
    """{"id": str, "state": int, "state_name": str} for an
    IMMDevice, or None."""
    if device is None:
        return None
    try:
        state = device.GetState()
        dev_id = device.GetId()
        return {"id": str(dev_id) if dev_id else "",
                "state": int(state),
                "state_name": state_name(int(state))}
    except Exception:  # noqa: BLE001
        return None


def _activate(device, iface):
    if device is None:
        return None
    try:
        import ctypes

        from comtypes import GUID

        raw = device.Activate(GUID(str(iface._iid_)), CLSCTX_ALL, None)
        return ctypes.cast(raw, ctypes.POINTER(iface))
    except Exception:  # noqa: BLE001
        return None


def endpoint_volume(device) -> dict | None:
    """{"mute": bool, "level": 0..1} from IAudioEndpointVolume,
    or None."""
    ifaces = _interfaces()
    if ifaces is None or device is None:
        return None
    vol = _activate(device, ifaces["IAudioEndpointVolume"])
    if vol is None:
        return None
    try:
        level = vol.GetMasterVolumeLevelScalar()
        mute = vol.GetMute()
        return {"mute": bool(mute), "level": float(level)}
    except Exception:  # noqa: BLE001
        return None


def endpoint_meter(device):
    """A live IAudioMeterInformation for the device, or None.
    The caller polls GetPeakValue on it."""
    ifaces = _interfaces()
    if ifaces is None or device is None:
        return None
    return _activate(device, ifaces["IAudioMeterInformation"])


def meter_peak(meter) -> float | None:
    if meter is None:
        return None
    try:
        return float(meter.GetPeakValue())
    except Exception:  # noqa: BLE001
        return None
