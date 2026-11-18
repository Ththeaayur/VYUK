"""Wave 6 integration tools — integration_harness_tools_register (split B11).

register_all: the 19-tool Wave 6 catalogue.
"""

from __future__ import annotations  # INTENTIONAL (B11 split): old module carried it; annotations stay lazy.

from .integration_harness_tools_handlers import extension_approve_handler, extension_audit_handler, extension_disable_handler, extension_enable_handler, extension_register_handler, extension_revoke_handler, extension_status_handler, miss_capability_map_handler, miss_compound_handler, miss_to_playbook_steps_handler, mission_build_brief_handler, mission_build_plan_handler, mission_evidence_board_handler, mission_journal_append_handler, mission_journal_entry_handler, mission_run_job_handler, workflow_build_plan_handler, workflow_distil_handler, workflow_to_playbook_steps_handler


def register_all(registry) -> None:
    """Register the 19 Wave 6 tools.

    Harness (7): audit/status read-only; register/approve/
    enable/revoke confirm-gated (register's directory is a
    write-destination arg); disable free-by-design app-state
    (reversible, harness store file only). WORK (3) + MISS (3)
    + mission composition (4): read-only pure composition.
    Mission journal append: free-by-design app-state (fixed
    file only, no caller-chosen destination). Mission run job:
    confirm-gated, stages only, never executes.
    """
    registry.register(
        name="extension_audit",
        description="Audit an extension manifest: validate its fields and check its licence against the allow-list (blocked, unknown, or missing licences fail closed). Pure composition, no state change, no extension code loaded. Use when the user wants an extension checked before registering it.",
        parameters={"type": "object", "properties": {
            "manifest_json": {"type": "string", "description": "JSON manifest {name, version X.Y.Z, licence, declared_permissions, declared_files}"},
        }, "required": ["manifest_json"]},
        handler=extension_audit_handler,
    )
    registry.register(
        name="extension_status",
        description="Show one registered extension's state, licence, permissions, and log. Read-only; an unknown name returns 'unknown extension', never an invented state. Use when the user asks about an extension's status.",
        parameters={"type": "object", "properties": {
            "name": {"description": "Name of the registered extension", "type": "string"},
        }, "required": ["name"]},
        handler=extension_status_handler,
    )
    registry.register(
        name="extension_register",
        description="Register a validated extension manifest in its directory (resolved, symlinks included). Registration does NOT approve or enable: the extension is inert until a human approves it, then enables it. The registration asks for confirmation first. Use when the user wants an extension registered.",
        parameters={"type": "object", "properties": {
            "manifest_json": {"type": "string", "description": "JSON manifest"},
            "directory": {"type": "string", "description": "The extension's own directory"},
        }, "required": ["manifest_json", "directory"]},
        handler=extension_register_handler,
    )
    registry.register(
        name="extension_approve",
        description="Approve a registered extension (human approval recorded). Approval does NOT enable it — enabling is a separate human decision. The approval asks for confirmation first. A revoked extension refuses here. Use when the user approves an extension.",
        parameters={"type": "object", "properties": {
            "name": {"description": "Name of the registered extension to approve", "type": "string"},
        }, "required": ["name"]},
        handler=extension_approve_handler,
    )
    registry.register(
        name="extension_enable",
        description="Enable an already-approved extension. A merely registered or revoked extension refuses. The enabling asks for confirmation first. Use when the user enables an approved extension.",
        parameters={"type": "object", "properties": {
            "name": {"description": "Name of the approved extension to enable", "type": "string"},
        }, "required": ["name"]},
        handler=extension_enable_handler,
    )
    registry.register(
        name="extension_disable",
        description="Disable an extension (reversible safety action; writes only the app's own harness store file). Use when the user wants an extension disabled quickly.",
        parameters={"type": "object", "properties": {
            "name": {"description": "Name of the extension to disable", "type": "string"},
            "reason": {"description": "Reason for disabling the extension", "type": "string"},
        }, "required": ["name"]},
        handler=extension_disable_handler,
    )
    registry.register(
        name="extension_revoke",
        description="Revoke an extension (terminal: no re-approval, re-enable, or re-registration afterwards). The revocation asks for confirmation first. Use when the user wants an extension revoked.",
        parameters={"type": "object", "properties": {
            "name": {"description": "Name of the extension to revoke", "type": "string"},
            "reason": {"description": "Reason for revoking the extension", "type": "string"},
        }, "required": ["name"]},
        handler=extension_revoke_handler,
    )
    registry.register(
        name="workflow_distil",
        description="Distil caller-supplied proven jobs into a reusable workflow (steps with source job + evidence ref, duplicates reported). Unproven, failed, or proof-free jobs refuse the WHOLE input, never partially. Pure composition; the learner never finds jobs itself and never executes anything. Use when the user wants a workflow learned from proven jobs.",
        parameters={"type": "object", "properties": {
            "jobs_json": {"type": "string", "description": "JSON list of proven jobs {job_id, goal_text, binder, field_proven: true, proof_reference}"},
        }, "required": ["jobs_json"]},
        handler=workflow_distil_handler,
    )
    registry.register(
        name="workflow_build_plan",
        description="Turn a distilled workflow into a PROPOSED loop plan (sequential dependencies, evidence refs, proposed_only, executes_nothing). A plan is a proposal, never an execution. Pure composition. Use when the user wants a workflow as a runnable plan.",
        parameters={"type": "object", "properties": {
            "workflow_json": {"type": "string", "description": "JSON workflow from workflow_distil"},
        }, "required": ["workflow_json"]},
        handler=workflow_build_plan_handler,
    )
    registry.register(
        name="workflow_to_playbook_steps",
        description="Convert a workflow to plain step texts + proof references for a CALLER to add through the existing J9 playbook (the loop's single playbook store). This tool writes nothing to J9 itself. Pure conversion. Use when the user wants workflow steps as playbook steps.",
        parameters={"type": "object", "properties": {
            "workflow_json": {"type": "string", "description": "JSON workflow from workflow_distil"},
        }, "required": ["workflow_json"]},
        handler=workflow_to_playbook_steps_handler,
    )
    registry.register(
        name="miss_compound",
        description="Compound proven workflows into one compounded workflow (input steps exactly, in input order, evidence refs preserved), or refuse the WHOLE input on any unproven, mixed, or provenance-free entry. Pure composition; the compounder never executes anything. Use when the user wants proven workflows compounded.",
        parameters={"type": "object", "properties": {
            "workflows_json": {"type": "string", "description": "JSON list of WORK workflow objects"},
        }, "required": ["workflows_json"]},
        handler=miss_compound_handler,
    )
    registry.register(
        name="miss_capability_map",
        description="Build a capability map from proven workflows: one capability per proven workflow (sources + evidence refs); every other input lands in 'unproven', never in 'capabilities'. Pure composition. Use when the user asks what the app has provenly done, or whether a capability claim holds.",
        parameters={"type": "object", "properties": {
            "workflows_json": {"type": "string", "description": "JSON list of WORK workflow objects"},
        }, "required": ["workflows_json"]},
        handler=miss_capability_map_handler,
    )
    registry.register(
        name="miss_to_playbook_steps",
        description="Convert a compounded workflow to plain step texts + proof references for a CALLER to add through J9. Refuses WHOLE on a deletion, forbidden-target, provenance-free, proof-free, or gate-free step. This tool writes nothing. Pure conversion. Use when the user wants a compound as playbook steps.",
        parameters={"type": "object", "properties": {
            "compound_json": {"type": "string", "description": "JSON compound from miss_compound"},
        }, "required": ["compound_json"]},
        handler=miss_to_playbook_steps_handler,
    )
    registry.register(
        name="mission_build_brief",
        description="Build a mission brief: goal, scope, constraints, known facts (only from proven workflow steps with source job + proof reference), evidence refs, and unknowns carried verbatim, never filled. An unproven step refuses the whole brief. Pure composition. Use when the user wants a mission briefed.",
        parameters={"type": "object", "properties": {
            "goal": {"description": "Goal of the mission brief", "type": "string"},
            "scope": {"description": "Scope of the mission brief", "type": "string"},
            "constraints_json": {"type": "string", "description": "JSON list of constraint strings"},
            "workflows_json": {"type": "string", "description": "JSON list of WORK workflow objects"},
            "evidence_refs_json": {"type": "string", "description": "JSON list of {source, detail} evidence refs"},
            "unknowns_json": {"type": "string", "description": "JSON list of unknown strings"},
        }, "required": ["goal"]},
        handler=mission_build_brief_handler,
    )
    registry.register(
        name="mission_build_plan",
        description="Build a mission's AGENT split plan from its brief + proven workflows (brief + default consequential-only delegation attached; plan validated through AGENT's own split_plan). A plan is a proposal, never an execution. Pure composition. Use when the user wants a mission as a runnable plan.",
        parameters={"type": "object", "properties": {
            "brief_json": {"type": "string", "description": "JSON brief from mission_build_brief"},
            "workflows_json": {"type": "string", "description": "JSON list of WORK workflow objects"},
        }, "required": ["brief_json", "workflows_json"]},
        handler=mission_build_plan_handler,
    )
    registry.register(
        name="mission_evidence_board",
        description="Build the evidence-board VIEW for a mission (one entry per evidence ref, duplicates folded and reported, stored: false). The board persists nothing. Pure composition. Use when the user wants a mission's evidence listed.",
        parameters={"type": "object", "properties": {
            "mission": {"description": "Mission the evidence board is for", "type": "string"},
            "receipts_or_binders_json": {"type": "string", "description": "JSON list of receipts or AGENT binder dicts"},
        }, "required": ["mission", "receipts_or_binders_json"]},
        handler=mission_evidence_board_handler,
    )
    registry.register(
        name="mission_journal_entry",
        description="Validate + construct one decision-journal entry (decision, at least one option considered, cited evidence with detail, a named approver who is never this module or the coordinator or an automatic approver, and a caller-supplied timestamp). Validation + construction only, no write. Use when the user wants a decision journaled.",
        parameters={"type": "object", "properties": {
            "decision": {"description": "The decision being journaled", "type": "string"},
            "options_json": {"type": "string", "description": "JSON list of options considered"},
            "evidence_cited_json": {"type": "string", "description": "JSON list of {source, detail} evidence refs"},
            "approved_by": {"description": "Named approver of the decision", "type": "string"},
            "timestamp": {"type": "string", "description": "Caller-supplied timestamp; never invented"},
        }, "required": ["decision", "options_json", "evidence_cited_json", "approved_by", "timestamp"]},
        handler=mission_journal_entry_handler,
    )
    registry.register(
        name="mission_journal_append",
        description="Append a validated journal entry (from mission_journal_entry) to the app's FIXED mission journal file (data/mission_journal_w6.json). No caller-chosen destination exists for this tool. Use when the user wants a validated journal entry recorded.",
        parameters={"type": "object", "properties": {
            "entry_json": {"type": "string", "description": "JSON journal entry from mission_journal_entry"},
        }, "required": ["entry_json"]},
        handler=mission_journal_append_handler,
    )
    registry.register(
        name="mission_run_job",
        description="Validate a mission plan + delegation level and STAGE it for the session's next top-level goal run for the same goal (confirm popup first). Staging never executes the job; the goal run consumes the staged plan once through the AGENT coordinator. A waiving delegation level refuses, never downgrades. Use when the user wants a validated mission plan staged to run.",
        parameters={"type": "object", "properties": {
            "goal": {"description": "Goal of the mission plan to stage", "type": "string"},
            "plan_json": {"type": "string", "description": "JSON split-plan list or mission_build_plan object"},
            "brief_json": {"type": "string", "description": "JSON brief from mission_build_brief (optional)"},
            "delegation": {"type": "string", "description": "step-approval, consequential-only (default), or autonomous-within-plan"},
        }, "required": ["goal", "plan_json"]},
        handler=mission_run_job_handler,
    )
