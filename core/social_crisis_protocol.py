"""Social track (v5.73) — CP-2.2 crisis protocol, standalone
guidance module.

This module provides the PROTOCOL, not detection. It does not
classify, score, or triage risk — there is deliberately no
detection or risk function here: a keyword matcher would
over-trigger on ordinary sadness and under-trigger on quiet
despair, and either failure is dangerous. The rule for any
companionship surface is: wire this protocol FIRST, before
any other companionship feature, and surface it whenever a
human (user or companion) raises concern — the surface
decides when, this module decides what.

Order is fixed: Tele-MANAS 14416 first (India's national
tele-mental-health helpline), then 112 (emergency). The
protocol never delays the call with conversation, never
offers therapy, never diagnoses, and never asks the person
to wait for a callback.
"""

TELE_MANAS = "14416"
EMERGENCY = "112"

CRISIS_LINES = [
    {"name": "Tele-MANAS (national tele-mental-health helpline)",
     "number": TELE_MANAS},
    {"name": "Emergency services", "number": EMERGENCY},
]


def crisis_protocol():
    """The full protocol dict, in display order. The first
    action is always the call — nothing precedes it."""
    return {
        "first_action": f"Call Tele-MANAS now: {TELE_MANAS}",
        "lines": list(CRISIS_LINES),
        "guidance": [
            "Call now — do not wait to finish a conversation.",
            "If you can, stay with a trusted person until help "
            "arrives; do not be alone.",
            "If there is immediate danger, call 112.",
            "You deserve help right now, whatever brought you "
            "here.",
        ],
        "never": [
            "never offer therapy or a diagnosis",
            "never score or classify risk",
            "never ask the person to wait for a callback",
            "never continue a normal conversation first",
        ],
    }


def crisis_notice_text():
    """Short text for display on any companionship surface.
    This ships WITH any companionship surface, never after
    it."""
    return (
        "If you're struggling right now, please call Tele-MANAS "
        f"at {TELE_MANAS} — free, confidential, 24x7. If there "
        "is immediate danger, call 112."
    )


def wire_check(surface_wires_crisis):
    """A surface self-check: pass True only if the surface
    actually wires crisis_protocol() before its companionship
    features. Returns a verdict dict; a False input is a
    hard fail, never a warning."""
    if surface_wires_crisis:
        return {"ok": True,
                "note": "crisis protocol wired with the surface"}
    return {"ok": False,
            "error": "companionship surface without a wired "
                     "crisis protocol must not ship"}
