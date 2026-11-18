"""Wave 7 Build 5 — P18 multiverse planner + compiler.

A multiverse is a set of candidate plans (branches) for one
caller-supplied goal, each branch a Build 4 proposal (exact
diff + regression guard + evidence ledger), plus a readable
routine per branch. The compiler turns a CALLER-CHOSEN branch
into a readable routine + a dry-run report. Nothing here
plans from nothing, chooses a branch, executes a branch,
applies a diff, promotes a copy, or deploys anything.

Gates, enforced in code, not prose (fail-closed whole: any
refused branch refuses the WHOLE multiverse, and any refused
compile refuses the WHOLE compile — no partial multiverse,
no partial routine, never a guessed routine):

  * Proposals are never trusted: every branch proposal is
    re-built and dry-run through Build 4
    (improvement_proposals.inspect_proposal), so a
    hand-edited branch claiming hashes, guards, or evidence
    it does not have is refused or rebuilt honestly. A
    branch without evidence or without a regression guard
    is refused by Build 4, and therefore refused here.
  * Multiverse means multiple: at least 2 branches, at
    most 8, each with a unique non-empty name.
  * Approve-before-real-files, copy-only scope: no branch
    touches a real file, ever. Dry-runs use throwaway
    copies only (Build 4 inspection + Build 3 SIM
    prediction, both of which discard their copies).
    Declared operations, when supplied, must be a subset
    of {read, write-copy, predict, propose}; move, rename,
    deploy, promote, execute, or apply operations refuse
    the whole multiverse.
  * Core-modification boundary (item 1692), enforced in
    code via Build 4: a diff path whose first component
    is "core" or "data" is refused outright UNLESS it
    carries a boundary check record naming exactly
    "core-modification-boundary-1692" with a non-empty
    reason and checked_by. Even with that check, the
    result is still only a proposal. Credential targets
    (.env, assistant.db, .pem, .key) are refused always,
    boundary check or not.
  * C-14: a branch whose declared actions include send,
    delete, or purchase carries a C-14 gate requirement
    in its routine, never an approval. A consequential
    branch that claims c14_required=False is refused
    whole; the module holds no approval and no confirm
    callable, and it never approves anything itself.
  * No auto-choice: compile_branch requires an exact
    branch name from the caller. A missing, empty, or
    unknown name is refused, never silently replaced
    with the first branch.
  * Fallback, never execution: if SIM prediction of the
    chosen branch fails (for example a core-touching
    branch, whose paths SIM refuses by design), the
    compiler falls back to the plain routine + the
    Build 4 proposal dry-run, states the fallback reason
    plainly, and still executes nothing. It never falls
    back to execution, and never to a guessed routine.
  * No executor argument anywhere in this module, and
    the module never imports an executor, a loop, or a
    promoter. A branch is proposed input, never an
    action taken. Full-VM universes (item 12) stay
    QUEUED; no VM form is built here. M4 itself is NOT
    built here; the M4 pattern is a reference only.

Own code, stdlib only. No third-party dependency is
introduced by this file (see gui/assets/
MULTIVERSE_LICENCE_READ_W7.md).
"""

from __future__ import annotations

import hashlib
import json

try:
    from . import improvement_proposals as _prop_mod
except Exception:  # noqa: BLE001 — package-context fallback for tests
    from core import improvement_proposals as _prop_mod  # type: ignore

try:
    from . import sim_predict as _sim_mod
except Exception:  # noqa: BLE001 — package-context fallback for tests
    try:
        from core import sim_predict as _sim_mod  # type: ignore
    except Exception:  # noqa: BLE001
        _sim_mod = None  # type: ignore

MAX_BRANCHES = 8
MIN_BRANCHES = 2
MAX_GOAL_CHARS = 1000
MAX_CONSTRAINT_CHARS = 500
MAX_STEPS = 50
MAX_STEP_CHARS = 1000

_CONSEQUENTIAL_ACTIONS = ("send", "delete", "purchase")
_REFUSED_OPERATIONS = ("move", "rename", "deploy", "promote",
                       "execute", "apply", "install", "run")
_ALLOWED_OPERATIONS = ("read", "write-copy", "predict", "propose")

ASSUMPTIONS = (
    "Branch proposals are caller-supplied; this module invents none.",
    "Dry-run copies are throwaway and discarded; nothing is promoted.",
    "A prediction is only as current as its seed; re-predict before acting.",
    "A compiled routine is a proposal, not an action; applying it is a "
    "separate human C-14 action, outside this module.",
)


def _fail(reason: str) -> dict:
    return {"ok": False, "reason": reason}


def _routine_for_branch(name: str, proposal: dict, dry_run: dict,
                        steps: list, actions: list,
                        c14_required: bool, constraints: list) -> dict:
    """Build the readable routine for one validated branch.

    The routine is deliberately lean: title, summary, and the
    full dry-run record live in the proposal / in the compile
    step, not duplicated here, so a multiverse result stays
    small enough for the funnel (which truncates string
    results over 4000 characters) to carry it untruncated.
    """
    diff_paths = [d["path"] for d in proposal["diffs"]]
    if steps:
        routine_steps = list(steps)
    else:
        routine_steps = []
        for diff in proposal["diffs"]:
            routine_steps.append(
                f"Change {diff['path']}: replace the old text "
                f"({len(diff['old_text'])} characters) with the new "
                f"text ({len(diff['new_text'])} characters).")
        for guard in proposal["regression_guards"]:
            routine_steps.append(f"Check regression guard: {guard}.")
    routine_steps.append(
        "Dry-run the branch in a throwaway copy and discard "
        "the copy; write nothing to a real file.")
    if c14_required:
        routine_steps.append(
            "Human C-14 approval is required before any real "
            "send, delete, or purchase action — this routine "
            "approves nothing itself.")
    return {
        "branch": name,
        "steps": routine_steps,
        "inputs": {
            "diff_paths": diff_paths,
            "regression_guards": list(proposal["regression_guards"]),
            "evidence_refs": [e["ref"] for e in proposal["evidence"]],
            "actions": list(actions),
        },
        "constraints": list(constraints),
        "scope": "copy-only",
        "core_touching": bool(proposal["core_touching"]),
        "boundary_check": proposal["boundary_check"],
        "c14_required": bool(c14_required),
        "dry_run_ok": bool(dry_run.get("ok")),
        "deployed": False,
        "executes_nothing": True,
    }


def _validate_branch(branch: object, seen_names: set) -> tuple:
    """Validate one branch; return (record, None) or (None, reason)."""
    if not isinstance(branch, dict):
        return None, "each branch must be an object"
    name = branch.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, "each branch needs a non-empty name"
    if name in seen_names:
        return None, f"duplicate branch name refused: {name!r}"
    seen_names.add(name)
    # Re-build + dry-run through Build 4: never trust the input.
    inspected = _prop_mod.inspect_proposal(branch.get("proposal"))
    if not inspected.get("ok"):
        return None, (f"branch {name!r}: "
                      f"{inspected.get('reason', 'proposal refused')}")
    proposal = inspected["proposal"]
    dry_run = inspected["dry_run"]
    steps = branch.get("steps", [])
    if steps is None:
        steps = []
    if not isinstance(steps, list):
        return None, f"branch {name!r}: steps must be a list"
    if len(steps) > MAX_STEPS:
        return None, f"branch {name!r}: too many steps"
    for step in steps:
        if not isinstance(step, str) or not step.strip():
            return None, f"branch {name!r}: each step must be a non-empty string"
        if len(step) > MAX_STEP_CHARS:
            return None, f"branch {name!r}: step too long"
    actions = branch.get("actions", [])
    if actions is None:
        actions = []
    if not isinstance(actions, list):
        return None, f"branch {name!r}: actions must be a list"
    for action in actions:
        if not isinstance(action, str) or not action.strip():
            return None, f"branch {name!r}: each action must be a non-empty string"
    # Copy-only scope: refused operations refuse the whole branch.
    for action in actions:
        if action.strip().lower() in _REFUSED_OPERATIONS:
            return None, (f"branch {name!r}: operation refused "
                          f"(copy-only scope): {action!r}")
    operations = branch.get("operations", [])
    if operations is None:
        operations = []
    if not isinstance(operations, list):
        return None, f"branch {name!r}: operations must be a list"
    for operation in operations:
        if operation not in _ALLOWED_OPERATIONS:
            return None, (f"branch {name!r}: operation refused "
                          f"(copy-only scope): {operation!r}")
    consequential = [a for a in actions
                     if a.strip().lower() in _CONSEQUENTIAL_ACTIONS]
    claimed = branch.get("c14_required")
    if consequential and claimed is False:
        return None, (f"branch {name!r}: consequential actions "
                      "must carry a C-14 requirement, never a waiver")
    c14_required = bool(consequential) or bool(claimed)
    return {
        "name": name,
        "proposal": proposal,
        "dry_run": dry_run,
        "actions": list(actions),
        "operations": list(operations),
        "c14_required": c14_required,
    }, None


def plan_multiverse(goal, branches, constraints=None) -> dict:
    """Plan a multiverse: validate every branch, refuse whole on any refusal.

    ``branches`` are caller-supplied branch objects, each with a
    ``name`` and a Build 4 ``proposal`` (plus optional ``steps``,
    ``actions``, ``operations``, ``c14_required``). This function
    invents no branch, no diff, no guard, and no evidence: a
    branch that Build 4 refuses is refused here, whole.
    """
    if not isinstance(goal, str) or not goal.strip() \
            or len(goal) > MAX_GOAL_CHARS:
        return _fail("goal must be a non-empty string")
    if constraints is None:
        constraints = []
    if not isinstance(constraints, list):
        return _fail("constraints must be a list")
    for constraint in constraints:
        if not isinstance(constraint, str) or not constraint.strip() \
                or len(constraint) > MAX_CONSTRAINT_CHARS:
            return _fail("each constraint must be a non-empty string")
    if not isinstance(branches, list):
        return _fail("branches must be a list")
    if len(branches) < MIN_BRANCHES:
        return _fail(
            f"a multiverse needs at least {MIN_BRANCHES} branches")
    if len(branches) > MAX_BRANCHES:
        return _fail(f"too many branches (max {MAX_BRANCHES})")
    seen: set[str] = set()
    records = []
    for branch in branches:
        record, reason = _validate_branch(branch, seen)
        if record is None:
            return _fail(reason)  # type: ignore[arg-type]
        records.append(record)
    out_branches = []
    for record in records:
        out_branches.append({
            "name": record["name"],
            "proposal": record["proposal"],
            "routine": _routine_for_branch(
                record["name"], record["proposal"], record["dry_run"],
                [], record["actions"], record["c14_required"],
                constraints),
            "c14_required": record["c14_required"],
            "core_touching": bool(record["proposal"]["core_touching"]),
            "deployed": False,
            "executes_nothing": True,
        })
        # Rebuild the routine with the caller's own steps, if any:
        # _validate_branch kept them only in the input, so recover
        # them from the original branch object by name.
        original = next(b for b in branches if b.get("name") == record["name"])
        caller_steps = original.get("steps") or []
        if caller_steps:
            out_branches[-1]["routine"] = _routine_for_branch(
                record["name"], record["proposal"], record["dry_run"],
                list(caller_steps), record["actions"],
                record["c14_required"], constraints)
    canonical = json.dumps(
        {"goal": goal, "branches": [
            {"name": b["name"], "proposal_id": b["proposal"]["id"]}
            for b in out_branches]},
        sort_keys=True)
    multiverse = {
        "kind": "MULTIVERSE",
        "id": hashlib.sha256(canonical.encode()).hexdigest()[:16],
        "goal": goal,
        "constraints": list(constraints),
        "branches": out_branches,
        "deployed": False,
        "executes_nothing": True,
        "note": (
            "PROPOSALS ONLY, NOT APPLIED. Choosing a branch is a "
            "caller act; compiling it produces a routine + dry-run "
            "report only. Nothing here executes, applies, promotes, "
            "or deploys anything."),
    }
    return {"ok": True, "multiverse": multiverse}


def compile_branch(multiverse, branch_name, seed=None, sentinels=None) -> dict:
    """Compile a CALLER-CHOSEN branch into a routine + dry-run report.

    The multiverse is never trusted: it is re-planned from its
    own goal/branches/constraints first, so a hand-edited
    multiverse claiming branches it does not have is refused.
    ``branch_name`` must name a branch exactly; a missing,
    empty, or unknown name is refused, never auto-picked.
    """
    if not isinstance(multiverse, dict):
        return _fail("multiverse must be an object")
    if multiverse.get("kind") != "MULTIVERSE":
        return _fail("multiverse kind must be MULTIVERSE")
    if not isinstance(branch_name, str) or not branch_name.strip():
        return _fail(
            "branch_name is required: the compiler never auto-chooses "
            "a branch")
    # Re-plan from the multiverse's own branches (proposals only).
    branch_inputs = []
    for branch in multiverse.get("branches", []):
        if not isinstance(branch, dict):
            return _fail("each multiverse branch must be an object")
        routine = branch.get("routine", {})
        inputs = routine.get("inputs", {}) if isinstance(routine, dict) else {}
        branch_inputs.append({
            "name": branch.get("name"),
            "proposal": branch.get("proposal"),
            "steps": [s for s in routine.get("steps", [])
                      if isinstance(s, str)] if routine else [],
            "actions": inputs.get("actions", []),
            "c14_required": branch.get("c14_required"),
        })
    # Caller steps in a stored routine include the appended
    # dry-run / C-14 footer steps; strip those before re-planning
    # so re-planning does not invent extra steps as input. The
    # footer lines are recognisable by their exact text.
    for item in branch_inputs:
        item["steps"] = [
            s for s in item["steps"]
            if not s.startswith("Dry-run the branch")
            and not s.startswith("Human C-14 approval")]
    replanned = plan_multiverse(
        multiverse.get("goal"), branch_inputs,
        multiverse.get("constraints", []))
    if not replanned.get("ok"):
        return replanned
    chosen = None
    for branch in replanned["multiverse"]["branches"]:
        if branch["name"] == branch_name:
            chosen = branch
            break
    if chosen is None:
        return _fail(f"unknown branch refused (never auto-chosen): "
                     f"{branch_name!r}")
    routine = chosen["routine"]
    numbered = [f"{i + 1}. {step}"
                for i, step in enumerate(routine["steps"])]
    # Fresh proposal dry-run for the chosen branch (the lean
    # plan routine no longer carries the full dry-run record).
    inspected = _prop_mod.inspect_proposal(chosen["proposal"])
    if not inspected.get("ok"):
        return inspected
    proposal_dry_run = inspected["dry_run"]
    sim_prediction = None
    fallback = False
    fallback_reason = None
    if seed is not None and _sim_mod is not None:
        operations = [{"kind": "write", "path": d["path"],
                       "content": d["new_text"]}
                      for d in chosen["proposal"]["diffs"]]
        predicted = _sim_mod.predict_plan(seed, operations, sentinels)
        if predicted.get("ok"):
            sim_prediction = predicted.get("prediction")
        else:
            # Fallback, never execution: routine + proposal
            # dry-run only, with the SIM refusal stated.
            sim_prediction = predicted
            fallback = True
            fallback_reason = predicted.get("reason")
    elif seed is not None and _sim_mod is None:
        fallback = True
        fallback_reason = "SIM prediction module unavailable"
    dry_run = {
        "kind": "DRY-RUN",
        "proposal_dry_run": proposal_dry_run,
        "sim_prediction": sim_prediction,
        "sim_prediction_run": sim_prediction is not None and not fallback,
        "fallback": fallback,
        "fallback_reason": fallback_reason,
        "executed": False,
        "executes_nothing": True,
        "note": (
            "Dry-run reports only; no real file was written, "
            "nothing was promoted, and nothing was executed. "
            "A fallback still executes nothing.") if fallback else (
            "Dry-run reports only; no real file was written, "
            "nothing was promoted, and nothing was executed."),
    }
    if seed is None:
        dry_run["note"] = (
            "Dry-run report (proposal dry-run only): no seed "
            "supplied, so no SIM file-effect prediction was run. "
            "No real file was written, nothing was promoted, "
            "and nothing was executed.")
    return {
        "ok": True,
        "kind": "COMPILED-ROUTINE",
        "branch_name": branch_name,
        "chosen_by": "caller",
        "multiverse_id": replanned["multiverse"]["id"],
        "goal": replanned["multiverse"]["goal"],
        "routine": {
            "branch": chosen["name"],
            "title": chosen["proposal"]["title"],
            "summary": chosen["proposal"]["summary"],
            "steps": numbered,
            "inputs": routine["inputs"],
            "assumptions": list(ASSUMPTIONS),
            "constraints": routine["constraints"],
            "scope": "copy-only",
            "core_touching": routine["core_touching"],
            "boundary_check": routine["boundary_check"],
            "c14_required": routine["c14_required"],
        },
        "dry_run": dry_run,
        "deployed": False,
        "executes_nothing": True,
        "note": (
            "COMPILED ROUTINE ONLY, NOT EXECUTED. Running this "
            "routine is a separate human C-14 action, outside "
            "this module."),
    }


def plan_from_json(text: object) -> dict:
    """Parse caller-supplied multiverse JSON and plan it (never trust it)."""
    try:
        value = json.loads(text)  # type: ignore[arg-type]
    except (ValueError, TypeError) as exc:
        return _fail(f"multiverse_json is not valid JSON: {exc}")
    if not isinstance(value, dict):
        return _fail("multiverse_json must be an object")
    return plan_multiverse(value.get("goal"), value.get("branches"),
                           value.get("constraints", []))


def compile_from_json(multiverse_text: object, branch_name: object,
                      seed_text: object = None,
                      sentinels_text: object = None) -> dict:
    """Parse a multiverse + optional seed, re-plan, and compile a branch."""
    try:
        multiverse = json.loads(multiverse_text)  # type: ignore[arg-type]
    except (ValueError, TypeError) as exc:
        return _fail(f"multiverse_json is not valid JSON: {exc}")
    seed = None
    if seed_text:
        try:
            seed = json.loads(seed_text)  # type: ignore[arg-type]
        except (ValueError, TypeError) as exc:
            return _fail(f"seed_json is not valid JSON: {exc}")
    sentinels = None
    if sentinels_text:
        try:
            sentinels = json.loads(sentinels_text)  # type: ignore[arg-type]
        except (ValueError, TypeError) as exc:
            return _fail(f"sentinels_json is not valid JSON: {exc}")
    return compile_branch(multiverse, branch_name, seed, sentinels)
