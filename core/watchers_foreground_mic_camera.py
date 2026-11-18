"""Foreground-mic-camera probes + live-engine registry, split from core/watchers.py (B45).

Home of the G3 family, the live-engine registry, and _DEFAULT_PROBES (which
sits in the G3 region and references G1 + G2 probes, imported below).
"""

# INTENTIONAL (skills split B45): stdlib names the moved code resolves
# at module level, found by the import-completeness prover.
import sys
import threading
import weakref

# INTENTIONAL (skills split B45): _DEFAULT_PROBES references G1 + G2
# probes by bare name; the G3 module imports exactly those probe names
# so the dict construction resolves locally (§3.5 P3 trap).
from .watchers_network_system import (
    _probe_battery,
    _probe_downloads,
    _probe_network,
    _probe_load,
    _probe_top_cpu_process,
    _probe_top_memory_process,
    _probe_disk_top_folders,
    _probe_network_diag,
)
from .watchers_file_privacy import (
    _probe_idle_seconds,
    _probe_disk,
    _probe_drives,
    _probe_clipboard,
    _probe_privacy,
    _probe_network_ssid,
    _probe_foreground_enabled,
    _probe_ambient_light_enabled,
    _probe_ambient_frame,
    _probe_file_sniffer,
    _probe_next_events,
)

_BATT_MIN_SPAN_SECONDS = 120       # an estimate needs a real span

_BATT_ESTIMATE_MAX_MINUTES = 24 * 60  # sanity bound for any estimate

# Call apps by process name — mirrors gui.main_window's
# _CALL_APP_NAMES (the gate's set); kept in sync by the N13 suite.
_CALL_APP_NAMES = frozenset({
    "zoom", "zoom meetings",
    "teams", "ms-teams", "microsoft teams", "msteams",
    "discord", "discordcanary", "discordptb",
    "skype", "skypeapp",
    "slack",
    "whatsapp", "whatsappdesktop",
    "webex", "webexmta", "cisco webex", "ciscocollabhost",
    "gotomeeting", "g2mcomm",
})

# Known media players by process name: fullscreen in one of these
# is a film, not a focus session, so the break nudge is held back.
_MEDIA_APP_NAMES = frozenset({
    "vlc", "mpv", "potplayer", "potplayermini64", "potplayermini",
    "mpc-hc", "mpc-hc64", "mpc-be", "mpc-be64", "kmplayer",
    "wmplayer", "microsoft.media.player", "media player",
    "plex", "plex media player", "kodi",
    "video.ui", "movies & tv", "films & tv",
})

def _normalise_process_name(name) -> str:
    """A process name for matching: basename, lowercased,
    trailing ".exe" stripped. "" for anything unusable."""
    try:
        text = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
        text = text.strip().lower()
        if text.endswith(".exe"):
            text = text[:-4]
        return text
    except Exception:  # noqa: BLE001
        return ""

def _probe_foreground_app():
    """The foreground window's process NAME (Windows only), or
    "" when it cannot be determined. Mirrors the GUI gate's own
    probe (QueryFullProcessImageNameW) — names only, never the
    window title. "" off Windows / on any failure."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong)]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = ctypes.c_ulong(0)
        if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return ""
        if not pid.value:
            return ""
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, buffer, ctypes.byref(size)):
                return ""
            return _normalise_process_name(buffer.value)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 — unknown foreground = no signal
        return ""

_foreground_lock = threading.Lock()

_foreground_cache = None  # str | None (None = never probed OK)

def _set_cached_foreground(name) -> None:
    """Record the latest foreground reading (normalised name,
    possibly ""). A None reading (probe failure) leaves the
    cache as-is. Never raises."""
    global _foreground_cache
    try:
        if name is None:
            return
        with _foreground_lock:
            _foreground_cache = _normalise_process_name(name)
    except Exception:  # noqa: BLE001
        pass

def current_foreground_name():
    """The foreground process name from the engine's last tick
    ("" = probed, nothing determinable; None = never probed).
    Never probes, never raises."""
    try:
        with _foreground_lock:
            return _foreground_cache
    except Exception:  # noqa: BLE001
        return None

def current_foreground_call_app():
    """The foreground process name when it is a known call app
    (the N1 gate's additional signal), else None. Never probes."""
    name = current_foreground_name()
    return name if name and name in _CALL_APP_NAMES else None

def current_foreground_media_app():
    """The foreground process name when it is a known media
    player, else None. Never probes."""
    name = current_foreground_name()
    return name if name and name in _MEDIA_APP_NAMES else None

def estimate_minutes_to_empty(history):
    """Minutes-to-empty from observed drain: `history` is a list
    of (datetime, percent) samples taken while unplugged. The
    estimate is percent ÷ observed drain rate (%/min, first→last
    sample), returned only when it is trustworthy: at least two
    samples spanning >= _BATT_MIN_SPAN_SECONDS, a real drop, and
    a result inside the sanity bound (1 minute … 24 hours).
    Anything else is None — no estimate beats a wild one."""
    try:
        samples = [(t, float(p)) for t, p in (history or [])
                   if t is not None]
        if len(samples) < 2:
            return None
        (t0, p0), (t1, p1) = samples[0], samples[-1]
        span_min = (t1 - t0).total_seconds() / 60.0
        if span_min * 60.0 < _BATT_MIN_SPAN_SECONDS:
            return None
        drop = p0 - p1
        if drop <= 0:
            return None  # flat or rising — no drain to project
        rate = drop / span_min
        if rate <= 0:
            return None
        minutes = p1 / rate
        if minutes < 1 or minutes > _BATT_ESTIMATE_MAX_MINUTES:
            return None
        return minutes
    except Exception:  # noqa: BLE001
        return None

def _os_estimate_is_wild(os_minutes, observed_minutes) -> bool:
    """True when the OS time-left estimate should not be trusted:
    beyond the sanity bound outright, or — when an observed
    drain-rate estimate exists — off from it by more than 4× in
    either direction."""
    try:
        if os_minutes is None:
            return False
        if float(os_minutes) > _BATT_ESTIMATE_MAX_MINUTES \
                or float(os_minutes) <= 0:
            return True
        if observed_minutes:
            ratio = float(os_minutes) / float(observed_minutes)
            return ratio > 4.0 or ratio < 0.25
        return False
    except Exception:  # noqa: BLE001
        return False

def _probe_fullscreen():
    """True when the foreground window covers >= 99% of its
    monitor in both dimensions (Windows only); None off Windows
    or on any failure (no foreground window, API error)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.MonitorFromWindow.argtypes = [ctypes.c_void_p,
                                             ctypes.c_ulong]

        class _Rect(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long),
                        ("bottom", ctypes.c_long)]

        class _MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong),
                        ("rcMonitor", _Rect), ("rcWork", _Rect),
                        ("dwFlags", ctypes.c_ulong)]

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = _Rect()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        hmon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not hmon:
            return None
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return None
        mon = info.rcMonitor
        win_w, win_h = rect.right - rect.left, rect.bottom - rect.top
        mon_w, mon_h = mon.right - mon.left, mon.bottom - mon.top
        if mon_w <= 0 or mon_h <= 0:
            return None
        return win_w >= 0.99 * mon_w and win_h >= 0.99 * mon_h
    except Exception:  # noqa: BLE001
        return None

_DEFAULT_PROBES = {
    "battery": _probe_battery,
    "downloads": _probe_downloads,
    "network": _probe_network,
    "idle_seconds": _probe_idle_seconds,
    "disk": _probe_disk,
    "drives": _probe_drives,
    "clipboard": _probe_clipboard,
    "privacy": _probe_privacy,
    "fullscreen": _probe_fullscreen,
    "load": _probe_load,
    "top_cpu_process": _probe_top_cpu_process,
    "top_memory_process": _probe_top_memory_process,
    "disk_top_folders": _probe_disk_top_folders,
    # v5.45 (NOSE Wave 3)
    "network_ssid": _probe_network_ssid,
    "network_diag": _probe_network_diag,
    "foreground_app": _probe_foreground_app,
    "foreground_enabled": _probe_foreground_enabled,
    "ambient_light_enabled": _probe_ambient_light_enabled,
    "ambient_frame": _probe_ambient_frame,
    "file_sniffer": _probe_file_sniffer,
    # v5.49 (programme item 35): the suggestions meeting producer.
    "next_events": _probe_next_events,
}

# Live-engine registry (v5.42): a running engine registers itself
# here on start() (and on tick(), so manually-driven engines are
# visible too) and clears the slot on stop(). A weakref is kept so
# a dead/forgotten engine never leaks or poses as live.
_live_engine_ref = None

def _set_live_engine(engine) -> None:
    global _live_engine_ref
    try:
        _live_engine_ref = weakref.ref(engine)
    except Exception:  # noqa: BLE001 — tracking must never raise
        _live_engine_ref = None

def _clear_live_engine(engine) -> None:
    global _live_engine_ref
    try:
        if _live_engine_ref is not None and _live_engine_ref() is engine:
            _live_engine_ref = None
    except Exception:  # noqa: BLE001
        _live_engine_ref = None

def _get_live_engine():
    global _live_engine_ref
    if _live_engine_ref is None:
        return None
    try:
        engine = _live_engine_ref()
    except Exception:  # noqa: BLE001
        return None
    if engine is None:
        _live_engine_ref = None
        return None
    # A stopped engine must not pose as live.
    try:
        if getattr(engine, "_stopped", False):
            return None
    except Exception:  # noqa: BLE001
        pass
    return engine

def network_state_snapshot() -> dict | None:
    """The streak-filtered network state, for core/ambient
    (v5.52, item 6).

    Read from the LIVE engine's announcement state machine —
    the same state that decides what the user is told — never
    from a fresh probe. The field failure this fixes: ambient
    used to mint its own binary from ONE raw _probe_network()
    call, and on institutional networks (captive portal /
    resolver interception / egress rules) that single TCP
    connect fails while the internet works, so the brain was
    handed "offline" as ground truth and cited it as fact.

    Returns None when there is no watcher state to mirror (no
    live engine, or the engine has never had a successful
    network probe) — the caller then OMITS the field rather
    than minting a one-probe verdict. Otherwise a dict:
      {"state": "offline", "verdict": str | None} — the engine
        has ANNOUNCED the drop: the fail streak reached
        NETWORK_FAILS_TO_ANNOUNCE and the N5 ladder ran; the
        verdict is the ladder's own words for that announcement
        (None when the ladder produced nothing usable).
      {"state": "unconfirmed", "verdict": None} — failures are
        accumulating below the announce threshold: one check
        says down, nothing has confirmed it.
      {"state": "online", "verdict": None} — the probe has
        succeeded and no failure streak is running.
    Never raises."""
    try:
        engine = _get_live_engine()
        if engine is None:
            return None
        if getattr(engine, "_net_down_announced", False):
            return {"state": "offline",
                    "verdict": getattr(engine, "_net_last_verdict", None)}
        if int(getattr(engine, "_net_fail_streak", 0) or 0) > 0:
            return {"state": "unconfirmed", "verdict": None}
        ever = getattr(engine, "_probe_ever_succeeded", None)
        if isinstance(ever, dict) and ever.get("network"):
            return {"state": "online", "verdict": None}
        return None
    except Exception:  # noqa: BLE001 — a mirror must never raise
        return None

def _is_probe_success(name, value) -> bool:
    """Per-probe success definition (v5.42), mirroring each
    watcher's own usage in tick() — a probe succeeded when it
    returned a usable reading, even if that reading says
    "nothing happening":
    - battery/load/disk: truthy tuple required (their watchers
      do `if not reading: return`).
    - drives: non-empty set only — _watch_drives treats an
      empty set as probe failure ("a real system has >= 1").
    - downloads: any non-None value, including {} — its
      watcher only aborts on None; {} is a valid empty snapshot
      (the default probe also returns {} on failure, so that
      failure mode is indistinguishable by design there).
    - network: False (offline) is a valid reading; only None fails.
    - idle_seconds: 0.0 (just active) is valid; only None fails.
    - clipboard: any non-None fingerprint; None fails.
    - privacy: empty frozenset IS success (nobody using cam/mic,
      probe worked); only None fails.
    - fullscreen: False (not fullscreen) is valid; only None fails.
    - foreground_app (N13): a non-empty NAME is success; "" means
      the foreground could not be determined, which off Windows
      is every tick — honest bookkeeping, not a watcher event.
    - network_diag (N5): a dict with at least one non-None rung
      is success; an all-None ladder could not actually run.
    - network_ssid (N5): any non-None string ("" = wired/none).
    """
    if value is None:
        return False
    if name in ("battery", "load", "disk", "drives"):
        return bool(value)
    if name == "foreground_app":
        return bool(value)
    if name == "network_diag":
        return isinstance(value, dict) and any(
            v is not None for v in value.values())
    return True
