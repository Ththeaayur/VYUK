"""Triage Wire Build 3 — the six remaining non-decision-gated WIRE
leaves (#8-#10 plus cr_marking, dv_doctor, st_workspace) as live
paths, without modifying the leaves themselves (edition precedent:
leaves take no cross-module imports; this coordinator imports them,
the same split as ``core/study_flow_t1.py`` and
``core/creative_dev_social_t2.py``):

* ``business_identity`` — Windows-profile identity. Live path:
  a read-only ``session_identity`` tool AND a failure-tolerant
  startup resolution (see ``startup_identity``; ``main.py`` calls
  it the same report-never-block way it calls the updater,
  integrity, and milestone steps). Windows only, by the task's
  guard: a non-Windows platform reports identity as unavailable,
  never as a POSIX identity dressed up as a Windows profile, and
  an unresolvable profile raises instead of inventing a name.
  No password, token, or credential is ever read or stored.
* ``fleet_redaction`` — redaction before any diagnostics
  preview. Live path: ``diagnostics_preview`` builds the bundle
  through ``fleet_consent.build_bundle`` with
  ``fleet_redaction.redact_log_text`` as the ONLY redaction
  function — there is no unredacted-preview function in this
  coordinator, and no send pipeline exists anywhere in the tree
  (``fleet_consent`` has no send function by construction;
  sending is the user's own action in their own client). The
  preview therefore shows redacted text plus types-and-counts
  only, never a redacted value. A caller that builds a bundle
  without a redact function gets raw content from the leaf —
  that bypass path is named here and is NOT wired by this build.
* ``accessibility_forgiving_selection`` — forgiving selection. Live path: the
  ``forgiving_select`` tool ranks candidates by distance,
  requires an EXPLICIT confirm event (voice_select, switch_press,
  head_nod, typed_confirm — dwell alone is refused by the leaf),
  and shapes the result as a ``tools.execute`` descriptor. The
  leaf never clicks and never dispatches (INV-2/3); dispatch is
  the loop's funnel, which revalidates any named control through
  the control-breadth path (Wiring Build 2) when a snapshot is
  available. When the caller supplies a control snapshot, this
  coordinator revalidates the selected control through
  ``control_breadth.reuse_control`` itself and refuses a stale
  selection instead of returning a descriptor for it.
* ``creative_exif_marking`` — native EXIF/XMP writers. Live path: the
  ``creative_mark_image`` tool, called ONLY after
  ``creative_dev_social_t2.gate_export`` returns ALLOW for the
  caller's ledger asset — a blocked or warned gate refuses the
  marking, so this is not a standalone tool that bypasses the
  ledger gate. The written file is read back (EXIF + XMP) and
  must match the requested marking, or the export fails.
* ``developer_telemetry_doctor`` — read-only telemetry posture. Live path:
  a row on the existing Doctor page calling ``posture_report()``
  (see ``gui/doctor_page.py``); there is NO tool for it, because
  a posture report is a page surface, not a loop action, and the
  leaf has no writer to gate. Report-only, never edits settings.
* ``st_workspace`` — study planner / progress book. Live path:
  three confirm-gated study workspace tools (plan, progress,
  exam prep) writing JSON files BESIDE the memory DB, never new
  memory-DB columns (the leaf's binding rule; frozen schema and
  C-1 therefore do not gate this line). Assessments carry the
  leaf's limits (local quiz result only, never a credential or
  grade prediction); leaked papers and real-exam answer keys
  are refused; medical/regulated advice tags are refused.

Honesty limits, stated not hidden:

* Identity on a non-Windows machine is UNAVAILABLE, not POSIX.
  The lab runs Linux, so the live tool honestly reports
  unavailable there; field proof on HIS Windows machine (his
  USERNAME + USERPROFILE, nothing else) is owed after the quiz.
* Diagnostics preview is types-and-counts only; the native
  detectors are a log pass, and the Presidio pilot is NOT
  invoked here (pilot scope + Windows footprint still owed).
* A forgiving selection without a snapshot returns a descriptor
  only; control revalidation happens in the planner when the
  loop dispatches it with a snapshot. A descriptor is not a
  click, and no click happens inside this module.
* Marking writes marking bytes only; no compliance, certification,
  or Art. 50 claim is made (the leaf's own study note), and no
  C2PA signing or JUMBF embedding is built here.
* Study workspace files are VYUK-side records only; there is no
  two-way sync with any school system, and no fact-retention
  (ST-6) state is smuggled into them.
* Field proof on HIS machine (a real identity resolved, a real
  redacted preview, a real forgiving selection landing on the
  intended control, a real marked export read back, a real
  posture report, a real study plan) is owed after the BB101
  quiz and is not claimed here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_DEFAULT_LEDGER_FILENAME = "rights_ledger.db"


def _data_dir() -> Path:
    try:
        from .config import DATA_DIR as _DATA_DIR

        return Path(_DATA_DIR)
    except Exception:  # noqa: BLE001 — config import must not break the store
        return Path(__file__).resolve().parent.parent / "data"


def _default_ledger_path() -> str:
    return str(_data_dir() / _DEFAULT_LEDGER_FILENAME)


def _ok(payload):
    return json.dumps({"status": "ok", **payload})


def _err(message):
    return json.dumps({"status": "error", "message": str(message)})


# ---------------------------------------------------------------------------
# Windows-profile identity (business_identity) — startup + tool.
# ---------------------------------------------------------------------------

def session_identity(env=None, system=None):
    """Resolve the Windows-profile identity, or fail closed.

    ``env`` / ``system`` are injectable for tests only; production
    callers pass nothing. Non-Windows platforms and unresolvable
    profiles return an error dict, never a guessed identity and
    never a POSIX identity presented as a Windows profile.
    """
    from . import business_identity as _ident

    try:
        if system is None:
            import platform as _platform

            system = _platform.system()
        if system != "Windows":
            return {"status": "error",
                    "message": "Windows-profile identity unavailable: "
                               "platform is %s, not Windows" % (system,)}
        ident = _ident.resolve_identity(env=env, system=system)
    except Exception as exc:  # noqa: BLE001 — identity fails closed
        return {"status": "error", "message": str(exc)}
    return {"status": "ok", "identity": ident}


def startup_identity():
    """Failure-tolerant startup resolution for main.py.

    Returns the same shape as ``session_identity``; main.py prints
    it and never lets it stop the launch (the updater / integrity /
    milestone posture). Production callers pass no arguments.
    """
    return session_identity()


def session_identity_handler():
    """Read-only tool handler: the current Windows-profile identity."""
    result = session_identity()
    if result["status"] != "ok":
        return _err(result["message"])
    return _ok({"identity": result["identity"]})


# ---------------------------------------------------------------------------
# Diagnostics preview, always redacted (fleet_redaction + fleet_consent).
# ---------------------------------------------------------------------------

def diagnostics_preview(sections_json):
    """Build a diagnostics bundle whose every section is redacted.

    ``sections_json`` is a JSON list of {name, content}. The ONLY
    redaction function ever passed to the bundle builder is
    ``fleet_redaction.redact_log_text``. There is deliberately no
    parameter, flag, or second function in this coordinator that
    builds a bundle without it: the leaf's unredacted build path
    (``build_bundle`` with no redact function) is the bypass path
    and is NOT wired here. No send exists: the preview is the
    exact bundle the user would send, in their own client.
    """
    from . import fleet_consent as _consent
    from . import fleet_redaction as _redact

    sections = json.loads(sections_json)
    if not isinstance(sections, list):
        raise ValueError("sections_json must be a JSON list of sections")
    bundle = _consent.build_bundle(sections, redact_fn=_redact.redact_log_text)
    return {"preview": _consent.preview_bundle(bundle),
            "sections": bundle["sections"]}


def diagnostics_preview_handler(sections_json):
    try:
        return _ok(diagnostics_preview(sections_json))
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


# ---------------------------------------------------------------------------
# Forgiving selection (accessibility_forgiving_selection) — descriptor + control path.
# ---------------------------------------------------------------------------

def forgiving_select(point, candidates, confirm_event, grid=None,
                     snapshot=None):
    """Rank, confirm, and shape a selection for tools.execute.

    ``point`` is [x, y] in the candidates' coordinate space,
    ``candidates`` a list of {id, x, y} (a control snapshot's
    controls, with id = control_id, work directly), ``grid`` an
    optional build_grid result (point may instead be a cell id
    string naming one of its cells). ``confirm_event`` must be an
    explicit confirm; dwell alone is refused by the leaf. When a
    control ``snapshot`` (list of control records, the shape
    control_breadth expects) is supplied, the selected control is
    revalidated through reuse_control and a stale selection is
    refused instead of returned. Without a snapshot the descriptor
    is returned with the note that planner revalidation happens
    at dispatch time; the descriptor itself performs no action.
    """
    from . import accessibility_forgiving_selection as _sel
    from .desktop import control_breadth as _breadth  # Phase 3 D2b: control_breadth moved to core/desktop/

    if isinstance(point, str):
        if not grid:
            raise ValueError("a cell-id point needs a grid")
        cell = [c for c in grid["cells"] if c["id"] == point]
        if not cell:
            raise ValueError("cell id %r is not in the grid" % (point,))
        ranked = [{"id": cell[0]["id"], "distance": 0.0, "confidence": 1.0}]
    else:
        ranked = _sel.rank_candidates(tuple(point), list(candidates),
                                      tolerance=0.35)
    if not ranked:
        raise ValueError("no candidate within tolerance: the product "
                         "must ask, not guess a target")
    top = ranked[0]
    confirmed = _sel.confirm_selection(top["id"], confirm_event)
    descriptor = _sel.to_execute_descriptor(confirmed)
    if snapshot is None:
        descriptor["note"] = ("dispatch through tools.execute; the "
                              "planner revalidates a named control "
                              "through control breadth when a snapshot "
                              "is available at dispatch time")
        return {"descriptor": descriptor, "ranked": ranked,
                "control_revalidated": False}
    record = None
    for entry in snapshot:
        if str(entry.get("control_id", "")) == str(top["id"]) or \
                str(entry.get("id", "")) == str(top["id"]):
            record = entry
            break
    if record is None:
        raise ValueError("selected control %r is not in the snapshot: "
                         "re-observe before selecting" % (top["id"],))
    from .desktop.control_hardening import ControlRecord as _ControlRecord  # Phase 3 D2b: control_hardening moved to core/desktop/

    # The record's fingerprint must come from the CANDIDATE (the
    # older observation the selection was made from), NOT from
    # the current snapshot entry — building it from the snapshot
    # would make revalidation vacuous, always passing. A
    # candidate with no fingerprint revalidates only on id
    # presence, and the leaf's fingerprint comparison then treats
    # the empty fingerprint as the recorded one.
    _candidate = None
    for cand in candidates:
        if str(cand.get("id", "")) == str(top["id"]):
            _candidate = cand
            break
    check = _breadth.reuse_control(
        _ControlRecord(control_id=str(top["id"]),
                       label=str(record.get("name", "")
                                 or record.get("label", "")),
                       fingerprint=str((_candidate or {}).get(
                           "fingerprint", ""))),
        list(snapshot))
    if not check.get("ok", False):
        raise ValueError("selected control is stale or absent: %s"
                         % (check.get("reason", check),))
    descriptor["control_action"] = {
        "tool": "uia_click_control",
        "args": {"control_id": top["id"]},
        "revalidated": True,
    }
    return {"descriptor": descriptor, "ranked": ranked,
            "control_revalidated": True}


def forgiving_select_handler(point_json, candidates_json, confirm_event,
                             grid_json=None, snapshot_json=None):
    try:
        point = json.loads(point_json)
        candidates = json.loads(candidates_json)
        grid = json.loads(grid_json) if grid_json else None
        snapshot = json.loads(snapshot_json) if snapshot_json else None
        return _ok(forgiving_select(point, candidates, confirm_event,
                                    grid=grid, snapshot=snapshot))
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


# ---------------------------------------------------------------------------
# Creative marking, gated (creative_exif_marking + cr_ledger gate, Build 2).
# ---------------------------------------------------------------------------

def mark_image(input_path, output_path, asset_id, ledger_path=None,
               copyright_notice=None, credit_line=None, licensor=None,
               web_statement=None, digital_source_type=None):
    """Mark a JPEG with EXIF + XMP, only after a cleared ledger gate.

    ``gate_export`` (Build 2) must return ALLOW for the caller's
    ledger asset, or this raises and writes nothing — marking is
    not a standalone path around the rights ledger. The output is
    read back (EXIF + XMP) and must carry the requested marking,
    or the export fails instead of shipping an unmarked file.
    """
    from . import creative_dev_social_t2 as _t2
    from . import creative_exif_marking as _mark

    verdict = _t2.gate_export(ledger_path or _default_ledger_path(),
                              asset_id)
    with open(input_path, "rb") as handle:
        jpeg = handle.read()
    marked = jpeg
    if copyright_notice is not None or credit_line is not None:
        marked = _mark.write_exif(marked,
                                  copyright_notice=copyright_notice,
                                  credit_line=credit_line)
    packet = _mark.build_xmp_packet(
        copyright_notice=copyright_notice, credit_line=credit_line,
        web_statement=web_statement, licensor=licensor,
        digital_source_type=digital_source_type)
    marked = _mark.embed_xmp(marked, packet)
    back_exif = _mark.read_exif(marked)
    back_xmp = _mark.read_xmp(marked)
    if copyright_notice is not None and \
            back_exif.get("copyright") != copyright_notice:
        raise ValueError("marked EXIF does not carry the copyright "
                         "notice; export failed, nothing written")
    if back_xmp is None or (copyright_notice is not None and
                            copyright_notice.encode() not in back_xmp):
        raise ValueError("marked XMP does not carry the copyright "
                         "notice; export failed, nothing written")
    parent = os.path.dirname(os.path.abspath(output_path))
    if not os.path.isdir(parent):
        raise ValueError("output parent folder must exist: %s" % (parent,))
    with open(output_path, "wb") as handle:
        handle.write(marked)
    return {"output_path": str(output_path),
            "gate_verdict": verdict.get("verdict"),
            "exif": back_exif, "xmp_present": True}


def creative_mark_image_handler(input_path, output_path, asset_id,
                                ledger_path=None, copyright_notice=None,
                                credit_line=None, licensor=None,
                                web_statement=None,
                                digital_source_type=None):
    try:
        return _ok(mark_image(input_path, output_path, int(asset_id),
                              ledger_path=ledger_path,
                              copyright_notice=copyright_notice,
                              credit_line=credit_line, licensor=licensor,
                              web_statement=web_statement,
                              digital_source_type=digital_source_type))
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


# ---------------------------------------------------------------------------
# Study workspace (st_workspace) — plan, progress, exam prep.
# ---------------------------------------------------------------------------

def _workspace(workspace_dir):
    from . import st_workspace as _ws

    return _ws.StudyWorkspace(workspace_dir)


def study_plan(workspace_dir, goals_json, topics_json,
               time_budget_minutes=60):
    """Create/adapt a learning plan and save it as plan.json.

    ``goals_json`` is a JSON list of goal strings; ``topics_json``
    a JSON list of {key, title, prerequisites}. The plan adapts
    (unfinished topics with complete prerequisites first) through
    the leaf's own ordering, and is saved beside the memory DB,
    never in it.
    """
    from . import st_workspace as _ws

    goals = json.loads(goals_json)
    topics = json.loads(topics_json)
    plan = _ws.LearningPlan(goals, topics,
                            time_budget_minutes=int(time_budget_minutes))
    ws = _workspace(workspace_dir)
    path = ws.save("plan.json", plan)
    return {"plan_path": path, "plan": plan.to_dict()
            if hasattr(plan, "to_dict") else
            {"goals": plan.goals, "topics": plan.topics,
             "time_budget_minutes": plan.time_budget_minutes}}


def study_progress(workspace_dir, completed_json=None,
                   assessment_json=None):
    """Record completed material / an assessment into progress.json."""
    from . import st_workspace as _ws

    ws = _workspace(workspace_dir)
    progress = _ws.Progress()
    if completed_json:
        for key in json.loads(completed_json):
            progress.complete(key)
    if assessment_json:
        data = json.loads(assessment_json)
        progress.record_assessment(
            _ws.Assessment(data["subject"], data["results"],
                           tags=data.get("tags", ())))
    path = ws.save("progress.json", progress)
    return {"progress_path": path, "progress": progress.to_dict()}


def study_exam_prep(workspace_dir, exam, syllabi_json=None,
                    sessions_json=None, papers_json=None):
    """Organise exam prep and save it as exam_prep.json.

    Leaked papers and real-exam answer keys are refused by the
    leaf; medical/regulated advice tags are refused by the leaf's
    boundary check (callers pass tags through session topics
    only — this handler refuses a paper flagged leaked/key).
    """
    from . import st_workspace as _ws

    prep = _ws.ExamPrep(exam, syllabi=json.loads(syllabi_json)
                        if syllabi_json else ())
    for session in (json.loads(sessions_json) if sessions_json else []):
        prep.add_session(session["topic"], session["minutes"])
    for paper in (json.loads(papers_json) if papers_json else []):
        prep.add_practice_paper(paper["title"],
                                leaked=bool(paper.get("leaked", False)),
                                real_exam_answer_key=bool(
                                    paper.get("real_exam_answer_key",
                                              False)))
    ws = _workspace(workspace_dir)
    path = ws.save("exam_prep.json", prep)
    return {"exam_prep_path": path, "exam_prep": prep.to_dict()}


def study_plan_handler(workspace_dir, goals_json, topics_json,
                       time_budget_minutes=60):
    try:
        return _ok(study_plan(workspace_dir, goals_json, topics_json,
                              time_budget_minutes=time_budget_minutes))
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def study_progress_handler(workspace_dir, completed_json=None,
                           assessment_json=None):
    try:
        return _ok(study_progress(workspace_dir,
                                  completed_json=completed_json,
                                  assessment_json=assessment_json))
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def study_exam_prep_handler(workspace_dir, exam, syllabi_json=None,
                            sessions_json=None, papers_json=None):
    try:
        return _ok(study_exam_prep(workspace_dir, exam,
                                   syllabi_json=syllabi_json,
                                   sessions_json=sessions_json,
                                   papers_json=papers_json))
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


# ---------------------------------------------------------------------------
# Registration (the module owns its catalogue, as t1/t2 do).
# ---------------------------------------------------------------------------

def register_all(registry) -> None:
    """Register the seven Triage Wire Build 3 tools.

    Read-only: session_identity, diagnostics_preview,
    forgiving_select. Confirm-gated writers (write parity):
    creative_mark_image (output_path), study_plan,
    study_progress, study_exam_prep (workspace_dir — the folder
    the handler writes its JSON record into).
    """
    registry.register(
        name="session_identity",
        description="Report the current Windows-profile identity (USERNAME + USERPROFILE from the OS profile, never a VYUK-minted account). On a non-Windows machine, or when no profile resolves, it reports identity as unavailable instead of inventing a name. No password, token, or credential is read or stored. Use when the user asks whose profile the app is running as.",
        parameters={"type": "object", "properties": {}},
        handler=session_identity_handler,
    )
    registry.register(
        name="diagnostics_preview",
        description="Build a diagnostics bundle preview that is ALWAYS redacted first: every section passes through the native redaction engine (emails, IPs, paths, API-key shapes replaced by type tags) and the preview shows redacted text plus types-and-counts only, never a redacted value. There is no send in this tool — the user sends the previewed bundle themselves in their own client. Use when the user wants to preview a diagnostics bundle before sending it.",
        parameters={"type": "object", "properties": {
            "sections_json": {"type": "string", "description": "JSON list of {name, content} sections to preview"},
        }, "required": ["sections_json"]},
        handler=diagnostics_preview_handler,
    )
    registry.register(
        name="forgiving_select",
        description="Make a forgiving selection from noisy input (voice grid, head pose, or a point): rank candidates by distance, require an explicit confirm event (voice_select, switch_press, head_nod, typed_confirm — dwell alone never confirms), and return a tools.execute descriptor for the confirmed target. It never clicks or dispatches itself; the loop dispatches the descriptor through its gates, revalidating a named control when a snapshot is available. A point with no candidate in tolerance is an error, never a guessed target. Use when the user selects by grid, head pattern, or imprecise point.",
        parameters={"type": "object", "properties": {
            "point_json": {"type": "string", "description": "JSON [x, y] point, or a JSON string naming a grid cell id"},
            "candidates_json": {"type": "string", "description": "JSON list of {id, x, y} candidates in the same coordinate space"},
            "confirm_event": {"type": "string", "enum": ["voice_select", "switch_press", "head_nod", "typed_confirm"], "description": "Explicit confirm event"},
            "grid_json": {"type": "string", "description": "Optional JSON grid (from a voice grid) when point_json names a cell id"},
            "snapshot_json": {"type": "string", "description": "Optional JSON control snapshot; when supplied the selected control is revalidated and a stale selection is refused"},
        }, "required": ["point_json", "candidates_json", "confirm_event"]},
        handler=forgiving_select_handler,
    )
    registry.register(
        name="creative_mark_image",
        description="Mark a JPEG with EXIF copyright/artist and an XMP packet (copyright, creator, credit, web statement, digital source type) using native stdlib writers only. It runs ONLY after the rights ledger gate clears the caller's asset (asset_id in the app's rights ledger); a blocked or warned gate refuses and writes nothing. The output is read back and must carry the requested marking, or the export fails. The file write asks for confirmation first. Use when the user wants a creative export marked with its rights information.",
        parameters={"type": "object", "properties": {
            "input_path": {"type": "string", "description": "Existing JPEG file to mark"},
            "output_path": {"type": "string", "description": "File path to write the marked JPEG to, in an existing folder"},
            "asset_id": {"type": "integer", "description": "Rights-ledger asset id; the ledger gate must return ALLOW for it"},
            "ledger_path": {"type": "string", "description": "Optional ledger file path; omit to use the app's own rights ledger file"},
            "copyright_notice": {"type": "string", "description": "Copyright notice to write"},
            "credit_line": {"type": "string", "description": "Credit line to write (EXIF Artist + XMP Credit)"},
            "licensor": {"type": "string", "description": "Licensor name (XMP creator)"},
            "web_statement": {"type": "string", "description": "Rights web statement URL"},
            "digital_source_type": {"type": "string", "description": "Controlled vocabulary digital source type, e.g. digitalCapture"},
        }, "required": ["input_path", "output_path", "asset_id"]},
        handler=creative_mark_image_handler,
    )
    registry.register(
        name="study_plan",
        description="Create a study learning plan (goals, topics with prerequisites, time budget) and save it as plan.json in the caller's study workspace folder. Plans live as files beside the memory DB, never in it. The file write asks for confirmation first. Use when the user wants a study plan for their topics.",
        parameters={"type": "object", "properties": {
            "workspace_dir": {"type": "string", "description": "Study workspace folder to write plan.json into"},
            "goals_json": {"type": "string", "description": "JSON list of goal strings"},
            "topics_json": {"type": "string", "description": "JSON list of {key, title, prerequisites} topics"},
            "time_budget_minutes": {"type": "integer", "description": "Time budget in minutes (default 60)"},
        }, "required": ["workspace_dir", "goals_json", "topics_json"]},
        handler=study_plan_handler,
    )
    registry.register(
        name="study_progress",
        description="Record study progress (completed material keys and/or one local assessment {subject, results: [{topic, score, total}]}) and save it as progress.json in the caller's study workspace folder. An assessment is a local quiz result only, never a credential or grade prediction. The file write asks for confirmation first. Use when the user finishes material or completes a practice assessment.",
        parameters={"type": "object", "properties": {
            "workspace_dir": {"type": "string", "description": "Study workspace folder to write progress.json into"},
            "completed_json": {"type": "string", "description": "Optional JSON list of completed material keys"},
            "assessment_json": {"type": "string", "description": "Optional JSON assessment {subject, results, optional tags}"},
        }, "required": ["workspace_dir"]},
        handler=study_progress_handler,
    )
    registry.register(
        name="study_exam_prep",
        description="Organise exam preparation (syllabi, revision sessions, practice papers) and save it as exam_prep.json in the caller's study workspace folder. Leaked papers and real-exam answer keys are refused, never stored. The file write asks for confirmation first. Use when the user wants exam-prep organisation for an exam.",
        parameters={"type": "object", "properties": {
            "workspace_dir": {"type": "string", "description": "Study workspace folder to write exam_prep.json into"},
            "exam": {"type": "string", "description": "Exam name"},
            "syllabi_json": {"type": "string", "description": "Optional JSON list of syllabus names"},
            "sessions_json": {"type": "string", "description": "Optional JSON list of {topic, minutes} revision sessions"},
            "papers_json": {"type": "string", "description": "Optional JSON list of {title, leaked, real_exam_answer_key} practice papers"},
        }, "required": ["workspace_dir", "exam"]},
        handler=study_exam_prep_handler,
    )


__all__ = [
    "session_identity", "startup_identity", "diagnostics_preview",
    "forgiving_select", "mark_image", "study_plan", "study_progress",
    "study_exam_prep", "register_all",
]
