"""Phase 2 voice foundations, NON-VETO only, Wave 2 (stdlib only).

Covers: command boundaries in a transcript stream, uncertain
recognition confirmation, and speaker labels. Vetoed items are
refused by name and never built here: Charon, M4, the earcon
family, F6 signatures, Space Grotesk pairing. Speaker labels are
NOT identity: a label is assigned only when the caller supplies a
distinguishing feature for that segment; otherwise the label is
"unknown-speaker". Voiceprint is not built, not implied, not
approximated. No new TTS engine, no wake change, no continuous
listening -- none of that is in this module.
"""

from __future__ import annotations

VETOED = ("charon", "m4", "earcon", "earcons", "f6",
          "space grotesk", "space-grotesk")


def veto_check(name: str) -> dict:
    """Return refused=True for a vetoed voice/feel item, else ok."""
    lowered = str(name or "").lower()
    for veto in VETOED:
        if veto in lowered:
            return {"ok": False, "name": name,
                    "reason": f"vetoed item '{name}' does not enter Wave 2"}
    return {"ok": True, "name": name, "reason": ""}


def split_commands(transcript_lines: list) -> list:
    """Split a transcript stream into command segments.

    A segment boundary is a line that is exactly a caller-supplied
    boundary marker: here, a blank line or a line ending with a
    full stop followed by a capitalised word is NOT guessed --
    boundaries are ONLY blank lines. Guessing boundaries from
    punctuation would mis-split commands, so this module does not.
    Each segment is a dict with its joined text and line count.
    """
    segments: list[dict] = []
    current: list[str] = []
    for line in transcript_lines:
        if not str(line).strip():
            if current:
                segments.append({"text": " ".join(current),
                                 "lines": len(current)})
                current = []
        else:
            current.append(str(line).strip())
    if current:
        segments.append({"text": " ".join(current), "lines": len(current)})
    return segments


def confirm_if_uncertain(text: str, confidence: float | None,
                         threshold: float | None) -> dict:
    """Decide whether an uncertain recognition needs confirmation.

    Governed numbers are never invented here: if the caller does
    not supply a threshold, the result is "unknown" and the caller
    must confirm -- never an automatic accept. Confidence must be
    a float in [0, 1]; anything else is "unknown".
    """
    if confidence is None or threshold is None:
        return {"text": text, "decision": "confirm",
                "reason": "confidence or threshold not supplied; "
                          "uncertain recognition always confirms"}
    try:
        conf = float(confidence)
        thresh = float(threshold)
    except (TypeError, ValueError):
        return {"text": text, "decision": "confirm",
                "reason": "non-numeric confidence/threshold; confirm"}
    if not (0.0 <= conf <= 1.0):
        return {"text": text, "decision": "confirm",
                "reason": "confidence out of range; confirm"}
    if conf < thresh:
        return {"text": text, "decision": "confirm",
                "reason": f"confidence {conf} below threshold {thresh}"}
    return {"text": text, "decision": "accept",
            "reason": f"confidence {conf} at or above threshold {thresh}"}


def speaker_label(segment: dict | None) -> str:
    """Label a segment only from a caller-supplied distinguishing feature."""
    if not isinstance(segment, dict):
        return "unknown-speaker"
    feature = segment.get("distinguishing_feature")
    if feature:
        return f"speaker:{feature}"
    return "unknown-speaker"
