"""S-6 capture guard (Accessibility track, wave 31, §5.23) — Nova's own code.

The plan's report never routed gaze/face/head modes through the
capture guard. This plan does: ALL gaze/face/head modes are
continuous capture, so S-6 applies IN FULL as an item condition on
every camera mode. No shortcut shape exists.

S-6, in full:
- DENYLIST: banned capture purposes are refused before any grant.
- PER-SESSION OPT-IN: a grant lives for one session only. There is
  no persistent camera grant; every session re-grants, or there is
  no capture.
- INDICATOR: capturing requires the indicator ON. Starting capture
  with the indicator off is refused; the indicator going off during
  capture forces a stop.
- RETENTION: default is NO retention — frames are discarded after
  processing. Retention needs an explicit record with an expiry, and
  expired retentions purge.
- DELETION: a session's capture records can be deleted, scoped to
  that session only. This is S-6's own deletion shape; it never
  touches any other store (PF-3 delete-everywhere is a separate,
  undecided ruling and is not implemented here).

DIGNITY RULE carried here because capture is where it bites:
disability/biometric data is NEVER telemetry. Any telemetry payload
carrying such fields is refused at this gate.

STANDALONE RULE (edition precedent): this module imports nothing from
any other ac_* module, and no ac_* module imports it. Captures are
records, not frames: this library holds no image, video, or biometric
data itself — the product's capture store does, behind this gate.
"""

from __future__ import annotations

__all__ = [
    "CaptureRefusal",
    "DENIED_PURPOSES",
    "TELEMETRY_DENIED_FIELDS",
    "SessionGrant",
    "grant_session",
    "check_purpose",
    "start_capture",
    "stop_capture",
    "add_retention",
    "purge_expired_retention",
    "delete_session",
    "telemetry_submit",
]

DENIED_PURPOSES = frozenset([
    "surveillance",
    "identification",
    "recording_others",
    "telemetry",
    "advertising",
    "background_collection",
])

TELEMETRY_DENIED_FIELDS = frozenset([
    "disability",
    "diagnosis",
    "condition",
    "gaze_trace",
    "face_embedding",
    "head_pose_trace",
    "biometric",
    "health",
])


class CaptureRefusal(Exception):
    """Raised when an S-6 condition blocks a capture operation."""


class SessionGrant:
    """A per-session camera grant. Sessions never inherit grants."""

    def __init__(self, session_id, mode):
        if not session_id or not mode:
            raise ValueError("session id and mode required")
        self.session_id = session_id
        self.mode = mode
        self.opted_in = False
        self.capturing = False
        self.indicator_on = False
        self.retentions = []

    def opt_in(self):
        self.opted_in = True
        return self

    def set_indicator(self, on):
        self.indicator_on = bool(on)
        if not on and self.capturing:
            self.capturing = False
        return self


def grant_session(session_id, mode):
    """Create a fresh, ungranted session. Opt-in is a separate step."""
    return SessionGrant(session_id, mode)


def check_purpose(purpose):
    """Refuse denylisted capture purposes before any grant is read."""
    if purpose in DENIED_PURPOSES:
        raise CaptureRefusal(
            "capture purpose %r is denylisted under S-6" % (purpose,))
    return {"purpose": purpose, "allowed": True}


def start_capture(grant, purpose):
    """Start capture under S-6: purpose, opt-in, indicator, in order."""
    check_purpose(purpose)
    if not grant.opted_in:
        raise CaptureRefusal(
            "capture refused: no per-session opt-in for session %r"
            % (grant.session_id,))
    if not grant.indicator_on:
        raise CaptureRefusal(
            "capture refused: indicator must be ON while capturing")
    grant.capturing = True
    return {"session_id": grant.session_id, "mode": grant.mode,
            "capturing": True, "indicator_on": True}


def stop_capture(grant):
    """Stop capture. Stopping is always allowed; state is honest."""
    was = grant.capturing
    grant.capturing = False
    return {"session_id": grant.session_id, "was_capturing": was,
            "capturing": False}


def add_retention(grant, record_id, expires_at):
    """Explicit retention only: a record with an expiry, or nothing.

    Default is no retention. This helper attaches an expiry-bearing
    retention record; frames without one are discarded by the
    product after processing, not stored.
    """
    if not expires_at:
        raise CaptureRefusal(
            "retention requires an expiry; no-expiry retention is "
            "refused (S-6 default is no retention)")
    grant.retentions.append({"record_id": record_id,
                             "expires_at": expires_at})
    return grant.retentions[-1]


def purge_expired_retention(grant, now):
    """Purge retentions whose expiry has passed. Returns purged ids."""
    kept = []
    purged = []
    for rec in grant.retentions:
        if rec["expires_at"] <= now:
            purged.append(rec["record_id"])
        else:
            kept.append(rec)
    grant.retentions = kept
    return purged


def delete_session(grant):
    """Delete this session's capture records, scoped to this session.

    Clears the grant's retentions and capturing state. It never
    touches any other session or store: PF-3 delete-everywhere is a
    separate undecided ruling and is not implemented here.
    """
    count = len(grant.retentions)
    grant.retentions = []
    grant.capturing = False
    grant.opted_in = False
    return {"session_id": grant.session_id,
            "deleted_retentions": count}


def telemetry_submit(payload):
    """Refuse any telemetry payload carrying disability/biometric data."""
    fields = set(payload.keys())
    bad = fields & TELEMETRY_DENIED_FIELDS
    if bad:
        raise CaptureRefusal(
            "telemetry refused: disability/biometric fields present: "
            "%s (disability/biometric data is never telemetry)"
            % ", ".join(sorted(bad)))
    return {"submitted": True, "fields": sorted(fields)}
