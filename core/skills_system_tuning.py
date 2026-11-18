"""
System skills IV for VYUK (v5.11, Pack M) — microphone control,
per-process CPU priority, and a privacy look at which apps last
used the camera/microphone/location sensors.

Same rules as the other packs: plain functions returning
human-readable strings; dependencies (pycaw, psutil, winreg) are
reached lazily and guarded, so this module imports cleanly on any
OS and a missing piece degrades to an honest line instead of a
crash. Changes are verified by reading the state back (the v5.9
standard): mic_control re-reads the mute state after setting it,
set_process_priority re-reads the priority from a fresh process
object, and a mismatch is reported, never smoothed over.
set_process_priority refuses to guess among same-named processes —
several matches means a refusal with the PID list.

Wiring: the parent (core/tools_handlers_pack_hands.py, _register_hands4_tools) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import sys
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return sys.platform.startswith("win")


# ---------------------------------------------------------------------------
# MICROPHONE — mute state and input level of the default mic (pycaw)
# ---------------------------------------------------------------------------


def _pycaw_mic_volume():
    """The pycaw IAudioEndpointVolume for the default microphone.

    Mirrors control_plus._pycaw_volume (which covers the speakers):
    GetMicrophone() is the capture-side twin of GetSpeakers() — the
    default capture endpoint (eCapture, console role). Raises
    ImportError when pycaw/comtypes are absent; kept as its own seam
    so the handler can be tested without the COM stack."""
    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    devices = AudioUtilities.GetMicrophone()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def _mic_state(volume) -> tuple[bool, int]:
    """(muted, input level %) read live from the endpoint."""
    muted = bool(volume.GetMute())
    level = round(float(volume.GetMasterVolumeLevelScalar()) * 100)
    return muted, level


def mic_control(action: str = "status", level=None) -> str:
    """Read or change the default microphone's mute state, or
    (action='set_level' with the level parameter) set its input
    level to an exact percentage, confirmed by reading it back."""
    act = (action or "status").strip().lower()
    if act not in ("status", "mute", "unmute", "toggle", "set_level"):
        return (f"Unknown microphone action: '{action}'. "
                "Use one of: status, mute, unmute, toggle, set_level.")
    if not _is_windows():
        return ("Microphone control needs Windows audio endpoints "
                "(pycaw) — this system is not Windows, so the "
                "microphone was not changed.")
    try:
        volume = _pycaw_mic_volume()
    except ImportError:
        return ("Couldn't control the microphone — the pycaw package "
                "isn't available in this VYUK install.")
    except Exception as e:  # noqa: BLE001
        return f"Couldn't reach the microphone: {e}"
    wanted_level = level  # the set_level parameter; the state read below reuses the name
    try:
        muted, level = _mic_state(volume)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the microphone's state: {e}"
    if act == "status":
        state = "muted" if muted else "live (not muted)"
        return f"Microphone is {state}; input level is {level}%."
    if act == "set_level":
        if wanted_level is None:
            return ("Setting the input level needs a level — pass "
                    "level as a percentage from 0 to 100.")
        try:
            wanted = int(wanted_level)
        except (TypeError, ValueError):
            return (f"That input level isn't a number: "
                    f"'{wanted_level}'. Give a percentage from 0 to 100.")
        clamped = max(0, min(100, wanted))
        try:
            volume.SetMasterVolumeLevelScalar(clamped / 100.0, None)
        except Exception as e:  # noqa: BLE001
            return f"Couldn't set the microphone's input level: {e}"
        try:
            _now_muted, now_level = _mic_state(volume)
        except Exception:  # noqa: BLE001 — the set landed; the read-back didn't
            return (f"Told the microphone to set its input level to "
                    f"{clamped}%, but couldn't read its level back to "
                    "confirm the change took.")
        verdict = (f"Microphone input level: {level}% → {now_level}% "
                   "(confirmed by reading it back).")
        if clamped != wanted:
            verdict += (f" The requested {wanted}% was clamped to "
                        f"{clamped}% — levels run 0-100.")
        return verdict
    target = {"mute": True, "unmute": False}.get(act)
    if target is None:  # toggle
        target = not muted
    if target == muted:
        state = "muted" if muted else "live"
        return f"Microphone is already {state}; input level is {level}%."
    try:
        volume.SetMute(target, None)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't change the microphone's mute state: {e}"
    try:
        now_muted, now_level = _mic_state(volume)
    except Exception:  # noqa: BLE001 — the set landed; the read-back didn't
        said = "mute" if target else "unmute"
        return (f"Told the microphone to {said}, but couldn't read "
                "its state back to confirm the change took.")
    if now_muted != target:
        actual = "muted" if now_muted else "unmuted"
        return (f"Tried to {'mute' if target else 'unmute'} the "
                f"microphone, but it still reads as {actual} — the "
                "change didn't take.")
    if now_muted:
        return f"Microphone muted (confirmed). Input level is {now_level}%."
    return (f"Microphone unmuted — it's live now (confirmed). "
            f"Input level is {now_level}%.")


# ---------------------------------------------------------------------------
# PROCESS PRIORITY — how hard Windows works for one process (psutil)
# ---------------------------------------------------------------------------

_PRIORITY_LABELS = ("idle", "below_normal", "normal", "above_normal", "high")

# psutil exposes the Windows priority-class constants by these names;
# the integers are the POSIX nice fallbacks for other platforms.
_PRIORITY_CONSTANTS = {
    "idle": "IDLE_PRIORITY_CLASS",
    "below_normal": "BELOW_NORMAL_PRIORITY_CLASS",
    "normal": "NORMAL_PRIORITY_CLASS",
    "above_normal": "ABOVE_NORMAL_PRIORITY_CLASS",
    "high": "HIGH_PRIORITY_CLASS",
}
_PRIORITY_POSIX = {
    "idle": 19, "below_normal": 10, "normal": 0,
    "above_normal": -10, "high": -20,
}


def _priority_value(psutil, label: str) -> int:
    const = _PRIORITY_CONSTANTS[label]
    if hasattr(psutil, const):
        return int(getattr(psutil, const))
    return _PRIORITY_POSIX[label]


def _priority_label(psutil, value) -> str:
    for label in _PRIORITY_LABELS:
        if _priority_value(psutil, label) == value:
            return label
    return str(value)


def _priority_label_text(label: str) -> str:
    return label.replace("_", " ")


def set_process_priority(target: str, priority: str) -> str:
    """Set one process's CPU priority class, verified by read-back."""
    label = (priority or "").strip().lower()
    if label not in _PRIORITY_LABELS:
        return (f"Unknown priority: '{priority}'. Use one of: "
                "idle, below_normal, normal, above_normal, high. "
                "(Realtime is deliberately not offered — it can "
                "starve Windows itself and freeze the PC.)")
    text = (target or "").strip()
    if not text:
        return ("Which process? Give a PID (e.g. '4321') or an exact "
                "process name (e.g. 'chrome.exe').")
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return ("Couldn't set process priority — the psutil package "
                "isn't available in this VYUK install.")

    proc = None
    if text.isdigit():
        pid = int(text)
        try:
            proc = psutil.Process(pid)
            name = proc.name()
        except psutil.NoSuchProcess:
            return f"No process with PID {pid} is running."
        except psutil.AccessDenied:
            return (f"Process {pid} exists but Windows won't let "
                    "VYUK inspect it — its priority was not changed.")
    else:
        matches = []
        for candidate in psutil.process_iter():
            try:
                if candidate.name().lower() == text.lower():
                    matches.append(candidate)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if not matches:
            return (f"No running process named '{text}'. Names must "
                    "match exactly — or pass a PID instead.")
        if len(matches) > 1:
            listing = ", ".join(
                f"{(m.name() or text)} (PID {m.pid})" for m in matches[:10])
            more = (f" … and {len(matches) - 10} more"
                    if len(matches) > 10 else "")
            return (f"{len(matches)} processes are named '{text}': "
                    f"{listing}{more}. Refusing to guess — run it "
                    "again with one PID as the target.")
        proc = matches[0]
        name = proc.name() or text
        pid = proc.pid

    try:
        old_value = proc.nice()
        old_label = _priority_label(psutil, old_value)
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        old_label = None
    new_value = _priority_value(psutil, label)
    try:
        proc.nice(new_value)
    except psutil.AccessDenied:
        return (f"Couldn't change the priority of '{name}' "
                f"(PID {pid}) — Windows refused access. System "
                "processes and other users' processes are protected "
                "this way; the priority was not changed.")
    except psutil.NoSuchProcess:
        return f"'{name}' (PID {pid}) exited before its priority could be set."
    try:
        actual_value = psutil.Process(pid).nice()
    except Exception:  # noqa: BLE001 — set landed; read-back didn't
        return (f"Set '{name}' (PID {pid}) to "
                f"{_priority_label_text(label)} priority, but "
                "couldn't read it back to confirm.")
    actual_label = _priority_label(psutil, actual_value)
    if actual_label != label:
        return (f"Asked Windows to set '{name}' (PID {pid}) to "
                f"{_priority_label_text(label)} priority, but it "
                f"still reads {_priority_label_text(actual_label)} — "
                "the change didn't take.")
    if old_label is not None and old_label != label:
        return (f"'{name}' (PID {pid}) priority: "
                f"{_priority_label_text(old_label)} → "
                f"{_priority_label_text(label)} (confirmed by "
                "reading it back).")
    return (f"'{name}' (PID {pid}) is at "
            f"{_priority_label_text(label)} priority "
            "(confirmed by reading it back).")


# ---------------------------------------------------------------------------
# PRIVACY — which apps last used the camera / microphone / location
# ---------------------------------------------------------------------------

_CONSENT_ROOT = (
    "Software\\Microsoft\\Windows\\CurrentVersion\\"
    "CapabilityAccessManager\\ConsentStore"
)
_PRIVACY_DEVICES = {
    "webcam": "camera",
    "microphone": "microphone",
    "location": "location",
}
_MAX_APPS_PER_DEVICE = 8
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _filetime_to_datetime(value) -> datetime | None:
    """Windows FILETIME (100-ns ticks since 1601, UTC) → aware
    datetime. Zero / missing / garbage → None (never used)."""
    try:
        ticks = int(value)
    except (TypeError, ValueError):
        return None
    if ticks <= 0:
        return None
    return _FILETIME_EPOCH + timedelta(microseconds=ticks // 10)


def _app_display_name(subkey_name: str) -> str:
    """Consent-store subkey names are app ids or '#' joined paths
    (C:#Program Files#Zoom#bin#Zoom.exe); show the leaf name."""
    name = (subkey_name or "").strip()
    if "#" in name:
        name = name.rstrip("#").split("#")[-1]
    return name or subkey_name


def _read_app_record(winreg, key, device: str, subkey_name: str):
    """One app's (start, stop) record from an open consent key, or
    None when the key carries no usage timestamps."""
    def _value(name):
        try:
            value, _type = winreg.QueryValueEx(key, name)
            return value
        except Exception:  # noqa: BLE001 — value absent
            return None

    start = _filetime_to_datetime(_value("LastUsedTimeStart"))
    if start is None:
        return None
    stop = _filetime_to_datetime(_value("LastUsedTimeStop"))
    return {"device": device, "app": _app_display_name(subkey_name),
            "start": start, "stop": stop}


def _enum_subkeys(winreg, key) -> list[str]:
    names = []
    index = 0
    while True:
        try:
            names.append(winreg.EnumKey(key, index))
        except Exception:  # noqa: BLE001 — OSError when exhausted
            break
        index += 1
        if index > 512:  # defensive cap; real stores are far smaller
            break
    return names


def _collect_usage(winreg) -> list[dict]:
    """Walk the CapabilityAccessManager consent store and return
    usage records ({device, app, start, stop}) for the camera,
    microphone and location. Missing keys simply yield no records —
    absence is not an error. The winreg module is passed in so the
    walk can be tested against a fake key tree."""
    records: list[dict] = []
    for device in _PRIVACY_DEVICES:
        try:
            device_key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, f"{_CONSENT_ROOT}\\{device}")
        except Exception:  # noqa: BLE001 — device key absent
            continue
        with device_key:
            for subkey_name in _enum_subkeys(winreg, device_key):
                try:
                    app_key = winreg.OpenKey(device_key, subkey_name)
                except Exception:  # noqa: BLE001
                    continue
                with app_key:
                    record = _read_app_record(
                        winreg, app_key, device, subkey_name)
                    if record is not None:
                        records.append(record)
                    # Non-packaged apps live one level deeper,
                    # under a "NonPackaged" container subkey.
                    for child_name in _enum_subkeys(winreg, app_key):
                        try:
                            child_key = winreg.OpenKey(app_key, child_name)
                        except Exception:  # noqa: BLE001
                            continue
                        with child_key:
                            record = _read_app_record(
                                winreg, child_key, device, child_name)
                            if record is not None:
                                records.append(record)
    return records


def _human_time(moment: datetime, now: datetime) -> str:
    """'today 14:32' / 'yesterday 09:05' / '12 Oct 2026, 14:32'."""
    clock = moment.strftime("%H:%M")
    if moment.date() == now.date():
        return f"today {clock}"
    if moment.date() == (now.date() - timedelta(days=1)):
        return f"yesterday {clock}"
    return f"{moment.day} {moment.strftime('%b %Y')}, {clock}"


def _format_usage(records: list[dict], now: datetime) -> str:
    """Render consent-store records as the privacy_usage result."""
    if not records:
        return ("No camera, microphone or location usage records "
                "were found on this PC — Windows records app access "
                "to those sensors, and there's nothing recorded.")
    lines = ["Which apps last used your sensors:"]
    for device, label in _PRIVACY_DEVICES.items():
        device_records = [r for r in records if r["device"] == device]
        if not device_records:
            continue
        device_records.sort(key=lambda r: r["start"], reverse=True)
        for record in device_records[:_MAX_APPS_PER_DEVICE]:
            start_local = record["start"].astimezone()
            if (record["stop"] is None
                    and now - start_local <= timedelta(hours=24)):
                lines.append(
                    f"• {record['app']} — {label}, IN USE NOW "
                    f"(since {_human_time(start_local, now)})")
            elif record["stop"] is None:
                lines.append(
                    f"• {record['app']} — {label}, last started "
                    f"{_human_time(start_local, now)} (no stop was "
                    "recorded)")
            else:
                lines.append(
                    f"• {record['app']} — {label}, last used "
                    f"{_human_time(start_local, now)}")
        extra = len(device_records) - _MAX_APPS_PER_DEVICE
        if extra > 0:
            lines.append(f"  … and {extra} more for {label}.")
    return "\n".join(lines)


def privacy_usage() -> str:
    """Which apps last used the camera, microphone, or location."""
    if not _is_windows():
        return ("Sensor usage history is a Windows record (the "
                "CapabilityAccessManager consent store) — this "
                "system is not Windows.")
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return ("Couldn't read sensor usage records — the Windows "
                "registry isn't reachable from here.")
    try:
        records = _collect_usage(winreg)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the sensor usage records: {e}"
    now = datetime.now().astimezone()
    return _format_usage(records, now)


# ---------------------------------------------------------------------------
# Registration — called by core/tools_handlers_pack_hands.py (_register_hands4_tools; Phase 5 docs-only rename, wiring unchanged)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every System IV pack tool on a ToolRegistry-like object."""
    registry.register(
        name="mic_control",
        description="Check or change the DEFAULT microphone's mute state: status (muted or live, plus the input level), mute, unmute, or toggle. Changes are confirmed by reading the state back. It can also set the input level to an exact percentage: action='set_level' with level 0-100, likewise read back. Use when the user asks if their mic is muted, wants it muted/unmuted for a call, or wants the mic louder or quieter.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["status", "mute", "unmute", "toggle", "set_level"],
                           "description": "What to do (default status)"},
                "level": {"type": "integer",
                          "description": "For set_level: the input level to set, 0-100 percent"},
            },
            "required": [],
        },
        handler=mic_control,
    )
    registry.register(
        name="set_process_priority",
        description="Set one running process's CPU priority (idle, below_normal, normal, above_normal, high) — higher priority gets CPU first under load. The target is a PID or an exact process name; if several processes share the name it refuses and lists their PIDs rather than guessing. The new priority is confirmed by reading it back. Realtime priority is deliberately NOT offered: it can starve Windows itself and freeze the PC.",
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "A process ID (e.g. '4321') or an exact process name (e.g. 'chrome.exe')"},
                "priority": {"type": "string",
                             "enum": ["idle", "below_normal", "normal",
                                      "above_normal", "high"],
                             "description": "The priority class to set"},
            },
            "required": ["target", "priority"],
        },
        handler=set_process_priority,
    )
    registry.register(
        name="privacy_usage",
        description="Show which apps most recently used this PC's camera, microphone, and location (from Windows' own CapabilityAccessManager consent records), including anything that appears to be using a sensor right now. Use when the user asks what used their camera or mic, or is checking for suspicious sensor access. Read-only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=privacy_usage,
    )
