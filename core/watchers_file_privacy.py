"""Watcher probes — file/clipboard/privacy group (skills split B44, part 1 of 2).

Moved verbatim from core/watchers.py (old lines 588-967): the
file-clipboard-privacy probe family, its state, and the senses_log
provider-registration block that registers structured_value_for.

Why this family first (measured call graph, B36 tokens-first
precedent): it is the ONLY probe group with ZERO out-edges to any
other family — every bare name its functions resolve is an import, a
builtin, or another name in this module. Its state (_CONSENT_STORE,
_privacy_state_lock/_privacy_state_cache, _structured_lock/
_structured_values/_STRUCTURED_MAX) is co-located here with its only
users (B25 pattern); no production, GUI, or test code reads that
state directly (grep-verified). The provider-registration Try block
travels with structured_value_for, the function it registers.
No `from __future__ import annotations` in the old file, so none here.
"""

import hashlib
import sys
import threading
from pathlib import Path


def _probe_network_ssid():
    """The SSID of the current Wi-Fi connection, or None (wired,
    off Windows/Linux tools, any failure). Windows parses
    `netsh wlan show interfaces`, Linux `nmcli`. Names only —
    nothing about the connection's contents is ever read."""
    try:
        import subprocess

        kwargs = {"capture_output": True, "timeout": 3,
                  "stdin": subprocess.DEVNULL}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = 0x08000000
            out = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                                 **kwargs).stdout or b""
            for line in out.decode("utf-8", "replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("SSID") and ":" in stripped \
                        and "BSSID" not in stripped:
                    value = stripped.split(":", 1)[-1].strip()
                    return value or None
            return None
        out = subprocess.run(
            ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
            **kwargs).stdout or b""
        for line in out.decode("utf-8", "replace").splitlines():
            if line.startswith("yes:"):
                return line[4:].strip() or None
        return None
    except Exception:  # noqa: BLE001
        return None


def _probe_file_sniffer(name):
    """N6: core.senses.sniff_file on a finished download, by file
    NAME (resolved inside ~/Downloads). Returns the sniff dict,
    or None on any failure — the notice then just offers to open
    the file without a verdict. Lazy import, like the timeline
    reader: importing watchers must not pull in the package."""
    try:
        try:
            from .senses import sniff_file
        except Exception:  # noqa: BLE001 — not imported as a package
            from voice_assistant.core.senses import sniff_file
        return sniff_file(Path.home() / "Downloads" / str(name))
    except Exception:  # noqa: BLE001
        return None


def _probe_next_events():
    """v5.49 (programme item 35): the next calendar events for
    the suggestions engine's meeting producer, in the
    google_services.next_events shape ambient.py consumes.
    Returns [] when Google isn't connected; never raises."""
    try:
        try:
            from . import google_services
        except Exception:  # noqa: BLE001 — not imported as a package
            from voice_assistant.core import google_services
        return google_services.next_events(within_hours=1, limit=5) or []
    except Exception:  # noqa: BLE001
        return []


def _probe_foreground_enabled():
    """N13 gate seam: foreground awareness is on unless a probe
    says otherwise. The GUI's Notices toggle governs the telling;
    this seam lets the engine itself be gated (and faked)."""
    return True


def _probe_ambient_light_enabled():
    """N12 gate seam: ambient light is OPT-IN — the default is
    OFF, and only an injected probe (the Settings wiring) turns
    the hourly camera sampling on."""
    return False


def _probe_ambient_frame():
    """N12: ONE webcam frame via the existing vision path, as
    (image_bytes, mime) or bare bytes; None when no camera. The
    caller reduces it to a brightness number and drops it — the
    frame is never stored or transmitted."""
    try:
        try:
            from .desktop.vision import vision_manager  # Phase 3 D4c: vision hub moved to core/desktop/
        except Exception:  # noqa: BLE001 — not imported as a package
            from voice_assistant.core.desktop.vision import vision_manager  # Phase 3 D4c: vision hub moved to core/desktop/
        return vision_manager.capture_webcam()
    except Exception:  # noqa: BLE001
        return None


def _probe_idle_seconds():
    """Seconds since the last keyboard/mouse input (Windows only).

    None off Windows or on any failure — the idle watcher stays off.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class _LastInputInfo(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint),
                        ("dwTime", ctypes.c_uint)]

        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        # DWORD arithmetic wraps every ~49.7 days — mask the delta.
        elapsed_ms = (ctypes.windll.kernel32.GetTickCount() - info.dwTime) \
            & 0xFFFFFFFF
        return elapsed_ms / 1000.0
    except Exception:  # noqa: BLE001
        return None


def _probe_disk():
    """(total, free) bytes for the system drive, or None."""
    try:
        import psutil

        usage = psutil.disk_usage(Path.home().anchor)
        return (usage.total, usage.free)
    except Exception:  # noqa: BLE001
        return None


def _probe_drives():
    """Set of current mountpoints; empty set on any failure."""
    try:
        import psutil

        return {part.mountpoint for part in psutil.disk_partitions(all=False)}
    except Exception:  # noqa: BLE001
        return set()


def _probe_clipboard():
    """Fingerprint of the clipboard TEXT (Windows only), or None.

    Returns (category, length, sha1_first12) — NEVER the text
    itself. Category is "link" when the stripped text starts with
    http:// or https://, "path" when it names a file/folder that
    exists, else "text". The clipboard is opened and closed inside
    this call and every API step is guarded; any failure (another
    app holding the clipboard, a non-text format, off Windows)
    returns None and the watcher skips the tick.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        CF_UNICODETEXT = 13
        user32.GetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        if not user32.OpenClipboard(None):
            return None
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                text = ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
        if not text:
            return None
        stripped = text.strip()
        if stripped.lower().startswith(("http://", "https://")):
            category = "link"
        elif Path(stripped).exists():
            category = "path"
        else:
            category = "text"
        digest = hashlib.sha1(
            text.encode("utf-8", "replace")).hexdigest()[:12]
        return (category, len(text), digest)
    except Exception:  # noqa: BLE001 — a broken probe disables its watcher
        return None


_CONSENT_STORE = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                  r"\CapabilityAccessManager\ConsentStore")


def _consent_app_active(winreg, key_path: str) -> list[str]:
    """App ids under one ConsentStore key whose use flags say "in
    use right now" (LastUsedTimeStart set, LastUsedTimeStop zero).
    Container subkeys (e.g. NonPackaged) are searched one level
    deeper. Any registry hiccup yields [] for that branch."""
    found: list[str] = []

    def visit(path: str, depth: int) -> None:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        with key:
            try:
                start, _ = winreg.QueryValueEx(key, "LastUsedTimeStart")
                stop, _ = winreg.QueryValueEx(key, "LastUsedTimeStop")
            except OSError:
                start, stop = 0, 0
            if start and not stop:
                # App ids encode paths with '#' separators; the
                # leaf is the readable app/exe name.
                leaf = path.rsplit("\\", 1)[-1].replace("#", "\\")
                found.append(leaf.rstrip("\\").rsplit("\\", 1)[-1])
                return
            if depth <= 0:
                return
            index = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                visit(path + "\\" + sub, depth - 1)

    visit(key_path, 1)
    return found


def _probe_privacy():
    """frozenset of "webcam:<app>" / "microphone:<app>" currently
    using the device (Windows only), or None off Windows / on
    failure. Reads the CapabilityAccessManager ConsentStore: an
    app counts as in use when LastUsedTimeStart > 0 and
    LastUsedTimeStop == 0. A successful reading refreshes the
    cached privacy state (see current_privacy_state) — the cache
    is the ONLY thing the speech path ever reads."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        active: set[str] = set()
        for device in ("webcam", "microphone"):
            for app in _consent_app_active(
                    winreg, _CONSENT_STORE + "\\" + device):
                if app:
                    active.add(f"{device}:{app}")
        result = frozenset(active)
        _set_cached_privacy_state(result)
        return result
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# N1 (v5.44) — cached privacy state. The engine probes privacy on
# its tick; the LAST successful reading is cached here so other
# layers (the GUI's call-aware speech gate) can ask "who holds
# the mic/camera right now?" WITHOUT running a probe — no new
# registry reads ever happen on a speech/notification path.
# Thread-safe: the engine thread writes, the GUI thread reads.
# ---------------------------------------------------------------------------

_privacy_state_lock = threading.Lock()
_privacy_state_cache = None  # frozenset | None (None = never probed OK)


def _set_cached_privacy_state(active) -> None:
    """Record the latest successful privacy reading (a frozenset
    of "webcam:<app>" / "microphone:<app>", possibly empty).
    Called by _probe_privacy on success and by the engine after
    every privacy probe (so injected probes count too). Never
    raises; a None reading (probe failure) leaves the cache as-is
    — a failed probe is not evidence that nobody holds the mic."""
    global _privacy_state_cache
    try:
        if active is None:
            return
        with _privacy_state_lock:
            _privacy_state_cache = frozenset(active)
    except Exception:  # noqa: BLE001 — caching must never raise
        pass


def current_privacy_state() -> dict:
    """The CURRENT privacy state from the last successful probe:
    {"mic_users": [app, ...], "camera_users": [app, ...],
     "active": frozenset | None}. Lists are sorted fresh copies —
    mutating them cannot poison the cache. "active" is None when
    no probe has succeeded yet (state unknown — callers must not
    read that as "nobody is using anything"). Never raises and
    never probes."""
    try:
        with _privacy_state_lock:
            cached = _privacy_state_cache
    except Exception:  # noqa: BLE001
        cached = None
    mic: list = []
    camera: list = []
    if cached:
        for entry in cached:
            kind, _, app = str(entry).partition(":")
            if not app:
                continue
            if kind == "microphone":
                mic.append(app)
            elif kind == "webcam":
                camera.append(app)
    return {"mic_users": sorted(mic), "camera_users": sorted(camera),
            "active": cached}


def current_mic_users() -> list:
    """App names currently holding the microphone, per the last
    successful privacy probe ([] when none — or when no probe
    has succeeded yet; current_privacy_state()["active"] tells
    the two apart). Never probes, never raises."""
    return current_privacy_state()["mic_users"]


def current_camera_users() -> list:
    """App names currently using the camera, per the last
    successful privacy probe. Never probes, never raises."""
    return current_privacy_state()["camera_users"]


# ---------------------------------------------------------------------------
# N7 (v5.45) — structured values for watcher notices. _emit records
# each delivered notice's measurement here, keyed by (kind,
# message); the senses log consults the registry through its
# value-provider seam when the GUI appends the notice, so the
# timeline line carries the real number (value/unit) alongside
# the prose. Bounded and thread-safe; a lookup miss is normal
# (not every kind carries a measurement).
# ---------------------------------------------------------------------------

_structured_lock = threading.Lock()
_structured_values: dict = {}
_STRUCTURED_MAX = 100


def _record_structured_value(kind, message, value, unit) -> None:
    try:
        if value is None:
            return
        with _structured_lock:
            _structured_values[(str(kind), str(message))] = (
                float(value), str(unit) if unit is not None else None)
            while len(_structured_values) > _STRUCTURED_MAX:
                _structured_values.pop(
                    next(iter(_structured_values)), None)
    except Exception:  # noqa: BLE001 — enrichment must never raise
        pass


def structured_value_for(kind, message):
    """The structured (value, unit) recorded for a delivered
    notice, or None. Also registered into core.senses_log as a
    value provider (below), which is how timeline lines pick
    the fields up. Never raises."""
    try:
        with _structured_lock:
            return _structured_values.get((str(kind), str(message)))
    except Exception:  # noqa: BLE001
        return None


try:  # register the provider without making it a hard dependency
    from . import senses_log as _senses_log_for_n7
    _senses_log_for_n7.register_value_provider(structured_value_for)
except Exception:  # noqa: BLE001 — standalone import: provider is a bonus
    pass
