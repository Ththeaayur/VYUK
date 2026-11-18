"""Ed25519 verification + feed-signature check — split from updater.py (B24)."""
from __future__ import annotations

import base64
import hashlib
import urllib.request
from ..version import VERSION
from .updater_paths import _CHECK_TIMEOUT_S  # INTENTIONAL: shared constant
from .updater_transport import _opener_for  # INTENTIONAL: shared helper


# ---------------------------------------------------------------------------
# Signed manifests — Ed25519 (RFC 8032) verification, pure Python
#
# The update zip's SHA-256 travels inside the manifest, so the
# manifest itself must be authenticated or the hash proves nothing:
# a compromised feed host could otherwise serve a hostile zip plus
# its matching hash and own every install. The release manager
# signs each manifest with an Ed25519 key held OFFLINE (see
# packaging/sign_manifest.py and packaging/README.md, "Signing the
# update feed"); the public key is pinned here. check() fetches the
# detached signature from "<feed>.sig" (base64 of the 64-byte
# signature over the exact manifest bytes) and verifies it BEFORE
# the manifest is parsed.
#
# No new dependencies: the verifier below is a compact pure-Python
# RFC 8032 implementation (point decompression + the verification
# equation [S]B = R + [H(R, A, M)]A, points in extended coordinates).
# Manifests are a few hundred bytes, so clarity beats speed — one
# verification costs a few milliseconds. The RFC 8032 §7.1 test
# vectors are exercised in test_v53_signing.py, and the
# implementation is cross-checked there against openssl.
# ---------------------------------------------------------------------------

# The pinned update-feed public key (raw Ed25519, hex). Rotating the
# signing key means shipping an app update carrying the new pin —
# there is deliberately no way to change it from the feed itself.
UPDATE_FEED_PUBLIC_KEY_HEX = \
    "84f32c05e8ab1fa68edbf1312dcbe0ff29ecea9ee7226f133203a38c9f497191"
# Test seam: suites monkeypatch this module attribute with a test
# keypair's public key; check() reads it fresh on every call.
PINNED_UPDATE_PUBLIC_KEY = bytes.fromhex(UPDATE_FEED_PUBLIC_KEY_HEX)

_UNTRUSTED_MESSAGE = ("the update feed's signature didn't verify, "
                      "so nothing it says can be trusted")

_ED_Q = 2**255 - 19                       # field prime
_ED_L = 2**252 + 27742317777372353535851937790883648493  # group order
_ED_D = (-121665 * pow(121666, _ED_Q - 2, _ED_Q)) % _ED_Q  # curve param
_ED_I = pow(2, (_ED_Q - 1) // 4, _ED_Q)   # sqrt(-1) mod q
_ED_IDENTITY = (0, 1, 1, 0)               # extended coords (X, Y, Z, T)


def _ed_xrecover(y: int, sign: int) -> int:
    """The x for a curve point with this y and x-sign, per RFC 8032
    §5.1.3. Raises ValueError when no such point exists."""
    xx = (y * y - 1) * pow(_ED_D * y * y + 1, _ED_Q - 2, _ED_Q) % _ED_Q
    x = pow(xx, (_ED_Q + 3) // 8, _ED_Q)
    if (x * x - xx) % _ED_Q != 0:
        x = x * _ED_I % _ED_Q
    if (x * x - xx) % _ED_Q != 0:
        raise ValueError("point is not on the curve")
    if x == 0 and sign:
        raise ValueError("x = 0 with the sign bit set")
    if (x & 1) != sign:
        x = _ED_Q - x
    return x


_ED_BASE_Y = 4 * pow(5, _ED_Q - 2, _ED_Q) % _ED_Q
_ED_BASE_X = _ed_xrecover(_ED_BASE_Y, 0)
_ED_BASE = (_ED_BASE_X, _ED_BASE_Y, 1, _ED_BASE_X * _ED_BASE_Y % _ED_Q)


def _ed_add(p: tuple, other: tuple) -> tuple:
    """Extended twisted-Edwards addition (a = -1 formulas)."""
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = other
    a = (y1 - x1) * (y2 - x2) % _ED_Q
    b = (y1 + x1) * (y2 + x2) % _ED_Q
    c = 2 * _ED_D * t1 * t2 % _ED_Q
    dd = 2 * z1 * z2 % _ED_Q
    e = (b - a) % _ED_Q
    f = (dd - c) % _ED_Q
    g = (dd + c) % _ED_Q
    h = (b + a) % _ED_Q
    return (e * f % _ED_Q, g * h % _ED_Q, f * g % _ED_Q, e * h % _ED_Q)


def _ed_mul(point: tuple, scalar: int) -> tuple:
    """Double-and-add scalar multiplication."""
    result = _ED_IDENTITY
    addend = point
    e = scalar
    while e > 0:
        if e & 1:
            result = _ed_add(result, addend)
        addend = _ed_add(addend, addend)
        e >>= 1
    return result


def _ed_encode(point: tuple) -> bytes:
    """The 32-byte point encoding: y little-endian, x's low bit in
    the top bit."""
    x, y, z, _t = point
    inv_z = pow(z, _ED_Q - 2, _ED_Q)
    ax = x * inv_z % _ED_Q
    ay = y * inv_z % _ED_Q
    encoded = bytearray(ay.to_bytes(32, "little"))
    if ax & 1:
        encoded[31] |= 0x80
    return bytes(encoded)


def _ed_decode_point(data: bytes) -> tuple:
    """Decodes a 32-byte point encoding. Raises ValueError on
    anything malformed or non-canonical."""
    if len(data) != 32:
        raise ValueError("point encoding must be 32 bytes")
    y = int.from_bytes(data, "little") & ((1 << 255) - 1)
    sign = data[31] >> 7
    if y >= _ED_Q:
        raise ValueError("non-canonical y coordinate")
    x = _ed_xrecover(y, sign)
    return (x, y, 1, x * y % _ED_Q)


def _ed25519_verify(public_key: bytes, message: bytes,
                    signature: bytes) -> bool:
    """RFC 8032 §5.1.7 verification. Never raises — any malformed
    input is simply not a valid signature (fail closed)."""
    try:
        if len(public_key) != 32 or len(signature) != 64:
            return False
        point_a = _ed_decode_point(public_key)
        point_r = _ed_decode_point(signature[:32])
        scalar_s = int.from_bytes(signature[32:], "little")
        if scalar_s >= _ED_L:
            return False
        h = int.from_bytes(
            hashlib.sha512(
                signature[:32] + public_key + message).digest(),
            "little") % _ED_L
        return _ed_encode(_ed_mul(_ED_BASE, scalar_s)) == \
            _ed_encode(_ed_add(point_r, _ed_mul(point_a, h)))
    except Exception:  # noqa: BLE001 — fail closed, never raise
        return False


def _verify_feed_signature(feed_url: str,
                           manifest_bytes: bytes) -> str | None:
    """None when the detached signature at "<feed_url>.sig" verifies
    against PINNED_UPDATE_PUBLIC_KEY over the exact manifest bytes;
    the untrusted message otherwise. A missing, unreachable,
    malformed or wrong-length signature and a signature that does
    not verify are deliberately the SAME outcome to the caller: the
    feed cannot be trusted. Loopback feeds follow the rule too —
    there is no development exemption."""
    sig_url = feed_url + ".sig"
    try:
        request = urllib.request.Request(
            sig_url, headers={"User-Agent": f"VYUK-Updater/{VERSION}"})
        with _opener_for(sig_url).open(
                request, timeout=_CHECK_TIMEOUT_S) as resp:
            sig_raw = resp.read(4096)
    except Exception:  # noqa: BLE001 — unverifiable is unverifiable
        return _UNTRUSTED_MESSAGE
    try:
        signature = base64.b64decode(sig_raw.strip(), validate=True)
    except Exception:  # noqa: BLE001 — not base64: not a signature
        return _UNTRUSTED_MESSAGE
    if len(signature) != 64:
        return _UNTRUSTED_MESSAGE
    if not _ed25519_verify(PINNED_UPDATE_PUBLIC_KEY, manifest_bytes,
                           signature):
        return _UNTRUSTED_MESSAGE
    return None
