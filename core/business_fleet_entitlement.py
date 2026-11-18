"""v5.72 (Business track, wave 18, BZ-1.9) — fleet-seat
entitlement, org-mode only. Reuses MN-1.3's offline
signed-entitlement/grace PATTERN (licence): Ed25519
verification (the tree's own RFC 8032 verifier), canonical
payload signing, offline grace 14–30 days (default 30), no
network anywhere.

Why a new format instead of reusing licence's parser:
licence.parse_licence REJECTS unknown fields, so seat
fields cannot ride inside a licence file — appending them
would either break verification or, worse, sit outside the
signed payload where they could be altered freely. The fleet
entitlement is therefore its own signed format, with the SAME
verification shape: the signature covers the canonical payload
of EVERY field except ``signature`` itself.

Format (JSON, one object)::

    {
      "product_id": "vyuk",
      "edition": "Business",
      "holder": "Acme Corp",
      "issued_at": "2026-01-01",
      "expires_at": "2027-01-01",   // null = perpetual
      "seats_purchased": 50,
      "enrolled_identities": ["alice@acme", "ben@acme"],
      "signature": "<base64 Ed25519 signature, 64 bytes>"
    }

Rules, all fail-closed except where noted:
  - Org-mode ONLY: edition must be "Business" (or the caller's
    expected_edition). A consumer edition is not seat-counted
    at all — the report itself rejects hard per-seat DRM for
    the consumer shape, so this module builds none.
  - Tampered payload/signature, malformed JSON, missing or
    unknown fields, bad dates, bad seat counts → invalid.
  - Expiry mirrors MN-1.3: time-limited entitlements get an
    offline grace window (14–30 days, default 30); inside grace
    the entitlement still entitles and says so plainly with
    days remaining; past grace it is expired. Perpetual never
    expires. A licence-server failure can never brick work
    because there is no licence server in this path at all —
    verification is fully offline.
  - Reconciliation (buyer-count vs enrolled-identity-count)
    happens in ONE format: reconcile() over the verified
    entitlement reports purchased, enrolled (unique),
    duplicates, delta, and within_limit. Over-enrollment is a
    WARNING, never a brick: entitled follows signature +
    expiry only, and the buyer true-ups on the report.

Stdlib only (plus licence's verifier). No network. No
funnel registration. No writes.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

try:
    from .licence import (
        DEFAULT_GRACE_DAYS,
        MAX_GRACE_DAYS,
        MIN_GRACE_DAYS,
        is_placeholder_key,
        verify_ed25519,
    )
except Exception:  # noqa: BLE001 — direct-module import fallback
    DEFAULT_GRACE_DAYS = 30
    MAX_GRACE_DAYS = 30
    MIN_GRACE_DAYS = 14

    def is_placeholder_key(public_key_b64):  # type: ignore[no-redef]
        try:
            return base64.b64decode(public_key_b64, validate=True) == bytes(32)
        except (binascii.Error, ValueError):
            return False

    def verify_ed25519(public_key, message, signature):  # type: ignore[no-redef]
        return False


_PAYLOAD_FIELDS = ("product_id", "edition", "holder", "issued_at",
                   "expires_at", "seats_purchased", "enrolled_identities")
_ENTITLEMENT_FIELDS = _PAYLOAD_FIELDS + ("signature",)
ORG_EDITION = "Business"


class EntitlementError(ValueError):
    """Raised for malformed entitlement input (fail-closed)."""


def canonical_entitlement_payload(data: dict) -> bytes:
    """Canonical bytes the signature covers: every payload field
    except ``signature`` itself, JSON, sorted keys, no
    whitespace. Any payload byte changed breaks verification."""
    payload = {k: data[k] for k in _PAYLOAD_FIELDS if k in data}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _parse_date(value, field_name):
    """Mirror of licence's rule: bare date means the END of
    that day (UTC); datetimes normalised to naive UTC."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("bad-date:%s" % field_name)
    text = value.strip()
    try:
        if "T" in text or " " in text:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        return datetime.combine(date.fromisoformat(text), time.max)
    except ValueError:
        raise ValueError("bad-date:%s" % field_name)


def _normalise_now(now):
    if now is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if isinstance(now, datetime):
        if now.tzinfo is not None:
            return now.astimezone(timezone.utc).replace(tzinfo=None)
        return now
    if isinstance(now, date):
        return datetime.combine(now, time.max)
    raise ValueError("now must be a date/datetime or None")


@dataclass(frozen=True)
class Entitlement:
    product_id: str
    edition: str
    holder: str
    issued_at: str
    expires_at: object  # str, or None for perpetual
    seats_purchased: int
    enrolled_identities: tuple

    @property
    def perpetual(self):
        return self.expires_at is None


@dataclass
class EntitlementResult:
    status: str  # valid | in_grace | expired | invalid
    entitled: bool
    perpetual: bool
    reason: str
    message: str
    entitlement: object = None
    grace_days_remaining: object = None
    expires_at: object = None

    def __bool__(self):
        return self.entitled


def parse_entitlement(data) -> Entitlement:
    """Validate entitlement structure (not the signature).
    Raises ValueError with a reason code on any violation."""
    if not isinstance(data, dict):
        raise ValueError("not-an-object")
    unknown = sorted(set(data) - set(_ENTITLEMENT_FIELDS))
    if unknown:
        raise ValueError("unknown-field:%s" % ",".join(unknown))
    for name in _ENTITLEMENT_FIELDS:
        if name not in data:
            raise ValueError("missing-field:%s" % name)
    for name in ("product_id", "edition", "holder", "issued_at"):
        if not isinstance(data[name], str) or not data[name].strip():
            raise ValueError("bad-field:%s" % name)
    if data["expires_at"] is not None and not isinstance(
            data["expires_at"], str):
        raise ValueError("bad-field:expires_at")
    _parse_date(data["issued_at"], "issued_at")
    if data["expires_at"] is not None:
        _parse_date(data["expires_at"], "expires_at")
    seats = data["seats_purchased"]
    if not isinstance(seats, int) or isinstance(seats, bool) or seats < 0:
        raise ValueError("bad-field:seats_purchased")
    enrolled = data["enrolled_identities"]
    if not isinstance(enrolled, list) or not all(
            isinstance(e, str) and e.strip() for e in enrolled):
        raise ValueError("bad-field:enrolled_identities")
    if not isinstance(data["signature"], str) or not data["signature"]:
        raise ValueError("bad-field:signature")
    return Entitlement(
        product_id=data["product_id"], edition=data["edition"],
        holder=data["holder"], issued_at=data["issued_at"],
        expires_at=data["expires_at"], seats_purchased=seats,
        enrolled_identities=tuple(e.strip() for e in enrolled))


def _invalid(reason, message, entitlement=None):
    return EntitlementResult(status="invalid", entitled=False,
                             perpetual=False, reason=reason,
                             message=message, entitlement=entitlement)


def check_fleet_entitlement(data, now=None, public_key_b64=None,
                            grace_days=None,
                            expected_edition=ORG_EDITION) -> EntitlementResult:
    """Check a fleet entitlement (dict, or JSON text via
    check_fleet_entitlement_text). Org-mode only: the edition
    must match expected_edition (default "Business"). Fail
    closed on any malformation, bad signature, or non-org
    edition. Expiry follows MN-1.3 grace semantics."""
    if grace_days is None:
        grace_days = DEFAULT_GRACE_DAYS
    if not isinstance(grace_days, int) or isinstance(grace_days, bool) \
            or not (MIN_GRACE_DAYS <= grace_days <= MAX_GRACE_DAYS):
        raise ValueError("grace_days must be an integer in %d..%d"
                         % (MIN_GRACE_DAYS, MAX_GRACE_DAYS))
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return _invalid("not-json", "entitlement is not valid JSON")
    try:
        ent = parse_entitlement(data)
    except ValueError as exc:
        return _invalid(str(exc), "entitlement structure is invalid: %s" % exc)
    if expected_edition is not None and ent.edition != expected_edition:
        return _invalid("not-org-edition",
                        "fleet-seat entitlement is org-mode only; "
                        "edition %r is not %r (consumer shapes are "
                        "never seat-counted)" % (ent.edition, expected_edition),
                        entitlement=ent)
    if public_key_b64 is None or is_placeholder_key(public_key_b64):
        return _invalid("no-verifying-key",
                        "no release public key configured; every "
                        "signature check fails closed",
                        entitlement=ent)
    try:
        public_key = base64.b64decode(public_key_b64, validate=True)
        signature = base64.b64decode(data["signature"], validate=True)
    except (binascii.Error, ValueError):
        return _invalid("bad-encoding", "public key or signature is "
                        "not valid base64", entitlement=ent)
    payload = canonical_entitlement_payload(data)
    if not verify_ed25519(public_key, payload, signature):
        return _invalid("bad-signature", "signature does not verify "
                        "over the entitlement payload", entitlement=ent)
    if ent.perpetual:
        return EntitlementResult(status="valid", entitled=True,
                                 perpetual=True, reason="", message=(
                                     "fleet entitlement is perpetual; "
                                     "it never expires"),
                                 entitlement=ent, expires_at=None)
    now_dt = _normalise_now(now)
    expires_dt = _parse_date(ent.expires_at, "expires_at")
    if now_dt <= expires_dt:
        return EntitlementResult(status="valid", entitled=True,
                                 perpetual=False, reason="", message=(
                                     "fleet entitlement is valid through "
                                     "%s" % ent.expires_at),
                                 entitlement=ent, expires_at=expires_dt)
    grace_end = expires_dt + timedelta(days=grace_days)
    if now_dt <= grace_end:
        remaining = (grace_end.date() - now_dt.date()).days
        return EntitlementResult(
            status="in_grace", entitled=True, perpetual=False, reason="",
            message=("fleet entitlement expired on %s; offline grace "
                     "period (%d day(s) left). Renew to keep the "
                     "fleet shape." % (ent.expires_at, remaining)),
            entitlement=ent, grace_days_remaining=remaining,
            expires_at=expires_dt)
    return EntitlementResult(status="expired", entitled=False,
                             perpetual=False, reason="expired",
                             message=("fleet entitlement expired on %s "
                                      "and its %d-day offline grace has "
                                      "passed" % (ent.expires_at, grace_days)),
                             entitlement=ent, expires_at=expires_dt)


def check_fleet_entitlement_text(text, **kwargs) -> EntitlementResult:
    if not isinstance(text, str):
        return _invalid("not-text", "entitlement text must be a string")
    try:
        data = json.loads(text)
    except ValueError:
        return _invalid("not-json", "entitlement is not valid JSON")
    return check_fleet_entitlement(data, **kwargs)


def reconcile(result: EntitlementResult) -> dict:
    """Buyer-count vs enrolled-identity-count, in ONE format, over
    a CHECKED entitlement result. Pure report — it never changes
    entitled: over-enrollment is a warning for true-up, never a
    brick (grace posture). Raises on unchecked/invalid input."""
    if not isinstance(result, EntitlementResult) or result.entitlement is None:
        raise EntitlementError("reconcile needs a checked entitlement "
                               "with a parsed payload")
    ent = result.entitlement
    enrolled = list(ent.enrolled_identities)
    unique = sorted(set(enrolled))
    duplicates = sorted({e for e in enrolled if enrolled.count(e) > 1})
    purchased = ent.seats_purchased
    delta = len(unique) - purchased
    return {
        "status": result.status,
        "entitled": result.entitled,
        "holder": ent.holder,
        "edition": ent.edition,
        "seats_purchased": purchased,
        "enrolled_count": len(unique),
        "duplicates": duplicates,
        "delta": delta,
        "within_limit": delta <= 0,
        "over_enrolled": delta > 0,
        "note": ("over-enrolled by %d seat(s): true-up the purchase; "
                 "work is NOT bricked by over-enrollment (grace "
                 "posture)" % delta) if delta > 0 else (
                     "%d seat(s) headroom" % (-delta) if delta < 0 else
                     "enrollment exactly matches purchase"),
    }
