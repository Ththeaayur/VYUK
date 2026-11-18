"""w8_wiring part module (skills split B14): w8_wiring_sleeptime."""
from __future__ import annotations

# Module-level imports travel verbatim with the units that use them.
from . import sleeptime



# ---------------------------------------------------------------------
# Sleeptime idle pass: wired, never from a voice path (item 9)
# ---------------------------------------------------------------------


def run_sleeptime_pass(candidates=(), *, base=None, writer=None,
                       from_voice_path: bool = False) -> dict:
    """One sleeptime idle pass (idle_pass) — file-store seam
    only, zero writes on quiet days. REFUSES when called from
    a voice path (from_voice_path=True): sleeptime never runs
    on a voice thread. Triggered by the watcher away-episode
    hook via maybe_sleeptime_on_idle (gui/window_memory.
    _init_watchers); the candidate source is gated
    (SLEEPTIME_CANDIDATE_GATE)."""
    if from_voice_path:
        return {"writes": 0, "refused": True,
                "detail": "sleeptime never runs from a voice path"}
    return sleeptime.idle_pass(base=base, candidates=candidates,
                                    writer=writer)




def maybe_sleeptime_on_idle(idle_seconds, threshold_seconds: float = 600.0,
                            candidates=(), **kwargs) -> dict:
    """Idle-gated sleeptime entry: runs idle_pass ONLY when a
    caller-supplied idle reading meets the threshold. The
    reading is supplied by the watcher away-episode hook
    (WatcherEngine sleeptime_hook, wired in
    gui/window_memory._init_watchers) — this module invents
    no idle signal. Returns a no-op report below threshold."""
    try:
        idle = float(idle_seconds)
    except (TypeError, ValueError):
        return {"writes": 0, "refused": True,
                "detail": "no usable idle reading supplied"}
    if idle < threshold_seconds:
        return {"writes": 0, "refused": False,
                "detail": f"idle {idle}s below {threshold_seconds}s "
                          "threshold; no sleeptime pass"}
    return run_sleeptime_pass(candidates=candidates, **kwargs)


