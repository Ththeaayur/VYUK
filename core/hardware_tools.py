"""Hardware-adjacent deltas as live tools.

Triage Wire pattern, as in core/well_tools.py:
this coordinator imports the hardware module, registers
tools via register_all(registry), each handler takes the
named schema fields, forwards to the module, and returns
the module result encoded as a JSON string verbatim;
refusal dicts flow the same way, and module raises become
an error JSON — handlers never raise.

Wiring (Wave 9 Build 4, HARDWARE — hardware-adjacent
deltas that do not move hardware by themselves):
  * hardware_inventory — read-only. It returns a
    hardware-inventory descriptor from a caller-supplied
    parts list, name + count as supplied, echoed back.
    Nothing here was measured, weighed, or checked
    against a real bench, and no measurement field is
    accepted beside a plain part count.
  * hardware_plan — read-only. It returns a
    hardware-plan descriptor from caller-supplied steps.
    Every step that would move hardware must name its
    independent physical stop; a move step without one
    refuses the whole plan. The plan describes only —
    it has no move, actuate, drive, print, or cut path,
    and describing a step does not perform it.
  * hardware_safety — read-only. It returns the safety
    descriptor stating, in plain words, what a plan here
    does NOT claim: no safety sizing, no engineering
    sign-off, no claim a design is safe or will hold.
  * hardware_record_plan — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-validates the plan
    itself (it never trusts a caller-supplied plan JSON,
    which could claim anything, including a safety claim
    or a move without a stop) and writes only the plan
    record to a caller-chosen .json file in an
    already-existing folder — no folder creation, no
    overwrite of a core/data path, no .env/.pem/.key
    target. The funnel refuses it without a human confirm
    handler, before the handler runs. Recording a plan
    moves nothing and claims nothing.

This coordinator is NOT a hardware controller: it
imports no serial, device, or hardware code, reaches no
device, moves nothing, sizes nothing, and signs nothing
off. It makes no claim about any specific project, arm,
bench, or bench test; those are outside this module
entirely.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import hardware as hardware_mod


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


def hardware_inventory_handler(parts_json):
    parts, err = _parse_json(parts_json, "parts_json")
    if err:
        return _error(err)
    return _handler(hardware_mod.build_hardware_inventory, parts)


def hardware_plan_handler(steps_json):
    steps, err = _parse_json(steps_json, "steps_json")
    if err:
        return _error(err)
    return _handler(hardware_mod.build_hardware_plan, steps)


def hardware_safety_handler():
    return _handler(hardware_mod.build_hardware_safety)


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
        return None, "output_path cannot be resolved: {exc}"
    return target, None


def hardware_record_plan_handler(plan_json, output_path):
    fields, err = _parse_json(plan_json, "plan_json")
    if err:
        return _error(err)
    if not isinstance(fields, dict):
        return _error("plan_json must be a JSON object")
    try:
        rebuilt = hardware_mod.validate_recordable_plan(fields)
    except (ValueError, TypeError) as exc:
        return _error(f"plan does not re-build: {exc}")
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "HARDWARE_PLAN_RECORD",
        "plan": rebuilt,
        "moved": False,
        "executed": False,
        "safety": rebuilt["safety"],
        "note": ("A hardware plan in words only, recorded "
                 "only. Recording moves nothing, performs "
                 "no step, and claims no safety sizing, "
                 "rating, or sign-off."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "HARDWARE_PLAN_RECORD",
                       "executed": False, "moves_nothing": True,
                       "report_path": str(target),
                       "moved": False,
                       "note": "Plan recorded; nothing moved, "
                               "nothing was performed, and no "
                               "safety claim was made."})


def register_all(registry) -> None:
    """Register the 4 hardware-adjacent tools."""
    registry.register(
        name="hardware_inventory",
        description="INVENTORY IN WORDS ONLY, NOTHING MEASURED: return a hardware-inventory descriptor from a caller-supplied parts list, name + count as supplied, echoed back. Nothing here was measured, weighed, or checked against a real bench, and no measurement field (weight, tolerance, load) is accepted beside a plain part count. No claim about any specific project, arm, or bench test is made.",
        parameters={"type": "object", "properties": {
            "parts_json": {"type": "string",
                           "description": "JSON list of caller-supplied part records {name, count}"},
        }, "required": ["parts_json"]},
        handler=hardware_inventory_handler,
    )
    registry.register(
        name="hardware_plan",
        description="PLAN IN WORDS ONLY, NOTHING MOVED: return a hardware-plan descriptor from caller-supplied steps. Every step that would move hardware must name its independent physical stop, a stop outside this software; a move step without one refuses the whole plan, and a stop is never defaulted or guessed. The plan describes only — it has no move, actuate, drive, print, or cut path, and describing a step does not perform it. No safety sizing, rating, or sign-off is claimed.",
        parameters={"type": "object", "properties": {
            "steps_json": {"type": "string",
                           "description": "JSON list of caller-supplied step records {description, moves_hardware, physical_stop}"},
        }, "required": ["steps_json"]},
        handler=hardware_plan_handler,
    )
    registry.register(
        name="hardware_safety",
        description="SAFETY DISCLAIMER ONLY, NO CLAIM MADE: return the hardware-safety descriptor stating, in plain words, what a plan here does NOT claim — no safety sizing, no engineering sign-off, no claim a design is safe or will hold, no load rating. If a safety claim is needed, it must come from a qualified check outside this tool.",
        parameters={"type": "object", "properties": {}},
        handler=hardware_safety_handler,
    )
    registry.register(
        name="hardware_record_plan",
        description="RECORD ONLY, NOTHING MOVED, NOTHING CLAIMED: re-validate a caller-supplied hardware plan (steps, with a named independent physical stop on every step that would move hardware) by re-running the builder that made it, and write only the plan record to a caller-chosen .json file in an already-existing folder. A plan that does not re-build — including a move step without a stop, or any safety claim — is refused, never written on trust. Recording moves nothing and claims nothing. The record asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "plan_json": {"type": "string",
                          "description": "JSON object of plan fields to re-validate {steps}"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["plan_json", "output_path"]},
        handler=hardware_record_plan_handler,
    )
