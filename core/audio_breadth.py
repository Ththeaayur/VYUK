"""Wave 4 AUDIO breadth — command boundaries, uncertain confirmation.

Stdlib only, no audio capture, no microphone, no identification.
Transcripts and confidences here are caller-supplied; this module
decides what may be acted on, never who spoke.

Rules, enforced in code:
  * A command is bounded: text outside an explicit start/end
    boundary is not a command, and an unterminated segment is
    refused, never guessed into an action.
  * Uncertain recognition must be confirmed: a segment whose
    confidence is below the caller-supplied threshold returns a
    confirm request, never an action. The threshold is a caller
    parameter, not a hidden default claim.
  * Interruption handling: an interrupt token ends the current
    segment cleanly; the partial segment is reported, not acted on.
  * Speaker labels are labels, never identity: a label is only
    assigned where the caller states the speakers are reliably
    distinguishable, and it is always "speaker-1" style, never a
    name. Where distinguishability is not stated, no label is
    assigned. Voiceprint does not exist here.
"""

from __future__ import annotations

START_MARKER = "[command]"
END_MARKER = "[/command]"
INTERRUPT_MARKER = "[interrupt]"


def split_commands(transcript: str) -> dict:
    """Split a caller-supplied transcript into bounded commands.

    Returns {"commands": [...], "partial": str or None, "interrupted": bool}.
    An interrupt marker ends the current segment: its text goes to
    "partial" and "interrupted" is True. Unterminated text after the
    last end marker is also "partial", never a command.
    """
    if not isinstance(transcript, str):
        return {"commands": [], "partial": None, "interrupted": False,
                "reason": "transcript is not text"}
    interrupted = INTERRUPT_MARKER in transcript
    # An interrupt ends everything after it for this pass.
    head = transcript.split(INTERRUPT_MARKER, 1)[0] if interrupted else transcript
    commands = []
    rest = head
    partial = None
    while True:
        start = rest.find(START_MARKER)
        if start == -1:
            if rest.strip():
                partial = rest.strip()
            break
        end = rest.find(END_MARKER, start + len(START_MARKER))
        if end == -1:
            partial = rest[start + len(START_MARKER):].strip() or None
            break
        commands.append(rest[start + len(START_MARKER):end].strip())
        rest = rest[end + len(END_MARKER):]
    if interrupted and partial is None:
        # Text before the interrupt that was not a closed command.
        tail = head.strip()
        if tail and tail not in commands:
            partial = tail
    return {"commands": commands, "partial": partial,
            "interrupted": interrupted}


def gate_recognition(command: str, confidence: float,
                     *, threshold: float = 0.7) -> dict:
    """Gate one recognised command on its caller-supplied confidence.

    Below threshold: a confirm request, never an action. At or above:
    permitted by this gate (the caller's own C-14 gate still applies).
    A confidence outside [0, 1] is invalid, never clamped silently.
    """
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return {"ok": False, "action": None,
                "reason": "confidence is not a number"}
    if confidence < 0 or confidence > 1:
        return {"ok": False, "action": None,
                "reason": f"confidence {confidence} outside [0, 1]"}
    if confidence < threshold:
        return {"ok": False, "action": None, "confirm": True,
                "reason": f"uncertain recognition ({confidence} < {threshold}): "
                          "confirm before acting"}
    return {"ok": True, "action": command, "confirm": False,
            "reason": "recognition confident enough for this gate"}


def speaker_label(speaker_index: int, *, reliably_distinguishable: bool) -> dict:
    """Assign a speaker label only where distinguishability is stated.

    Labels are "speaker-1", "speaker-2", ... — never a name, never
    an identity claim. Without a stated distinguishability, no label.
    """
    if not reliably_distinguishable:
        return {"ok": False, "label": None,
                "reason": "speakers not stated as reliably distinguishable: "
                          "no label assigned"}
    if not isinstance(speaker_index, int) or speaker_index < 1:
        return {"ok": False, "label": None,
                "reason": "speaker index must be a positive integer"}
    return {"ok": True, "label": f"speaker-{speaker_index}",
            "reason": "label only, not identity"}
