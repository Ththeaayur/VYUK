"""
Session epoch for VYUK (v5.48, programme item 20, B-T1.7).

A process-wide, monotonically increasing counter that is bumped
every time the live session stops being the live one — a client
disconnects / stops, or core/brain/brain.py swaps the inner session.
Consumers (today: core/funnel/tools_registry.py's executor funnel) stamp the
epoch when work is dispatched and compare it later: if the epoch
moved while the work was waiting (e.g. a human confirmation was
in flight across a disconnect), the earlier context no longer
owns the machine and the work must not proceed on a stale
approval.

Deliberately tiny and dependency-free (stdlib only, no imports
from the rest of core) so every client can bump it without any
import-cycle risk. Bumps never decrease the value and never
raise: a failed bump must not break session teardown.
"""

import logging
import threading

log = logging.getLogger("nova.session_epoch")

_lock = threading.Lock()
_epoch = 0


def current_epoch() -> int:
    """The current session epoch (0 before the first bump)."""
    with _lock:
        return _epoch


def bump_epoch(reason: str = "") -> int:
    """Advance the epoch by one and return the new value.

    Called at every session end/start seam (the clients' stop()
    and start() finally blocks, lite's upgrade handoff, and
    core/brain/brain.py's session swaps). `reason` is a short label for
    the log line only; it never affects the value.
    """
    global _epoch
    with _lock:
        _epoch += 1
        new_value = _epoch
    if reason:
        log.info("session epoch -> %s (%s)", new_value, reason)
    else:
        log.debug("session epoch -> %s", new_value)
    return new_value


def epoch_changed_since(stamp: int) -> bool:
    """True when the epoch has moved since `stamp` was taken."""
    return current_epoch() != stamp
