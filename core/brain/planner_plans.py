# planner_plans.py — planner family split out of
# _execution_succeeded moved here from the checkpoint region:
# it is the ONLY plans->checkpoint edge, and moving this leaf
# predicate breaks the plans<->checkpoint import cycle.
# core/brain/planner.py (skills split B41, part 2 of 2).
# Bodies below are AST-verbatim from the old module; only the
# import block above is new. Cross-family names are imported
# with INTENTIONAL comments, never redefined.

import inspect
import json
from .planner_storage import (
    MAX_PLAN_STEPS,
    _PLANS_PATH,
    _damaged_note,
    _json_args,
    _load_plans,
    _normalise_on_fail,
    _normalise_when,
    _now,
    _save_list,
    _short,
)
from ..funnel.tool_result import (
    LEGACY_ERROR_STATUS as _LEGACY_ERROR_STATUS,
    REFUSAL_MARKERS as _REFUSAL_MARKERS,
    normalized_error_status as _normalized_error_status,
)




# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


def _normalise_check(item: dict) -> dict | None:
    """A per-action/step result check (v5.45, B12), normalised to
    {"contains": str?, "status": str?}. Accepted shapes: a nested
    "check"/"expect" dict {contains|expect_contains|substring,
    status|expect_status}, or flat expect_contains / expect_status
    keys on the item itself. Anything else yields None (no check —
    the pre-B12 behaviour)."""
    raw = None
    for key in ("check", "expect"):
        if isinstance(item.get(key), dict):
            raw = item[key]
            break
    flat = {}
    if raw:
        flat.update(raw)
    for key in ("expect_contains", "expect_status", "contains", "status"):
        if item.get(key) not in (None, "") and key not in flat:
            flat[key] = item[key]
    if not flat:
        return None
    check: dict = {}
    contains = (flat.get("contains") or flat.get("expect_contains")
                or flat.get("substring"))
    if isinstance(contains, str) and contains:
        check["contains"] = contains
    status = flat.get("status") or flat.get("expect_status")
    if isinstance(status, str) and status:
        check["status"] = status.strip().lower()
    return check or None



def check_result(check: dict | None, result) -> tuple[bool, str]:
    """Evaluates a B12 result check against one tools.execute()
    outcome. Returns (passed, reason). `result` may be the
    execute dict or a bare result string. A check passes only
    when EVERY declared condition holds: the expected substring
    appears in the result text (case-insensitive), and/or the
    execute status equals the expected status. No check -> pass."""
    if not check:
        return True, ""
    if isinstance(result, dict):
        status = str(result.get("status") or "")
        text = str(result.get("result") or result.get("message") or "")
    else:
        status = "success"
        text = str(result or "")
    expected_status = check.get("status")
    if expected_status and status.lower() != expected_status:
        return False, (f"expected status '{expected_status}', "
                       f"got '{status or 'unknown'}'")
    contains = check.get("contains")
    if contains and contains.lower() not in text.lower():
        return False, (f"expected the result to contain "
                       f"'{contains}'; it said: {_short(text, 100)}")
    return True, ""



# ---------------------------------------------------------------------------
# v5.49 conditionals + verification depth (programme items 31 + 32)
#
# `when` / `on_fail` (item 32): a plan step or routine action may
# carry a `when` probe — {"tool": name, "args"?: {...},
# "expect_contains"?: str, "expect_status"?: str} — evaluated by
# the DRIVERS ONLY (run_plan / run_routine / run_goal), through the
# same tools.execute() funnel as any action, all gates intact.
# When the probe does not pass, `on_fail` decides what happens:
# "skip" (the default for when-failures), "halt", or
# {"goto": <1-based step number>}. For a failed ACTION the default
# stays today's halt; on_fail "skip"/{"goto": n} overrides it.
# Invalid when/on_fail values are rejected at parse time
# (_parse_steps / _parse_actions) and mark a stored record damaged
# at load (_conditional_fields_valid via the validators above).
#
# `expect` (item 31): a FREE-TEXT expectation string on a step,
# distinct from B12's expect_contains/expect_status result checks
# (an `expect` DICT stays a B12 check — see _normalise_check).
# The drivers ask an injected verify_expect callable —
# (expect_text, before_snap, after_snap, result) ->
# (verdict, reason), verdict in {"met", "not-met", "unverifiable"}
# — and record the verdict on the step. No callable injected (or a
# callable that fails) means "unverifiable", which never strikes
# and never changes today's behaviour. Three "not-met" verdicts on
# the SAME step halt the walk with a report (three-strikes).
# ---------------------------------------------------------------------------

_EXPECT_STRIKE_LIMIT = 3



def _runtime_when(item: dict) -> tuple[dict | None, str | None]:
    """A step/action's `when` at run time (records can be hand-
    edited past the parse boundary, so the drivers re-normalise)."""
    if item.get("when") is None:
        return None, None
    return _normalise_when(item.get("when"))



def _runtime_on_fail(item: dict) -> tuple[object, str | None]:
    """A step/action's `on_fail` at run time; (None, error) when
    a hand-edited value is malformed."""
    if item.get("on_fail") is None:
        return None, None
    return _normalise_on_fail(item.get("on_fail"))



def _expect_text(item: dict) -> str | None:
    """The free-text `expect` of a step/action (v5.49, item 31):
    a STRING expect only — a dict expect is B12's check shape."""
    raw = item.get("expect")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None



def _normalise_verdict(raw) -> str:
    """A verify_expect verdict, normalised to met / not-met /
    unverifiable. Anything unrecognised is unverifiable — an
    unparseable verdict must never strike or halt a walk."""
    value = str(raw or "").strip().lower().replace("_", "-")
    if value in ("met", "not-met", "unverifiable"):
        return value
    return "unverifiable"



def _verify_sync(verify_expect, expect_text, before_snap, after_snap,
                 result) -> tuple[str, str]:
    """Call a verify_expect callable from a SYNC driver
    (run_routine). A callable that raises, returns a malformed
    pair, or returns an awaitable it cannot await here degrades
    to ("unverifiable", reason) — never a crash, never a strike."""
    if verify_expect is None:
        return "unverifiable", "no verification callback was supplied"
    try:
        outcome = verify_expect(expect_text, before_snap, after_snap, result)
        if inspect.isawaitable(outcome):
            try:
                outcome.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            return "unverifiable", "verification callback is async; the sync driver cannot await it"
        verdict, reason = outcome
        return _normalise_verdict(verdict), str(reason or "")
    except Exception as e:  # noqa: BLE001 — verification never breaks a walk
        return "unverifiable", f"verification callback failed: {e}"



async def _verify_async(verify_expect, expect_text, before_snap, after_snap,
                        result) -> tuple[str, str]:
    """_verify_sync for the async drivers (run_plan / run_goal):
    sync and async callables are both accepted."""
    if verify_expect is None:
        return "unverifiable", "no verification callback was supplied"
    try:
        outcome = verify_expect(expect_text, before_snap, after_snap, result)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        verdict, reason = outcome
        return _normalise_verdict(verdict), str(reason or "")
    except Exception as e:  # noqa: BLE001 — verification never breaks a walk
        return "unverifiable", f"verification callback failed: {e}"



def _record_expect_verdict(step: dict, verdict: str, reason: str) -> None:
    """Persist one expect verdict on a plan step (v5.49, item 31):
    last verdict + reason, an append-only history, and the
    not-met strike count (a met verdict resets the strikes — the
    three-strikes rule counts CONSECUTIVE verification failures
    of the same step's action)."""
    step["expect_verdict"] = verdict
    step["expect_reason"] = reason
    history = step.get("expect_history")
    if not isinstance(history, list):
        history = []
        step["expect_history"] = history
    history.append({"verdict": verdict, "reason": reason, "at": _now()})
    if verdict == "not-met":
        step["expect_strikes"] = int(step.get("expect_strikes") or 0) + 1
    elif verdict == "met":
        step["expect_strikes"] = 0



def _probe_snapshot():
    """One desktop snapshot for verification/observation, via
    core.desktop.uia.desktop_snapshot. None when the probe has no eyes
    here (non-Windows, probe failure) — never raises."""
    try:
        from ..desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        return uia.desktop_snapshot()
    except Exception:  # noqa: BLE001
        return None



def _execute_tool(tool: str, args, cancel_token=None) -> dict:
    """The ONE execution funnel for the v5.49 drivers: the live
    registry's execute(), with the full gate stack (confirm gate,
    injection taint, scoped approvals, executor limits) exactly
    as a live tool call gets it. Lazy import, like run_routine's:
    the funnel registry imports this module at registration time. Suites
    fake the seam by patching the registry's execute or this
    helper — both reach the same funnel in production.

    B20: ``cancel_token`` is the goal loop's per-step token
    (planner_rungoal.live_token) — passed straight to the
    registry, which hands it ONLY to handlers that declare a
    `cancel_token` parameter. None (the default) keeps the
    old per-call token behaviour exactly. A cancelled result
    is returned as-is: the caller (run_goal) turns it into a
    cancelled goal, never a failure, never a retry (B12:
    "cancelled" is not a transient kind). The token is
    passed ONLY when supplied: test fakes of registry.execute
    take two arguments, and an unconditional third argument
    would break every one of them (the coordinator's
    _executor_accepts_token rule, same reason)."""
    from ..funnel.tools_registry import tools as registry  # Phase 4 S1b INTENTIONAL: registry split to funnel

    if cancel_token is None:
        return registry.execute(tool, dict(args or {}))
    return registry.execute(tool, dict(args or {}),
                            cancel_token=cancel_token)



def _known_tools() -> set | None:
    """Registered tool names, or None when the registry cannot be
    read (callers then let execute() itself report the problem)."""
    try:
        from ..funnel.tools_registry import tools as registry  # Phase 4 S1b INTENTIONAL: registry split to funnel

        return {d["name"] for d in registry.get_declarations()}
    except Exception:  # noqa: BLE001
        return None



def _is_unknown_outcome(result) -> bool:
    """Audit A5: True when a funnel result's outcome is unknown
    (a timeout, or an explicit outcome="unknown"). Used by the
    checkpoint adapter so no layer above auto-retries it."""
    return isinstance(result, dict) and (
        result.get("status") in ("timeout", "unknown")
        or result.get("outcome") == "unknown")



def _failure_reason(result) -> str:
    """Why an execute() outcome did not do the thing."""
    if isinstance(result, dict) and result.get("status") == "success":
        return f"refused: {_short(str(result.get('result', '')))}"
    if isinstance(result, dict):
        return str(result.get("message", "unknown error"))
    return _short(str(result or ""))



# B12 (2026-11-07): transient vs genuine-refusal, read ONLY from
# the funnel's own taxonomy (core/funnel/tool_result.py) — never
# a guessed sentence. The retry decision in run_goal reads this
# and nothing else:
#   * status "refused" (any kind) — a gate prevented execution;
#     the call never ran. Never retried.
#   * status "timeout" / "unknown" — the outcome is UNKNOWN
#     (audit A5: the timeout message says plainly not to
#     auto-retry until reconciled, and _is_unknown_outcome
#     exists so no layer above auto-retries it). A retry could
#     double-execute an action that already landed. Never
#     retried. This CORRECTS the audit's loose "timeout"
#     example: a deadline timeout is not a retryable blip.
#   * status "success" carrying a REFUSAL_MARKERS sentence — a
#     refusal returned as prose (_failure_reason renders it
#     "refused: ..."). Never retried.
#   * status "failure", kind "handler_error" — the handler ran
#     and raised. Network blips, app-busy and file-locked all
#     surface here: tools_registry_exec_dispatch._audited maps
#     a raised exception to kind _handler_error_kind, and
#     anything that is not a timeout or a missing dependency
#     lands as handler_error. TRANSIENT — one retry.
#   * status "failure", kind "unavailable" — a dependency,
#     program or bundled asset the tool needs is missing
#     (ImportError / FileNotFoundError from the tool's own
#     machinery), or a domain-routing error (unknown tool /
#     router). The identical call fails identically. Not
#     transient — halts.
#   * status "failure", kind "wallpaper_not_set" — a persistent
#     state (file missing, monitor gone, SPI refused); the
#     R5/B1 design pauses for a human fix because the identical
#     call fails identically. Not transient — halts.
#   * status "failure" with any other kind, or no kind, or a
#     non-dict result — unclassifiable. Fail-closed: halts (the
#     pre-B12 behaviour). A new kind that proves transient
#     belongs in _TRANSIENT_FAILURE_KINDS with its evidence.
#   * legacy status "error" — mapped by kind exactly as the
#     funnel maps it (normalized_error_status): refusal kinds
#     -> refused, anything else -> failure, then the failure
#     rule above.
_TRANSIENT_FAILURE_KINDS = frozenset({"handler_error"})


def _is_transient_failure(result) -> bool:
    """True when a failed execute() outcome may be retried once
    (B12) — status "failure" with a transient kind, and nothing
    else. A refusal, a timeout/unknown outcome, a refusal
    sentence, and every persistent or unclassifiable failure
    all return False, so the goal halts exactly as before."""
    if not isinstance(result, dict):
        return False
    status = result.get("status")
    if status == _LEGACY_ERROR_STATUS:
        status = _normalized_error_status(result.get("kind"))
    if status != "failure":
        return False
    return result.get("kind") in _TRANSIENT_FAILURE_KINDS


def _is_confirm_denial(result) -> bool:
    """True when an execute() outcome is a HUMAN confirm
    denial (M-23 / §2.5 pause_on_denial) — the confirm gate's
    audited not_confirmed dict: status "refused" (or the
    legacy "error" that normalises to it) with kind
    "not_confirmed". A denial covers both an explicit No and
    a confirm timeout (default-deny). Narrow on purpose:
    kind "refused" alone is every gate (failsafe, kill-chain,
    validation, budget) and is NOT a human denial, so those
    still halt as before."""
    if not isinstance(result, dict):
        return False
    status = result.get("status")
    if status == _LEGACY_ERROR_STATUS:
        status = _normalized_error_status(result.get("kind"))
    return status == "refused" and result.get("kind") == "not_confirmed"



def _eval_when(when: dict, known_tools) -> tuple[bool, str]:
    """Evaluate a `when` probe through tools.execute (v5.49,
    item 32) — all gates intact, exactly like an action. Passes
    iff the probe tool succeeds (no refusal) AND every declared
    expect_contains / expect_status holds."""
    tool = when.get("tool", "")
    if known_tools is not None and tool not in known_tools:
        return False, f"the 'when' tool '{tool}' is not a registered tool"
    if tool in ("run_routine", "run_plan"):
        return False, f"the 'when' tool '{tool}' cannot nest plans/routines"
    result = _execute_tool(tool, when.get("args") or {})
    if not _execution_succeeded(result):
        return False, f"the 'when' probe '{tool}' did not succeed ({_failure_reason(result)})"
    check: dict = {}
    if when.get("expect_contains"):
        check["contains"] = when["expect_contains"]
    if when.get("expect_status"):
        check["status"] = when["expect_status"]
    passed, reason = check_result(check or None, result)
    if not passed:
        return False, f"the 'when' probe '{tool}' result check failed ({reason})"
    return True, _short(str(result.get("result", "") if isinstance(result, dict) else result or ""), 100)



def _parse_steps(steps) -> tuple[list[dict], str | None]:
    """Normalise steps (list of strings/dicts, or a JSON string of one)
    into stored step dicts. Returns (steps, error_or_None)."""
    if steps is None:
        return [], "No steps were given — a plan needs at least one step."
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except Exception:  # noqa: BLE001
            steps = [steps]  # a bare string is one plain-text step
    if not isinstance(steps, list) or not steps:
        return [], "Steps must be a non-empty list of step texts (or step objects with a 'text' field)."
    if len(steps) > MAX_PLAN_STEPS:
        return [], f"Too many steps: {len(steps)}. A plan is capped at {MAX_PLAN_STEPS} steps."
    parsed: list[dict] = []
    for i, item in enumerate(steps):
        if isinstance(item, str):
            text = item.strip()
            if not text:
                return [], f"Step {i + 1} is empty."
            parsed.append({"text": text, "tool": None, "args": None, "status": "pending"})
        elif isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            if not text:
                return [], f"Step {i + 1} has no 'text'."
            tool = item.get("tool")
            args = item.get("args")
            step = {
                "text": text,
                "tool": str(tool) if tool else None,
                "args": args if isinstance(args, dict) else _json_args(args),
                "status": "pending",
            }
            check = _normalise_check(item)
            if check:
                step["check"] = check
            # v5.49 (programme items 31 + 32): a free-text expect
            # string and the when/on_fail conditionals ride on the
            # step; invalid conditional values reject the plan at
            # parse, so a stored plan's conditionals are always
            # well-formed.
            expect_text = _expect_text(item)
            if expect_text:
                step["expect"] = expect_text
            when, when_error = _normalise_when(item.get("when"))
            if when_error:
                return [], f"Step {i + 1}: {when_error}"
            if when:
                step["when"] = when
            on_fail, fail_error = _normalise_on_fail(item.get("on_fail"))
            if fail_error:
                return [], f"Step {i + 1}: {fail_error}"
            if on_fail is not None:
                step["on_fail"] = on_fail
            parsed.append(step)
        else:
            return [], f"Step {i + 1} is neither text nor an object: {item!r}"
    # v5.49 (programme item 32): a goto must land inside the plan.
    for i, step in enumerate(parsed):
        on_fail = step.get("on_fail")
        if isinstance(on_fail, dict) and not 1 <= on_fail["goto"] <= len(parsed):
            return [], (f"Step {i + 1} has on_fail goto {on_fail['goto']}, "
                        f"but the plan has only {len(parsed)} step(s).")
    return parsed, None



def _next_plan_id(plans: list) -> str:
    highest = 0
    for p in plans:
        pid = str(p.get("id", ""))
        if pid.startswith("plan-"):
            try:
                highest = max(highest, int(pid.split("-", 1)[1]))
            except ValueError:
                pass
    return f"plan-{highest + 1}"



def _find_plan(plans: list, plan_id) -> dict | None:
    wanted = str(plan_id).strip().lower()
    for p in plans:
        if str(p.get("id", "")).lower() == wanted:
            return p
    return None



def _progress(plan: dict) -> tuple[int, int]:
    steps = plan.get("steps", [])
    done = sum(1 for s in steps if s.get("status") == "done")
    return done, len(steps)



def create_plan(goal: str, steps=None) -> str:
    """Create a multi-step plan working toward a goal."""
    goal = str(goal or "").strip()
    if not goal:
        return "Cannot create a plan without a goal."
    parsed, error = _parse_steps(steps)
    if error:
        return f"Could not create plan: {error}"
    plans, damaged = _load_plans()
    plan = {
        "id": _next_plan_id(plans),
        "goal": goal,
        "created_at": _now(),
        "steps": parsed,
    }
    plans.append(plan)
    if not _save_list(_PLANS_PATH, plans):
        return "Could not create plan: failed to write data/plans.json."
    lines = [f"Plan {plan['id']} created: {goal} ({len(parsed)} steps)"]
    for i, s in enumerate(parsed):
        via = f" [via {s['tool']}]" if s.get("tool") else ""
        lines.append(f"  {i + 1}. {s['text']}{via}")
    if damaged:
        lines.append(_damaged_note(damaged))
    return "\n".join(lines)



def list_plans() -> str:
    """List all plans with step progress."""
    plans, damaged = _load_plans()
    if not plans:
        base = "No plans yet. Create one with create_plan."
        return f"{base}\n{_damaged_note(damaged)}" if damaged else base
    lines = [f"{len(plans)} plan(s):"]
    for p in plans:
        done, total = _progress(p)
        state = " — complete" if total and done == total else ""
        lines.append(f"  {p.get('id')}: {p.get('goal')} ({done}/{total} steps done){state}")
    if damaged:
        lines.append(_damaged_note(damaged))
    return "\n".join(lines)



def get_plan(plan_id: str) -> str:
    """Show one plan's full step list with statuses.

    Step numbers are 1-based everywhere (create_plan, get_plan and
    update_plan_step all agree) — the number printed here is the
    number update_plan_step expects. Steps are stored as an ordered
    list with no indices, so this convention needs no data
    migration: stored plans stay exactly as interpretable as before.
    """
    plans, damaged = _load_plans()
    plan = _find_plan(plans, plan_id)
    if plan is None:
        msg = f"No plan found with id '{plan_id}'. Use list_plans to see existing plans."
        return f"{msg}\n{_damaged_note(damaged)}" if damaged else msg
    marks = {"done": "✓", "skipped": "–", "pending": "•"}
    done, total = _progress(plan)
    lines = [f"Plan {plan.get('id')}: {plan.get('goal')} ({done}/{total} done)"]
    for i, s in enumerate(plan.get("steps", []), start=1):
        via = f" [tool: {s['tool']}]" if s.get("tool") else ""
        lines.append(f"  {marks.get(s.get('status'), '•')} {i}. {s.get('text')}{via} ({s.get('status')})")
    lines.append("Step numbers start at 1 — use them with update_plan_step.")
    if damaged:
        lines.append(_damaged_note(damaged))
    return "\n".join(lines)



# Step statuses a crashed/interrupted run can leave behind
# (v5.49, programme item 32): run_plan marks a step "running"
# before executing it and persists that, so a process that dies
# mid-step leaves exactly this trace. Such a step was NOT
# verified done — a resume re-runs it, never skips it and never
# counts it.
_RESUMABLE_STATUSES = frozenset(
    {"running", "crashed", "interrupted", "in_progress", "failed"})



def plan_progress(plan_id: str) -> dict:
    """Resume API (v5.49, programme item 32): where a plan stands,
    as data — per-step status + verification record + recorded
    expect verdict (item 31), and the cursor: the 1-based index
    of the first step that is not done/skipped, i.e. exactly
    where run_plan(plan_id) resumes. A step left "running" by a
    dead process reports as resumable, NOT done: it is re-run on
    resume, never skipped and never double-counted. Returns
    {"found": False, ...} for an unknown plan id."""
    plans, damaged = _load_plans()
    plan = _find_plan(plans, plan_id)
    if plan is None:
        return {"found": False, "plan_id": str(plan_id),
                "error": f"No plan found with id '{plan_id}'. "
                         "Use list_plans to see existing plans.",
                "damaged_records_ignored": damaged}
    steps_out: list[dict] = []
    cursor = None
    done = skipped = 0
    for i, step in enumerate(plan.get("steps", []), start=1):
        status = str(step.get("status") or "pending")
        if status == "done":
            done += 1
        elif status == "skipped":
            skipped += 1
        elif cursor is None:
            cursor = i
        steps_out.append({
            "index": i,
            "text": step.get("text"),
            "tool": step.get("tool"),
            "status": status,
            "resumable": status in _RESUMABLE_STATUSES,
            "verification": step.get("verification"),
            "expect": _expect_text(step),
            "expect_verdict": step.get("expect_verdict"),
            "expect_reason": step.get("expect_reason"),
            "expect_strikes": int(step.get("expect_strikes") or 0),
        })
    total = len(steps_out)
    return {
        "found": True,
        "plan_id": plan.get("id"),
        "goal": plan.get("goal"),
        "total": total,
        "done": done,
        "skipped": skipped,
        "pending": total - done - skipped,
        "complete": total > 0 and cursor is None,
        "cursor": cursor,
        "steps": steps_out,
        "damaged_records_ignored": damaged,
    }



def _set_step_status(plan_id, step_index, status: str) -> str:
    plans, damaged = _load_plans()
    plan = _find_plan(plans, plan_id)
    if plan is None:
        msg = f"No plan found with id '{plan_id}'. Use list_plans to see existing plans."
        return f"{msg}\n{_damaged_note(damaged)}" if damaged else msg
    try:
        idx = int(step_index)
    except (TypeError, ValueError):
        return f"Step index must be a number, got '{step_index}'."
    steps = plan.get("steps", [])
    # Step numbers are 1-based, matching create_plan/get_plan output:
    # the number the user or brain read off the plan is the number
    # they pass back here. (Storage is an ordered list — no indices
    # are persisted, so nothing about old plans changes meaning.)
    if idx < 1 or idx > len(steps):
        return f"Plan {plan.get('id')} has {len(steps)} steps (numbered 1–{len(steps)}); step {idx} doesn't exist."
    steps[idx - 1]["status"] = status
    if not _save_list(_PLANS_PATH, plans):
        return "Could not update the plan: failed to write data/plans.json."
    done, total = _progress(plan)
    verb = "completed" if status == "done" else "skipped"
    return f"Step {idx} of plan {plan.get('id')} marked {verb}: \"{steps[idx - 1].get('text')}\" ({done}/{total} done)."



def complete_step(plan_id: str, step_index) -> str:
    """Mark one plan step as done."""
    return _set_step_status(plan_id, step_index, "done")



def skip_step(plan_id: str, step_index) -> str:
    """Mark one plan step as skipped."""
    return _set_step_status(plan_id, step_index, "skipped")



def update_plan_step(action: str, plan_id: str, step_index) -> str:
    """Registry-facing dispatcher: action is 'complete' or 'skip'."""
    act = str(action or "").strip().lower()
    if act in ("complete", "done", "completed"):
        return complete_step(plan_id, step_index)
    if act in ("skip", "skipped"):
        return skip_step(plan_id, step_index)
    return f"Unknown plan-step action: '{action}'. Use 'complete' or 'skip'."



def delete_plan(plan_id: str) -> str:
    """Delete a plan entirely."""
    plans, damaged = _load_plans()
    plan = _find_plan(plans, plan_id)
    if plan is None:
        msg = f"No plan found with id '{plan_id}'. Use list_plans to see existing plans."
        return f"{msg}\n{_damaged_note(damaged)}" if damaged else msg
    plans.remove(plan)
    if not _save_list(_PLANS_PATH, plans):
        return "Could not delete the plan: failed to write data/plans.json."
    return f"Plan {plan.get('id')} deleted: \"{plan.get('goal')}\"."



# run_plan() is the driver B15 asks for. DESIGN CALL (documented
# per the item): a registry tool for it would take the registry
# past its pinned 477 count, so the driver is a planner-level
# async routine the clients expose (LocalLiveSession.run_plan,
# GroqSession.run_plan, GeminiLiveSession.run_plan): each client
# supplies `pick_tool` — ONE model call per undeclared step
# through its own chat path — and `emit` for progress lines.
# Steps that declare a tool (and args) run those directly, no
# model call needed. Every execution goes through
# tools.execute ONLY (confirmation gate + injection taint
# inherited unchanged), the failsafe is checked before EVERY
# step, and a refusal / error / check mismatch HALTS the walk
# with the progress so far. Each step's verification outcome is
# recorded: the tool result text itself (which carries H5's
# post-action probe sentence when a probe ran) plus the B12
# check verdict, stored on the step as "verification".
# ---------------------------------------------------------------------------

def _execution_succeeded(result) -> bool:
    """Did this tools.execute outcome actually DO the thing?
    Status must be success, and a refusal sentence returned as a
    success string (the skills packs' shape) does NOT count."""
    if not isinstance(result, dict):
        return bool(str(result or "").strip())
    if result.get("status") != "success":
        return False
    text = str(result.get("result") or "")
    return not any(marker in text for marker in _REFUSAL_MARKERS)
