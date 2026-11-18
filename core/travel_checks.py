"""Travel descriptor checks (B1 split of core/travel.py).

Constant tables + the refusal/validation helpers the builders use.
Pure: no booking, payment, routing, driving, or hardware behaviour.
"""

import re

# Whole-word action verbs: a step/leg label shaped as one
# of these is a command, not a description, and refuses
# the whole call. Mode labels are labels, not steps, and
# are not scanned against this list.
_ACTION_WORDS = frozenset({
    "book", "booking", "booked", "buy", "purchase", "purchased",
    "pay", "paid", "send", "sent", "drive", "drives", "steer",
    "accelerate", "brake", "autopilot", "execute", "run", "deploy",
    "install", "promote", "push", "merge", "sign", "apply",
    "schedule", "remind", "move", "actuate", "print", "cut",
    "render", "renders", "rendered",
})

# Claim phrases: a caller statement that something already
# happened (booked, paid, sent, routed, driven, rendered)
# refuses whole — this module records plans, not outcomes.
_CLAIM_PHRASES = (
    "already booked", "already paid", "already sent",
    "was sent", "has been sent", "was booked", "was paid",
    "route computed", "computed by", "was driven",
    "was rendered", "was moved",
)

# Safety-sizing / sign-off phrases: never a descriptor claim.
_SAFETY_PHRASES = (
    "will hold", "load-bearing", "sign-off", "certified safe",
    "rated for", "maximum load", "safety-critical",
    "engineering sign-off",
)

# Fake-data phrases: a shield HUD must never show a fake.
_FAKE_PHRASES = (
    "fake attack", "fake pin", "fake data", "simulated attack",
)

# Routing-service names: naming one as used is a routing claim.
_ROUTING_WORDS = frozenset({
    "graphhopper", "routings service", "routing service",
})


def _refuse(reason):
    return {"ok": False, "reason": reason}


def _words(text):
    return set(re.findall(r"[a-z][a-z\-]*", text.lower()))


def _check_text(text, field):
    """Refuse whole on an action, claim, safety, fake, or
    routing phrase anywhere in one free-text field."""
    if not isinstance(text, str):
        return f"{field} must be a string"
    lowered = text.lower()
    for phrase in _CLAIM_PHRASES + _SAFETY_PHRASES + _FAKE_PHRASES:
        if phrase in lowered:
            return f"{field} carries a refused claim: {phrase}"
    for word in _ROUTING_WORDS:
        if word in lowered:
            return f"{field} names a routing service"
    return None


def _check_action(action):
    if action != "describe":
        return f"only 'describe' is performed, not {action!r}"
    return None


def _check_labels(labels, field):
    """A label list: non-empty strings, no action-shaped
    label, no refused phrase. Returns an error or None."""
    if not isinstance(labels, list):
        return f"{field} must be a list"
    for label in labels:
        if not isinstance(label, str) or not label.strip():
            return f"{field} entries must be non-empty strings"
        err = _check_text(label, field)
        if err:
            return err
        if _words(label) & _ACTION_WORDS:
            return f"{field} entry is shaped as an action: {label!r}"
    return None


def _dedup(labels):
    seen = []
    dropped = 0
    for label in labels:
        if label not in seen:
            seen.append(label)
        else:
            dropped += 1
    return seen, dropped


def _check_leg(leg):
    if not isinstance(leg, dict):
        return "each leg must be an object"
    allowed = {"origin", "destination", "mode", "note"}
    for key in leg:
        if key not in allowed:
            return f"leg carries an unexpected field: {key}"
    for field in ("origin", "destination"):
        value = leg.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"leg {field} must be a non-empty string"
        err = _check_text(value, field)
        if err:
            return err
    for field in ("mode", "note"):
        if field in leg:
            err = _check_text(leg[field], field)
            if err:
                return err
    return None


def _check_legs(legs):
    if not isinstance(legs, list):
        return "legs must be a list"
    for leg in legs:
        err = _check_leg(leg)
        if err:
            return err
    return None
