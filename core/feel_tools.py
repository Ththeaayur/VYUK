"""Wave 8 Build 4 tools — feel, non-veto only, as live tools.

Triage Wire pattern, as in core/phase5_tools.py: this
coordinator imports the feel module, registers tools via
register_all(registry), each handler takes the named schema
fields, forwards to the module, and returns the module result
encoded as a JSON string verbatim; refusal dicts flow the same
way, and module raises become an error JSON — handlers never
raise.

Wiring (Wave 8 Build 4, three tools):
  * feel_validate — read-only. Validates a whole feel input
    (orb level, HUD descriptor, sound spec, sound pack,
    particle tier, HUD language, font). No file, no asset,
    no render, no listening test is claimed.
  * feel_orb_map — read-only. Maps a caller-supplied measured
    audio level to orb parameters. Mapping only: no orb
    asset is bundled and the orb licence sighting stays OWED.
  * feel_report — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-validates the feel input
    itself (it never trusts caller-supplied result JSON) and
    writes only the validation report to a caller-chosen
    .json file in an already-existing folder. The funnel
    refuses it without a human confirm handler, before the
    handler runs. Writing the report is not approving the
    feel — feel verdicts are the owner's eye/ear only.

This coordinator is NOT a gate owner: confirm-gating is the
funnel's job (core/funnel/tools_gate.py classification). No vetoed
feel, no face, no fake data, no font asset, no sound pack
bundled, no payment surface, no voiceprint, no continuous
capture, no vehicle driving, no medical diagnosis, no
marketplace/store is built or wired here.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import feel as feel_mod


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


def feel_validate_handler(feel_json):
    feel, err = _parse_json(feel_json, "feel_json")
    if err:
        return _error(err)
    return _handler(feel_mod.validate_feel, feel)


def feel_orb_map_handler(level):
    if isinstance(level, bool) or not isinstance(level, (int, float)):
        return _error("level must be a number between 0.0 and 1.0")
    return _handler(feel_mod.orb_map, level)


def feel_report_handler(feel_json, output_path):
    feel, err = _parse_json(feel_json, "feel_json")
    if err:
        return _error(err)
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    result = feel_mod.validate_feel(feel)
    if not result.get("ok"):
        return json.dumps(result)
    try:
        target.write_text(json.dumps(result, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": result.get("kind"),
                       "executed": False, "executes_nothing": True,
                       "report_path": str(target),
                       "note": "Report written; nothing was executed, "
                               "approved, or rendered. Feel verdicts "
                               "are the owner's eye/ear only."})


def register_all(registry) -> None:
    """Register the 3 Wave 8 Build 4 feel tools."""
    registry.register(
        name="feel_validate",
        description="FEEL VALIDATION ONLY, NO RENDER, NO LISTENING TEST: validate a feel input (measured orb level, HUD descriptor, procedural sound spec, sound pack ledger record, particle tier, HUD language, font status) against the §7 feel rules — one blue accent, amber for waiting/confirm, red only for stop/failure, measured value or explicit unknown, no face, no vetoed feel. A refused part refuses the whole input. Nothing is rendered and nothing is approved.",
        parameters={"type": "object", "properties": {
            "feel_json": {"type": "string",
                          "description": "JSON object with any of orb_level, hud, sound, sound_pack, particle_tier, hud_language, font"},
        }, "required": ["feel_json"]},
        handler=feel_validate_handler,
    )
    registry.register(
        name="feel_orb_map",
        description="ORB MAPPING ONLY, NO ASSET, NO MEASUREMENT: map a caller-supplied measured audio level (0.0–1.0) to Aura orb parameters (scale, glow, pulse rate, blue accent). This module measures no audio and captures nothing; the level is caller-supplied. The Aura orb licence sighting stays OWED and no orb asset is bundled.",
        parameters={"type": "object", "properties": {
            "level": {"type": "number",
                      "description": "Measured audio level, 0.0–1.0, caller-supplied"},
        }, "required": ["level"]},
        handler=feel_orb_map_handler,
    )
    registry.register(
        name="feel_report",
        description="FEEL REPORT ONLY, NOT AN APPROVAL: re-validate a feel input itself and write only the validation report to a caller-chosen .json file in an already-existing folder. The report asks for confirmation first. Writing it does not approve the feel — feel verdicts are the owner's eye/ear only, never a lab claim.",
        parameters={"type": "object", "properties": {
            "feel_json": {"type": "string",
                          "description": "JSON feel input, re-validated by this tool, never trusted from the caller"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["feel_json", "output_path"]},
        handler=feel_report_handler,
    )
