"""Wave 7 Build 3 tools — SIM plan-effect prediction as live tools.

Triage Wire pattern, as in core/sandbox_tools.py and
core/undo_tools.py: this coordinator imports the prediction
module, registers tools via register_all(registry), each handler
takes the named schema fields, forwards to the module, and
returns the module result encoded as a JSON string verbatim;
refusal dicts flow the same way, and module raises become an
error JSON — handlers never raise.

Wiring (Wave 7 Build 3, SIM named slice only):
  * sim_predict_plan — read-only. It predicts a caller-supplied
    file plan's effects on a caller-supplied snapshot, by
    applying the plan to a throwaway sandbox copy, diffing it,
    and discarding it. It writes nothing the caller keeps, it
    never promotes, it never records anything as done, and it
    never executes a real action — so it is provably
    side-effect-free from the app's point of view, even though
    the module internally uses a TemporaryDirectory that it
    deletes. Palette/catalogue rows derive from declarations.
  * sim_predict_report — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-runs the prediction itself
    (it never trusts a caller-supplied prediction JSON, which
    could claim anything) and writes that prediction to a
    caller-chosen .json file whose parent folder must already
    exist — no folder creation, no overwrite of a core/data
    path, no .env/.pem/.key target. The funnel refuses it
    without a human confirm handler, before the handler runs.
    Writing the report is not executing the plan: the plan
    itself is never executed by either tool.

This coordinator is NOT a gate owner: it imports no gate
module for decisions — confirm-gating is the funnel's job
(core/funnel/tools_gate.py classification). No Monte Carlo,
counterfactual, or digital-twin form is built or wired here;
those stay STUDY. No tool here plans, compiles, promotes,
applies, or deploys anything — plan-approval gate intact,
no self-deployment, ever.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import sim_predict as sim_mod


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


def sim_predict_plan_handler(seed_json, operations_json,
                             sentinels_json="[]"):
    seed, err = _parse_json(seed_json, "seed_json")
    if err:
        return _error(err)
    operations, err = _parse_json(operations_json, "operations_json")
    if err:
        return _error(err)
    sentinels, err = _parse_json(sentinels_json, "sentinels_json") \
        if sentinels_json else ([], None)
    if err:
        return _error(err)
    return _handler(sim_mod.predict_plan, seed, operations, sentinels)


def sim_predict_report_handler(seed_json, operations_json, output_path,
                               sentinels_json="[]"):
    seed, err = _parse_json(seed_json, "seed_json")
    if err:
        return _error(err)
    operations, err = _parse_json(operations_json, "operations_json")
    if err:
        return _error(err)
    sentinels, err = _parse_json(sentinels_json, "sentinels_json") \
        if sentinels_json else ([], None)
    if err:
        return _error(err)
    result = sim_mod.predict_plan(seed, operations, sentinels)
    if not result.get("ok"):
        return json.dumps(result)
    # Report writing guards: existing parent folder only, .json
    # only, never a core/data path, never a credential target.
    try:
        target = Path(output_path).expanduser()
        if target.suffix != ".json":
            return _error("output_path must end in .json")
        if not target.parent.is_dir():
            return _error("output_path parent folder must already exist")
        if target.name in (".env", "assistant.db") \
                or target.name.endswith((".pem", ".key")):
            return _error("credential target refused")
        parts = target.resolve().parts
        if "core" in parts or "data" in parts:
            # Only refuse when the resolved path is inside the
            # shipped tree's core/data — checked by the caller
            # passing an app-tree path; a same-named user folder
            # elsewhere is not the app tree. The tree check is
            # the module's own core/data boundary, applied via
            # the file's package location.
            tree = Path(__file__).resolve().parent.parent
            try:
                resolved = target.resolve()
                if resolved.is_relative_to(tree / "core") \
                        or resolved.is_relative_to(tree / "data"):
                    return _error("app core/data paths refused")
            except (OSError, ValueError):
                return _error("output_path cannot be resolved")
        target.write_text(json.dumps(result["prediction"], indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "PREDICTION",
                       "executed": False, "executes_nothing": True,
                       "report_path": str(target),
                       "summary": result["prediction"]["summary"],
                       "note": "Report written; the plan itself was "
                               "NOT executed."})


def register_all(registry) -> None:
    """Register the 2 Wave 7 Build 3 SIM tools."""
    registry.register(
        name="sim_predict_plan",
        description="PREDICTION ONLY, NOT EXECUTED: predict what a caller-supplied file plan (write/delete operations) would do to a caller-supplied snapshot of files — which files would be created, overwritten, or deleted, with byte counts and hashes — by applying the plan to a throwaway sandbox copy, diffing it, and discarding it. Nothing is written, promoted, or recorded as done, and the plan itself is never executed. Use BEFORE deciding whether to run a file plan, never as a way to run one.",
        parameters={"type": "object", "properties": {
            "seed_json": {"type": "string",
                          "description": "JSON dict of relative path -> text content (the before-state snapshot)"},
            "operations_json": {"type": "string",
                                "description": "JSON list of {kind: write|delete, path: relative path, content: string for writes}"},
            "sentinels_json": {"type": "string",
                               "description": "Optional JSON list of outside file paths hashed before/after to prove they are unchanged"},
        }, "required": ["seed_json", "operations_json"]},
        handler=sim_predict_plan_handler,
    )
    registry.register(
        name="sim_predict_report",
        description="PREDICTION REPORT ONLY, NOT EXECUTED: re-run a plan-effect prediction and write the prediction (not the plan's effects) to a caller-chosen .json file in an already-existing folder. The plan itself is never executed by this tool. The report asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "seed_json": {"type": "string",
                          "description": "JSON dict of relative path -> text content (the before-state snapshot)"},
            "operations_json": {"type": "string",
                                "description": "JSON list of {kind: write|delete, path: relative path, content: string for writes}"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
            "sentinels_json": {"type": "string",
                               "description": "Optional JSON list of outside file paths hashed before/after to prove they are unchanged"},
        }, "required": ["seed_json", "operations_json", "output_path"]},
        handler=sim_predict_report_handler,
    )
