"""Wave 6 integration tools — integration_harness_tools_workflow (split B11).

Workflow / MISS / mission composition tools + mission_run_job.
"""

from __future__ import annotations  # INTENTIONAL (B11 split): old module carried it; annotations stay lazy.

import json
import os
import tempfile
from .integration_harness_tools import _journal_path
from .integration_harness_tools_extensions import _note_irreversible
from .integration_harness_tools_helpers import _norm, _parse_dict, _parse_json, _parse_list


def _proven_job_from_dict(item):
    from . import workflow_learning as _w

    if not isinstance(item, dict):
        raise ValueError("learning input entries must be JSON objects")
    if "binder" in item:
        return _w.ProvenJob(
            item.get("job_id") or item.get("name") or "",
            item.get("goal_text") or "",
            item.get("binder") or {},
            field_proven=bool(item.get("field_proven", False)),
            proof_reference=str(item.get("proof_reference") or ""),
        )
    # A bare AGENT binder dict: provenance must be asserted by
    # the caller (field_proven + proof_reference on the dict),
    # otherwise the learner refuses it as unproven — a binder
    # alone is never proof.
    return _w.ProvenJob(
        item.get("job_id") or item.get("name") or "",
        item.get("goal_text") or "",
        dict(item),
        field_proven=bool(item.get("field_proven", False)),
        proof_reference=str(item.get("proof_reference") or ""),
    )


def _workflow_from_dict(data):
    """Rebuild a WORK Workflow object from its as_dict() JSON."""
    from . import workflow_learning as _w

    if not isinstance(data, dict) or "steps" not in data:
        raise ValueError("workflow_json must be a WORK workflow object")
    steps = []
    for raw in data.get("steps") or []:
        if isinstance(raw, dict):
            steps.append(_w.WorkflowStep(
                raw.get("id") or "",
                raw.get("text") or "",
                raw.get("source_job_id") or "",
                raw.get("evidence_ref") or {},
                raw.get("gate_requirement"),
            ))
        else:
            steps.append(_w.WorkflowStep("", str(raw), "", {}))
    return _w.Workflow(
        data.get("name") or "learned-workflow",
        steps,
        list(data.get("sources") or []),
        list(data.get("duplicates") or []),
    )


def workflow_distil(jobs_json):
    """Distil proven jobs into a workflow, or refuse whole."""
    from . import workflow_learning as _w

    jobs = _parse_list(jobs_json, "jobs_json")
    proven = [_proven_job_from_dict(item) for item in jobs]
    return _w.distil_workflows(proven).as_dict()


def workflow_build_plan(workflow_json):
    """Turn a workflow into a PROPOSED loop plan (never execution)."""
    from . import workflow_learning as _w

    workflow = _workflow_from_dict(_parse_dict(workflow_json, "workflow_json"))
    return _w.build_loop_plan(workflow)


def workflow_to_playbook_steps(workflow_json):
    """Convert a workflow to plain J9 step texts (writes nothing)."""
    from . import workflow_learning as _w

    workflow = _workflow_from_dict(_parse_dict(workflow_json, "workflow_json"))
    return _w.workflow_to_playbook_steps(workflow)


def miss_compound(workflows_json):
    """Compound proven workflows, or refuse whole."""
    from . import miss_extensions_learning as _m

    workflows = _parse_list(workflows_json, "workflows_json")
    return _m.compound_workflows(workflows).as_dict()


def miss_capability_map(workflows_json):
    """One capability per proven workflow; the rest in unproven."""
    from . import miss_extensions_learning as _m

    workflows = _parse_list(workflows_json, "workflows_json")
    return _m.capability_map_from_workflows(workflows)


def miss_to_playbook_steps(compound_json):
    """Convert a compound to plain J9 step texts (writes nothing)."""
    from . import miss_extensions_learning as _m

    compound = _parse_dict(compound_json, "compound_json")
    return _m.compound_to_playbook_steps(compound)


def mission_build_brief(goal, scope="", constraints_json="[]",
                        workflows_json="[]", evidence_refs_json="[]",
                        unknowns_json="[]"):
    """Build a mission brief (facts only from proven workflows)."""
    from . import mission_control as _mc

    constraints = _parse_list(constraints_json, "constraints_json")
    workflows = _parse_list(workflows_json, "workflows_json")
    evidence_refs = _parse_list(evidence_refs_json, "evidence_refs_json")
    unknowns = _parse_list(unknowns_json, "unknowns_json")
    brief = _mc.build_mission_brief(
        goal, scope=scope or "", constraints=constraints,
        workflows=workflows, evidence_refs=evidence_refs or None,
        unknowns=unknowns)
    return brief.as_dict()


def _combined_workflow(workflows):
    """Concatenate workflow dicts into one WORK Workflow object.

    Steps keep workflow order, then step order; duplicates
    (same source job + normalised step text) fold into the
    existing step and are reported, never silently doubled.
    """
    from . import workflow_learning as _w

    steps, seen, duplicates, sources = [], {}, [], []
    for workflow in workflows:
        if not isinstance(workflow, dict) or "steps" not in workflow:
            raise ValueError("workflows_json entries must be WORK workflow objects")
        for source in workflow.get("sources") or []:
            if source not in sources:
                sources.append(source)
        for raw in workflow.get("steps") or []:
            if not isinstance(raw, dict):
                raise ValueError("workflow steps must be JSON objects")
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            key = (str(raw.get("source_job_id") or ""), _norm(text))
            if key in seen:
                duplicates.append({
                    "source_job_id": key[0], "step": text,
                    "reason": "duplicate step folded into the existing step, not doubled",
                })
                continue
            seen[key] = True
            steps.append(_w.WorkflowStep(
                raw.get("id") or "", text, key[0],
                raw.get("evidence_ref") or {},
                raw.get("gate_requirement")))
    if not steps:
        raise ValueError("mission plan needs at least one workflow step")
    return _w.Workflow("mission-workflow", steps, sources, duplicates), duplicates


def mission_build_plan(brief_json, workflows_json):
    """Build a mission's AGENT split plan (proposal, never execution).

    The plan is WORK's loop-plan shape, validated through
    AGENT's own ``split_plan`` before it is returned, plus the
    mission brief and the default delegation level
    (``consequential-only`` — overridable at ``mission_run_job``,
    never a waiving level). Production mission handoffs go
    through AGENT's ``build_handoff_packet`` with these two
    keys (the single packet builder, per the Build 5 fold).
    """
    from . import agent_coordination as _agent
    from . import mission_control as _mc
    from . import workflow_learning as _w

    brief = _parse_dict(brief_json, "brief_json")
    if not str(brief.get("goal") or "").strip():
        raise ValueError("brief_json must carry a mission goal")
    workflows = _parse_list(workflows_json, "workflows_json")
    if not workflows:
        raise ValueError("mission plan needs at least one workflow")
    combined, duplicates = _combined_workflow(workflows)
    plan = _w.build_loop_plan(combined, parent_goal=brief["goal"])
    # Defence in depth: the returned plan must pass AGENT's own
    # validation, not just WORK's shape — a plan that cannot
    # split cannot run, so it must not be staged.
    _agent.split_plan(plan["goal_text"], plan["plan"])
    plan["duplicates"] = duplicates
    plan["mission_brief"] = brief
    plan["delegation"] = _mc.set_delegation("consequential-only").as_dict()
    return plan


def mission_evidence_board(mission, receipts_or_binders_json):
    """Build the evidence-board VIEW (persists nothing)."""
    from . import mission_control as _mc

    items = _parse_list(receipts_or_binders_json, "receipts_or_binders_json")
    return _mc.build_evidence_board(mission, items)


def mission_journal_entry(decision, options_json, evidence_cited_json,
                          approved_by, timestamp):
    """Validate + construct one decision-journal entry (no write)."""
    from . import mission_control as _mc

    options = _parse_list(options_json, "options_json")
    evidence_cited = _parse_list(evidence_cited_json, "evidence_cited_json")
    return _mc.journal_entry(decision, options, evidence_cited,
                             approved_by, timestamp)


def mission_journal_append(entry_json):
    """Append a validated journal entry to the FIXED journal file.

    The entry must already pass ``mission_journal_entry``
    validation (same fields). A corrupt or non-list journal
    file refuses, never overwrites — history is never silently
    replaced. No caller-chosen destination exists for this tool.
    """
    from . import mission_control as _mc

    entry = _parse_dict(entry_json, "entry_json")
    for key in ("decision", "options_considered", "evidence_cited",
                "approved_by", "timestamp"):
        if key not in entry:
            raise ValueError(f"entry_json is missing {key!r} — "
                             f"validate it with mission_journal_entry first")
    validated = _mc.journal_entry(
        entry["decision"], entry["options_considered"],
        entry["evidence_cited"], entry["approved_by"], entry["timestamp"])
    path = _journal_path()
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"mission journal file is unreadable ({exc}); "
                             f"refusing, file left untouched at {path}")
        if not isinstance(existing, list):
            raise ValueError("mission journal file is not a list; "
                             f"refusing, file left untouched at {path}")
    else:
        existing = []
    existing.append(validated)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(existing, indent=2, sort_keys=True))
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    # Wave 7 Build 2: an appended audit entry cannot be taken
    # back, so it is recorded non-undoable (when an undo
    # store is configured); undo skips and lists it.
    _note_irreversible("journal-" + str(len(existing)),
                        "mission journal entry appended")
    return {"appended": validated, "entries": len(existing),
            "journal_file": str(path)}


def mission_run_job(goal, plan_json, brief_json="", delegation="consequential-only"):
    """Validate + STAGE a mission plan (never executes a job).

    The funnel confirm-gates this tool, so staging happens
    only after a human confirm. Execution happens only on the
    session's next top-level goal run for the same goal, which
    consumes the staged plan once through the AGENT
    coordinator. A waiving delegation level refuses, never
    downgrades; a bad plan refuses, never stages.
    """
    from . import agent_coordination as _agent
    from . import mission_control as _mc
    from . import mission_jobs as _jobs

    goal_text = str(goal or "").strip()
    if not goal_text:
        raise ValueError("mission_run_job needs a goal")
    plan_data = _parse_json(plan_json, "plan_json")
    if isinstance(plan_data, dict) and "plan" in plan_data:
        plan_list = plan_data["plan"]
        embedded_brief = plan_data.get("mission_brief")
    elif isinstance(plan_data, list):
        plan_list = plan_data
        embedded_brief = None
    else:
        raise ValueError("plan_json must be a split-plan list or a "
                         "mission_build_plan object")
    if not isinstance(plan_list, list) or not plan_list:
        raise ValueError("plan_json carries no sub-goals")
    subgoals = _agent.split_plan(goal_text, plan_list)
    dial = _mc.set_delegation(delegation or "consequential-only")
    brief = None
    if brief_json:
        brief = _parse_dict(brief_json, "brief_json")
        if not str(brief.get("goal") or "").strip():
            raise ValueError("brief_json must carry a mission goal")
    elif isinstance(embedded_brief, dict):
        brief = embedded_brief
    staged = _jobs.stage_plan(goal_text, plan_list, brief=brief,
                              dial=dial.level)
    return {
        "staged": True,
        "goal": staged["goal"],
        "sub_goals": staged["sub_goals"],
        "delegation": staged["dial"],
        "note": "plan staged — send/confirm the goal to run it",
    }
