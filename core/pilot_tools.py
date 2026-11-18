"""Workshop, student, and dev PILOT slices as live tools.

Triage Wire pattern, as in core/gaming_tools.py:
this coordinator imports the pilot module, registers
tools via register_all(registry), each handler takes the
named schema fields, forwards to the module, and returns
the module result encoded as a JSON string verbatim;
refusal dicts flow the same way, and module raises become
an error JSON — handlers never raise.

Wiring (Wave 9 Build 8, P15 / P16 / P17 PILOT slices):
  * pilot_bench — read-only. It returns a
    project-bench descriptor from a caller-supplied
    project name plus bench notes (P15-7 lab notebook,
    record form only). Nothing is probed, photographed,
    or measured.
  * pilot_build_steps — read-only. It returns a
    build-step descriptor from caller-supplied step
    labels plus an assumptions note (P15-10 blueprint
    plan record; P16-10 worked steps). An action-shaped
    step refuses whole; the steps are never run.
  * pilot_study_session — read-only. It returns a
    study-session descriptor from a caller-supplied
    subject, materials, and questions (P16-6 quiet
    question bank, record form). Every material carries
    its source as supplied; a material without a source,
    or flagged protected, refuses the whole session.
    Nothing is summarised, generated, or read live.
  * pilot_revision_plan — read-only. It returns a
    revision-plan descriptor from caller-supplied topics
    in order (P16-2 study plan that bends, plan record;
    P16-12 concept maps, topics record). A plan
    schedules nothing and sets no reminder.
  * pilot_dev_workflow — read-only. It returns a
    dev-workflow descriptor from a caller-supplied repo
    label plus workflow step labels (P17-1 terminal
    dry-run, plan record; P17-4 policy as code, rule
    labels as steps). Never run, never deployed, never
    signed; the repo is never read.
  * pilot_review_note — read-only. It returns a
    code-review-note descriptor from caller-supplied
    file:line cites plus notes (P17-11 stack trace to
    cause, cite-and-note form; P17-12 pre-push
    guardian, check notes only; P17-14 diff storyteller,
    caller-supplied summary notes plus cites). No cause
    stated, no check run, no summary generated, nothing
    approved or merged.
  * pilot_record_pilot — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-validates the pilot
    descriptor itself (it never trusts a caller-supplied
    pilot JSON, which could claim anything) and writes
    only the pilot record to a caller-chosen .json file
    in an already-existing folder — no folder creation,
    no overwrite of a core/data path, no .env/.pem/.key
    target. The funnel refuses it without a human confirm
    handler, before the handler runs. Recording a pilot
    descriptor runs nothing, reads nothing live, deploys
    nothing, and signs nothing.

This coordinator is NOT an executor, a reader, or a
signer: it imports no subprocess, eval, exec, network,
or file-reading code, runs no step, reads no repo,
deploys nothing, and signs nothing. Any execute, run,
deploy, install, promote, push, merge, sign, or apply
input refuses whole in the module.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import pilot as pilot_mod


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


def pilot_bench_handler(project_name, bench_notes=None,
                                action="describe"):
    return _handler(pilot_mod.build_project_bench,
                    project_name, bench_notes, action)


def pilot_build_steps_handler(steps, assumptions=None,
                              action="describe"):
    return _handler(pilot_mod.build_build_steps,
                    steps, assumptions, action)


def pilot_study_session_handler(subject, materials, questions=None,
                                action="describe"):
    return _handler(pilot_mod.build_study_session,
                    subject, materials, questions, action)


def pilot_revision_plan_handler(topics, action="describe"):
    return _handler(pilot_mod.build_revision_plan,
                    topics, action)


def pilot_dev_workflow_handler(repo_label, steps, action="describe"):
    return _handler(pilot_mod.build_dev_workflow,
                    repo_label, steps, action)


def pilot_review_note_handler(cites, notes, action="describe"):
    return _handler(pilot_mod.build_review_note,
                    cites, notes, action)


def _check_output_path(output_path):
    try:
        target = Path(output_path).expanduser()
        if target.suffix != ".json":
            return None, "output_path must end in .json"
        if not target.parent.is_dir():
            return None, "output_path parent folder must already exist"
        if target.name in (".env", "assistant.db") \
                or target.name.endswith((".pem", ".key")):
            return None, "credential target refused"
        tree = Path(__file__).resolve().parent.parent
        try:
            resolved = target.resolve()
            if resolved.is_relative_to(tree / "core") \
                    or resolved.is_relative_to(tree / "data"):
                return None, "app core/data paths refused"
        except (OSError, ValueError):
            return None, "output_path cannot be resolved"
    except Exception as exc:  # noqa: BLE001
        return None, f"output_path cannot be resolved: {exc}"
    return target, None


def pilot_record_pilot_handler(record_kind, record_json, output_path):
    fields, err = _parse_json(record_json, "record_json")
    if err:
        return _error(err)
    if not isinstance(fields, dict):
        return _error("record_json must be a JSON object")
    try:
        rebuilt = pilot_mod.validate_recordable_pilot(record_kind,
                                                      fields)
    except (ValueError, TypeError) as exc:
        return _error(f"pilot descriptor does not re-build: {exc}")
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "PILOT_RECORD",
        "record_kind": record_kind.strip(),
        "pilot": rebuilt,
        "executed": False,
        "deployed": False,
        "signed": False,
        "read_live": False,
        "note": ("A pilot descriptor the user chose, "
                 "recorded only. Recording runs nothing, "
                 "reads nothing live, deploys nothing, "
                 "and signs nothing."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "PILOT_RECORD",
                       "executed": False, "executes_nothing": True,
                       "deployed": False, "signed": False,
                       "read_live": False,
                       "report_path": str(target),
                       "note": "Pilot descriptor recorded; nothing "
                               "was run, read live, deployed, or "
                               "signed."})


def register_all(registry) -> None:
    """Register the 7 pilot tools."""
    registry.register(
        name="pilot_bench",
        description="DESCRIPTOR ONLY, NOTHING PROBED OR MEASURED: return a project-bench descriptor from a caller-supplied project name plus bench notes (P15-7 lab notebook, record form only). Nothing is probed, photographed, or measured, and no bench, file, or device is touched. Any claim of a live read or measurement refuses the whole call.",
        parameters={"type": "object", "properties": {
            "project_name": {"type": "string",
                             "description": "Caller-supplied project name"},
            "bench_notes": {"type": "string",
                            "description": "Caller-supplied bench notes"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["project_name"]},
        handler=pilot_bench_handler,
    )
    registry.register(
        name="pilot_build_steps",
        description="DESCRIPTOR ONLY, NEVER RUN: return a build-step descriptor from caller-supplied step labels plus an assumptions note (P15-10 blueprint plan record; P16-10 worked steps). An action-shaped step (execute, run, deploy, install, promote, push, merge, sign, apply) refuses the whole descriptor. Describing steps does not perform them, and no diagram is drawn or exported.",
        parameters={"type": "object", "properties": {
            "steps": {"type": "array", "items": {"type": "string"},
                      "description": "Caller-supplied step labels, in order"},
            "assumptions": {"type": "string",
                            "description": "Caller-supplied assumptions note"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["steps"]},
        handler=pilot_build_steps_handler,
    )
    registry.register(
        name="pilot_study_session",
        description="DESCRIPTOR ONLY, NOTHING GENERATED OR READ LIVE: return a study-session descriptor from a caller-supplied subject, materials, and questions (P16-6 quiet question bank, record form). Every material must carry its source as supplied; a material without a source, or flagged protected, refuses the whole session. Nothing is summarised, no question or answer is generated, and nothing is read from a file or portal.",
        parameters={"type": "object", "properties": {
            "subject": {"type": "string",
                        "description": "Caller-supplied subject"},
            "materials": {"type": "array", "items": {"type": "object"},
                          "description": "Caller-supplied materials, each with title and source"},
            "questions": {"type": "array", "items": {"type": "string"},
                          "description": "Caller-supplied questions, in order"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["subject", "materials"]},
        handler=pilot_study_session_handler,
    )
    registry.register(
        name="pilot_revision_plan",
        description="PLAN RECORD ONLY, NOTHING SCHEDULED: return a revision-plan descriptor from caller-supplied topics in order (P16-2 study plan that bends, plan record; P16-12 concept maps, topics record). A plan schedules nothing and sets no reminder; day-by-day scheduling and reshaping on a missed day are not built. Any schedule or remind action refuses the whole call.",
        parameters={"type": "object", "properties": {
            "topics": {"type": "array", "items": {"type": "string"},
                       "description": "Caller-supplied topic labels, in order"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["topics"]},
        handler=pilot_revision_plan_handler,
    )
    registry.register(
        name="pilot_dev_workflow",
        description="DESCRIPTOR ONLY, NEVER RUN, NEVER DEPLOYED, NEVER SIGNED: return a dev-workflow descriptor from a caller-supplied repo label plus workflow step labels (P17-1 terminal dry-run, plan record; P17-4 policy as code, rule labels as steps). An action-shaped step refuses the whole workflow. The repo is never read, no terminal command is built, nothing is deployed, and nothing is signed.",
        parameters={"type": "object", "properties": {
            "repo_label": {"type": "string",
                           "description": "Caller-supplied repo label, never read"},
            "steps": {"type": "array", "items": {"type": "string"},
                      "description": "Caller-supplied step labels, in order"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["repo_label", "steps"]},
        handler=pilot_dev_workflow_handler,
    )
    registry.register(
        name="pilot_review_note",
        description="DESCRIPTOR ONLY, NOTHING APPROVED OR MERGED: return a code-review-note descriptor from caller-supplied file:line cites plus notes (P17-11 stack trace to cause, cite-and-note form; P17-12 pre-push guardian, check notes only; P17-14 diff storyteller, caller-supplied summary notes plus cites). No cause is stated, no check is run, no summary is generated, and nothing is approved or merged.",
        parameters={"type": "object", "properties": {
            "cites": {"type": "array", "items": {"type": "string"},
                      "description": "Caller-supplied file:line cites"},
            "notes": {"type": "string",
                      "description": "Caller-supplied review notes"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["cites", "notes"]},
        handler=pilot_review_note_handler,
    )
    registry.register(
        name="pilot_record_pilot",
        description="RECORD ONLY, NOTHING RUN, READ, DEPLOYED, OR SIGNED: re-validate a caller-supplied pilot descriptor (bench, build-steps, study-session, revision-plan, dev-workflow, or review-note) by re-running the builder that made it, and write only the pilot record to a caller-chosen .json file in an already-existing folder. A descriptor that does not re-build is refused, never written on trust. The record asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "record_kind": {"type": "string",
                            "description": "One of bench, build-steps, study-session, revision-plan, dev-workflow, review-note"},
            "record_json": {"type": "string",
                            "description": "JSON object of the pilot fields to re-validate"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["record_kind", "record_json", "output_path"]},
        handler=pilot_record_pilot_handler,
    )
