# w4_wiring_trials.py — split from core/w4_wiring.py (Phase B B12): browser-agent trials + camoufox lane status.
# Move only; the facade in core/w4_wiring.py re-exports every name.

from __future__ import annotations  # keep def-time annotation semantics identical to the old module

from .w4_wiring_declarations import (  # INTENTIONAL (w4_wiring split B12): bare-name reads of the owed constants travel with the trial functions that read them.
    BROWSER_AGENT_LIVE_TRIALS_OWED,
    CAMOUFOX_DECISION_OWED,
)



# ---------------------------------------------------------------------------
# Item 5 — HE-2/HE-3 trial-invocation path (trial runner only)
# ---------------------------------------------------------------------------

def run_browser_agent_trial(pilot: Callable[..., Any], task: Any,
                            worker: Any, max_steps: int = 12) -> Any:
    """Run ONE pilot on ONE task through the staged harness's
    guarded adapter (password/CAPTCHA steps refused before
    dispatch, forbidden step kinds fail the trial). The
    returned TrialResult is live=False unless the live runner
    marks it with mark_live() on a Windows box — stub runs
    here can never feed a winner selection."""
    from .desktop import browser_agent_pilots as _pilots  # Phase 3 D2a: browser family moved to core/desktop/

    return _pilots.run_trial(pilot, task, worker, max_steps=max_steps)




def select_trial_winner(results: list[Any]) -> tuple[Any, str]:
    """Select the pilot-both winner — or defer with NO-DATA.

    This is a pass-through to the harness's select_winner,
    which requires LIVE results for BOTH pilots on the same
    task set. Offscreen (stub / one-sided / tied data) it
    returns (None, NO-DATA/TIE reason) and this module
    invents nothing on top of it. A winner here is still
    not a live feature: wiring only the winner behind a
    live browser-agent tool is a later, separate step.
    """
    from .desktop import browser_agent_pilots as _pilots  # Phase 3 D2a: browser family moved to core/desktop/

    return _pilots.select_winner(results)




def trial_loser_removal_plan(winner: Any) -> dict[str, Any]:
    """What happens to the loser once a live winner exists.

    At pilot stage (winner None) this is a PLAN (deferred,
    nothing removed, nothing declared). This function deletes
    nothing; the coordinator performs the removal and records
    it. Never wire both pilots as live features, never wire
    a loser.
    """
    from .desktop import browser_agent_pilots as _pilots  # Phase 3 D2a: browser family moved to core/desktop/

    return _pilots.loser_removal_plan(winner)




def browser_agent_trial_status() -> dict[str, Any]:
    """Trial state as data: which pilots are even importable
    on this box, and what is owed before any winner exists."""
    from .desktop import browser_agent_pilots as _pilots  # Phase 3 D2a: browser family moved to core/desktop/

    return {
        "pilots": ("browser-use", "stagehand"),
        "browser_use_available": _pilots.browser_use_available(),
        "stagehand_available": _pilots.stagehand_available(),
        "winner": None,
        "winner_reason": (
            "NO-DATA: pilot-both live trials on a Windows box "
            "with a real browser are owed."
        ),
        "live_feature_wired": False,
        "telemetry_note": _pilots.telemetry_guard_note(),
        "owed": BROWSER_AGENT_LIVE_TRIALS_OWED,
    }




# ---------------------------------------------------------------------------
# Item 6 — Camoufox lane status (verification only, never a launch)
# ---------------------------------------------------------------------------

def camoufox_lane_status(tree_root: Any = None) -> dict[str, Any]:
    """Verify the lane module is intact and report its state
    as data. Launches nothing, imports nothing beyond the
    lane module itself, and never touches her Chrome profile:
    the profile directory is computed, isolation is proven
    by assert_lane_isolation, and geoip stays permanently
    off (lane_launch_options(geoip=True) raises in the lane
    module — asserted by the suite, not called here, since
    launch options are the trial runner's data, not a wire).
    """
    from .desktop import browser_camoufox_lane as _lane  # Phase 3 D2a: browser family moved to core/desktop/

    profile = _lane.lane_profile_dir(tree_root)
    try:
        _lane.assert_lane_isolation(profile)
        isolation = "ok"
    except ValueError as exc:
        isolation = f"failed: {exc}"
    return {
        "wired": False,
        "lane_enabled": _lane.lane_enabled(),
        "camoufox_available": _lane.camoufox_available(),
        "profile_dir": str(profile),
        "isolation": isolation,
        "geoip": False,
        "forbidden": list(_lane.lane_forbidden()),
        "decision_owed": CAMOUFOX_DECISION_OWED,
    }
