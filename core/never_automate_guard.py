"""
Never-automate guard for VYUK (v5.70, Life Admin track — LA-6
extension of the Money track's MN-5.14 list).

If any built helper could serve one of these shapes, it refuses
BY CONSTRUCTION — the caller raises before doing anything — not
by policy text. Pure logic: no network, no I/O, no telemetry,
no imports beyond the standard library (in fact, none at all).

Kinds (frozen for this track):
  "irctc"             — no IRCTC automation of any kind.
  "tatkal"            — no Tatkal automation (sub-35-second entry
                        is itself a detected bot signal, beside
                        the legal bar).
  "solver_farm"       — no CAPTCHA-solver farms (session/token
                        exfiltration).
  "booking_for_others"— no booking for others on a personal ID.
  "queue_jumping"     — no queue-jumping as a category.
  "aadhaar_beyond_act"— no Aadhaar handling beyond what the
                        Aadhaar Act permits.
  "otp_handling"      — the MN-5.14 OTP class, carried here so
                        the whole never-automate set is callable
                        from one place: never enter, read, relay,
                        store, or submit an OTP (nor a UPI/card
                        PIN, CVV, or 3-D Secure password — those
                        sibling MN-5.14 secrets share this refusal
                        shape; the kind name stays "otp_handling"
                        because OTP is the class this track adds
                        to the capture denylist).

Refusal, not redaction: refuse_if_never_automate() RAISES
NeverAutomateRefused — it never returns a masked or partial
result, and there is no "proceed with the secret removed" path.
The honest shape for term-barred surfaces is link-out +
guidance, never automation: the caller surfaces the exception's
message and stops.
"""

from __future__ import annotations


class NeverAutomateRefused(RuntimeError):
    """Raised when a never-automate shape is requested.

    `.kind` is the normalised kind; the message is the honest
    refusal text, safe to surface verbatim (it names the class,
    never any secret value)."""

    def __init__(self, kind: str, reason: str):
        super().__init__(reason)
        self.kind = kind


NEVER_AUTOMATE_REASONS = {
    "irctc": (
        "refused — IRCTC booking automation is never automated: "
        "she prepares and reads, and the human books on IRCTC's "
        "own surface."),
    "tatkal": (
        "refused — Tatkal automation is never automated: "
        "sub-35-second entry is itself a detected bot signal, "
        "beside the legal bar. The human books."),
    "solver_farm": (
        "refused — CAPTCHA-solver farms are never used: they "
        "exfiltrate the session and token the challenge protects."),
    "booking_for_others": (
        "refused — booking for others on a personal ID is never "
        "automated: a personal ID books only for its holder."),
    "queue_jumping": (
        "refused — queue-jumping automation is never built, as a "
        "category, whatever the queue."),
    "aadhaar_beyond_act": (
        "refused — Aadhaar handling beyond what the Aadhaar Act "
        "permits is never automated, including storing, relaying, "
        "or typing a full Aadhaar number into a portal."),
    "otp_handling": (
        "refused — OTP handling is never automated (MN-5.14): "
        "an OTP is never entered, read, relayed, stored, or "
        "submitted by the assistant, and neither is a UPI PIN, "
        "card PIN/CVV, or 3-D Secure password."),
}


def _normalise_kind(kind) -> str:
    """Lowercased, stripped, separators unified ("-" and spaces
    become "_") — so "Tatkal", "solver-farm" and "solver_farm"
    are the same kind. Non-string input becomes "" (unknown)."""
    try:
        text = str(kind)
    except Exception:  # noqa: BLE001 — unprintable kind is unknown
        return ""
    return text.strip().lower().replace("-", "_").replace(" ", "_")


def is_never_automate(kind) -> bool:
    """True when `kind` is one of the frozen never-automate kinds."""
    return _normalise_kind(kind) in NEVER_AUTOMATE_REASONS


def refusal_text(kind) -> str | None:
    """The refusal text for a never-automate `kind`, or None
    when `kind` is not a never-automate shape (None is NOT an
    approval — it only means this guard has nothing to refuse;
    the caller's own gates still apply)."""
    return NEVER_AUTOMATE_REASONS.get(_normalise_kind(kind))


def refuse_if_never_automate(kind) -> None:
    """Raise NeverAutomateRefused when `kind` is a never-automate
    shape; return None otherwise. An UNKNOWN kind raises
    ValueError — a caller asking about a kind this guard does not
    know is a caller bug, and silently passing it would let a new
    shape slip through unnamed. Callers that only know the seven
    frozen kinds never see the ValueError."""
    normalised = _normalise_kind(kind)
    reason = NEVER_AUTOMATE_REASONS.get(normalised)
    if reason is not None:
        raise NeverAutomateRefused(normalised, reason)
    raise ValueError(
        f"unknown never-automate kind {kind!r}; known kinds are "
        f"{sorted(NEVER_AUTOMATE_REASONS)}.")
