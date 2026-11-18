from __future__ import annotations

import datetime  # INTENTIONAL (B31: moved code resolves datetime by bare name)
from . import wallpaper as _wallpaper  # INTENTIONAL (B31: moved code resolves _wallpaper by bare name)

# ------------------------------------------------------------------ #
# 5. Wallpaper schedule — resolution only, opt-in + pilot gated
# ------------------------------------------------------------------ #

def wallpaper_for_now(opt_in: bool, entries=None, hour: int | None = None):
    """Resolve the wallpaper state for an hour WITHOUT applying it.

    Applies nothing unless opt_in is True AND the pilot reports
    itself available AND the host is Windows — and the pilot is
    still PILOT, so even then this returns the resolution with
    applied=False and names the closing step. Pilot status is
    honoured: art the pilot reports unavailable is never applied.
    Returns {"state", "art", "applied", "reason"}.
    """
    if hour is None:
        hour = datetime.datetime.now().hour
    state = _wallpaper.state_for_hour(hour)
    art = None
    if entries:
        art = _wallpaper.resolve_art(state, entries)
    status = _wallpaper.pilot_status()
    if not opt_in:
        return {"state": state, "art": art, "applied": False,
                "reason": "wallpaper-opt-in-off"}
    if status.get("disposition") != "PILOT":
        return {"state": state, "art": art, "applied": False,
                "reason": "pilot-status-not-pilot"}
    return {"state": state, "art": art, "applied": False,
            "reason": ("pilot: Windows validation (apply + revert) "
                       "and explicit owner decision owed; nothing "
                       "applied by resolution")}
