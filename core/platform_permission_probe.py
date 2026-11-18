"""Platform (Windows) permission probes — PF-18 B3.

Reads the Windows mic/camera privacy state so the Permissions
page can show the OS side next to the VYUK-side grant, and tell
"Windows has the mic off" apart from "VYUK has it off" apart
from "no microphone is usable" (the mic arc: Doctor -9996,
'Input ()' would not open).

READ PATH (chosen from evidence; the field leg — verifying
these exact values against Settings on his Windows machine —
is still owed):
  HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\
  CapabilityAccessManager\\ConsentStore\\<microphone|webcam>
    "Value" (REG_SZ "Allow"/"Deny") — the master switch,
    "Let apps access your microphone/camera".
    ...\\<same>\\NonPackaged
    "Value" (REG_SZ "Allow"/"Deny") — the "Let desktop apps
    access" toggle. VYUK is a desktop (non-packaged) app, so
    THIS is the toggle that gates VYUK. It is shared across
    ALL desktop apps — Windows offers no per-app switch for
    desktop apps on shipping builds, so the probe never claims
    per-app attribution: it reports what Windows allows desktop
    apps, and says so. (Windows 11 Insider builds have begun
    adding per-desktop-app controls; until one ships, the
    shared toggle is the whole story.)

REJECTED alternatives (do not re-open without field evidence):
  - WinRT AppCapabilityAccess (AppCapability.Create("microphone")
    .CheckAccess()): the documented API, but it needs the
    `winrt`/`winsdk` PyPI package — a new dependency (licence
    check owed, not in requirements, not vendored) that cannot
    be exercised in the Linux lab, and for a desktop app it
    returns the same shared-toggle state the registry holds.
    If the field leg shows the registry values disagree with
    Settings on his machine, this is the documented fallback.
  - HKLM instead of HKCU: machine-wide values exist, but the
    per-user HKCU store governs the current user's session;
    HKLM may not exist and needs admin to read reliably.
  - ctypes RegOpenKeyEx: winreg is the stdlib wrapper and
    already the in-tree pattern (theme_sync, wallpaper,
    watchers_file_privacy); no reason to drop to ctypes.
  - Writing anything / OS revocation: D2 §6 — no supported API
    lets an app revoke its own OS permission; the page links to
    Windows privacy settings instead (jump link, user-pressed).

HONESTY RULES (the loophole family, closed here):
  - VYUK-side vs OS-side are separate fields, never merged.
  - A probe failure (non-Windows, missing winreg, missing key,
    unexpected value, any exception) reads "unknown", never a
    guess — and never "denied" (a failed probe must not look
    like Windows blocking the mic).
  - Read-only: only OpenKey(KEY_READ) + QueryValueEx. No
    SetValueEx / CreateKeyEx / DeleteValue anywhere in this
    module; the test suite proves that by source scan.
  - Never raises: every public function returns unknown on any
    failure.
  - The user's stored denial always wins over the OS report
    (D2 §5.4): the diagnosis says "denied by you" when VYUK
    is off, whatever Windows says.

Note what the ConsentStore ALREADY had in the tree: the "in
use" flags (LastUsedTimeStart/Stop) are read by
core/watchers_file_privacy.py (_probe_privacy) and the
last-used listing in core/skills_system_tuning.py. Nobody read
the "Value" Allow/Deny state per capability or the NonPackaged
desktop-apps toggle — that is the gap this module closes.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

ALLOWED = "allowed"
DENIED = "denied"
UNKNOWN = "unknown"

_CONSENT_ROOT = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                 r"\CapabilityAccessManager\ConsentStore")
_CAPABILITY_KEYS = {"microphone": "microphone", "camera": "webcam"}

_SHARED_TOGGLE_NOTE = (
    "This Windows switch is shared by every desktop app — "
    "it is not VYUK-specific, and Windows cannot say whether "
    "VYUK in particular is allowed.")

# The D2 §5.4 copy, verbatim: the page must say this wherever
# the VYUK switch and the Windows switch sit side by side.
D2_OS_COPY = (
    "Switching this off stops VYUK using it. To change what "
    "Windows itself allows, open Windows privacy settings.")


@dataclass
class PlatformPermissionState:
    """One capability's Windows-side privacy state.

    ``state`` is the effective answer for a desktop app:
    "allowed" (master on, desktop-apps toggle on or default),
    "denied" (master off, or desktop-apps toggle off), or
    "unknown" (could not be read — never a guess).
    ``master`` / ``desktop_apps`` are the two switches as read;
    ``per_app`` is always False — Windows cannot attribute
    desktop-app consent to VYUK specifically.
    """

    permission_id: str
    state: str = UNKNOWN
    master: str = UNKNOWN
    desktop_apps: str = UNKNOWN
    per_app: bool = False
    reason: str = ""
    source: str = "unavailable"

    @property
    def shared_note(self):
        return _SHARED_TOGGLE_NOTE


def _normalise(value):
    """A ConsentStore "Value" string → "allow" / "deny" /
    "unknown". Anything that is not exactly Allow/Deny (after
    stripping) is unknown — never upgraded to an answer."""
    if not isinstance(value, str):
        return UNKNOWN
    lowered = value.strip().lower()
    if lowered == "allow":
        return "allow"
    if lowered == "deny":
        return "deny"
    return UNKNOWN


def _read_value(registry, key_path, name):
    """One REG_SZ read, or None on any failure. KEY_READ only —
    this module never opens a key for writing."""
    try:
        key = registry.OpenKey(registry.HKEY_CURRENT_USER,
                               key_path, 0, registry.KEY_READ)
    except Exception:  # noqa: BLE001 — missing key reads as no value
        return None
    try:
        with key:
            value, _kind = registry.QueryValueEx(key, name)
            return value
    except Exception:  # noqa: BLE001 — missing value reads as no value
        return None


def _probe_one(permission_id, registry):
    """One capability's state from an injected winreg-shaped
    registry (the real winreg module, or a test fake with
    HKEY_CURRENT_USER / KEY_READ / OpenKey / QueryValueEx)."""
    cap_key = _CAPABILITY_KEYS[permission_id]
    base = _CONSENT_ROOT + "\\" + cap_key
    master = _normalise(_read_value(registry, base, "Value"))
    desktop = _normalise(
        _read_value(registry, base + "\\NonPackaged", "Value"))
    if master == "deny":
        # Master off greys out the desktop toggle in Settings:
        # nothing can use the device.
        return PlatformPermissionState(
            permission_id, DENIED, master, desktop,
            reason=("Windows has the %s off for all apps."
                    % permission_id),
            source="windows-consent-store")
    if master == "allow" and desktop == "deny":
        return PlatformPermissionState(
            permission_id, DENIED, master, desktop,
            reason=("Windows has the %s off for desktop apps. "
                    % permission_id + _SHARED_TOGGLE_NOTE),
            source="windows-consent-store")
    if master == "allow":
        # No stored desktop value means never flipped: the
        # Windows default is allowed. Record the effective
        # value, and say where it came from.
        effective = desktop if desktop in ("allow", "deny") \
            else "allow"
        reason = ("Windows allows desktop apps to use the %s."
                  % permission_id)
        if desktop == UNKNOWN:
            reason += (" (No stored desktop-apps value — "
                       "Windows default is allowed.)")
        return PlatformPermissionState(
            permission_id, ALLOWED, master, effective,
            reason=reason, source="windows-consent-store")
    # master unreadable / unexpected: unknown, never a guess.
    return PlatformPermissionState(
        permission_id, UNKNOWN, master, desktop,
        reason=("Windows %s privacy state could not be read — "
                "unknown, not a denial." % permission_id),
        source="windows-consent-store")


def probe_platform_permissions(registry=None):
    """{"microphone": PlatformPermissionState, "camera": ...}.

    ``registry`` is an injected winreg-shaped module (tests);
    None means the real winreg on Windows. Off Windows, or on
    any failure, both ids read "unknown". Never raises."""
    try:
        if registry is None:
            if sys.platform != "win32":
                return {
                    pid: PlatformPermissionState(
                        pid, UNKNOWN, UNKNOWN, UNKNOWN,
                        reason=("Not Windows — no Windows privacy "
                                "state to read."),
                        source="unavailable: not Windows")
                    for pid in ("microphone", "camera")}
            try:
                import winreg as _winreg
            except Exception:  # noqa: BLE001 — no registry API
                return {
                    pid: PlatformPermissionState(
                        pid, UNKNOWN, UNKNOWN, UNKNOWN,
                        reason="Windows registry API unavailable.",
                        source="unavailable: winreg import failed")
                    for pid in ("microphone", "camera")}
            registry = _winreg
        return {pid: _probe_one(pid, registry)
                for pid in ("microphone", "camera")}
    except Exception:  # noqa: BLE001 — a probe never takes the page down
        return {
            pid: PlatformPermissionState(
                pid, UNKNOWN, UNKNOWN, UNKNOWN,
                reason="Platform probe failed — unknown, not a denial.",
                source="unavailable: probe failed")
            for pid in ("microphone", "camera")}


def probe_microphone_device(device_reader=None):
    """{"device": "present"|"absent"|"unknown", "detail": str}.

    The default reader is core.mmdevice's default capture
    endpoint — the same endpoint Windows Sound settings calls
    the default microphone. An ACTIVE default reads "present";
    a default that exists but is disabled / not present /
    unplugged reads "absent" (a device is configured but not
    usable — the -9996 shape). No default configured at all
    reads "absent" too (nothing usable is set — the fix is to
    pick one in Settings), while a failed enumeration reads
    "unknown" — the two are separated by
    default_capture_endpoint_detail()'s E_NOTFOUND signal
    (voice-control batch, 2026-11-16, §10 P2); before it, both
    collapsed to "unknown" and the doctor could not tell them
    apart. Off Windows or on any failure: "unknown". Never
    raises.
    ``device_reader`` is an injected callable returning a
    {"state": int, "state_name": str} dict (or None) — tests.
    A None from the injected reader still reads "unknown":
    the seam carries no E_NOTFOUND signal, so the probe never
    guesses there either.
    """
    try:
        if device_reader is None:
            try:
                from . import mmdevice as _mm
            except Exception:  # noqa: BLE001 — no MMDevice layer
                return {"device": UNKNOWN,
                        "detail": "No device reader available."}
            # Voice-control batch (2026-11-16, §10 P2): separate
            # "no default configured" from "enumeration failed"
            # via default_capture_endpoint_detail()'s E_NOTFOUND
            # signal — different fixes, so they must not share
            # one "unknown". The fallback branch (no detail fn)
            # keeps the old collapsed reading for exotic mmdevice
            # shims; the injected-reader branch below keeps it
            # too, because the seam carries no such signal.
            _detail_fn = getattr(
                _mm, "default_capture_endpoint_detail", None)
            if callable(_detail_fn):
                try:
                    endpoint, _why = _detail_fn()
                except Exception:  # noqa: BLE001
                    return {"device": UNKNOWN,
                            "detail": "Microphone enumeration failed."}
                if _why == "no_default":
                    return {"device": "absent",
                            "detail": ("No default microphone is "
                                       "configured in Windows — "
                                       "nothing is set as the default "
                                       "input. Fix: Settings > Sound "
                                       "> Input — pick a microphone.")}
                if _why in ("enumeration_failed", "unavailable"):
                    return {"device": UNKNOWN,
                            "detail": ("Microphone enumeration failed."
                                       if _why == "enumeration_failed"
                                       else "No device reader available.")}
                if endpoint is None:
                    return {"device": UNKNOWN,
                            "detail": "Microphone enumeration failed."}
            else:
                try:
                    endpoint = _mm.default_capture_endpoint()
                except Exception:  # noqa: BLE001 — enumeration failed
                    return {"device": UNKNOWN,
                            "detail": "Microphone enumeration failed."}
                if endpoint is None:
                    return {"device": UNKNOWN,
                            "detail": ("No default microphone returned — "
                                       "could be no device, or enumeration "
                                       "failing; never guessed as absent.")}
            info = _mm.endpoint_state_and_id(endpoint)
            if not info:
                return {"device": UNKNOWN,
                        "detail": "Default microphone state unreadable."}
            return _device_from_info(info)
        info = device_reader()
        if not info:
            return {"device": UNKNOWN,
                    "detail": ("No default microphone returned — "
                               "could be no device, or enumeration "
                               "failing; never guessed as absent.")}
        return _device_from_info(info)
    except Exception:  # noqa: BLE001 — a probe never takes the page down
        return {"device": UNKNOWN,
                "detail": "Microphone device probe failed."}


def _device_from_info(info):
    """A mmdevice endpoint_state_and_id-shaped dict → the
    device reading. ACTIVE is present; DISABLED / NOT_PRESENT /
    UNPLUGGED is absent (configured but not usable)."""
    try:
        state = int(info.get("state"))
    except (TypeError, ValueError):
        return {"device": UNKNOWN,
                "detail": "Default microphone state unreadable."}
    name = str(info.get("state_name") or "unknown")
    if state == 0x00000001:  # DEVICE_STATE_ACTIVE
        return {"device": "present",
                "detail": "Default microphone is active."}
    return {"device": "absent",
            "detail": ("Default microphone is %s." % name)}


def diagnose_microphone(vyuk_state, os_state, device):
    """The mic-arc three-way distinction, as (headline, fix).

    ``vyuk_state``: "granted"/"denied"/"unknown" from the VYUK
    grants store. ``os_state``: a PlatformPermissionState (or a
    dict with "state"/"master"/"desktop_apps"). ``device``: a
    probe_microphone_device-shaped dict (or a plain
    "present"/"absent"/"unknown" string). The user's stored
    denial always wins over the OS report (D2 §5.4): "denied by
    you" is the headline whenever VYUK is off, whatever
    Windows says.
    """
    vyuk = vyuk_state if vyuk_state in ("granted", "denied") \
        else UNKNOWN
    if isinstance(os_state, dict):
        os_s = os_state.get("state", UNKNOWN)
        master = os_state.get("master", UNKNOWN)
    else:
        os_s = getattr(os_state, "state", UNKNOWN)
        master = getattr(os_state, "master", UNKNOWN)
    if isinstance(device, dict):
        dev = device.get("device", UNKNOWN)
        detail = device.get("detail", "")
    else:
        dev = device if device in ("present", "absent") else UNKNOWN
        detail = ""
    if vyuk == "denied":
        if os_s == DENIED:
            return (
                "Off — you switched VYUK's microphone off, and "
                "Windows has it off too.",
                "Flip VYUK's microphone switch on, then open "
                "Windows privacy settings → Microphone and turn "
                "the Windows switch on.")
        if os_s == UNKNOWN:
            return (
                "Off — you switched VYUK's microphone off.",
                "Flip the microphone switch on in Settings. "
                "Windows' state couldn't be read, so check it in "
                "Windows privacy settings if it still doesn't work.")
        return (
            "Off — you switched VYUK's microphone off.",
            "Flip the microphone switch on in Settings. Windows "
            "allows it, so VYUK's switch is the only thing off.")
    if os_s == DENIED:
        if master == "deny":
            return (
                "Windows has the microphone off for all apps.",
                "Open Windows privacy settings → Microphone → "
                "turn on 'Let apps access your microphone'.")
        return (
            "Windows has the microphone off for desktop apps.",
            "Open Windows privacy settings → Microphone → turn "
            "on 'Let desktop apps access your microphone'. That "
            "switch is shared by every desktop app, not just VYUK.")
    if os_s == UNKNOWN:
        return (
            "Windows' microphone state couldn't be read — "
            "unknown, not a denial.",
            "Open Windows privacy settings → Microphone and "
            "check the switches there.")
    if dev == "absent":
        detail_text = (detail or "not usable").strip()
        if detail_text.startswith("Default microphone is "):
            detail_text = detail_text[
                len("Default microphone is "):].rstrip(".")
        return (
            "Windows allows the microphone, but no microphone "
            "is usable.",
            "The default microphone is %s — plug it in or "
            "enable it in Windows Sound settings. VYUK and "
            "Windows both allow it; there is just no working "
            "device (the -9996 case)."
            % detail_text)
    if dev == "present":
        return (
            "Microphone should work — VYUK on, Windows on, "
            "device present.",
            "If it still fails, the cause is elsewhere (a "
            "driver, another app holding the mic, exclusive "
            "mode).")
    return (
        "VYUK and Windows both allow the microphone; couldn't "
        "tell whether a microphone is connected.",
        "Open Windows Sound settings and check a microphone is "
        "connected and enabled.")
