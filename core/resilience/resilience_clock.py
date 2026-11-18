"""RL-7.1 clock discipline + RL-7.2 DST datetimes (wave 25, §5.18).

Load-bearing: misclassifying sleep as crash would let safe mode cry
wolf, so the dual-clock classifier below is conservative in exactly
one direction — only a confident "crash" verdict may feed crash-loop
counting. "Sleep" and "unknown" never do.

Rules:
- Monotonic clocks for ALL durations/timeouts (deadline helpers take a
  monotonic duration and an injectable clock; tests fake sleep by
  jumping the injected clock).
- Wall clock ONLY for "run at" times (retry availability, crash
  timestamps, cached-at labels) — never for durations.
- Every heartbeat and checkpoint carries dual-clock fields (wall +
  monotonic + session id) so post-hoc classification separates sleep
  from crash.
- A Live resumption handle is dead after any suspend longer than
  RS-1's ~10-minute window, and dead after any crash; unknown clocks
  fail CLOSED for resumption (a handle of unknown provenance must not
  resume), while "unknown" never feeds safe-mode counting (different
  consumers, different fail directions — both documented here).

RL-7.2: store UTC, convert at display, re-resolve the local zone on
every call instead of caching it at startup (the Windows
timezone-change broadcast re-resolve is later wiring). The ``whenever``
library is NOT used (its LICENSE file read is still owed — its README
claims MIT, the file was never opened); this is a native rule only,
plus a STUDY note.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

LIVE_HANDLE_WINDOW_S = 600.0
DEFAULT_TOLERANCE_S = 5.0


def new_session_id() -> str:
    """A fresh session/boot id for dual-clock logs."""
    return uuid.uuid4().hex


def dual_clock_log(session_id: str, wall: float | None = None,
                   monotonic: float | None = None) -> dict:
    """One dual-clock record: wall + monotonic + session id."""
    return {"wall": time.time() if wall is None else float(wall),
            "monotonic": (time.monotonic() if monotonic is None
                          else float(monotonic)),
            "session_id": session_id}


def monotonic_deadline(duration_s: float, clock=None) -> float:
    """A deadline from a monotonic duration (never wall arithmetic)."""
    now = (clock or time.monotonic)()
    return now + float(duration_s)


def deadline_remaining(deadline: float, clock=None) -> float:
    """Seconds left on a monotonic deadline, floored at zero."""
    now = (clock or time.monotonic)()
    return max(0.0, float(deadline) - now)


def deadline_expired(deadline: float, clock=None) -> bool:
    """True when a monotonic deadline has passed."""
    now = (clock or time.monotonic)()
    return now >= float(deadline)


def classify_dual_clock(last: dict, now: dict,
                        tolerance_s: float = DEFAULT_TOLERANCE_S) -> dict:
    """Classify the gap between two dual-clock records.

    Verdicts:
    - "crash": session/boot id changed AND the last record was not a
      clean shutdown (a restart without a shutdown record).
    - "clean": same session with consistent clocks, or a session change
      after a clean shutdown (an orderly restart).
    - "sleep": wall clock ran ahead of monotonic by more than the
      tolerance (the monotonic clock paused while the machine slept).
      The result carries "gap_s" so callers can apply their own window
      (e.g. RS-1's 10-minute handle window).
    - "unknown": clocks inconsistent in the impossible direction
      (monotonic ahead of wall beyond tolerance). Never feeds safe-mode
      counting; fail-closed elsewhere (see live_handle_alive).
    Only "crash" may feed crash-loop counting. A "sleep" misread as
    "crash" is the wolf-crying defect this classifier exists to prevent.
    """
    if last.get("session_id") != now.get("session_id"):
        if last.get("clean_shutdown"):
            return {"verdict": "clean",
                    "reason": "orderly restart after a clean shutdown",
                    "gap_s": 0.0}
        return {"verdict": "crash",
                "reason": "session changed without a clean shutdown",
                "gap_s": 0.0}
    wall_delta = float(now["wall"]) - float(last["wall"])
    mono_delta = float(now["monotonic"]) - float(last["monotonic"])
    gap = wall_delta - mono_delta
    if gap > tolerance_s:
        return {"verdict": "sleep",
                "reason": "wall clock ran ahead of monotonic: the "
                "machine slept",
                "gap_s": gap}
    if gap < -tolerance_s:
        return {"verdict": "unknown",
                "reason": "monotonic ran ahead of wall: inconsistent "
                "clocks, neither sleep nor crash",
                "gap_s": gap}
    return {"verdict": "clean",
            "reason": "clocks consistent: continuous run",
            "gap_s": gap}


# RL-3.5 name for the same classifier (the resume handler's entry point).
classify_wake = classify_dual_clock


def live_handle_alive(last: dict, now: dict,
                      window_s: float = LIVE_HANDLE_WINDOW_S) -> dict:
    """Is a Live resumption handle still alive across this gap?

    Dead after any suspend longer than RS-1's ~10-minute window, dead
    after any crash, and dead on unknown clocks (fail-closed: a handle
    of unknown provenance must not resume). Alive only for continuous
    runs and short sleeps within the window.
    """
    verdict = classify_dual_clock(last, now)
    kind = verdict["verdict"]
    if kind == "crash":
        return {"alive": False,
                "reason": "handle never survives a crash"}
    if kind == "unknown":
        return {"alive": False,
                "reason": "unknown clocks: fail closed, handle dead"}
    if kind == "sleep" and verdict["gap_s"] > window_s:
        return {"alive": False,
                "reason": "suspend longer than the %.0f s window"
                % window_s}
    return {"alive": True, "reason": "continuous run or short sleep"}


# ---------------------------------------------------------------------
# RL-7.2 DST-safe datetimes (native rule only + STUDY note)
# ---------------------------------------------------------------------

def store_utc(moment: datetime) -> datetime:
    """Store datetimes as aware UTC. Naive input is refused outright —
    a naive datetime has no zone, and guessing one is how DST bugs are
    born. Callers convert at display via :func:`display_local`.
    """
    if moment.tzinfo is None:
        raise ValueError("refusing naive datetime: pass an aware value")
    return moment.astimezone(timezone.utc)


def display_local(utc_moment: datetime, tz_name: str) -> datetime:
    """Convert stored UTC to local time, re-resolving the zone fresh
    on every call (never cached at startup, so a Windows timezone-change
    broadcast takes effect without a restart — the broadcast hookup
    itself is later wiring).

    STUDY note: the ``whenever`` library was evaluated for this rule
    and NOT taken; its LICENSE file read stays owed (README claims MIT,
    file never opened). This native rule is the shipped behaviour.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        zone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("unknown timezone: %s" % tz_name) from exc
    return utc_moment.astimezone(zone)
