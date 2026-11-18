"""VYUK skill signing (v5.67, Skills edition track, fence 4, SK4-12/14).

ONE signing story: OMS-style detached directory signing that
UPGRADES the existing plugins_hashes.json pattern
(core/plugin_loader.py: per-file SHA-256 hashes + a detached
Ed25519 .sig sidecar, release-time use, key never in tree).

* The per-file hashes remain as the INNER integrity check inside
  the signed manifest; the signature adds "unchanged, by VYUK,
  after this review" over the manifest's exact bytes.
* A signature proves ORIGIN and INTEGRITY -- NOT behaviour. A
  VYUK-signed skill can still be wrong, buggy, or (if review
  missed it) malicious in its text; signing never upgrades a
  skill to "trusted" and never exempts it from the admission
  gate (core/skill_gate.py) or the funnel gates.
* Trust is WITHDRAWABLE: a local, file-backed trust store maps
  key-id -> trusted/untrusted, and ``untrust()`` hard-disables
  affected skills (the gate consults the store's verdicts).
* Keys: this module NEVER generates, stores, or embeds a private
  key in the tree or in a test. Signing helpers take an
  externally supplied 32-byte seed; tests generate ephemeral
  keys in-memory per run (or use precomputed test vectors).
* Revocation (SK4-14): the NETWORK revocation fetch is a
  REFUSED STUB -- ``fetch_revocation_list`` raises
  ``RevocationFetchRefused`` naming owner decision D-15 and
  performs NO network I/O (this module imports no network
  library at all). The LOCAL kill list works offline:
  ``revoke_local`` hard-disables a key with a plain-language
  notice record -- the skill is never silently deleted, and the
  record states what was turned off and why. If the owner later
  rules out even an anonymous fetch, the fallback is
  revocation-via-app-update (declared in the leaf handoff,
  not built here).

Ed25519 is pure stdlib (hashlib), mirroring core/health/updater.py's
RFC 8032 verification semantics: 64-byte signatures over exact
bytes, base64 sidecars, fail-closed verification. The signing
side (absent from updater.py, which only verifies) is
implemented here from RFC 8032 and cross-checked against the
updater's verifier in the suite.

Stdlib only. Product VERSION is untouched (still 5.66.0).
No funnel tool declarations are added by this module.
"""

import base64
import hashlib
import json
from pathlib import Path

MODULE_VERSION = "5.67.0"  # module marker only, NOT the product VERSION

MANIFEST_NAME = "vyuk-skill-manifest.json"
SIGNATURE_NAME = MANIFEST_NAME + ".sig"
DEFAULT_TRUST_STORE_NAME = "skill_trust_store.json"
DEFAULT_KILL_LIST_NAME = "skill_kill_list.json"

# RFC 8032 curve constants (identical to core/health/updater.py).
_ED_Q = 2**255 - 19
_ED_L = 2**252 + 27742317777372353535851937790883648493
_ED_D = (-121665 * pow(121666, _ED_Q - 2, _ED_Q)) % _ED_Q
_ED_I = pow(2, (_ED_Q - 1) // 4, _ED_Q)
_ED_IDENTITY = (0, 1, 1, 0)


def _ed_xrecover(y, sign):
    xx = (y * y - 1) * pow(_ED_D * y * y + 1, _ED_Q - 2, _ED_Q) % _ED_Q
    x = pow(xx, (_ED_Q + 3) // 8, _ED_Q)
    if (x * x - xx) % _ED_Q != 0:
        x = x * _ED_I % _ED_Q
    if (x * x - xx) % _ED_Q != 0:
        raise ValueError("point is not on the curve")
    if x & 1 != sign:
        x = _ED_Q - x
    return x


_ED_BASE_Y = 4 * pow(5, _ED_Q - 2, _ED_Q) % _ED_Q
_ED_BASE_X = _ed_xrecover(_ED_BASE_Y, 0)
_ED_BASE = (_ED_BASE_X, _ED_BASE_Y, 1, _ED_BASE_X * _ED_BASE_Y % _ED_Q)


def _ed_add(p, other):
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


def _ed_mul(point, scalar):
    result = _ED_IDENTITY
    addend = point
    e = scalar
    while e > 0:
        if e & 1:
            result = _ed_add(result, addend)
        addend = _ed_add(addend, addend)
        e >>= 1
    return result


def _ed_encode(point):
    x, y, z, _t = point
    inv_z = pow(z, _ED_Q - 2, _ED_Q)
    ax = x * inv_z % _ED_Q
    ay = y * inv_z % _ED_Q
    encoded = bytearray(ay.to_bytes(32, "little"))
    if ax & 1:
        encoded[31] |= 0x80
    return bytes(encoded)


def _ed_decode_point(data):
    if len(data) != 32:
        raise ValueError("point encoding must be 32 bytes")
    y = int.from_bytes(data, "little") & ((1 << 255) - 1)
    sign = data[31] >> 7
    if y >= _ED_Q:
        raise ValueError("non-canonical y coordinate")
    x = _ed_xrecover(y, sign)
    return (x, y, 1, x * y % _ED_Q)


def _clamp(seed_half):
    clamped = bytearray(seed_half)
    clamped[0] &= 248
    clamped[31] &= 127
    clamped[31] |= 64
    return int.from_bytes(clamped, "little")


def public_key_from_seed(seed):
    """The 32-byte Ed25519 public key for an externally supplied
    32-byte seed. The seed itself is never stored by this module."""
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    digest = hashlib.sha512(seed).digest()
    return _ed_encode(_ed_mul(_ED_BASE, _clamp(digest[:32])))


def key_id_for_public_key(public_key):
    """Key-id: the public key's hex. Publisher identity IS the
    signature key, never a display name (SK4-16)."""
    if len(public_key) != 32:
        raise ValueError("public key must be 32 bytes")
    return public_key.hex()


def sign_message(seed, message):
    """RFC 8032 signing over exact bytes. Returns 64 bytes."""
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    digest = hashlib.sha512(seed).digest()
    scalar_a = _clamp(digest[:32])
    prefix = digest[32:]
    public_key = _ed_encode(_ed_mul(_ED_BASE, scalar_a))
    nonce = int.from_bytes(
        hashlib.sha512(prefix + message).digest(), "little") % _ED_L
    point_r = _ed_encode(_ed_mul(_ED_BASE, nonce))
    h = int.from_bytes(
        hashlib.sha512(point_r + public_key + message).digest(),
        "little") % _ED_L
    scalar_s = (nonce + h * scalar_a) % _ED_L
    return point_r + scalar_s.to_bytes(32, "little")


def verify_message(public_key, message, signature):
    """RFC 8032 verification, same semantics as
    core/health/updater.py's verifier: fail closed, never raises."""
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
    except Exception:  # noqa: BLE001 -- fail closed, never raise
        return False


# ---------------------------------------------------------------------------
# Manifest: build / verify (hashes), sign / verify (signature)
# ---------------------------------------------------------------------------

def build_manifest(skill_dir):
    """Per-file SHA-256 manifest for a skill directory: {relative
    posix path: hex digest}, sorted. The manifest file itself and
    its signature sidecar are excluded so signing is stable."""
    root = Path(skill_dir)
    manifest = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in (MANIFEST_NAME, SIGNATURE_NAME):
            continue
        manifest[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


def manifest_bytes(manifest):
    """Canonical manifest bytes: sorted keys, tight separators.
    The signature covers EXACTLY these bytes."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def verify_manifest(skill_dir, manifest):
    """Inner integrity check only (hashes, no signature):
    (True, []) when every file matches, else (False, reasons)
    naming missing, changed, and extra files."""
    root = Path(skill_dir)
    reasons = []
    actual = build_manifest(skill_dir)
    for rel, expected in sorted(manifest.items()):
        if rel not in actual:
            reasons.append(f"missing file: {rel}")
        elif actual[rel] != expected:
            reasons.append(f"changed file: {rel}")
    for rel in sorted(set(actual) - set(manifest)):
        reasons.append(f"extra file not in manifest: {rel}")
    return (not reasons), reasons


def sign_manifest_bytes(seed, manifest):
    """Detached signature (64 bytes) over the manifest's exact
    canonical bytes, from an externally supplied seed."""
    return sign_message(seed, manifest_bytes(manifest))


def verify_manifest_signature(public_key, manifest, signature):
    """True when the detached signature verifies over the
    manifest's exact canonical bytes."""
    return verify_message(public_key, manifest_bytes(manifest), signature)


def write_signed_manifest(skill_dir, seed):
    """Build the manifest, write it plus its base64 detached
    signature sidecar into the skill directory, and return
    (manifest, signature bytes). Release-time use only: the seed
    is supplied by the caller and never stored."""
    root = Path(skill_dir)
    manifest = build_manifest(root)
    signature = sign_manifest_bytes(seed, manifest)
    (root / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    (root / SIGNATURE_NAME).write_text(
        base64.b64encode(signature).decode("ascii") + "\n",
        encoding="utf-8")
    return manifest, signature


def verify_signed_manifest(skill_dir, public_key):
    """Full verification: (a) the sidecar signature verifies over
    the manifest file's EXACT bytes as stored, and (b) the stored
    manifest's hashes match the directory. (False, reasons) on
    any failure -- fail closed, never raises."""
    root = Path(skill_dir)
    manifest_path = root / MANIFEST_NAME
    sig_path = root / SIGNATURE_NAME
    try:
        stored_bytes = manifest_path.read_bytes()
        manifest = json.loads(stored_bytes.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False, ["manifest missing or unreadable"]
    if not isinstance(manifest, dict):
        return False, ["manifest is not a JSON object"]
    try:
        signature = base64.b64decode(
            sig_path.read_text(encoding="utf-8").strip(), validate=True)
    except Exception:  # noqa: BLE001
        return False, ["signature sidecar missing or not valid base64"]
    if len(signature) != 64:
        return False, ["signature sidecar is not 64 bytes"]
    # The signature covers the manifest's canonical bytes (what
    # write_signed_manifest signs); a manifest stored in
    # pretty-printed form parses back to the same dict, so also
    # accept a signature over the exact stored bytes (the
    # plugins_hashes.json sidecar convention, where the stored
    # bytes ARE the signed bytes). Either form verifying is
    # sufficient; neither verifying is a refusal.
    if not verify_message(public_key, stored_bytes, signature) and \
            not verify_message(public_key, manifest_bytes(manifest),
                               signature):
        return False, ["signature does not verify over the manifest "
                       "bytes (wrong key, or manifest tampered)"]
    ok, reasons = verify_manifest(skill_dir, manifest)
    if not ok:
        return False, reasons
    return True, []


# ---------------------------------------------------------------------------
# Trust store (withdrawable trust) + local kill list (SK4-14)
# ---------------------------------------------------------------------------

def _load_json_file(path, default):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default
    return data if isinstance(data, dict) else default


def _save_json_file(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def load_trust_store(store_path):
    """{key-id: {"trusted": bool, "reason": str}}. Missing or
    unreadable store = empty (nothing trusted)."""
    return _load_json_file(store_path, {})


def is_trusted(store_path, key_id):
    """True only for an explicitly trusted key-id. Unknown keys
    are NOT trusted (deny-by-default)."""
    entry = load_trust_store(store_path).get(key_id)
    return bool(entry and entry.get("trusted") is True)


def trust_key(store_path, key_id, reason=""):
    """Mark a key-id trusted. Returns the stored entry."""
    store = load_trust_store(store_path)
    store[key_id] = {"trusted": True, "reason": reason}
    _save_json_file(store_path, store)
    return store[key_id]


def untrust(store_path, key_id, reason=""):
    """Withdraw trust: hard-disables every skill signed with this
    key (the gate consults is_trusted(); an untrusted key's
    skills are refused). Returns the stored entry."""
    store = load_trust_store(store_path)
    store[key_id] = {"trusted": False, "reason": reason}
    _save_json_file(store_path, store)
    return store[key_id]


def load_kill_list(kill_list_path):
    """{key-id: {"reason": str, "notice": str}}. Missing store =
    empty (nothing killed)."""
    return _load_json_file(kill_list_path, {})


def is_revoked(kill_list_path, key_id):
    """True when the key-id is on the local kill list."""
    return key_id in load_kill_list(kill_list_path)


def revoke_local(kill_list_path, key_id, reason):
    """Offline kill: hard-disable a signing key with a
    plain-language notice record. Nothing is deleted -- the
    record states what was turned off and why, so the disable
    is visible, not silent. Returns the notice record."""
    notice = (
        f"Skill signed with key {key_id[:16]}... has been turned "
        f"off: {reason}. It was not deleted; it will not run, "
        f"load, or receive grants until this kill entry is "
        f"removed by a human.")
    store = load_kill_list(kill_list_path)
    store[key_id] = {"reason": reason, "notice": notice}
    _save_json_file(kill_list_path, store)
    return {"key_id": key_id, "reason": reason, "notice": notice,
            "disabled": True}


class RevocationFetchRefused(Exception):
    """Raised by fetch_revocation_list: a network revocation
    fetch is refused pending owner decision D-15."""


def fetch_revocation_list(url=None):
    """REFUSED STUB (SK4-14, owner decision D-15 pending). This
    function performs NO network I/O -- it raises before any
    request could be made, and this module imports no network
    library at all. Callers must use the local kill list
    (revoke_local) until D-15 is ruled."""
    raise RevocationFetchRefused(
        "Network revocation fetch refused: owner decision D-15 "
        "is pending (whether VYUK may fetch a revocation list, "
        "even anonymously, has not been ruled). No request was "
        "made. Use the offline local kill list (revoke_local) "
        "instead; if even an anonymous fetch is later ruled out, "
        "the fallback is revocation-via-app-update.")
