"""Wave 6 integration tools — integration_harness_tools_handlers (split B11).

The 19 *_handler wrappers (JSON-string handlers for register_all).
"""

from __future__ import annotations  # INTENTIONAL (B11 split): old module carried it; annotations stay lazy.

from .integration_harness_tools_extensions import extension_approve, extension_audit, extension_disable, extension_enable, extension_register, extension_revoke, extension_status
from .integration_harness_tools_helpers import _handler
from .integration_harness_tools_workflow import miss_capability_map, miss_compound, miss_to_playbook_steps, mission_build_brief, mission_build_plan, mission_evidence_board, mission_journal_append, mission_journal_entry, mission_run_job, workflow_build_plan, workflow_distil, workflow_to_playbook_steps


def extension_audit_handler(manifest_json):
    return _handler(extension_audit, manifest_json=manifest_json)


def extension_status_handler(name):
    return _handler(extension_status, name=name)


def extension_register_handler(manifest_json, directory):
    return _handler(extension_register, manifest_json=manifest_json,
                    directory=directory)


def extension_approve_handler(name):
    return _handler(extension_approve, name=name)


def extension_enable_handler(name):
    return _handler(extension_enable, name=name)


def extension_disable_handler(name, reason=""):
    return _handler(extension_disable, name=name, reason=reason)


def extension_revoke_handler(name, reason=""):
    return _handler(extension_revoke, name=name, reason=reason)


def workflow_distil_handler(jobs_json):
    return _handler(workflow_distil, jobs_json=jobs_json)


def workflow_build_plan_handler(workflow_json):
    return _handler(workflow_build_plan, workflow_json=workflow_json)


def workflow_to_playbook_steps_handler(workflow_json):
    return _handler(workflow_to_playbook_steps, workflow_json=workflow_json)


def miss_compound_handler(workflows_json):
    return _handler(miss_compound, workflows_json=workflows_json)


def miss_capability_map_handler(workflows_json):
    return _handler(miss_capability_map, workflows_json=workflows_json)


def miss_to_playbook_steps_handler(compound_json):
    return _handler(miss_to_playbook_steps, compound_json=compound_json)


def mission_build_brief_handler(goal, scope="", constraints_json="[]",
                                workflows_json="[]", evidence_refs_json="[]",
                                unknowns_json="[]"):
    return _handler(mission_build_brief, goal=goal, scope=scope,
                    constraints_json=constraints_json,
                    workflows_json=workflows_json,
                    evidence_refs_json=evidence_refs_json,
                    unknowns_json=unknowns_json)


def mission_build_plan_handler(brief_json, workflows_json):
    return _handler(mission_build_plan, brief_json=brief_json,
                    workflows_json=workflows_json)


def mission_evidence_board_handler(mission, receipts_or_binders_json):
    return _handler(mission_evidence_board, mission=mission,
                    receipts_or_binders_json=receipts_or_binders_json)


def mission_journal_entry_handler(decision, options_json, evidence_cited_json,
                                  approved_by, timestamp):
    return _handler(mission_journal_entry, decision=decision,
                    options_json=options_json,
                    evidence_cited_json=evidence_cited_json,
                    approved_by=approved_by, timestamp=timestamp)


def mission_journal_append_handler(entry_json):
    return _handler(mission_journal_append, entry_json=entry_json)


def mission_run_job_handler(goal, plan_json, brief_json="",
                            delegation="consequential-only"):
    return _handler(mission_run_job, goal=goal, plan_json=plan_json,
                    brief_json=brief_json, delegation=delegation)
