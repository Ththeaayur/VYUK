"""Wave 6 integration — session-only staged mission plans.

The ONLY new mechanism in the Wave 6 wiring spec (WAVE6_WIRING_SPEC
§5.3): a tool row is synchronous and can never run a multi-minute
coordinator job, so ``mission_run_job`` STAGES a validated plan
here and the session's next top-level goal run consumes it once
through the AGENT coordinator (see the ``plan`` parameter on
``cloud_text_client.run_goal`` / ``local_client.run_goal``).

Session-only, in-memory, never persisted: cleared on consume
and on session restart. A plan lost to a restart is honestly
reported as lost, never resurrected — there is no pending-job
file, and adding one would be a new store decision with its own
gate (owner-vetoable spec decision 4), not a silent upgrade.

Consumption rule (parent decision, recorded here): a staged
plan is consumed only by a goal run for the SAME goal text (or
a staged plan with no goal). A goal run for a different goal
leaves the staged plan untouched — a staged plan must never
hijack an unrelated goal. A staged plan inside a running loop
does NOT hijack that loop: it waits for the next top-level
goal run.

This module imports nothing and decides nothing: it holds a
plan, hands it back once, and forgets it. It is not a gate
owner — every refusal in the Wave 6 wiring traces to
``tools_gate`` classification, ``confirm_gate`` approval, the
``resilience_gate`` trip, or a staging module's own
fail-closed validation.
"""

from __future__ import annotations

_STAGED = None


def stage_plan(goal, plan, brief=None, dial="consequential-only"):
    """Stage one validated plan for the next top-level goal run.

    ``plan`` is the AGENT split-plan list (already validated by
    the caller through ``split_plan``); ``brief`` is the mission
    brief dict or None; ``dial`` is the delegation level string.
    Staging overwrites any previously staged plan — only one
    plan can be pending, and the newest staged plan is the one
    that runs, so a stale plan can never run by surprise after
    a newer stage. Returns a summary, never the plan itself
    (the plan is consumed, not re-read, by the goal run).
    """
    global _STAGED
    _STAGED = {
        "goal": str(goal or "").strip(),
        "plan": list(plan),
        "brief": dict(brief) if isinstance(brief, dict) else brief,
        "dial": str(dial or "consequential-only"),
    }
    names = []
    for item in plan:
        if isinstance(item, dict):
            names.append(str(item.get("name") or ""))
        else:
            names.append(str(getattr(item, "name", "") or ""))
    return {
        "staged": True,
        "goal": _STAGED["goal"],
        "sub_goals": [n for n in names if n],
        "dial": _STAGED["dial"],
    }


def take_staged_plan():
    """Consume the staged plan once, clearing the holder.

    Returns the staged dict, or None when nothing is staged.
    A second call without a new stage returns None — the plan
    runs once, never twice.
    """
    global _STAGED
    staged, _STAGED = _STAGED, None
    return staged


def peek_staged_plan():
    """Read the staged plan without consuming it (tests / UI)."""
    if _STAGED is None:
        return None
    return dict(_STAGED)


def clear_staged_plan():
    """Drop the staged plan without running it."""
    global _STAGED
    _STAGED = None


def consume_plan_for_goal(goal_text):
    """Consume the staged plan for this goal run, if it matches.

    Returns the staged dict when the staged goal equals this
    goal run's goal text (or the staged goal is empty), and
    consumes it. Returns None — leaving the staged plan
    untouched — for any other goal text, so an unrelated goal
    run never hijacks or destroys a staged mission.
    """
    if _STAGED is None:
        return None
    staged_goal = str(_STAGED.get("goal") or "").strip()
    if staged_goal and staged_goal != str(goal_text or "").strip():
        return None
    return take_staged_plan()


__all__ = [
    "stage_plan",
    "take_staged_plan",
    "peek_staged_plan",
    "clear_staged_plan",
    "consume_plan_for_goal",
]
