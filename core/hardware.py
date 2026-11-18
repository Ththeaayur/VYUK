"""Hardware-adjacent deltas — descriptors and records only, nothing moves.

Wave 9 Build 4 (HARDWARE), per build_flow_v6.md: "HARDWARE
(hardware-adjacent deltas that do not move hardware by
themselves) — BUILD, Wave 9, with Tier 2 plus a physical
stop independent of software for any item that later
moves hardware."

WHAT THIS MODULE IS
-------------------
* A hardware-inventory descriptor: a caller-supplied
  parts list — name + count as supplied — in, a
  plain-language inventory record out. Nothing here is
  measured, weighed, or checked against a real bench:
  the record echoes what the caller told it, and says so.
* A hardware-plan descriptor: caller-supplied steps in,
  a plain-language plan out. Every step that would move
  hardware must name its independent physical stop — a
  stop that exists outside the software, such as a hand
  on a switch, a mechanical limit, or a person present
  to intervene. A step moving hardware without a named
  physical stop refuses the whole plan; a stop is never
  defaulted, guessed, or quietly supplied.
* A hardware-safety descriptor: states, in plain words,
  what a plan here does NOT claim — no safety sizing,
  no engineering sign-off, no claim a design is safe
  or will hold. The disclaimer is part of every plan
  record, not an optional extra.
* A plan record validator: re-runs the plan builder on
  caller-supplied steps and requires an exact match, so
  a record is never written on trust.

WHAT THIS MODULE IS NOT — HARD REFUSALS
---------------------------------------
Any call carrying a refused kind of input refuses WHOLE,
never with a partial answer:
* any move, actuate, drive, print, or cut command to
  hardware — this module has no execute path at all,
  and no function here moves, drives, or commands
  anything;
* any plan step moving hardware without a named
  independent physical stop;
* any safety-sizing, load-bearing, will-it-hold, or
  engineering sign-off input or output — this module
  sizes nothing, rates nothing, and signs nothing off;
* any measured, weight, or tolerance value the caller
  did not supply as an ordinary inventory field — in
  fact, inventory parts and plan steps accept only
  their named fields, and any extra field (weight,
  tolerance, load, measured value) refuses whole, so a
  measurement can never slip in beside a plain part
  count.

This is a general product module. It describes
caller-supplied parts and steps only, and it makes no
claim about any specific project, arm, bench, or bench
test — those are outside this module entirely.

The module imports no serial, device, or hardware code
— proven by the AST test in tests/test_hardware_w9.py.
It reaches no device, moves nothing, and measures
nothing: there is nothing here to move anything with,
by construction.

FAIL-CLOSED, WHOLE-CALL
-----------------------
A refused phrase, a move step without a physical stop,
an unexpected field, a missing or negative count, or
an out-of-range value refuses the whole call. Nothing
is truncated, rounded into shape, or quietly replaced:
a plan the caller did not describe is worse than no
plan, and a safety claim softened into a hint is still
a safety claim.

LICENCE / DEPENDENCIES
----------------------
Own code, standard library only — in fact, no imports
beyond ``__future__`` annotations. No new dependency, no
telemetry write, no database access, no network access.
Nothing here reads or writes the install tree's ``data/``
directory.
"""

from __future__ import annotations

__all__ = [
    "SAFETY_CLAIM_PHRASES",
    "MEASURED_TOKENS",
    "MAX_PARTS",
    "MAX_COUNT",
    "MAX_STEPS",
    "MAX_FIELD_CHARS",
    "refusal_reason",
    "build_hardware_inventory",
    "build_hardware_plan",
    "build_hardware_safety",
    "validate_recordable_plan",
]

#: Safety-claim phrases that refuse a call whole. These
#: are claims this module never makes and never repeats
#: as its own: sizing, rating, holding, or signing off.
#: Matched on the lower-cased input text.
SAFETY_CLAIM_PHRASES = (
    "will hold",
    "will it hold",
    "load-bearing",
    "load bearing",
    "safety sign-off",
    "engineering sign-off",
    "sign-off",
    "signed off",
    "certified safe",
    "proven safe",
    "safe to use",
    "rated for",
    "maximum load",
    "max load",
    "weight limit",
    "safety-critical",
    "safety critical",
)

#: Measured-value tokens that refuse a call whole when
#: they appear as whole words in free text. A part name
#: such as "counterweight" does not trip "weight", but a
#: free-text claim carrying a weight or a tolerance does:
#: this module takes no measurements in and gives none
#: out, and a measurement beside a plain part count
#: would be presented as measured when it is not.
MEASURED_TOKENS = frozenset({
    "weight",
    "tolerance",
})

#: Caller-supplied bounds. A longer list or a larger
#: count is refused, not split or shortened silently.
MAX_PARTS = 500
MAX_COUNT = 100000
MAX_STEPS = 100
MAX_FIELD_CHARS = 500

_SAFETY_DISCLAIMER = (
    "This plan makes no safety claim. It does not size "
    "anything, it does not rate any load, it does not "
    "say a design is safe, and it does not say anything "
    "will hold. It is not an engineering sign-off, and "
    "nothing in it may be read as one. Any step that "
    "would move hardware names its independent physical "
    "stop, and that stop is outside this software."
)


def _tokens_in(text):
    """Lower-cased, word-ish tokens of a free-text input,
    for refusal scanning. Numbers and punctuation carry no
    refusal meaning, so only letter runs are kept."""
    lowered = str(text).lower()
    current = []
    for char in lowered:
        if char.isalpha() or char == "-":
            current.append(char)
        else:
            if current:
                yield "".join(current)
                current = []
    if current:
        yield "".join(current)


def refusal_reason(text):
    """The refusal reason for a free-text input, or None
    when it carries no refused kind of content. Phrases
    match on the lower-cased text; measured tokens match
    as whole words."""
    if text is None:
        return None
    if not isinstance(text, str):
        return "refused — free text must be a string"
    lowered = text.lower()
    for phrase in SAFETY_CLAIM_PHRASES:
        if phrase in lowered:
            return (f"refused — safety claim ({phrase!r}): "
                    f"this module sizes nothing, rates nothing, "
                    f"and signs nothing off")
    for token in _tokens_in(text):
        if token in MEASURED_TOKENS:
            return (f"refused — measured value ({token!r}): "
                    f"this module takes no measurements in and "
                    f"gives none out")
    return None


def _check_text_field(name, value):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if len(value) > MAX_FIELD_CHARS:
        raise ValueError(f"{name} exceeds {MAX_FIELD_CHARS} chars")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must not be empty")
    reason = refusal_reason(stripped)
    if reason is not None:
        raise ValueError(reason)
    return stripped


def _check_count(name, value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    if value > MAX_COUNT:
        raise ValueError(f"{name} exceeds {MAX_COUNT}")
    return value


def build_hardware_inventory(parts):
    """A hardware-inventory descriptor from caller-supplied
    parts only.

    Each part is exactly {"name": str, "count": int} —
    a name and a count as supplied, nothing else. Any
    extra field (weight, tolerance, load, measured value)
    refuses the whole inventory, because a measurement
    beside a plain part count would be presented as
    measured when it is not. The record echoes what the
    caller told it, and says so: nothing here was
    measured, weighed, or checked against a real bench."""
    if not isinstance(parts, (list, tuple)):
        raise ValueError("parts must be a list of part records")
    if len(parts) == 0:
        raise ValueError("parts must not be empty")
    if len(parts) > MAX_PARTS:
        raise ValueError(f"parts exceeds {MAX_PARTS} entries")
    echoed = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("each part must be an object")
        unexpected = sorted(set(part) - {"name", "count"})
        if unexpected:
            raise ValueError(
                f"part carries unexpected fields {unexpected}: "
                f"only name and count are accepted, so no "
                f"measurement can slip in beside a part count")
        name = _check_text_field("name", part.get("name"))
        count = _check_count("count", part.get("count"))
        echoed.append({"name": name, "count": count})
    return {
        "kind": "HARDWARE_INVENTORY",
        "parts": echoed,
        "total_parts": sum(p["count"] for p in echoed),
        "measured": False,
        "note": ("A parts list you supplied, echoed back. "
                 "Nothing here was measured, weighed, or "
                 "checked against a real bench, and no "
                 "claim about any specific project, arm, "
                 "or bench test is made."),
    }


def _check_step(step):
    """One plan step, validated whole. A step is exactly
    {"description": str, "moves_hardware": bool,
    "physical_stop": str or None}. A step that would move
    hardware without a named independent physical stop
    refuses whole — a stop is never defaulted or guessed.
    Any extra field refuses whole."""
    if not isinstance(step, dict):
        raise ValueError("each step must be an object")
    missing = {"description", "moves_hardware", "physical_stop"} - set(step)
    if missing:
        raise ValueError(
            f"step is missing fields {sorted(missing)}: all three "
            f"fields are required, so a physical stop can never "
            f"be absent by omission")
    unexpected = sorted(
        set(step) - {"description", "moves_hardware", "physical_stop"})
    if unexpected:
        raise ValueError(
            f"step carries unexpected fields {unexpected}: "
            f"only description, moves_hardware, and "
            f"physical_stop are accepted")
    description = _check_text_field("description", step.get("description"))
    moves = step.get("moves_hardware")
    if not isinstance(moves, bool):
        raise ValueError("moves_hardware must be true or false")
    stop = step.get("physical_stop")
    if moves:
        if stop is None:
            raise ValueError(
                f"step {description!r} would move hardware but "
                f"names no independent physical stop: a stop "
                f"outside this software is required, and one "
                f"is never defaulted or guessed")
        stop_text = _check_text_field("physical_stop", stop)
    else:
        if stop is not None and stop != "":
            stop_text = _check_text_field("physical_stop", stop)
        else:
            stop_text = None
    return {
        "description": description,
        "moves_hardware": moves,
        "physical_stop": stop_text,
    }


def build_hardware_plan(steps):
    """A hardware-plan descriptor from caller-supplied
    steps only.

    The plan describes; it never executes. It has no
    move, actuate, drive, print, or cut path, and no
    step in it moves anything by being described here.
    Every step that would move hardware must name its
    independent physical stop, and the safety disclaimer
    is part of the plan, not an optional extra."""
    if not isinstance(steps, (list, tuple)):
        raise ValueError("steps must be a list of step records")
    if len(steps) == 0:
        raise ValueError("steps must not be empty")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"steps exceeds {MAX_STEPS} entries")
    checked = [_check_step(step) for step in steps]
    return {
        "kind": "HARDWARE_PLAN",
        "steps": checked,
        "moves_anything": any(s["moves_hardware"] for s in checked),
        "moved": False,
        "executed": False,
        "safety": _SAFETY_DISCLAIMER,
        "note": ("A plan in words only. Nothing here moved, "
                 "and describing a step does not perform it. "
                 "Any step that would move hardware names "
                 "its independent physical stop above."),
    }


def build_hardware_safety():
    """A hardware-safety descriptor: what a plan here
    does NOT claim, in plain words. No input is needed
    and no claim is made — the descriptor exists so the
    absence of a safety claim is stated, not implied."""
    return {
        "kind": "HARDWARE_SAFETY",
        "claims": [
            "no safety sizing",
            "no engineering sign-off",
            "no claim a design is safe",
            "no claim anything will hold",
            "no load rating",
        ],
        "safety": _SAFETY_DISCLAIMER,
        "note": ("If a safety claim is needed, it must come "
                 "from a qualified check outside this tool. "
                 "Nothing in this module provides one."),
    }


def validate_recordable_plan(fields):
    """Re-validate a plan a caller wants recorded, by
    re-running the builder that made it and requiring an
    exact match. A record that does not re-build is
    refused, never written on trust. Returns the rebuilt
    plan dict, or raises ValueError."""
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    unexpected = sorted(set(fields) - {"steps"})
    if unexpected:
        raise ValueError(
            f"plan carries unexpected fields {unexpected}: a "
            f"record that does not re-build exactly is "
            f"refused, never written on trust")
    supplied = fields.get("steps")
    if not isinstance(supplied, (list, tuple)):
        raise ValueError("plan steps must be a list")
    rebuilt = build_hardware_plan(supplied)
    normalised = []
    for step in supplied:
        description = step["description"].strip() \
            if isinstance(step.get("description"), str) else None
        stop = step.get("physical_stop")
        if isinstance(stop, str):
            stop = stop.strip() or None
        normalised.append({
            "description": description,
            "moves_hardware": step.get("moves_hardware"),
            "physical_stop": stop,
        })
    if normalised != rebuilt["steps"]:
        raise ValueError("plan steps do not re-build")
    return rebuilt
