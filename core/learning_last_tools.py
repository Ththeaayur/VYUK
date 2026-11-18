"""Wave 8 Build 5 tools — learning-last continuation, as live tools.

Triage Wire pattern, as in core/feel_tools.py: this
coordinator imports the learning-last module, registers tools
via register_all(registry), each handler takes the named schema
fields, forwards to the module, and returns the module result
encoded as a JSON string verbatim; refusal dicts flow the same
way, and module raises become an error JSON — handlers never
raise.

Wiring (Wave 8 Build 5, two tools):
  * learnlast_spec — read-only. Builds a learning-last spec
    from caller-supplied proven jobs plus a proposed diff,
    regression guards, and optional extra evidence. The jobs
    are re-validated as proven-only (Wave 6 WORK rule) and
    the diff is re-validated by the Wave 7 proposal builder;
    nothing is trusted from the caller. A spec changes no
    shipped behaviour and is never applied by this tool.
  * learnlast_report — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-builds the spec itself
    (it never trusts caller-supplied spec JSON) and writes
    only the spec record to a caller-chosen .json file in an
    already-existing folder. The funnel refuses it without a
    human confirm handler, before the handler runs. Writing
    the report is not applying the spec — application is a
    separate human C-14 action, outside this module.

This coordinator is NOT a gate owner: confirm-gating is the
funnel's job (core/funnel/tools_gate.py classification). No
auto-collection, no auto-execution, no self-deployment, no
M4 itself, no payment surface, no voiceprint, no continuous
capture, no vehicle driving, no medical diagnosis, no
marketplace/store is built or wired here.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import learning_last as learn_mod


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


def _check_output_path(output_path):
    """Destination checks shared by file-writing tools:
    .json only, parent folder must already exist, no credential
    target, no app core/data path. Returns (Path, error)."""
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
        resolved = target.resolve()
        if resolved.is_relative_to(tree / "core") \
                or resolved.is_relative_to(tree / "data"):
            return None, "app core/data paths refused"
    except (OSError, ValueError):
        return None, "output_path cannot be resolved"
    return target, None


def _spec_from_payload(payload):
    if not isinstance(payload, dict):
        return None, "spec input must be a JSON object"
    return payload, None


def learnlast_spec_handler(spec_json):
    payload, err = _parse_json(spec_json, "spec_json")
    if err:
        return _error(err)
    payload, err = _spec_from_payload(payload)
    if err:
        return _error(err)
    return _handler(
        learn_mod.build_learning_spec,
        payload.get("jobs"),
        payload.get("title"),
        payload.get("summary"),
        payload.get("diffs"),
        payload.get("regression_guards"),
        boundary_check=payload.get("boundary_check"),
        extra_evidence=payload.get("extra_evidence"),
    )


def learnlast_report_handler(spec_json, output_path):
    payload, err = _parse_json(spec_json, "spec_json")
    if err:
        return _error(err)
    payload, err = _spec_from_payload(payload)
    if err:
        return _error(err)
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    result = learn_mod.build_learning_spec(
        payload.get("jobs"),
        payload.get("title"),
        payload.get("summary"),
        payload.get("diffs"),
        payload.get("regression_guards"),
        boundary_check=payload.get("boundary_check"),
        extra_evidence=payload.get("extra_evidence"),
    )
    if not result.get("ok"):
        return json.dumps(result)
    try:
        target.write_text(json.dumps(result["spec"], indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "LEARNING_LAST_SPEC",
                       "executed": False, "executes_nothing": True,
                       "deployed": False,
                       "report_path": str(target),
                       "note": "Spec report written; nothing was applied, "
                               "deployed, or changed. Applying a spec is "
                               "a separate human C-14 action."})


def register_all(registry) -> None:
    """Register the 2 Wave 8 Build 5 learning-last tools."""
    registry.register(
        name="learnlast_spec",
        description="LEARNING-LAST SPEC ONLY, NO BEHAVIOUR CHANGE, NO APPLICATION: build a spec from caller-supplied field-proven jobs (Wave 6 WORK rule: binder completed, field_proven with a proof reference, at least one evidence entry and one step) plus an exact diff and named regression guards. Unproven or mixed input refuses the whole spec. The output is a Wave 7 proposal (status PROPOSED, deployed False) — it changes nothing and is never applied by this tool.",
        parameters={"type": "object", "properties": {
            "spec_json": {"type": "string",
                          "description": "JSON object with jobs, title, summary, diffs, regression_guards, and optional boundary_check and extra_evidence"},
        }, "required": ["spec_json"]},
        handler=learnlast_spec_handler,
    )
    registry.register(
        name="learnlast_report",
        description="LEARNING-LAST SPEC REPORT ONLY, NOT AN APPLICATION: re-build a learning-last spec itself (never trusting caller-supplied spec JSON) and write only the spec record to a caller-chosen .json file in an already-existing folder. The report asks for confirmation first. Writing it does not apply the spec — application is a separate human C-14 action through the plan-approval gate.",
        parameters={"type": "object", "properties": {
            "spec_json": {"type": "string",
                          "description": "JSON spec input, re-built by this tool, never trusted from the caller"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["spec_json", "output_path"]},
        handler=learnlast_report_handler,
    )
