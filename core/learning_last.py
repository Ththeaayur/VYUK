"""Wave 8 Build 5 — Phase 8 learning-last continuation.

Learning LAST, in the Phase 8 sense: this module is the LAST
step of a learning chain, never the first. It consumes ONLY
field-proven evidence and produces a SPEC — a Wave 7 proposal
(exact diff + regression guard + evidence ledger) — and
nothing else. It does not change shipped behaviour, does not
collect proof, does not execute, and does not deploy.

What "proven" means here is inherited, not reinvented: the
Wave 6 WORK rule (core/workflow_learning.py). A job counts
only when its binder says "completed", the caller asserts
field_proven=True WITH a non-empty proof_reference, and the
binder carries at least one evidence entry and at least one
step. Anything else — unproven, failed, refused, halted,
stopped, not-run, evidence-free, step-free, or missing a
proof reference — refuses the WHOLE spec. A mixed
proven + unproven input produces no spec at all, so an
unproven job can never slip into a spec behind a proven one.

What this module never does, by construction:
  - It never auto-collects proven jobs. The caller supplies
    the jobs; there is no scan, no harvest, no watcher, and
    no job store read here. Auto-collection would fabricate
    proof (a job the caller never asserted as proven would
    still look proven), so it is refused by absence: no
    such function exists in this module.
  - It never applies, deploys, promotes, or installs the
    spec. The output has status PROPOSED and deployed False
    always. Applying a spec is a separate human C-14 action,
    outside this module, through the plan-approval gate.
  - It never writes a shipped file. The only file write in
    this build is the report tool (core/learning_last_tools.py),
    which writes only the spec record to a caller-chosen
    .json file in an already-existing folder. This module
    itself writes nothing, touches no store, and holds no
    run_goal callable, no funnel, and no deployer.
  - It never creates a second playbook store. The loop's
    single playbook store is J9; a spec's steps are a
    proposal, and a caller that wants them in J9 adds them
    through the existing J9 store itself, if it chooses.
  - It never trusts caller-claimed hashes, guards, or
    evidence. Diffs are re-validated and re-hashed by the
    Wave 7 proposal builder; guards must name tests; the
    evidence ledger is rebuilt from the proven jobs'
    proof references, with any caller extra evidence
    appended only after the same kind/ref/detail check.
  - It never lets a step approve itself. A binder step
    carrying an approval claim has the claim stripped by
    the WORK distillation; a send/delete/purchase step
    carries its C-14 gate requirement on the spec, and no
    approval flag is ever set here.
  - It never touches memory DB, vault, .env, or
    credentials as a spec target: the Wave 7 proposal
    builder refuses those paths, and the WORK validation
    refuses steps targeting them.

A spec without a regression guard is refused: a learned
change with no named test that must pass before and after
is not a spec, it is a wish. A spec without a diff is
refused for the same reason: the Wave 7 proposal shape
requires an exact diff, so a learning-last spec always
says precisely what would change, file by file.

Own code, stdlib only. No third-party dependency is
introduced by this file, so no licence read is owed by
this build itself (see LEARNLAST_LICENCE_READ_W8.md).
"""

from __future__ import annotations

from . import improvement_proposals as proposals
from . import workflow_learning as work

__all__ = [
    "build_learning_spec",
    "spec_from_json",
]

_MAX_JOBS = 50
_MAX_TITLE_CHARS = 200
_MAX_SUMMARY_CHARS = 2000


def _fail(reason: str) -> dict:
    return {"ok": False, "reason": reason, "spec": None}


def _coerce_jobs(jobs) -> tuple:
    """Turn caller-supplied jobs into WORK ProvenJobs, or refuse.

    Accepts ProvenJob objects or plain dicts with job_id,
    goal_text, binder, field_proven, proof_reference. Anything
    else — a string, a list entry without a binder, a job
    missing its id — is a refusal reason, never skipped.
    """
    if not isinstance(jobs, list) or not jobs:
        return None, "learning input is empty: a spec needs at least one proven job"
    if len(jobs) > _MAX_JOBS:
        return None, f"too many jobs (max {_MAX_JOBS})"
    proven = []
    for index, raw in enumerate(jobs):
        if isinstance(raw, work.ProvenJob):
            proven.append(raw)
            continue
        if not isinstance(raw, dict):
            return None, f"job {index}: must be an object or a ProvenJob"
        try:
            proven.append(work.ProvenJob(
                raw.get("job_id"),
                raw.get("goal_text"),
                raw.get("binder"),
                field_proven=raw.get("field_proven", False),
                proof_reference=raw.get("proof_reference", ""),
            ))
        except Exception as exc:  # noqa: BLE001
            return None, f"job {index}: cannot be read as a proven job: {exc}"
    return proven, None


def build_learning_spec(jobs, title, summary, diffs, regression_guards,
                        boundary_check=None, extra_evidence=None) -> dict:
    """Build a learning-last spec from proven jobs only.

    Returns {"ok": True, "spec": spec} or {"ok": False,
    "reason": ..., "spec": None}. Fail-closed whole: any
    refused part refuses the whole spec, no partial spec
    is ever returned. The spec changes nothing: it is a
    proposal (status PROPOSED, deployed False, executes
    nothing) whose learned behaviour is distilled from the
    proven jobs and whose exact change is the caller's
    diff, re-validated by the Wave 7 proposal builder.
    """
    if not isinstance(title, str) or not title.strip() \
            or len(title) > _MAX_TITLE_CHARS:
        return _fail("title must be a non-empty string")
    if not isinstance(summary, str) or not summary.strip() \
            or len(summary) > _MAX_SUMMARY_CHARS:
        return _fail("summary must be a non-empty string")
    proven, reason = _coerce_jobs(jobs)
    if reason:
        return _fail(reason)
    ok, reasons = work.validate_jobs(proven)
    if not ok:
        return _fail("learning refused: " + "; ".join(reasons))
    try:
        workflow = work.distil_workflows(proven, workflow_name=title.strip())
    except ValueError as exc:
        return _fail(f"learning refused: {exc}")
    # Evidence ledger, rebuilt from the proven jobs — never
    # trusted from the caller. One entry per proven job, keyed
    # on its proof reference, so the spec's justification can
    # be checked job by job against field runs.
    ledger = []
    for job in proven:
        ledger.append({
            "kind": "field_proven_job",
            "ref": job.proof_reference,
            "detail": f"proven job {job.job_id}: {job.goal_text}",
        })
    if extra_evidence is not None:
        if not isinstance(extra_evidence, list):
            return _fail("extra_evidence must be a list")
        for entry in extra_evidence:
            if not isinstance(entry, dict) \
                    or not str(entry.get("kind") or "").strip() \
                    or not str(entry.get("ref") or "").strip() \
                    or not str(entry.get("detail") or "").strip():
                return _fail(
                    "extra_evidence entries need kind, ref, and detail")
            ledger.append({
                "kind": str(entry["kind"]),
                "ref": str(entry["ref"]),
                "detail": str(entry["detail"]),
            })
    built = proposals.build_proposal(
        title, summary, diffs, regression_guards, ledger,
        boundary_check=boundary_check)
    if not built.get("ok"):
        return _fail(built.get("reason", "proposal refused"))
    # Learned behaviour, distilled — step texts with their
    # source job, gate requirement where one applies, and
    # duplicates folded and reported, never silently dropped.
    learned_steps = []
    for step in getattr(workflow, "steps", []):
        learned_steps.append({
            "step_id": getattr(step, "id", None),
            "text": getattr(step, "text", ""),
            "source_job_id": getattr(step, "source_job_id", None),
            "c14_requirement": getattr(step, "gate_requirement", None),
        })
    spec = {
        "kind": "LEARNING_LAST_SPEC",
        "status": "PROPOSED",
        "title": title,
        "summary": summary,
        "learned_from": {
            "workflow_name": getattr(workflow, "name", title),
            "source_job_ids": list(getattr(workflow, "sources", [])),
            "steps": learned_steps,
            "duplicates_folded": list(getattr(workflow, "duplicates", [])),
            "proof_references": [job.proof_reference for job in proven],
        },
        "proposal": built["proposal"],
        "deployed": False,
        "executes_nothing": True,
        "applies_to": None,
        "note": (
            "SPEC ONLY, NOT APPLIED. This spec changes no shipped "
            "behaviour. Applying it is a separate human C-14 action "
            "through the plan-approval gate, outside this module. "
            "This module never applies, deploys, promotes, or "
            "installs anything, and never collects jobs by itself: "
            "the caller supplies proven jobs, and unproven input "
            "refuses the whole spec."
        ),
    }
    return {"ok": True, "spec": spec}


def spec_from_json(text) -> dict:
    """Rebuild a spec from JSON text, re-validating everything.

    The JSON must carry jobs, title, summary, diffs, and
    regression_guards; it is passed through build_learning_spec
    (never trusted), so a hand-edited spec with an unproven
    job, a missing guard, or a tampered diff is refused.
    """
    import json

    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        return _fail(f"spec JSON is not valid: {exc}")
    if not isinstance(payload, dict):
        return _fail("spec JSON must be an object")
    return build_learning_spec(
        payload.get("jobs"),
        payload.get("title"),
        payload.get("summary"),
        payload.get("diffs"),
        payload.get("regression_guards"),
        boundary_check=payload.get("boundary_check"),
        extra_evidence=payload.get("extra_evidence"),
    )
