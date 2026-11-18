"""Wave 7 Build 4 tools — self-improvement PROPOSALS as live tools.

Triage Wire pattern, as in core/sim_tools.py: this
coordinator imports the proposal module, registers tools via
register_all(registry), each handler takes the named schema
fields, forwards to the module, and returns the module result
encoded as a JSON string verbatim; refusal dicts flow the same
way, and module raises become an error JSON — handlers never
raise.

Wiring (Wave 7 Build 4, Evolution Mode item 26 form only):
  * improvement_propose — read-only. It validates a
    caller-supplied proposal (exact diff + regression guard +
    evidence ledger) and dry-runs it in a throwaway copy,
    which is then discarded. It writes nothing the caller
    keeps, it never applies, promotes, or deploys anything,
    so it is provably side-effect-free from the app's point
    of view, even though the module internally uses a
    TemporaryDirectory that it deletes. Palette/catalogue
    rows derive from declarations.
  * improvement_report — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-validates and re-dry-runs
    the proposal itself (it never trusts a caller-supplied
    proposal JSON, which could claim anything) and writes
    only the proposal record to a caller-chosen .json file
    whose parent folder must already exist — no folder
    creation, no overwrite of a core/data path, no
    .env/.pem/.key target. The funnel refuses it without a
    human confirm handler, before the handler runs.
    Writing the report is not applying the proposal: the
    proposal itself is never applied by either tool.

This coordinator is NOT a gate owner: it imports no gate
module for decisions — confirm-gating is the funnel's job
(core/funnel/tools_gate.py classification). No M4 itself is built
or wired here; the M4 pattern is a reference only. No tool
here plans, compiles, promotes, applies, or deploys
anything — plan-approval gate intact, no self-deployment,
ever.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import improvement_proposals as prop_mod


def _error(reason: str) -> str:
    return json.dumps({"ok": False, "reason": reason})


def _handler(fn, *args, **kwargs):
    try:
        return json.dumps(fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")


def improvement_propose_handler(proposal_json):
    return _handler(prop_mod.proposal_from_json, proposal_json)


def improvement_report_handler(proposal_json, output_path):
    result = prop_mod.proposal_from_json(proposal_json)
    if not result.get("ok"):
        return json.dumps(result)
    # Report writing guards: existing parent folder only, .json
    # only, never an app core/data path, never a credential target.
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
        target.write_text(json.dumps(result["proposal"], indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "PROPOSAL",
                       "deployed": False, "executes_nothing": True,
                       "report_path": str(target),
                       "proposal_id": result["proposal"]["id"],
                       "note": "Report written; the proposal itself was "
                               "NOT applied."})


def register_all(registry) -> None:
    """Register the 2 Wave 7 Build 4 proposal tools."""
    registry.register(
        name="improvement_propose",
        description="PROPOSAL ONLY, NOT APPLIED: validate a caller-supplied self-improvement proposal (exact file-by-file diff with hashes, named regression guard tests, and an evidence ledger) and dry-run it in a throwaway copy that is discarded. A proposal without evidence, without a regression guard, or touching a core path without the core-modification boundary check is refused whole. Nothing is applied, promoted, or deployed by this tool; applying a proposal is a separate human action.",
        parameters={"type": "object", "properties": {
            "proposal_json": {"type": "string",
                              "description": "JSON proposal object: {title, summary, diffs: [{path, old_text, new_text}], regression_guards: [test names], evidence: [{kind, ref, detail}], boundary_check (only for core/data paths): {check: 'core-modification-boundary-1692', reason, checked_by}}"},
        }, "required": ["proposal_json"]},
        handler=improvement_propose_handler,
    )
    registry.register(
        name="improvement_report",
        description="PROPOSAL REPORT ONLY, NOT APPLIED: re-validate and re-dry-run a self-improvement proposal and write only the proposal record to a caller-chosen .json file in an already-existing folder. The proposal itself is never applied by this tool. The report asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "proposal_json": {"type": "string",
                              "description": "JSON proposal object, as for improvement_propose"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["proposal_json", "output_path"]},
        handler=improvement_report_handler,
    )
