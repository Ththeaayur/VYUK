from __future__ import annotations  # INTENTIONAL (skills split B21): moved code was written under postponed evaluation.
import datetime

# ===========================================================================
# Presence lock — lock-ONLY
# ===========================================================================

# Conservative defaults, designed to avoid false locks: five
# minutes of continuous absence before locking, with a one-minute
# activity grace window (recent input resets the dwell clock).
ABSENCE_DWELL_SECONDS = 300
ACTIVITY_GRACE_SECONDS = 60


class PresenceRefused(RuntimeError):
    """Raised when presence lock refuses (no signal, or no lock seam)."""


class PresenceLock:
    """Absence-dwell presence lock, lock-ONLY.

    ``presence_signal`` is a caller-supplied callable returning
    True when the user is present, False when absent, or None
    when unknown — or an object with ``is_present()``. The signal
    is NEVER stored (no disk writes, no history; the only state
    is in-memory timestamps). ``lock_fn`` is the caller-supplied
    lock seam (default: ctypes LockWorkStation on Windows);
    tests inject a fake. ``evaluate(now)`` returns a dict:
    {"action": "none"|"locked"|"refused", "reason": ...}. A lock
    fires at most once per absence episode (it resets when
    presence returns). There is deliberately NO unlock function
    in this module — unlock stays Windows' job.

    Without a presence signal, ``evaluate`` REFUSES — it never
    locks blind. Below the dwell threshold it does nothing.
    """

    def __init__(self, presence_signal=None, lock_fn=None,
                 dwell_seconds: int = ABSENCE_DWELL_SECONDS,
                 grace_seconds: int = ACTIVITY_GRACE_SECONDS):
        self._signal = presence_signal
        self._lock_fn = lock_fn
        self.dwell_seconds = int(dwell_seconds)
        self.grace_seconds = int(grace_seconds)
        self._absent_since: datetime.datetime | None = None
        self._locked_this_episode = False
        self._last_seen_present: datetime.datetime | None = None

    def _read_signal(self):
        if self._signal is None:
            return None
        try:
            if callable(self._signal):
                return self._signal()
            fn = getattr(self._signal, "is_present", None)
            if callable(fn):
                return fn()
        except Exception:  # noqa: BLE001 — a broken signal is "unknown"
            return None
        return None

    def _default_lock(self) -> tuple[bool, str]:
        import sys
        if sys.platform.startswith("win"):
            try:
                import ctypes
                if ctypes.windll.user32.LockWorkStation():
                    return True, "Windows LockWorkStation succeeded."
                return False, ("Windows LockWorkStation reported failure; "
                               "the PC may still be unlocked.")
            except Exception as exc:  # noqa: BLE001
                return False, f"could not lock the PC: {exc}"
        return False, ("locking uses the Windows LockWorkStation call; "
                       "no lock command is available on this system.")

    def evaluate(self, now: datetime.datetime | None = None) -> dict:
        now = now or datetime.datetime.now()
        if self._signal is None:
            return {"action": "refused",
                    "reason": ("presence lock refused — no presence signal "
                               "was supplied. Without a signal the lock "
                               "never fires; it never locks blind.")}
        present = self._read_signal()
        if present is True:
            self._absent_since = None
            self._locked_this_episode = False
            self._last_seen_present = now
            return {"action": "none",
                    "reason": "user present — dwell clock reset."}
        if present is None:
            # Unknown is not absence: hold, do not lock.
            return {"action": "none",
                    "reason": ("presence unknown — holding; unknown "
                               "presence never triggers a lock.")}
        # Absent.
        if self._last_seen_present is not None:
            since_present = (now - self._last_seen_present).total_seconds()
            if since_present < self.grace_seconds:
                return {"action": "none",
                        "reason": ("within the activity grace window — "
                                   "holding to avoid a false lock.")}
        if self._absent_since is None:
            self._absent_since = now
        absent_for = (now - self._absent_since).total_seconds()
        if absent_for < self.dwell_seconds:
            return {"action": "none",
                    "reason": (f"absent {int(absent_for)}s, below the "
                               f"{self.dwell_seconds}s dwell threshold — "
                               "no lock.")}
        if self._locked_this_episode:
            return {"action": "none",
                    "reason": "already locked this absence episode."}
        lock_fn = self._lock_fn or self._default_lock
        try:
            result = lock_fn()
        except Exception as exc:  # noqa: BLE001
            return {"action": "refused",
                    "reason": f"lock seam failed: {exc}"}
        if isinstance(result, tuple):
            ok, detail = bool(result[0]), str(result[1]) if len(result) > 1 else ""
        else:
            ok, detail = bool(result), ""
        if ok:
            self._locked_this_episode = True
            return {"action": "locked",
                    "reason": "absence dwell exceeded — workstation locked. "
                              + detail}
        return {"action": "refused",
                "reason": "lock seam reported failure — " + detail}
