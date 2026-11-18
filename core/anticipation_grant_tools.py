"""Wave 8 Build 1 tools — anticipation-grant path as live tools.

Triage Wire pattern, as in core/sim_tools.py and
core/multiverse_tools.py: this coordinator imports the grant
path module, registers tools via register_all(registry), each
handler takes the named schema fields, forwards to the module,
and returns the module result encoded as a JSON string verbatim;
refusal dicts flow the same way, and module raises become an
error JSON — handlers never raise.

Wiring (Wave 8 Build 1, suggestion cards only):
  * antic_suggest_cards — read-only. It produces suggestion
    cards from caller-supplied context, gated on a
    caller-supplied grant record. Without a granted
    suggestion-cards record it returns zero cards with a stated
    reason. It captures nothing, watches nothing, pre-warms
    nothing, and executes nothing — a card's action is a
    description, never run by this tool.
  * antic_grant_status — read-only. It inspects a
    caller-supplied grant record (or the absence of one) and
    states plainly whether the suggestion-cards path is open
    and why. It grants nothing and upgrades nothing.
  * antic_cards_report — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-runs the gate itself (it
    never trusts a caller-supplied card JSON, which could claim
    anything) and writes only the card record to a caller-chosen
    .json file whose parent folder must already exist — no
    folder creation, no overwrite of a core/data path, no
    .env/.pem/.key target. The funnel refuses it without a human
    confirm handler, before the handler runs. Writing the report
    is not granting the grant, and it is not executing a card.

This coordinator is NOT a gate owner: it imports no gate
module for decisions — confirm-gating is the funnel's job
(core/funnel/tools_gate.py classification). No continuous capture, no
pre-warming, no screen memory, no clipboard history, no
watch-and-learn form is built or wired here; the grant itself
waits on the owner's explicit opt-in (v6 section 4) and is not
granted by any tool here. A test grant fixture is data for a
test, never a live grant.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import anticipation_grant_path as antic_mod


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


def _parse_grant(grant_json):
    """Parse a grant record JSON into a GrantRecord, or None
    when the caller supplied no record (an absent record is a
    refusal, never a default). A malformed record is an error,
    never treated as a grant."""
    if grant_json is None or str(grant_json).strip() == "":
        return None, None
    record, err = _parse_json(grant_json, "grant_json")
    if err:
        return None, err
    if not isinstance(record, dict):
        return None, "grant_json must be a JSON object"
    try:
        grant = antic_mod.build_grant_record(
            granted=record.get("granted", False),
            grantor=record.get("grantor", ""),
            scope=record.get("scope", ""),
            timestamp=record.get("timestamp", ""),
        )
    except (ValueError, TypeError) as exc:
        return None, f"grant_json invalid: {exc}"
    return grant, None


def antic_suggest_cards_handler(context_json, grant_json=""):
    context, err = _parse_json(context_json, "context_json")
    if err:
        return _error(err)
    grant, err = _parse_grant(grant_json)
    if err:
        return _error(err)
    return _handler(antic_mod.gate_cards, context, grant)


def antic_grant_status_handler(grant_json=""):
    grant, err = _parse_grant(grant_json)
    if err:
        return _error(err)
    return _handler(antic_mod.inspect_grant, grant)


def antic_cards_report_handler(context_json, grant_json, output_path):
    context, err = _parse_json(context_json, "context_json")
    if err:
        return _error(err)
    grant, err = _parse_grant(grant_json)
    if err:
        return _error(err)
    result = antic_mod.gate_cards(context, grant)
    if not result.get("ok"):
        return json.dumps(result)
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
    return json.dumps({"ok": True, "kind": "SUGGESTION_CARDS",
                       "executed": False, "executes_nothing": True,
                       "report_path": str(target),
                       "cards": len(result["cards"]),
                       "note": "Card record written; the grant itself "
                               "is NOT granted by this tool, and no "
                               "card action was executed."})


def register_all(registry) -> None:
    """Register the 3 Wave 8 Build 1 anticipation-grant tools."""
    registry.register(
        name="antic_suggest_cards",
        description="SUGGESTION CARDS ONLY, NOTHING CAPTURED, NOTHING EXECUTED: produce suggestion cards from caller-supplied context (topics the caller lists, in plain text), gated on a caller-supplied anticipation grant record. Without a granted suggestion-cards grant it returns zero cards with a stated reason. No continuous capture, no pre-warming, no screen or clipboard reading, no watcher — a card's action is a description of what it WOULD take, never run by this tool. The grant itself is not granted by this tool.",
        parameters={"type": "object", "properties": {
            "context_json": {"type": "string",
                             "description": "JSON list of {topic, reason, action, expiry} items — caller-supplied context only; capture-derived items are refused whole"},
            "grant_json": {"type": "string",
                           "description": "Optional JSON grant record {granted, grantor, scope, timestamp}; only granted + scope suggestion-cards opens the path; absent means refused"},
        }, "required": ["context_json"]},
        handler=antic_suggest_cards_handler,
    )
    registry.register(
        name="antic_grant_status",
        description="GRANT INSPECTION ONLY, GRANTS NOTHING: inspect a caller-supplied anticipation grant record (or its absence) and state plainly whether the suggestion-cards path is open and why. This tool never grants, upgrades, or persists a grant.",
        parameters={"type": "object", "properties": {
            "grant_json": {"type": "string",
                           "description": "Optional JSON grant record {granted, grantor, scope, timestamp}; absent means no grant record"},
        }, "required": []},
        handler=antic_grant_status_handler,
    )
    registry.register(
        name="antic_cards_report",
        description="SUGGESTION-CARD REPORT ONLY, NOT A GRANT, NOTHING EXECUTED: re-run the anticipation gate and write only the card record to a caller-chosen .json file in an already-existing folder. The grant itself is not granted by this tool, and no card action is executed. The report asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "context_json": {"type": "string",
                             "description": "JSON list of {topic, reason, action, expiry} items — caller-supplied context only"},
            "grant_json": {"type": "string",
                           "description": "JSON grant record {granted, grantor, scope, timestamp}; only granted + scope suggestion-cards opens the path"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["context_json", "grant_json", "output_path"]},
        handler=antic_cards_report_handler,
    )
