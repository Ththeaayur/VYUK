"""Wave 9 Build 10 tools: TENSION graduation step 1
plus graduation ledger, as live tools.

Triage Wire pattern, as in core/travel_tools.py:
this coordinator imports the graduation module,
registers tools via register_all(registry), each handler
takes the named schema fields, forwards to the module,
and returns the module result encoded as a JSON string
verbatim; refusal dicts flow the same way, and module
raises become an error JSON — handlers never raise.

TOOLBOX INDIRECTION (why two tools, not five)
---------------------------------------------
The fitted Gemini declaration budget is a HARD cap
(240,000 B) and stood at 239,334 B after Build 9 —
headroom about 666 B, with descriptions already capped
to 55 chars by the fitter. Five separate tools, each
with its own parameter schema, do not fit that
headroom: their schemas alone would push the fitted
total over the cap, and the fitted full list would then
have been further over it (the domain-router build later
routed around the fitted list entirely; DECL-01 retired
it 2026-11-01). The overflow protocol
(~/workspace/code-lab/overflow_queue.md) names toolbox
indirection as an alternative to dropping items, so
this build uses it: ONE read-only descriptor tool with
a "kind" parameter (gate, retention, indicator,
ledger) carries all four descriptors, and ONE
confirm-gated record tool carries the ledger record.
Nothing is dropped and nothing is queued: every
descriptor the brief names is built, wired, grouped,
and tested — they share one tool each by kind, not one
tool each by name. Parameter schemas here carry no
per-property descriptions, for the same fitted-cap
reason: at the 55-char cap the fitter would truncate
them anyway, and each one costs fitted bytes the
budget no longer has. Tool descriptions lead with
their safety clause, so the 55-char fitted copy still
carries it.

Wiring (Wave 9 Build 10, TENSION graduation 1):
  * grad_describe — read-only. kind "gate" checks a
    caller-supplied choice record through the
    first-open gate and states the recorded retention
    rule and the indicator requirement with the
    answer; enabled only on the user's own explicit
    choice for exactly continuous-display. kind
    "retention" returns the plain-language retention
    rule for caller-supplied days, or the current
    7-day builder-chosen default with the note that
    it is NOT ruled by the owner. kind "indicator"
    returns the indicator requirement in plain words.
    kind "ledger" returns a graduation ledger entry:
    evidence plus form plus still-owed, never a claim
    the item is built, shipped, or field-proven.
    Nothing is captured, granted, or chosen for any
    user by any kind.
  * grad_record — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-validates the
    graduation ledger entry itself (it never trusts a
    caller-supplied graduation JSON, which could claim
    anything) and writes only the graduation record
    to a caller-chosen .json file in an already-existing
    folder — no folder creation, no overwrite of a
    core/data path, no .env/.pem/.key target. The
    funnel refuses it without a human confirm handler,
    before the handler runs. Recording captures
    nothing, grants nothing, and decides nothing —
    PF-3 stays open.

This coordinator is NOT a capture tool, a grant tool,
or a delete tool: it imports no watcher, screen,
clipboard, keystroke, or sensor code, captures
nothing, grants nothing, chooses nothing for any
user, and never claims the PF-3 global
delete-everywhere rule, which is still open.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import grad as grad_mod


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


def grad_describe_handler(kind, action="describe",
                          record_json=None,
                          retention_days=None,
                          item_id=None, evidence=None,
                          form=None, still_owed=None):
    """Dispatch one descriptor kind. Fields that belong
    to another kind refuse whole here, before the module
    runs — a descriptor never silently ignores an input
    it was handed."""
    if kind == "gate":
        if retention_days is not None or item_id is not None \
                or evidence is not None or form is not None \
                or still_owed is not None:
            return _error(
                "kind 'gate' takes only record_json, not "
                "retention or ledger fields")
        if record_json is None:
            return _handler(grad_mod.describe, "gate",
                            action=action)
        fields, err = _parse_json(record_json, "record_json")
        if err:
            return _error(err)
        if not isinstance(fields, dict):
            return _error("record_json must be a JSON object")
        if "action" in fields:
            return _error(
                "record_json must not carry its own action; "
                "only 'describe' is performed")
        return _handler(grad_mod.describe, "gate",
                        action=action, **fields)
    if kind == "retention":
        if record_json is not None or item_id is not None \
                or evidence is not None or form is not None \
                or still_owed is not None:
            return _error(
                "kind 'retention' takes only retention_days")
        return _handler(grad_mod.describe, "retention",
                        action=action,
                        retention_days=retention_days)
    if kind == "indicator":
        if record_json is not None or retention_days is not None \
                or item_id is not None or evidence is not None \
                or form is not None or still_owed is not None:
            return _error("kind 'indicator' takes no fields")
        return _handler(grad_mod.describe, "indicator",
                        action=action)
    if kind == "ledger":
        if record_json is not None or retention_days is not None:
            return _error(
                "kind 'ledger' takes only item_id, evidence, "
                "form, and still_owed")
        return _handler(grad_mod.describe, "ledger",
                        action=action, item_id=item_id,
                        evidence=evidence, form=form,
                        still_owed=still_owed)
    return _error(
        "kind must be one of gate, retention, indicator, ledger")


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


def grad_record_handler(record_json, output_path):
    fields, err = _parse_json(record_json, "record_json")
    if err:
        return _error(err)
    if not isinstance(fields, dict):
        return _error("record_json must be a JSON object")
    try:
        rebuilt = grad_mod.validate_recordable_graduation(fields)
    except (ValueError, TypeError) as exc:
        return _error(
            f"graduation entry does not re-build: {exc}")
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "GRADUATION_RECORD",
        "graduation": rebuilt,
        "built": False, "shipped": False,
        "field_proven": False,
        "captured": False, "granted": False,
        "note": ("A graduation ledger entry the user "
                 "chose, recorded only. Recording "
                 "captures nothing, grants nothing, "
                 "chooses nothing for any user, and "
                 "decides nothing — PF-3 stays open, "
                 "and this record is not a claim the "
                 "item is built, shipped, or "
                 "field-proven."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "GRADUATION_RECORD",
                       "built": False, "shipped": False,
                       "field_proven": False,
                       "captured": False, "granted": False,
                       "report_path": str(target),
                       "note": "Graduation entry recorded; nothing "
                               "was captured, granted, or decided."})


def register_all(registry) -> None:
    """Register the 2 graduation tools (toolbox
    indirection: grad_describe carries the gate,
    retention, indicator, and ledger descriptors by
    kind; grad_record carries the ledger record)."""
    registry.register(
        name="grad_describe",
        description="GATE ONLY, NOTHING CAPTURED OR GRANTED: describe the continuous-display gate check, the retention rule, the indicator requirement, or a graduation ledger entry, from caller-supplied fields only. kind 'gate' checks a choice record through the first-open gate (enabled only on the user's own explicit choice for exactly continuous-display, with a named retention and deletion rule); kind 'retention' states the plain-language kept-notes rule (7-day builder-chosen default, NOT ruled by the owner, unless the caller supplies days); kind 'indicator' states the on-screen sign requirement in plain words; kind 'ledger' records evidence plus buildable form plus still-owed, never a claim the item is built, shipped, or field-proven. PF-3 stays open.",
        parameters={"type": "object", "properties": {
            "kind": {"description": "Which descriptor kind to describe","type": "string",
                     "enum": ["gate", "retention",
                              "indicator", "ledger"]},
            "record_json": {"description": "JSON object of the descriptor fields to re-validate", "type": "string"},
            "retention_days": {"description": "Caller-supplied retention days for the retention rule", "type": "integer"},
            "item_id": {"description": "Id of the graduation ledger item", "type": "string"},
            "evidence": {"description": "Gate-evidence reference for the ledger entry", "type": "string"},
            "form": {"description": "Buildable-form description for the ledger entry", "type": "string"},
            "still_owed": {"description": "List of items still owed","type": "array",
                           "items": {"type": "string"}},
            "action": {"description": "Only 'describe' is performed", "type": "string"},
        }, "required": ["kind"]},
        handler=grad_describe_handler,
    )
    registry.register(
        name="grad_record",
        description="RECORD ONLY, NOTHING CAPTURED OR GRANTED: re-validate a caller-supplied graduation ledger entry (item id, gate-evidence reference, buildable-form description, still-owed list) by re-running the builder that made it, and write only the graduation record to a caller-chosen .json file in an already-existing folder. An entry that does not re-build is refused, never written on trust. The record asks for confirmation first, and it is not a claim the item is built, shipped, or field-proven; PF-3 stays open.",
        parameters={"type": "object", "properties": {
            "record_json": {"description": "JSON object of the descriptor fields to re-validate", "type": "string"},
            "output_path": {"description": "Caller-chosen .json file in an already-existing folder", "type": "string"},
        }, "required": ["record_json", "output_path"]},
        handler=grad_record_handler,
    )
