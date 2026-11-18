"""Plan approval for direct multi-step requests
(UI rethink Build 4, plan §4.5).

Fix 8's approve-plan-once, extended beyond coordinator tasks
to ANY direct multi-step request: when the owner asks VYUK to
do several things ("set up the morning: open the briefing,
check the calendar, remind me at 9"), VYUK shows the plan
ONCE, in plain words, before running it — the safe steps this
approval would cover, and the steps that will still ask one
by one with the reason each — and the owner approves the plan
with one tap. Same shape as Fix 8 (per task, per step, exact
args digest, dies with the task, safe default is "Ask me each
time", Enter / close / 60 s silence = ask each time). It
covers direct multi-step requests, not single calls (a single
call still asks once), and it never covers a never-list step
(preview() puts those in "still individual" with the reason,
and approve_plan() re-checks eligibility before recording).

How it works: the text-goal entries (core/voice/
cloud_text_goal.py, core/voice/local_client_plangoal.py)
call plan_direct_goal() before run_goal(). The helper asks
the client's own picker-model for the full plan (one extra
model call, through the ask_plan seam each client supplies
with its own reply parsing), validates every step's tool
name against the live registry, and only when at least two
steps are approvable does it show the dialog. Any failure
— no handler, model failure, unparseable plan, fewer than
two approvable steps, decline, timeout, error — returns
(None, <outcome>) and the goal runs exactly as today, on
the picker alone with per-call asks. Nothing about the
goal loop changes when no plan is approved.

Boundary, stated honestly (§4.9 shape): the LIVE voice
loop is NOT extended — the model invents its steps turn by
turn there, so no upfront plan exists to show. Live
multi-step requests are covered by per-kind always-allow +
per-call asks, not by plan approval. The text-goal path
(typed/chat) is where a direct multi-step request lands as
a plan.
"""

from __future__ import annotations

import json
import types
import uuid


def plan_prompt(goal_text: str, candidate_tools) -> str:
    """The planning prompt: list the full plan as JSON.
    `candidate_tools` is the picker's ranked hint (or empty);
    the model may use any registered tool name exactly, and
    must not invent tools."""
    ranked = ", ".join(str(t) for t in (candidate_tools or []))
    return (
        "Plan the whole goal before doing anything, as JSON.\n"
        f"Goal: {goal_text}\n\n"
        f"Candidate tools: {ranked or '(none ranked)'}\n\n"
        'Reply with ONLY one JSON object: {"steps": '
        '[{"tool": "<registered tool name>", "args": {...}}, ...]} '
        "— one entry per step, in order, using a registered tool "
        "name exactly; do not invent tools. If the goal needs "
        "one step, list one. If it is already done, list no "
        "steps.")


def parse_plan_steps(reply_text, valid_tools) -> list:
    """The model's plan reply as validated step dicts
    [{tool, args}]. Accepts {"steps": [...]} or a bare [...]
    list; anything else (a single action, prose, bad JSON)
    reads as no plan. A step whose tool is not a registered
    name, or whose args are not an object, is dropped —
    never invented, never guessed. Never raises."""
    try:
        text = str(reply_text or "").strip()
        if not text:
            return []
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return []
    if isinstance(parsed, dict):
        steps = parsed.get("steps")
    elif isinstance(parsed, list):
        steps = parsed
    else:
        return []
    if not isinstance(steps, list):
        return []
    valid = set(valid_tools or ())
    out = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        tool = str(step.get("tool", "") or "").strip()
        if not tool or tool not in valid:
            continue
        args = step.get("args", {})
        if not isinstance(args, dict):
            continue
        out.append({"tool": tool, "args": dict(args)})
    return out


def _step_defs(steps: list):
    """StepDef list for the plan-approval machinery (the
    same shape _re_gate_remaining builds). Falls back to
    plain namespace objects when StepDef is unavailable —
    preview() and approve_plan() only read .tool / .args /
    .label."""
    try:
        from ..brain.task_coordinator_steps import StepDef
    except Exception:  # noqa: BLE001 — plain objects below
        StepDef = None
    defs = []
    for step in steps:
        if StepDef is not None:
            defs.append(StepDef(
                tool=str(step["tool"]),
                args=dict(step["args"]),
                label=str(step["tool"])))
        else:
            defs.append(types.SimpleNamespace(
                tool=str(step["tool"]),
                args=dict(step["args"]),
                label=str(step["tool"])))
    return defs


async def plan_direct_goal(goal_text, *, ask_plan, emit=None):
    """The direct-plan pass for one text goal.

    ``ask_plan(goal_text)`` is the client's model seam: it
    returns the validated step list (its own reply parsing;
    [] on any failure). Returns ((task_id, [StepDef]) | None,
    outcome) where outcome is one of: "approved",
    "ask_each_time", "timeout", "no_handler", "error",
    "too_few_steps", "no_plan". A None pair always means
    "run the goal exactly as today" — the caller passes it
    as run_goal(plan_approval=None). Never raises."""
    try:
        from .. import plan_approval as _pa
    except Exception:  # noqa: BLE001 — no plan machinery
        return None, "error"
    try:
        if not _pa.has_plan_handler():
            # No dialog exists to approve with: skip the
            # model call entirely — a plan nobody can
            # approve is pure overhead.
            return None, "no_handler"
    except Exception:  # noqa: BLE001 — no handler: no plan
        return None, "no_handler"
    try:
        steps = await ask_plan(str(goal_text or ""))
    except Exception:  # noqa: BLE001 — model failure: no plan
        return None, "no_plan"
    if not isinstance(steps, list) or not steps:
        return None, "no_plan"
    try:
        defs = _step_defs(steps)
        task_id = uuid.uuid4().hex
        task = types.SimpleNamespace(
            task_id=task_id, title=str(goal_text or "task"),
            steps=defs)
        preview = _pa.preview(task)
    except Exception:  # noqa: BLE001 — no plan: today's behaviour
        return None, "error"
    approvable = preview.get("approvable") or []
    if len(approvable) < 2:
        # A single call still asks once — that is one pop,
        # not "almost everything". No dialog for one step.
        return None, "too_few_steps"
    try:
        indices, outcome = _pa.request_plan_decision_ex(task)
    except Exception:  # noqa: BLE001 — the dialog failed
        return None, "error"
    if outcome == "approved" and indices:
        try:
            # approve_plan re-checks eligibility and REFUSES
            # any index it will not record (a never-list step
            # smuggled in by the dialog's return, a file
            # created since preview, a bad digest). The pair
            # carries ONLY the indices approve_plan actually
            # recorded — never the dialog's raw list.
            result = _pa.approve_plan(task_id, defs, indices)
            recorded = result.get("approved", []) or []
        except Exception:  # noqa: BLE001 — record write is best-effort
            recorded = []
        approved = [defs[i] for i in recorded
                    if isinstance(i, int) and 0 <= i < len(defs)]
        if not approved:
            return None, "ask_each_time"
        if callable(emit):
            try:
                emit(f"Plan approved — {len(approved)} step"
                     f"{'s' if len(approved) != 1 else ''} covered, "
                     "the rest will ask one by one.")
            except Exception:  # noqa: BLE001 — progress is courtesy
                pass
        return (task_id, approved), "approved"
    if outcome == "timeout" and callable(emit):
        try:
            emit("No answer to the plan — asking step by step instead.")
        except Exception:  # noqa: BLE001 — progress is courtesy
            pass
    return None, (outcome or "ask_each_time")
