"""P19 travel + P20 PILOT + P23 recipes/scoreboard as
live tools.

Triage Wire pattern, as in core/pilot_tools.py:
this coordinator imports the travel module, registers
tools via register_all(registry), each handler takes the
named schema fields, forwards to the module, and returns
the module result encoded as a JSON string verbatim;
refusal dicts flow the same way, and module raises become
an error JSON — handlers never raise.

Wiring (Wave 9 Build 9, P19 / P20 / P23):
  * travel_trip — read-only. It returns a trip
    descriptor from caller-supplied legs only (P19-14
    trip pack, contents labels; P19-19 smart packing,
    checklist labels; P19-18 parking note, caller
    supplied; P19-20 travel for every body,
    caller-supplied accessibility note, never inferred).
    No legs means NO trip, NOT_COMPUTED. Nothing is
    booked, routed, or paid.
  * travel_door_to_door — read-only. It returns a
    door-to-door plan descriptor from caller-supplied
    legs in order (P19-12, non-commercial-routing
    form). No commercial routing is used and no route
    is computed by a routing service.
  * travel_transport — read-only. It returns a
    transport-handler descriptor from a caller-supplied
    mode label (P19 chauffeur position: handler yes,
    literal driver no). A driving label returns the
    driving-mode note only; a safe-arrival label
    returns the safe-arrival note only, NOT an
    emergency-services replacement.
  * travel_loadout — read-only. It returns a loadout
    descriptor from a caller-supplied name plus
    capability and rule labels (P20-1, non-hardware
    form). Applied False, restored False.
  * travel_scene — read-only. It returns a hologram or
    scene descriptor from caller-supplied scene labels
    (P20-6 Pepper's ghost, illusion not true hologram).
    Hardware False, rendered False.
  * travel_spatial — read-only. It returns a
    spatial-desktop descriptor from caller-supplied
    zone labels (P20-7 layout record; P20-9 augmented
    labels, label records only, confidence refused).
    Read_live False, never a live desktop read.
  * travel_sonify — read-only. It returns a
    sonification cue-map descriptor from caller-supplied
    state and cue labels (P20-10, non-hardware form).
    Played False, audio False.
  * travel_recipe — read-only. It returns a shareable
    recipe descriptor from caller-supplied steps plus
    source as supplied (P23-25). A sourceless step
    refuses whole; a personal step refuses whole.
  * travel_scoreboard — read-only. It returns an open
    scoreboard descriptor from caller-supplied
    graduation-evidence entries only (P23-26). Any
    numeric score or rate field refuses whole.
  * travel_record — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-validates the travel
    descriptor itself (it never trusts a caller-supplied
    travel JSON, which could claim anything) and writes
    only the travel record to a caller-chosen .json file
    in an already-existing folder — no folder creation,
    no overwrite of a core/data path, no .env/.pem/.key
    target. The funnel refuses it without a human confirm
    handler, before the handler runs. Recording books
    nothing, pays nothing, routes nothing, drives
    nothing, renders nothing, and moves nothing.

This coordinator is NOT a booker, a payer, a router, a
driver, a renderer, or a mover: it imports no vehicle,
payment, routing-service, or hardware code, books
nothing, pays nothing, routes nothing commercially,
drives nothing, renders nothing, and moves nothing. Any
booking, purchase, payment, send, vehicle-control,
commercial-routing, safety-sizing, or fake-data input
refuses whole in the module.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import travel as travel_mod


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


def travel_trip_handler(legs, accessibility_note=None,
                        pack_items=None, parking_note=None,
                        action="describe"):
    return _handler(travel_mod.build_trip, legs,
                    accessibility_note, pack_items,
                    parking_note, action)


def travel_door_to_door_handler(legs, deadline=None,
                                assumptions=None,
                                action="describe"):
    return _handler(travel_mod.build_door_to_door, legs,
                    deadline, assumptions, action)


def travel_transport_handler(mode_label, action="describe"):
    return _handler(travel_mod.build_transport, mode_label,
                    action)


def travel_loadout_handler(name, capabilities, rules,
                           action="describe"):
    return _handler(travel_mod.build_loadout, name,
                    capabilities, rules, action)


def travel_scene_handler(labels, scene_kind="hologram",
                         action="describe"):
    return _handler(travel_mod.build_scene, labels,
                    scene_kind, action)


def travel_spatial_handler(zones, augmented_labels=None,
                           action="describe"):
    return _handler(travel_mod.build_spatial, zones,
                    augmented_labels, action)


def travel_sonify_handler(states, cues, action="describe"):
    return _handler(travel_mod.build_sonify, states, cues,
                    action)


def travel_recipe_handler(title, steps, action="describe"):
    return _handler(travel_mod.build_recipe, title, steps,
                    action)


def travel_scoreboard_handler(entries, action="describe"):
    return _handler(travel_mod.build_scoreboard, entries,
                    action)


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


def travel_record_handler(record_kind, record_json, output_path):
    fields, err = _parse_json(record_json, "record_json")
    if err:
        return _error(err)
    if not isinstance(fields, dict):
        return _error("record_json must be a JSON object")
    try:
        rebuilt = travel_mod.validate_recordable_travel(
            record_kind, fields)
    except (ValueError, TypeError) as exc:
        return _error(f"travel descriptor does not re-build: {exc}")
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "TRAVEL_RECORD",
        "record_kind": record_kind.strip(),
        "travel": rebuilt,
        "booked": False, "paid": False, "routed": False,
        "drives": False, "rendered": False, "moved": False,
        "note": ("A travel, spatial, or recipe descriptor "
                 "the user chose, recorded only. Recording "
                 "books nothing, pays nothing, routes "
                 "nothing, drives nothing, renders nothing, "
                 "and moves nothing."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "TRAVEL_RECORD",
                       "booked": False, "paid": False,
                       "routed": False, "drives": False,
                       "rendered": False, "moved": False,
                       "report_path": str(target),
                       "note": "Travel descriptor recorded; nothing "
                               "was booked, paid, routed, driven, "
                               "rendered, or moved."})


def register_all(registry) -> None:
    """Register the 10 travel tools."""
    registry.register(
        name="travel_trip",
        description="DESCRIPTOR ONLY, NOTHING BOOKED OR ROUTED: return a trip descriptor from caller-supplied legs only (P19 trip pack, packing checklist, parking note, accessibility note as supplied, never inferred). No legs means NO trip, NOT_COMPUTED. Nothing is booked, routed commercially, or paid.",
        parameters={"type": "object", "properties": {
            "legs": {"type": "array", "items": {"type": "object"},
                     "description": "Caller-supplied legs, each origin + destination + optional mode/note"},
            "accessibility_note": {"type": "string",
                                   "description": "Caller-supplied accessibility note, never inferred"},
            "pack_items": {"type": "array", "items": {"type": "string"},
                           "description": "Caller-supplied pack checklist labels"},
            "parking_note": {"type": "string",
                             "description": "Caller-supplied parking note, saved only when asked"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["legs"]},
        handler=travel_trip_handler,
    )
    registry.register(
        name="travel_door_to_door",
        description="PLAN RECORD ONLY, NO COMMERCIAL ROUTING: return a door-to-door plan descriptor from caller-supplied legs in order (P19-12, non-commercial-routing form). No commercial routing is used and no route is computed by a routing service; the plan lists the legs you supplied, in order.",
        parameters={"type": "object", "properties": {
            "legs": {"type": "array", "items": {"type": "object"},
                     "description": "Caller-supplied legs, in order"},
            "deadline": {"type": "string",
                         "description": "Caller-supplied deadline label"},
            "assumptions": {"type": "string",
                            "description": "Caller-supplied assumptions note"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["legs"]},
        handler=travel_door_to_door_handler,
    )
    registry.register(
        name="travel_transport",
        description="HANDLER NOTE ONLY, NEVER A DRIVER: return a transport-handler descriptor from a caller-supplied mode label (P19 chauffeur position: handler yes, literal driver no). A driving label returns the driving-mode note only (voice-only, short, OFF by default, local law first); a safe-arrival label returns the safe-arrival note only, NOT an emergency-services replacement.",
        parameters={"type": "object", "properties": {
            "mode_label": {"type": "string",
                           "description": "Caller-supplied transport mode label"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["mode_label"]},
        handler=travel_transport_handler,
    )
    registry.register(
        name="travel_loadout",
        description="DESCRIPTOR ONLY, NOTHING APPLIED: return a loadout descriptor from a caller-supplied name plus capability and rule labels (P20-1, non-hardware form). The record lists exactly what it allows; applied False, restored False — suiting up and suiting down are actions, not built here.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string",
                     "description": "Caller-supplied loadout name"},
            "capabilities": {"type": "array", "items": {"type": "string"},
                             "description": "Caller-supplied capability labels"},
            "rules": {"type": "array", "items": {"type": "string"},
                      "description": "Caller-supplied rule labels"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["name", "capabilities", "rules"]},
        handler=travel_loadout_handler,
    )
    registry.register(
        name="travel_scene",
        description="DESCRIPTOR ONLY, NOT A RENDER: return a hologram or scene descriptor from caller-supplied scene labels (P20-6 Pepper's ghost, a classic illusion, not a true hologram). Hardware False, rendered False — a description, not a render.",
        parameters={"type": "object", "properties": {
            "labels": {"type": "array", "items": {"type": "string"},
                       "description": "Caller-supplied scene labels"},
            "scene_kind": {"type": "string",
                           "description": "'hologram' or 'scene'"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["labels"]},
        handler=travel_scene_handler,
    )
    registry.register(
        name="travel_spatial",
        description="DESCRIPTOR ONLY, NOTHING READ LIVE: return a spatial-desktop descriptor from caller-supplied zone labels (P20-7 layout record; P20-9 augmented labels, label records only, confidence refused). Read_live False, never a live desktop read.",
        parameters={"type": "object", "properties": {
            "zones": {"type": "array", "items": {"type": "string"},
                      "description": "Caller-supplied zone labels"},
            "augmented_labels": {"type": "array", "items": {"type": "object"},
                                 "description": "Caller-supplied zone + label entries, no confidence"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["zones"]},
        handler=travel_spatial_handler,
    )
    registry.register(
        name="travel_sonify",
        description="DESCRIPTOR ONLY, NOT A SOUND: return a sonification cue-map descriptor from caller-supplied state and cue labels (P20-10, non-hardware form). Played False, audio False — a map, not a sound; quiet, optional, and off in calls.",
        parameters={"type": "object", "properties": {
            "states": {"type": "array", "items": {"type": "string"},
                       "description": "Caller-supplied system-state labels"},
            "cues": {"type": "array", "items": {"type": "object"},
                     "description": "Caller-supplied state + cue pairs"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["states", "cues"]},
        handler=travel_sonify_handler,
    )
    registry.register(
        name="travel_recipe",
        description="DESCRIPTOR ONLY, NOTHING SHARED: return a shareable recipe descriptor from caller-supplied steps plus source as supplied (P23-25). A sourceless step refuses whole; a step flagged personal refuses whole, since a shareable recipe carries no personal data.",
        parameters={"type": "object", "properties": {
            "title": {"type": "string",
                      "description": "Caller-supplied recipe title"},
            "steps": {"type": "array", "items": {"type": "object"},
                      "description": "Caller-supplied steps, each step + source"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["title", "steps"]},
        handler=travel_recipe_handler,
    )
    registry.register(
        name="travel_scoreboard",
        description="DESCRIPTOR ONLY, NOTHING SCORED: return an open scoreboard descriptor from caller-supplied graduation-evidence entries only, item plus evidence reference as supplied (P23-26). Any numeric score or rate field refuses whole — a score must come from a bench pipeline, not a descriptor.",
        parameters={"type": "object", "properties": {
            "entries": {"type": "array", "items": {"type": "object"},
                        "description": "Caller-supplied entries, each item + evidence"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["entries"]},
        handler=travel_scoreboard_handler,
    )
    registry.register(
        name="travel_record",
        description="RECORD ONLY, NOTHING BOOKED, PAID, ROUTED, DRIVEN, RENDERED, OR MOVED: re-validate a caller-supplied travel descriptor (trip, door-to-door, transport, loadout, scene, spatial, sonify, recipe, or scoreboard) by re-running the builder that made it, and write only the travel record to a caller-chosen .json file in an already-existing folder. A descriptor that does not re-build is refused, never written on trust. The record asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "record_kind": {"type": "string",
                            "description": "One of trip, door-to-door, transport, loadout, scene, spatial, sonify, recipe, scoreboard"},
            "record_json": {"type": "string",
                            "description": "JSON object of the travel fields to re-validate"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["record_kind", "record_json", "output_path"]},
        handler=travel_record_handler,
    )
