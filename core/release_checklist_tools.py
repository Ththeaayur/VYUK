"""Wave 8 Build 2 tools — release checklist + installer validation
as live tools.

Triage Wire pattern, as in core/anticipation_grant_tools.py: this coordinator
imports the checklist module, registers tools via register_all,
each handler takes the named schema fields, forwards to the module,
and returns the module result encoded as a JSON string verbatim;
refusal dicts flow the same way, and module raises become an error
JSON — handlers never raise.

Wiring (Wave 8 Build 2, Track L remainder):
  * release_checklist — read-only. Evaluates the release
    checklist against caller-supplied evidence. It verifies
    nothing itself: the manifest verdict, battery verdict, and
    CC-1 report it is given are the gate's own verdicts, and a
    missing evidence field fails its check, never defaults to a
    pass. Signing, feed hosting, and installer compilation appear
    in every result as DEFERRED, never as passed or built.
  * installer_validate — read-only. Validates an installer
    artifact's structure against the checklist: required files,
    version form and consistency, no signed claim, no live-feed
    claim. Validation only — it compiles nothing, signs nothing,
    hosts nothing, and executes nothing.
  * release_checklist_report — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-evaluates the checklist
    itself (it never trusts caller-supplied verdict JSON) and
    writes only the checklist report to a caller-chosen .json
    file whose parent folder must already exist — no folder
    creation, no core/data path, no .env/.pem/.key target. The
    funnel refuses it without a human confirm handler, before
    the handler runs. Writing the report is not shipping the
    build, and a PASS verdict here is a checklist verdict, not
    a field proof: the deferred items still stand.

This coordinator is NOT a gate owner: confirm-gating is the
funnel's job (core/funnel/tools_gate.py classification). It grants no
CC-1 failure, signs nothing, compiles no installer, and hosts
no feed.

Own code, stdlib only. No third-party dependency is introduced
by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import release_checklist as checklist_mod


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


def release_checklist_handler(evidence_json):
    evidence, err = _parse_json(evidence_json, "evidence_json")
    if err:
        return _error(err)
    if not isinstance(evidence, dict):
        return _error("evidence_json must be a JSON object")
    return _handler(checklist_mod.evaluate_checklist, evidence)


def installer_validate_handler(artifact_json):
    artifact, err = _parse_json(artifact_json, "artifact_json")
    if err:
        return _error(err)
    if not isinstance(artifact, dict):
        return _error("artifact_json must be a JSON object")
    return _handler(checklist_mod.validate_installer_artifact, artifact)


def release_checklist_report_handler(evidence_json, output_path):
    evidence, err = _parse_json(evidence_json, "evidence_json")
    if err:
        return _error(err)
    if not isinstance(evidence, dict):
        return _error("evidence_json must be a JSON object")
    result = checklist_mod.evaluate_checklist(evidence)
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
    return json.dumps({"ok": True, "kind": "RELEASE_CHECKLIST",
                       "verdict": result["verdict"],
                       "executes_nothing": True,
                       "report_path": str(target),
                       "failures": result["failures"],
                       "deferred": [d["item"] for d in
                                    result["deferred"]],
                       "note": "Checklist report written; this is a "
                               "checklist verdict, not a ship, and "
                               "signing, feed hosting, and installer "
                               "compilation remain DEFERRED."})


def register_all(registry) -> None:
    """Register the 3 Wave 8 Build 2 Track L tools."""
    registry.register(
        name="release_checklist",
        description="RELEASE CHECKLIST ONLY, NOT A SHIP: evaluate the release checklist (version single-source, signed manifest integrity, data baseline, registry/catalogue grouping, battery, CC-1 report, installer files) against caller-supplied evidence. One failed required check blocks the verdict; signing, feed hosting, and installer compilation appear as DEFERRED, never as passed or built. This tool verifies no signature and runs no battery itself — the verdicts it is given are the gate's own.",
        parameters={"type": "object", "properties": {
            "evidence_json": {"type": "string",
                              "description": "JSON object of checklist evidence (version, manifest_ok, manifest_checked, data_bytes, registry_total, catalogue_total, catalogue_more_skills, battery_ok, battery_failures, cc1_report_present, cc1_true_exit, cc1_failures, cc1_grants, installer_files_present); a missing field fails its check, never defaults to a pass"},
        }, "required": ["evidence_json"]},
        handler=release_checklist_handler,
    )
    registry.register(
        name="installer_validate",
        description="INSTALLER VALIDATION ONLY, NOT A COMPILE, NOT A SIGN, NOT A FEED: validate an installer artifact's structure against the checklist — required files present, version in semver form and consistent with the manifest version, no signed claim, no live-feed claim. A malformed artifact is refused whole with every defect named. This tool compiles no installer, signs nothing, and hosts nothing.",
        parameters={"type": "object", "properties": {
            "artifact_json": {"type": "string",
                              "description": "JSON object {files, version, manifest_version, claims_signed, claims_feed}; claims_signed or claims_feed true is a defect, because signing and feed hosting are DEFERRED"},
        }, "required": ["artifact_json"]},
        handler=installer_validate_handler,
    )
    registry.register(
        name="release_checklist_report",
        description="CHECKLIST REPORT ONLY, NOT A SHIP: re-evaluate the release checklist and write only the report to a caller-chosen .json file in an already-existing folder. Signing, feed hosting, and installer compilation remain DEFERRED. The report asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "evidence_json": {"type": "string",
                              "description": "JSON object of checklist evidence, as for release_checklist"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["evidence_json", "output_path"]},
        handler=release_checklist_report_handler,
    )
