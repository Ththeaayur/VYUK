"""Travel descriptor builders (B1 split of core/travel.py).

The nine descriptor builders + the _BUILDERS kind table. Pure:
descriptors and records only, nothing booked, routed, or paid.
"""

# INTENTIONAL (travel split B1): check helpers live in
# travel_checks; builders resolve these bare names there.
from .travel_checks import (
    _check_action,
    _check_labels,
    _check_legs,
    _check_text,
    _dedup,
    _refuse,
    _words,
)

def build_trip(legs, accessibility_note=None, pack_items=None,
               parking_note=None, action="describe"):
    """Trip descriptor, caller-supplied legs only. No legs
    means NO trip: NOT_COMPUTED, not an empty trip."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_legs(legs)
    if err:
        return _refuse(err)
    if accessibility_note is not None:
        err = _check_text(accessibility_note, "accessibility_note")
        if err:
            return _refuse(err)
    if pack_items is not None:
        err = _check_labels(pack_items, "pack_items")
        if err:
            return _refuse(err)
    if parking_note is not None:
        err = _check_text(parking_note, "parking_note")
        if err:
            return _refuse(err)
    if not legs:
        return {
            "ok": True, "kind": "TRIP_NOT_COMPUTED",
            "computed": False, "legs": [],
            "note": ("No legs were supplied, so no trip was "
                     "computed. Supply legs to describe a trip; "
                     "nothing is booked, routed, or paid here."),
        }
    return {
        "ok": True, "kind": "TRIP_DESCRIPTOR",
        "legs": [dict(leg) for leg in legs],
        "accessibility_note": accessibility_note,
        "accessibility_inferred": False,
        "pack_items": list(pack_items) if pack_items else [],
        "parking_note": parking_note,
        "computed": False, "booked": False, "routed": False,
        "paid": False,
        "note": ("A trip as you supplied it. Nothing was "
                 "booked, routed commercially, or paid; the "
                 "accessibility note is yours as supplied, "
                 "never inferred."),
    }


def build_door_to_door(legs, deadline=None, assumptions=None,
                       action="describe"):
    """Door-to-door plan descriptor, non-commercial-routing
    form: the legs you supplied, in order, nothing routed."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_legs(legs)
    if err:
        return _refuse(err)
    for field, value in (("deadline", deadline),
                         ("assumptions", assumptions)):
        if value is not None:
            err = _check_text(value, field)
            if err:
                return _refuse(err)
    if not legs:
        return {
            "ok": True, "kind": "TRIP_NOT_COMPUTED",
            "computed": False, "legs": [],
            "note": ("No legs were supplied, so no "
                     "door-to-door plan was computed."),
        }
    return {
        "ok": True, "kind": "DOOR_TO_DOOR_PLAN",
        "legs": [dict(leg) for leg in legs],
        "deadline": deadline, "assumptions": assumptions,
        "computed": False, "routed": False,
        "commercial_routing_used": False,
        "note": ("A door-to-door plan listing the legs you "
                 "supplied, in order. No commercial routing "
                 "was used here and no route was computed "
                 "by a routing service."),
    }


def build_transport(mode_label, action="describe"):
    """Transport-handler descriptor: a mode label in, a
    handler note out. Never a vehicle control, never a
    driver."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if not isinstance(mode_label, str) or not mode_label.strip():
        return _refuse("mode_label must be a non-empty string")
    err = _check_text(mode_label, "mode_label")
    if err:
        return _refuse(err)
    lowered = mode_label.strip().lower()
    if _words(mode_label) & {"steer", "accelerate", "brake",
                             "autopilot", "self-driving",
                             "selfdriving"}:
        return _refuse("mode_label carries vehicle control")
    if lowered in ("driving", "drive", "car", "driving mode"):
        note = ("Driving-mode note only: voice-only, short "
                "answers, OFF by default, local law first, "
                "and navigation stays in the maps app. This "
                "is not driving, and nothing here controls "
                "a vehicle.")
    elif lowered in ("safe-arrival", "safe arrival",
                     "i don't feel safe", "i dont feel safe"):
        note = ("Safe-arrival note only: this is NOT a "
                "replacement for emergency services, and it "
                "says plainly to call them when it is "
                "serious. Sharing would be opt-in, to "
                "contacts set up in advance — no contacts "
                "are collected and nothing is shared here.")
    else:
        note = ("A transport handler note for the mode you "
                "named. This handles transport; it never "
                "controls a vehicle and is never a driver.")
    return {
        "ok": True, "kind": "TRANSPORT_HANDLER",
        "mode_label": mode_label.strip(),
        "controlled": False, "drives": False,
        "note": note,
    }


def build_loadout(name, capabilities, rules, action="describe"):
    """Loadout descriptor: a bundle of capability labels
    and rule labels, listing exactly what it allows.
    Applied False, restored False — suiting up and
    suiting down are actions, not built here."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if not isinstance(name, str) or not name.strip():
        return _refuse("name must be a non-empty string")
    err = _check_text(name, "name")
    if err:
        return _refuse(err)
    for field, value in (("capabilities", capabilities),
                         ("rules", rules)):
        err = _check_labels(value, field)
        if err:
            return _refuse(err)
    caps, caps_dropped = _dedup(capabilities)
    rule_list, rules_dropped = _dedup(rules)
    return {
        "ok": True, "kind": "LOADOUT_DESCRIPTOR",
        "name": name.strip(), "capabilities": caps,
        "rules": rule_list,
        "duplicates_dropped": caps_dropped + rules_dropped,
        "applied": False, "restored": False,
        "note": ("A loadout listing exactly what it allows. "
                 "Nothing was turned on, nothing was closed, "
                 "and nothing was restored — suiting up and "
                 "suiting down are actions, not built here."),
    }


def build_scene(labels, scene_kind="hologram", action="describe"):
    """Hologram/scene descriptor: scene labels in, a scene
    record out. Hardware False, rendered False — a
    description, not a render."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if scene_kind not in ("hologram", "scene"):
        return _refuse("scene_kind must be 'hologram' or 'scene'")
    err = _check_labels(labels, "labels")
    if err:
        return _refuse(err)
    shown, dropped = _dedup(labels)
    note = ("A scene description, not a render. No "
            "hardware was used and nothing was rendered.")
    if scene_kind == "hologram":
        note += (" A Pepper's ghost hologram is a classic "
                 "illusion, not a true hologram, and this "
                 "record describes it that way.")
    return {
        "ok": True, "kind": "SCENE_DESCRIPTOR",
        "scene_kind": scene_kind, "labels": shown,
        "duplicates_dropped": dropped,
        "hardware": False, "rendered": False, "note": note,
    }


def build_spatial(zones, augmented_labels=None, action="describe"):
    """Spatial-desktop descriptor: zone labels in, a layout
    record out. Never a live desktop read. Augmented
    labels are caller-supplied label records only; a
    confidence value is refused."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_labels(zones, "zones")
    if err:
        return _refuse(err)
    entries = []
    if augmented_labels is not None:
        if not isinstance(augmented_labels, list):
            return _refuse("augmented_labels must be a list")
        for entry in augmented_labels:
            if not isinstance(entry, dict):
                return _refuse("each augmented label must be an object")
            if set(entry) != {"zone", "label"}:
                return _refuse(
                    "each augmented label is exactly zone + label; "
                    "confidence and other fields are refused")
            for field in ("zone", "label"):
                if not isinstance(entry[field], str) \
                        or not entry[field].strip():
                    return _refuse(
                        f"augmented label {field} must be a "
                        "non-empty string")
                err = _check_text(entry[field], field)
                if err:
                    return _refuse(err)
            entries.append(dict(entry))
    zones_out, dropped = _dedup(zones)
    return {
        "ok": True, "kind": "SPATIAL_DESCRIPTOR",
        "zones": zones_out, "augmented_labels": entries,
        "duplicates_dropped": dropped,
        "read_live": False, "rendered": False,
        "note": ("A spatial layout as you supplied it. "
                 "Nothing was read from a live desktop, "
                 "and no label carries a recognition "
                 "confidence — confidence must come from "
                 "a recogniser, not a descriptor."),
    }


def build_sonify(states, cues, action="describe"):
    """Sonification descriptor: state labels plus cue
    labels in, a cue-map record out. Played False, audio
    False — a map, not a sound."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    err = _check_labels(states, "states")
    if err:
        return _refuse(err)
    if not isinstance(cues, list):
        return _refuse("cues must be a list")
    pairs = []
    for cue in cues:
        if not isinstance(cue, dict):
            return _refuse("each cue must be an object")
        if set(cue) != {"state", "cue"}:
            return _refuse("each cue is exactly state + cue")
        for field in ("state", "cue"):
            if not isinstance(cue[field], str) \
                    or not cue[field].strip():
                return _refuse(
                    f"cue {field} must be a non-empty string")
            err = _check_text(cue[field], field)
            if err:
                return _refuse(err)
        pairs.append(dict(cue))
    states_out, dropped = _dedup(states)
    return {
        "ok": True, "kind": "SONIFY_DESCRIPTOR",
        "states": states_out, "cues": pairs,
        "duplicates_dropped": dropped,
        "played": False, "audio": False,
        "note": ("A cue map, not a sound. Nothing was "
                 "played. Sonification stays quiet, "
                 "optional, and off in calls."),
    }


def build_recipe(title, steps, action="describe"):
    """Shareable-recipe descriptor: recipe steps plus
    source as supplied in, a recipe record out.
    Provenance on every step — a sourceless step refuses
    whole, and a step flagged personal refuses whole."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if not isinstance(title, str) or not title.strip():
        return _refuse("title must be a non-empty string")
    err = _check_text(title, "title")
    if err:
        return _refuse(err)
    if not isinstance(steps, list) or not steps:
        return _refuse("steps must be a non-empty list")
    clean = []
    for step in steps:
        if not isinstance(step, dict):
            return _refuse("each recipe step must be an object")
        if set(step) != {"step", "source"}:
            return _refuse(
                "each recipe step is exactly step + source; "
                "personal flags and other fields are refused")
        if not isinstance(step["step"], str) \
                or not step["step"].strip():
            return _refuse("recipe step text must be non-empty")
        if not isinstance(step["source"], str) \
                or not step["source"].strip():
            return _refuse(
                "every recipe step needs its source as "
                "supplied; a sourceless step refuses whole")
        err = _check_text(step["step"], "step")
        if err:
            return _refuse(err)
        err = _check_text(step["source"], "source")
        if err:
            return _refuse(err)
        clean.append(dict(step))
    return {
        "ok": True, "kind": "RECIPE_DESCRIPTOR",
        "title": title.strip(), "steps": clean,
        "shared": False,
        "note": ("A shareable recipe as you supplied it, "
                 "with a source on every step and no "
                 "personal data. Nothing was shared here; "
                 "the review step and permission summary "
                 "before install are not built here."),
    }


def build_scoreboard(entries, action="describe"):
    """Open-scoreboard descriptor: graduation-evidence
    entries only — item plus evidence reference as
    supplied — in, a scoreboard record out. Never an
    invented score: any numeric score or rate field
    refuses whole."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if not isinstance(entries, list) or not entries:
        return _refuse("entries must be a non-empty list")
    clean = []
    for entry in entries:
        if not isinstance(entry, dict):
            return _refuse("each scoreboard entry must be an object")
        if set(entry) != {"item", "evidence"}:
            return _refuse(
                "each scoreboard entry is exactly item + "
                "evidence; numeric scores and rates are "
                "refused — a score must come from a bench "
                "pipeline, not a descriptor")
        for field in ("item", "evidence"):
            if not isinstance(entry[field], str) \
                    or not entry[field].strip():
                return _refuse(
                    f"scoreboard entry {field} must be a "
                    "non-empty string")
            err = _check_text(entry[field], field)
            if err:
                return _refuse(err)
        clean.append(dict(entry))
    return {
        "ok": True, "kind": "SCOREBOARD_DESCRIPTOR",
        "entries": clean, "scores": None,
        "note": ("A scoreboard listing only what you "
                 "evidenced, item by item. Nothing was "
                 "scored here and nothing is claimed "
                 "beyond the supplied evidence."),
    }


_BUILDERS = {
    "trip": ("legs", "accessibility_note", "pack_items",
             "parking_note"),
    "door-to-door": ("legs", "deadline", "assumptions"),
    "transport": ("mode_label",),
    "loadout": ("name", "capabilities", "rules"),
    "scene": ("labels", "scene_kind"),
    "spatial": ("zones", "augmented_labels"),
    "sonify": ("states", "cues"),
    "recipe": ("title", "steps"),
    "scoreboard": ("entries",),
}
