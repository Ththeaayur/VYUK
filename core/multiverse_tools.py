"""Wave 7 Build 5 tools — P18 multiverse planner + compiler as live tools.

Triage Wire pattern, as in core/proposal_tools.py: this
coordinator imports the multiverse module, registers tools
via register_all(registry), each handler takes the named
schema fields, forwards to the module, and returns the
module result encoded as a JSON string verbatim; refusal
dicts flow the same way, and module raises become an
error JSON — handlers never raise.

Wiring (Wave 7 Build 5, P18 gates only):
  * multiverse_plan — read-only. It plans a caller-supplied
    multiverse (goal + branches, each a Build 4 proposal
    with diff + guard + evidence) and returns every branch
    with its readable routine. A branch without evidence,
    without a guard, touching a core path without the
    1692 boundary check, or claiming a move/rename/
    deploy/promote operation is refused whole, so no
    partial multiverse is ever returned. It writes
    nothing the caller keeps, never chooses a branch,
    never applies, promotes, or deploys anything.
  * multiverse_compile — read-only. It compiles a
    CALLER-CHOSEN branch (exact name, never auto-chosen)
    into a readable routine + dry-run report (Build 4
    proposal dry-run, plus Build 3 SIM prediction when
    a seed is supplied). It re-plans the multiverse
    itself, so it never trusts a caller-supplied
    multiverse JSON. An uncompilable branch falls back
    to routine + proposal dry-run only, never to
    execution, never to a guessed routine.
  * multiverse_compile_report — confirm-gated,
    output_path in _WRITE_DESTINATION_ARGS. It
    re-compiles the chosen branch itself and writes
    only the compiled routine + dry-run report to a
    caller-chosen .json file whose parent folder must
    already exist — no folder creation, no overwrite
    of a core/data path, no .env/.pem/.key target.
    The funnel refuses it without a human confirm
    handler, before the handler runs. Writing the
    report is not running the routine: the routine
    itself is never executed by any tool here.

This coordinator is NOT a gate owner: it imports no gate
module for decisions — confirm-gating is the funnel's job
(core/funnel/tools_gate.py classification). No M4 itself is built
or wired here; no tool here chooses, plans from nothing,
promotes, applies, or deploys anything — plan-approval
gate intact, no self-deployment, ever. Full-VM universes
(item 12) stay QUEUED; no VM form is built or wired here.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import multiverse_planner as _mv_mod


def _error(reason: str) -> str:
    return json.dumps({"ok": False, "reason": reason})


def _handler(fn, *args, **kwargs):
    try:
        return json.dumps(fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")


def _parse_json(text, field):
    try:
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        return None, f"{field} is not valid JSON: {exc}"
    return value, None


def multiverse_plan_handler(goal, branches_json, constraints_json="[]"):
    branches, err = _parse_json(branches_json, "branches_json")
    if err:
        return _error(err)
    constraints, err = _parse_json(constraints_json, "constraints_json") \
        if constraints_json else ([], None)
    if err:
        return _error(err)
    return _handler(_mv_mod.plan_multiverse, goal, branches, constraints)


def multiverse_compile_handler(multiverse_json, branch_name,
                               seed_json="", sentinels_json=""):
    multiverse, err = _parse_json(multiverse_json, "multiverse_json")
    if err:
        return _error(err)
    seed = None
    if seed_json:
        seed, err = _parse_json(seed_json, "seed_json")
        if err:
            return _error(err)
    sentinels = None
    if sentinels_json:
        sentinels, err = _parse_json(sentinels_json, "sentinels_json")
        if err:
            return _error(err)
    return _handler(_mv_mod.compile_branch, multiverse, branch_name,
                    seed, sentinels)


def multiverse_compile_report_handler(multiverse_json, branch_name,
                                      output_path, seed_json="",
                                      sentinels_json=""):
    multiverse, err = _parse_json(multiverse_json, "multiverse_json")
    if err:
        return _error(err)
    seed = None
    if seed_json:
        seed, err = _parse_json(seed_json, "seed_json")
        if err:
            return _error(err)
    sentinels = None
    if sentinels_json:
        sentinels, err = _parse_json(sentinels_json, "sentinels_json")
        if err:
            return _error(err)
    result = _mv_mod.compile_branch(multiverse, branch_name, seed, sentinels)
    if not result.get("ok"):
        return json.dumps(result)
    try:
        target = Path(output_path).expanduser()
        if target.suffix != ".json":
            return _error("output_path must end in .json")
        if not target.parent.is_dir():
            return _error("output_path parent folder must already exist")
        if target.name in (".env", "assistant.db") \
                or target.name.endswith((".pem", ".key")):
            return _error("credential target refused")
        tree = Path(__file__).resolve().parent.parent
        try:
            resolved = target.resolve()
            if resolved.is_relative_to(tree / "core") \
                    or resolved.is_relative_to(tree / "data"):
                return _error("app core/data paths refused")
        except (OSError, ValueError):
            return _error("output_path cannot be resolved")
        target.write_text(json.dumps(result, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "COMPILED-ROUTINE",
                       "deployed": False, "executes_nothing": True,
                       "report_path": str(target),
                       "branch_name": branch_name,
                       "note": "Report written; the routine itself was "
                               "NOT executed."})


def register_all(registry) -> None:
    """Register the 3 Wave 7 Build 5 multiverse tools."""
    registry.register(
        name="multiverse_plan",
        description="PROPOSALS ONLY, NOT APPLIED: plan a caller-supplied multiverse for one goal — at least two branches, each a Build 4 proposal (exact diff, named regression guard, evidence ledger) with a readable routine. A branch without evidence, without a guard, touching a core path without the core-modification boundary check, or claiming a move/rename/deploy/promote operation refuses the whole multiverse. Nothing is chosen, applied, promoted, or deployed by this tool.",
        parameters={"type": "object", "properties": {
            "goal": {"type": "string",
                     "description": "The goal the branches are for"},
            "branches_json": {"type": "string",
                              "description": "JSON list of branch objects: {name, proposal: {title, summary, diffs: [{path, old_text, new_text}], regression_guards: [test names], evidence: [{kind, ref, detail}], boundary_check (only for core/data paths)}, steps (optional plain-language steps), actions (optional), operations (optional, copy-only subset)}"},
            "constraints_json": {"type": "string",
                                 "description": "Optional JSON list of constraint strings"},
        }, "required": ["goal", "branches_json"]},
        handler=multiverse_plan_handler,
    )
    registry.register(
        name="multiverse_compile",
        description="COMPILED ROUTINE ONLY, NOT EXECUTED: compile a CALLER-CHOSEN branch (exact name, never auto-chosen) from a multiverse into a readable routine + dry-run report (Build 4 proposal dry-run, plus Build 3 SIM prediction when a seed is supplied). The multiverse is re-planned, never trusted. An uncompilable branch falls back to routine + proposal dry-run only, never to execution, never to a guessed routine.",
        parameters={"type": "object", "properties": {
            "multiverse_json": {"type": "string",
                                "description": "JSON multiverse object returned by multiverse_plan (re-planned, never trusted)"},
            "branch_name": {"type": "string",
                            "description": "Exact name of the branch to compile; required, never auto-chosen"},
            "seed_json": {"type": "string",
                          "description": "Optional JSON dict of relative path -> text content (before-state snapshot for SIM prediction)"},
            "sentinels_json": {"type": "string",
                               "description": "Optional JSON list of outside file paths hashed before/after to prove they are unchanged"},
        }, "required": ["multiverse_json", "branch_name"]},
        handler=multiverse_compile_handler,
    )
    registry.register(
        name="multiverse_compile_report",
        description="COMPILED ROUTINE REPORT ONLY, NOT EXECUTED: re-compile a caller-chosen branch and write only the compiled routine + dry-run report to a caller-chosen .json file in an already-existing folder. The routine itself is never executed by this tool. The report asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "multiverse_json": {"type": "string",
                                "description": "JSON multiverse object, as for multiverse_compile"},
            "branch_name": {"type": "string",
                            "description": "Exact name of the branch to compile"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
            "seed_json": {"type": "string",
                          "description": "Optional JSON seed dict, as for multiverse_compile"},
            "sentinels_json": {"type": "string",
                               "description": "Optional JSON sentinel list, as for multiverse_compile"},
        }, "required": ["multiverse_json", "branch_name", "output_path"]},
        handler=multiverse_compile_report_handler,
    )
