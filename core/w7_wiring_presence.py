# Part of the core/w7_wiring.py split (skills split B13).
# Family: presence.

from __future__ import annotations

from typing import Any, Optional

from . import daily  # sibling module used by this family (PresenceLock)

PRESENCE_WINDOWS_VALIDATION_OWED = (
    "Presence lock still needs Windows validation: the "
    "LockWorkStation seam, the idle-probe signal, and the "
    "dwell/grace behaviour have only been exercised with "
    "injected signals in tests, never on a real Windows desktop."
)

_PRESENCE_IDLE_PRESENT_SECONDS = 60.0

_PRESENCE_LOCK: Optional[daily.PresenceLock] = None

_PRESENCE_ENABLED = False

def presence_signal_from_idle(idle_seconds) -> Optional[bool]:
    """The idle-probe signal shape: True = present (recent
    input), False = absent, None = unknown (probe down — and
    unknown NEVER triggers a lock, by the class's own rule)."""
    if idle_seconds is None:
        return None
    try:
        idle = float(idle_seconds)
    except (TypeError, ValueError):
        return None
    return idle < _PRESENCE_IDLE_PRESENT_SECONDS

def _idle_probe_signal() -> Optional[bool]:
    """Default presence signal: the watchers' input-idle probe
    (Windows GetLastInputInfo; None off Windows or on probe
    failure). Lazy import so this module never imports the
    watcher engine at module level."""
    try:
        from . import watchers as _watchers
        return presence_signal_from_idle(
            _watchers._probe_idle_seconds())
    except Exception:  # noqa: BLE001 — a broken probe is "unknown"
        return None

def presence_lock(signal=None, lock_fn=None,
                  dwell_seconds: int = None,
                  grace_seconds: int = None) -> daily.PresenceLock:
    """The lock object itself: caller-supplied signal (or the
    idle-probe signal when none is given at enable time) +
    caller-supplied lock seam (default: ctypes
    LockWorkStation on Windows). Lock-ONLY — there is no
    unlock function anywhere in this layer or the module."""
    global _PRESENCE_LOCK
    if _PRESENCE_LOCK is None:
        kwargs: dict[str, Any] = {}
        if dwell_seconds is not None:
            kwargs["dwell_seconds"] = dwell_seconds
        if grace_seconds is not None:
            kwargs["grace_seconds"] = grace_seconds
        _PRESENCE_LOCK = daily.PresenceLock(
            presence_signal=signal, lock_fn=lock_fn, **kwargs)
    return _PRESENCE_LOCK

def enable_presence_lock(signal=None, lock_fn=None,
                         dwell_seconds: int = None,
                         grace_seconds: int = None) -> dict:
    """Arm the presence poller (owner decision — see
    PRESENCE_ACTIVATION_OWED). Without an explicit signal the
    idle-probe signal is used; without a lock seam the Windows
    LockWorkStation default applies. Returns the arming record;
    nothing is locked by arming — evaluate() still needs a
    full dwell of continuous absence."""
    global _PRESENCE_ENABLED, _PRESENCE_LOCK
    _PRESENCE_LOCK = None
    _PRESENCE_LOCK = daily.PresenceLock(
        presence_signal=(signal if signal is not None
                         else _idle_probe_signal),
        lock_fn=lock_fn,
        **({"dwell_seconds": dwell_seconds}
           if dwell_seconds is not None else {}),
        **({"grace_seconds": grace_seconds}
           if grace_seconds is not None else {}))
    _PRESENCE_ENABLED = True
    return {"enabled": True,
            "dwell_seconds": _PRESENCE_LOCK.dwell_seconds,
            "grace_seconds": _PRESENCE_LOCK.grace_seconds,
            "windows_validation_owed": PRESENCE_WINDOWS_VALIDATION_OWED}

def disable_presence_lock() -> dict:
    """Disarm the presence poller. A disabled poller never
    evaluates and never locks."""
    global _PRESENCE_ENABLED
    _PRESENCE_ENABLED = False
    return {"enabled": False}

def presence_enabled() -> bool:
    return bool(_PRESENCE_ENABLED)

def reset_presence() -> None:
    """Test seam: disarm + drop the lock object."""
    global _PRESENCE_LOCK, _PRESENCE_ENABLED
    _PRESENCE_LOCK = None
    _PRESENCE_ENABLED = False

def poll_presence_lock(now=None) -> dict:
    """One presence poll, for the watcher tick. Disabled ->
    {"action": "disabled", ...} and nothing is evaluated.
    Enabled -> the lock's own evaluate(): "none" below the
    dwell, "locked" after a full dwell of continuous absence,
    "refused" without a signal or on a failed lock seam.
    Unknown presence holds; it never locks."""
    if not _PRESENCE_ENABLED:
        return {"action": "disabled",
                "reason": "presence lock poller is disabled "
                          "(see PRESENCE_ACTIVATION_OWED)"}
    lock = _PRESENCE_LOCK or presence_lock(signal=_idle_probe_signal)
    result = lock.evaluate(now)
    return {"action": result.get("action"),
            "reason": result.get("reason")}
