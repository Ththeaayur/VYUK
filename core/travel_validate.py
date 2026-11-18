"""Travel descriptor re-validation (B1 split of core/travel.py).

validate_recordable_travel re-runs the builder for one record kind
and returns the rebuilt descriptor; a record is never written on
trust.
"""

# INTENTIONAL (travel split B1): builders live in
# travel_builders; validate resolves these bare names there.
from .travel_builders import (
    build_door_to_door,
    build_loadout,
    build_recipe,
    build_scene,
    build_scoreboard,
    build_sonify,
    build_spatial,
    build_transport,
    build_trip,
)

def validate_recordable_travel(record_kind, fields):
    """Re-run the builder for one record kind and return the
    rebuilt descriptor. A record is never written on trust:
    a caller-supplied JSON could claim anything, so the
    descriptor is rebuilt from its own fields and the
    rebuilt form is what gets recorded."""
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    if record_kind == "trip":
        rebuilt = build_trip(
            fields.get("legs"),
            fields.get("accessibility_note"),
            fields.get("pack_items"),
            fields.get("parking_note"))
        allowed = {"legs", "accessibility_note", "pack_items",
                   "parking_note"}
    elif record_kind == "door-to-door":
        rebuilt = build_door_to_door(
            fields.get("legs"), fields.get("deadline"),
            fields.get("assumptions"))
        allowed = {"legs", "deadline", "assumptions"}
    elif record_kind == "transport":
        rebuilt = build_transport(fields.get("mode_label"))
        allowed = {"mode_label"}
    elif record_kind == "loadout":
        rebuilt = build_loadout(
            fields.get("name"), fields.get("capabilities"),
            fields.get("rules"))
        allowed = {"name", "capabilities", "rules"}
    elif record_kind == "scene":
        rebuilt = build_scene(
            fields.get("labels"), fields.get("scene_kind",
                                              "hologram"))
        allowed = {"labels", "scene_kind"}
    elif record_kind == "spatial":
        rebuilt = build_spatial(
            fields.get("zones"), fields.get("augmented_labels"))
        allowed = {"zones", "augmented_labels"}
    elif record_kind == "sonify":
        rebuilt = build_sonify(
            fields.get("states"), fields.get("cues"))
        allowed = {"states", "cues"}
    elif record_kind == "recipe":
        rebuilt = build_recipe(
            fields.get("title"), fields.get("steps"))
        allowed = {"title", "steps"}
    elif record_kind == "scoreboard":
        rebuilt = build_scoreboard(fields.get("entries"))
        allowed = {"entries"}
    else:
        raise ValueError(f"unknown record_kind: {record_kind}")
    if set(fields) - allowed:
        raise ValueError("record carries unexpected fields")
    if not rebuilt.get("ok"):
        raise ValueError(rebuilt.get("reason", "rebuild refused"))
    # The rebuilt descriptor is what gets recorded, never
    # the presented fields: a tampered presentation that
    # does not rebuild is refused above, and a partial
    # presentation records only with builder defaults
    # filled in by the rebuild itself.
    return rebuilt
