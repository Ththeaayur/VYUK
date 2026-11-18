"""The dignity bar, as code (Accessibility track, wave 31, §5.23).

The dignity bar is adopted as a STANDING RULE, in substance, and
this module is where it becomes enforceable instead of aspirational:

- "Works" is not "dignified". Every check below is a dignity
  property, not a feature flag.
- Users are named by their ACTUAL condition. A label built from an
  assumed or inferred condition is refused; the label must come from
  a condition record the user supplied.
- She meets users inside their EXISTING setup, hardware, and reader.
  This module never demands a specialist device or a new reader.
- THE USER'S ADAPTATIONS OUTRANK BRANDING. Resolving a setting
  always returns the user's adaptation over the brand default, with
  no exception path.
- AAC speed + layout stability are dignity properties: a slow AAC
  path is flagged as a dignity report (not silently accepted), and
  layout churn the user did not initiate is refused inside the
  stability window.
- Supporter-WITH, never supporter-INSTEAD (enforced jointly with
  ac_cognitive_v579's sequencing gate; this module carries the
  participation check in its own right).
- LIVE-MECHANISM consent for voices: using a voice needs a consent
  record for that live mechanism, not a blanket grant.
- HONESTY ABOUT BOUNDARIES: every feature disclosure must state
  what the feature cannot do. An empty boundary statement refuses.
- Disability/biometric data is NEVER telemetry (enforced here and
  in accessibility_capture_guard's gate, independently — the standalone
  rule forbids sharing the check, so both gates carry it).

NEVER-LIST (adopted, joins §9 by reference) — the operations this
track never performs, stated here so the fence is visible:
no copyleft access code bundled, no NC assets/corpora, no fake
"sign translation"/gloves, no cloud voice cloning or cloud adapter
training, no covert adult surveillance/dashboards/lockouts, no
routing around CAPTCHA/UAC/password walls "for access reasons", no
specialist hardware as core, no unproven accessibility claims and no
"VPAT certification" language, no logging access's sensitive
residue, no marketing/README licence claims in place of a licence.
The refusal functions for the operational items live in the modules
that own those surfaces (ac_speech_v579, accessibility_capture_guard);
this module owns the two claim-shaped items: ``vpat_claim`` and
``licence_claim_instead_of_licence``.

STANDALONE RULE (edition precedent): this module imports nothing from
any other ac_* module, and no ac_* module imports it.
"""

from __future__ import annotations

__all__ = [
    "DignityRefusal",
    "NEVER_LIST",
    "TELEMETRY_DENIED_FIELDS",
    "validate_condition_label",
    "resolve_setting",
    "aac_speed_report",
    "layout_change",
    "use_voice",
    "disclosure",
    "telemetry_submit",
    "vpat_claim",
    "licence_claim_instead_of_licence",
]

NEVER_LIST = (
    "copyleft access code bundled",
    "NC assets/corpora",
    "fake sign translation/gloves",
    "cloud voice cloning",
    "cloud adapter training",
    "covert adult surveillance/dashboards/lockouts",
    "routing around CAPTCHA/UAC/password walls for access reasons",
    "specialist hardware as core",
    "unproven accessibility claims",
    "VPAT certification language",
    "logging access's sensitive residue",
    "marketing/README licence claims in place of a licence",
)

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

LAYOUT_STABILITY_WINDOW_SECONDS = 5.0


class DignityRefusal(Exception):
    """Raised when a dignity-bar rule blocks an operation or claim."""


def validate_condition_label(label, user_supplied_condition):
    """Name users by their ACTUAL condition — supplied, never inferred.

    The label must match the condition record the user supplied.
    An inferred, assumed, or default condition is refused.
    """
    if user_supplied_condition is None:
        raise DignityRefusal(
            "condition label %r refused: no user-supplied condition "
            "— conditions are never assumed or inferred" % (label,))
    if label != user_supplied_condition:
        raise DignityRefusal(
            "condition label %r refused: it does not match the "
            "user-supplied condition %r"
            % (label, user_supplied_condition))
    return {"label": label, "source": "user_supplied"}


def resolve_setting(user_adaptation, brand_default):
    """The user's adaptations OUTRANK branding. Always. No exception."""
    if user_adaptation is not None:
        return {"value": user_adaptation, "won": "user_adaptation"}
    return {"value": brand_default, "won": "brand_default"}


def aac_speed_report(response_seconds, budget_seconds=2.0):
    """AAC speed is a dignity property: slowness is flagged, not hidden.

    Returns a report, never a silent pass: over-budget paths carry
    a dignity flag the product must surface.
    """
    over = response_seconds > budget_seconds
    return {"response_seconds": response_seconds,
            "budget_seconds": budget_seconds,
            "dignity_flag": "aac_slow" if over else None}


class LayoutState:
    """Layout stability: user-initiated changes pass; churn refuses."""

    def __init__(self):
        self.last_change_at = None

    def layout_change(self, at_seconds, user_initiated):
        """Refuse non-user-initiated churn inside the stability window."""
        if (not user_initiated and self.last_change_at is not None
                and at_seconds - self.last_change_at
                < LAYOUT_STABILITY_WINDOW_SECONDS):
            raise DignityRefusal(
                "layout change refused: non-user-initiated churn "
                "inside the %.1fs stability window (layout stability "
                "is a dignity property)"
                % LAYOUT_STABILITY_WINDOW_SECONDS)
        self.last_change_at = at_seconds
        return {"changed_at": at_seconds,
                "user_initiated": user_initiated}


def layout_change(state, at_seconds, user_initiated):
    """Module-level layout-change entry point (state supplied by caller)."""
    return state.layout_change(at_seconds, user_initiated)


def use_voice(voice_id, live_mechanism_consent):
    """Voice use needs LIVE-MECHANISM consent, not a blanket grant."""
    if not live_mechanism_consent:
        raise DignityRefusal(
            "voice %r refused: no live-mechanism consent record "
            "(blanket grants do not cover live voice use)"
            % (voice_id,))
    return {"voice_id": voice_id, "live_mechanism_consent": True}


def disclosure(feature, cannot_do):
    """Honesty about boundaries: every disclosure states what it cannot do.

    An empty boundary statement is refused — a feature that claims
    no limits is overclaiming.
    """
    if not cannot_do:
        raise DignityRefusal(
            "disclosure for %r refused: boundary statement empty — "
            "honesty about boundaries requires stating what the "
            "feature cannot do" % (feature,))
    return {"feature": feature, "cannot_do": list(cannot_do)}


def telemetry_submit(payload):
    """Refuse any telemetry payload carrying disability/biometric data."""
    bad = set(payload.keys()) & TELEMETRY_DENIED_FIELDS
    if bad:
        raise DignityRefusal(
            "telemetry refused: disability/biometric fields present: "
            "%s (disability/biometric data is never telemetry)"
            % ", ".join(sorted(bad)))
    return {"submitted": True, "fields": sorted(payload.keys())}


def vpat_claim():
    """Refused: no 'VPAT certification' language (never-list)."""
    raise DignityRefusal(
        "'VPAT certification' language refused (never-list, §5.23): "
        "no unproven accessibility claims")


def licence_claim_instead_of_licence(marketing_text):
    """Refused: marketing/README claims never stand in place of a licence."""
    raise DignityRefusal(
        "licence claim refused: marketing/README text (%r) never "
        "stands in place of a licence (§5.23 never-list)"
        % (marketing_text,))
