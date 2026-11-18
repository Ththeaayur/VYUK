"""Wave 10 (v5.65) — Offline Ed25519 licence files (MN-1.3 mechanism).

Verification ONLY. This module never signs, never generates keys, and
never contacts a network: a licence file is trusted if and only if its
Ed25519 signature over the canonical payload verifies under the
shipped public key. Stdlib only (``hashlib`` + ``json`` + ``base64``);
no PyNaCl, no new dependency.

The Ed25519 verifier below is our own code, written directly from
RFC 8032 (twisted Edwards curve, extended coordinates). It is
deliberately small (~70 lines of curve code) so it can be audited by
eye. It is used ONLY for licence files — it is not a general-purpose
crypto library, and nothing else in the app should import it as one.

Licence file format (JSON, one object)::

    {
      "product_id": "vyuk",
      "edition": "Pro",
      "holder": "A. Example",
      "issued_at": "2026-01-01",
      "expires_at": "2027-01-01",   // null = perpetual
      "signature": "<base64 Ed25519 signature, 64 bytes>"
    }

The signature covers the CANONICAL payload: the same five fields
(``product_id``, ``edition``, ``holder``, ``issued_at``,
``expires_at``) serialised as JSON with sorted keys and no
whitespace. ``signature`` itself is never part of the payload, so a
signature cannot be transplanted onto a different payload: changing
any payload byte breaks verification, and the result is reported as
``invalid`` in plain language.

Public key custody — READ THIS BEFORE PACKAGING:
The constant ``TEST_PUBLIC_KEY_B64`` shipped here is an OBVIOUSLY
PLACEHOLDER test key (32 zero bytes). It must never verify a
licence: the zero encoding decodes to a 4-torsion point, so a
crafted signature CAN verify under it (small-order forgery) —
which is why check_licence refuses the placeholder key outright
below instead of relying on verification failure. The packaging
coordinator MUST replace it with the real
release public key (see ``LICENCE_CUSTODY.md`` at the tree root) and
confirm the replacement by fingerprint before any release build.
Shipping the placeholder in a release build means no licence on earth
verifies — that failure is loud and safe, never silently permissive.

Expiry rules:
* Time-limited licences get an OFFLINE GRACE window of 14–30 days
  after ``expires_at`` (configurable in that range, default 30 days).
  Inside grace the licence still entitles, and the result says so
  plainly with the days remaining. Past grace it is ``expired``.
* PERPETUAL licences (``expires_at`` is null) NEVER expire and are
  NEVER disabled by an expiry check: an expired check on a perpetual
  licence reports, and the licence stays valid. There is no code path
  in this module that turns a perpetual licence off.
* Tampered payload or signature, malformed JSON, missing/unknown
  fields, bad dates, or a wrong product/edition (when the caller
  asks for one) all fail closed as ``invalid`` with a reason code.

Dates: ``issued_at`` / ``expires_at`` accept ``YYYY-MM-DD`` or a full
ISO datetime. A bare date means the END of that day (UTC), so a
licence expiring ``2027-01-01`` is good through that day. All
comparisons are naive UTC; callers passing ``now`` should pass UTC.

NO sale happens in this wave. This is the verification mechanism
only: no store, no payment rails, no selling code anywhere here.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha512

__all__ = [
    "TEST_PUBLIC_KEY_B64",
    "DEFAULT_GRACE_DAYS",
    "MIN_GRACE_DAYS",
    "MAX_GRACE_DAYS",
    "is_placeholder_key",
    "verify_ed25519",
    "canonical_payload",
    "Licence",
    "LicenceResult",
    "parse_licence",
    "check_licence",
    "check_licence_text",
    "check_licence_file",
]

# ---------------------------------------------------------------------------
# Ed25519 verification (RFC 8032), own code, stdlib only.
# ---------------------------------------------------------------------------

_Q = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _Q - 2, _Q)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)
# Base point, extended coordinates (X, Y, T, Z) with T = x*y, Z = 1.
_BX = 15112221349535400772501151409588531511454012693041857206046113283949847762202
_BY = 46316835694926478169428394003475163141307993866256225615783033603165251855960
_BASE = (_BX, _BY, (_BX * _BY) % _Q, 1)
_IDENTITY = (0, 1, 0, 1)


def _ed_add(p, q):
    x1, y1, t1, z1 = p
    x2, y2, t2, z2 = q
    a = (x1 * x2) % _Q
    b = (y1 * y2) % _Q
    c = (t1 * _D * t2) % _Q
    d = (z1 * z2) % _Q
    e = ((x1 + y1) * (x2 + y2) - a - b) % _Q
    f = (d - c) % _Q
    g = (d + c) % _Q
    # Curve parameter a_twist = -1, so H = B - a_twist*A = B + A
    # (local ``a`` here is X1*X2, not the curve parameter).
    h = (b + a) % _Q
    return ((e * f) % _Q, (g * h) % _Q, (e * h) % _Q, (f * g) % _Q)


def _ed_double(p):
    x1, y1, _t1, z1 = p
    a = (x1 * x1) % _Q
    b = (y1 * y1) % _Q
    c = (2 * z1 * z1) % _Q
    d = (-a) % _Q
    e = ((x1 + y1) * (x1 + y1) - a - b) % _Q
    g = (d + b) % _Q
    f = (g - c) % _Q
    h = (d - b) % _Q
    return ((e * f) % _Q, (g * h) % _Q, (e * h) % _Q, (f * g) % _Q)


def _scalarmult(point, scalar):
    result = _IDENTITY
    addend = point
    while scalar > 0:
        if scalar & 1:
            result = _ed_add(result, addend)
        addend = _ed_double(addend)
        scalar >>= 1
    return result


def _encode_point(point):
    x, y, _t, z = point
    inv_z = pow(z, _Q - 2, _Q)
    ax = (x * inv_z) % _Q
    ay = (y * inv_z) % _Q
    return (ay | ((ax & 1) << 255)).to_bytes(32, "little")


def _decode_point(data):
    if len(data) != 32:
        raise ValueError("point must be 32 bytes")
    raw = int.from_bytes(data, "little")
    sign = (raw >> 255) & 1
    y = raw & ((1 << 255) - 1)
    if y >= _Q:
        raise ValueError("point y out of range")
    xx = ((y * y - 1) * pow((_D * y * y + 1) % _Q, _Q - 2, _Q)) % _Q
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = (x * _I) % _Q
    if (x * x - xx) % _Q != 0:
        raise ValueError("point not on curve")
    if (x & 1) != sign:
        x = _Q - x
    return (x, y, (x * y) % _Q, 1)


def verify_ed25519(public_key, message, signature):
    """Return True iff ``signature`` (64 bytes) is a valid RFC 8032
    Ed25519 signature over ``message`` under ``public_key`` (32 bytes).
    Anything malformed — wrong lengths, undecodable point, S >= L —
    returns False, never raises."""
    try:
        if len(public_key) != 32 or len(signature) != 64:
            return False
        s = int.from_bytes(signature[32:], "little")
        if s >= _L:
            return False
        r_point = _decode_point(signature[:32])
        a_point = _decode_point(public_key)
        h = int.from_bytes(
            sha512(signature[:32] + public_key + message).digest(),
            "little") % _L
        left = _scalarmult(_BASE, s)
        right = _ed_add(r_point, _scalarmult(a_point, h))
        return _encode_point(left) == _encode_point(right)
    except (ValueError, OverflowError):
        return False


# ---------------------------------------------------------------------------
# Licence format, canonical payload, expiry rules.
# ---------------------------------------------------------------------------

# PLACEHOLDER TEST KEY — 32 zero bytes, base64. check_licence
# refuses this key outright (a crafted signature CAN verify under
# the zero point's 4-torsion, so verification failure alone is not
# a safe refusal — see the module docstring).
# The packaging coordinator replaces this with the real release public
# key; see LICENCE_CUSTODY.md. NEVER put a real private key here — the
# app ships ONLY the public key, and this constant is not a secret.
TEST_PUBLIC_KEY_B64 = base64.b64encode(bytes(32)).decode("ascii")

MIN_GRACE_DAYS = 14
MAX_GRACE_DAYS = 30
DEFAULT_GRACE_DAYS = 30

_PAYLOAD_FIELDS = ("product_id", "edition", "holder", "issued_at",
                   "expires_at")
_LICENCE_FIELDS = _PAYLOAD_FIELDS + ("signature",)


def is_placeholder_key(public_key_b64):
    """True iff the given base64 public key is the shipped placeholder."""
    try:
        return base64.b64decode(public_key_b64, validate=True) == bytes(32)
    except (binascii.Error, ValueError):
        return False


def canonical_payload(data):
    """Canonical bytes the signature covers: the five payload fields,
    JSON, sorted keys, no whitespace. ``data`` may carry ``signature``
    — it is always excluded."""
    payload = {k: data[k] for k in _PAYLOAD_FIELDS if k in data}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _parse_date(value, field_name):
    """Parse an ISO date/datetime to naive UTC datetime. A bare date
    means the end of that day. Raises ValueError on bad input."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("bad-date:%s" % field_name)
    text = value.strip()
    try:
        if "T" in text or " " in text:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        parsed_date = date.fromisoformat(text)
        return datetime.combine(parsed_date, time.max)
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
class Licence:
    product_id: str
    edition: str
    holder: str
    issued_at: str
    expires_at: object  # str, or None for perpetual

    @property
    def perpetual(self):
        return self.expires_at is None


@dataclass
class LicenceResult:
    status: str  # valid | in_grace | expired | invalid
    entitled: bool
    perpetual: bool
    reason: str  # "" when valid/in_grace; else a machine-readable code
    message: str  # plain-language report for the user
    licence: object = None  # Licence, or None when unparseable
    grace_days_remaining: object = None  # int, or None when N/A
    expires_at: object = None  # datetime, or None when perpetual/unparsed

    def __bool__(self):
        return self.entitled


def parse_licence(data):
    """Validate licence structure (not the signature). Returns a
    Licence, or raises ValueError with a reason code."""
    if not isinstance(data, dict):
        raise ValueError("not-an-object")
    unknown = sorted(set(data) - set(_LICENCE_FIELDS))
    if unknown:
        raise ValueError("unknown-field:%s" % ",".join(unknown))
    for name in _LICENCE_FIELDS:
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
    if not isinstance(data["signature"], str) or not data["signature"]:
        raise ValueError("bad-field:signature")
    return Licence(product_id=data["product_id"],
                   edition=data["edition"],
                   holder=data["holder"],
                   issued_at=data["issued_at"],
                   expires_at=data["expires_at"])


def _invalid(reason, message, licence=None):
    return LicenceResult(status="invalid", entitled=False, perpetual=False,
                         reason=reason, message=message, licence=licence)


def check_licence(data, now=None, public_key_b64=None, grace_days=None,
                  expected_product_id=None, expected_edition=None):
    """Check a licence (dict, or JSON text via check_licence_text).
    Fail closed: ANY malformed input, bad signature, or expired-beyond-
    grace licence returns entitled=False with a plain-language message.
    A perpetual licence is never expired and never disabled here."""
    if grace_days is None:
        grace_days = DEFAULT_GRACE_DAYS
    if not isinstance(grace_days, int) or isinstance(grace_days, bool) \
            or not (MIN_GRACE_DAYS <= grace_days <= MAX_GRACE_DAYS):
        raise ValueError(
            "grace_days must be an integer in %d..%d"
            % (MIN_GRACE_DAYS, MAX_GRACE_DAYS))
    if public_key_b64 is None:
        public_key_b64 = TEST_PUBLIC_KEY_B64
    if is_placeholder_key(public_key_b64):
        # Audit Fix Batch 3: refuse the placeholder key outright,
        # never verify against it. The zero encoding decodes to a
        # 4-torsion point, so a crafted signature CAN verify under
        # it (small-order forgery) — verification failure alone is
        # not a safe refusal. Same pattern as bz19's
        # "no-verifying-key" refusal. No honest licence is affected:
        # no signer holds this key's discrete log, so every licence
        # checked under it was already invalid; forged ones now are
        # too, with an honest reason.
        return _invalid("placeholder-key",
                        "The app's licence key is still the placeholder "
                        "test key, so no licence can be verified. A "
                        "release key is required before any sale.")
    try:
        public_key = base64.b64decode(public_key_b64, validate=True)
    except (binascii.Error, ValueError):
        return _invalid("bad-public-key",
                        "The app's licence key is damaged, so no licence "
                        "can be verified. Reinstalling the app fixes this.")
    if len(public_key) != 32:
        return _invalid("bad-public-key",
                        "The app's licence key is damaged, so no licence "
                        "can be verified. Reinstalling the app fixes this.")
    try:
        licence = parse_licence(data)
    except ValueError as exc:
        return _invalid(str(exc),
                        "This licence file is damaged or incomplete "
                        "(%s), so it cannot be accepted." % exc)
    if expected_product_id is not None \
            and licence.product_id != expected_product_id:
        return _invalid("wrong-product",
                        "This licence is for a different product "
                        "(%s), not %s."
                        % (licence.product_id, expected_product_id),
                        licence)
    if expected_edition is not None \
            and licence.edition != expected_edition:
        return _invalid("wrong-edition",
                        "This licence is for the %s edition, not %s."
                        % (licence.edition, expected_edition),
                        licence)
    try:
        signature = base64.b64decode(data["signature"], validate=True)
    except (binascii.Error, ValueError):
        return _invalid("bad-signature-encoding",
                        "This licence's signature is damaged, so it "
                        "cannot be accepted.", licence)
    if not verify_ed25519(public_key, canonical_payload(data), signature):
        return _invalid("signature-mismatch",
                        "This licence's signature does not match its "
                        "contents — the file has been tampered with or "
                        "was not issued for this app.", licence)
    moment = _normalise_now(now)
    if licence.perpetual:
        # Perpetual licences never expire: an expiry check reports,
        # and the licence stays valid. No disabling path exists here.
        return LicenceResult(
            status="valid", entitled=True, perpetual=True, reason="",
            message="Licence valid: perpetual licence for %s, held by %s."
                    % (licence.edition, licence.holder),
            licence=licence, grace_days_remaining=None, expires_at=None)
    expires = _parse_date(licence.expires_at, "expires_at")
    if moment <= expires:
        return LicenceResult(
            status="valid", entitled=True, perpetual=False, reason="",
            message="Licence valid until %s." % licence.expires_at,
            licence=licence, grace_days_remaining=None, expires_at=expires)
    grace_end = expires + timedelta(days=grace_days)
    if moment <= grace_end:
        remaining = (grace_end - moment).days
        return LicenceResult(
            status="in_grace", entitled=True, perpetual=False, reason="",
            message="Licence expired on %s but still within its %d-day "
                    "offline grace period (%d day(s) left). Renew to "
                    "avoid interruption."
                    % (licence.expires_at, grace_days, remaining),
            licence=licence, grace_days_remaining=remaining,
            expires_at=expires)
    return LicenceResult(
        status="expired", entitled=False, perpetual=False,
        reason="expired",
        message="Licence expired on %s and its %d-day offline grace "
                "period has ended. Renew to restore access."
                % (licence.expires_at, grace_days),
        licence=licence, grace_days_remaining=0, expires_at=expires)


def check_licence_text(text, **kwargs):
    """Check a licence from JSON text. Malformed JSON fails closed."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _invalid("malformed-json",
                        "This licence file is not valid JSON, so it "
                        "cannot be accepted.")
    return check_licence(data, **kwargs)


def check_licence_file(path, **kwargs):
    """Check a licence from a file path. An unreadable file fails
    closed as invalid (the caller reports the message)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        return _invalid("unreadable-file",
                        "The licence file could not be read, so it "
                        "cannot be accepted.")
    return check_licence_text(text, **kwargs)
