"""
Secrets at rest (v5.47, programme item 14 — B-T4.21 + D-C5):
authenticated encryption for VYUK's secret blobs, with pluggable
backends tried in a fixed order.

Before v5.47 every secret VYUK kept was plaintext on disk: the
Google OAuth token (data/google_token.json) and the API keys in
the package-root .env could be read by anyone — or any program —
that could read the files. This module is the one place that
knows how a secret is protected, so each store (core/config.py
for .env keys, core/google_services.py for the token) just hands
over plaintext and stores the envelope it gets back.

Backends, in the order protect_bytes() tries them:

1. **keyring** — the OS credential store via the ``keyring``
   package, when it is importable. The blob itself lives in the
   OS store under a random reference id; the envelope carries
   only that id. (keyring is NOT a dependency of this package
   today, so on a stock install this branch is a clean
   try-import that falls through — it lights up by itself if a
   future build ships keyring.)
2. **Windows DPAPI** — win32 only, via ctypes
   (CryptProtectData / CryptUnprotectData with
   CRYPTPROTECT_UI_FORBIDDEN): the blob is sealed to the current
   Windows user account; no key material of ours exists at all.
   The raw calls are the module-level ``_dpapi_protect`` /
   ``_dpapi_unprotect`` functions so tests can monkeypatch a fake
   DPAPI seam on any platform (together with ``_IS_WINDOWS``).
3. **Machine key** — the always-available pure-stdlib fallback.
   A random 32-byte key is generated once and stored in a key
   file OUTSIDE data/ — by default ``.vyuk_secret_key`` in the
   package root, beside .env (data/ is user data that tools and
   backups treat casually; the key must not ride along with the
   ciphertexts it protects). The file is created with 0o600
   permissions where the OS honours them. The directory can be
   overridden with the VYUK_SECRET_KEY_DIR environment variable
   or the ``set_key_dir()`` setter (tests point it at a temp dir).
   The cipher construction, from stdlib primitives only, is
   exactly this — an HMAC-CTR stream cipher with
   encrypt-then-MAC, NOT a home-grown block cipher:

     keystream block i = HMAC-SHA256(key, nonce || i as 8-byte BE)
     ciphertext        = plaintext XOR keystream (truncated)
     tag               = HMAC-SHA256(key, b"tag" || nonce || ct)
     payload           = nonce (16 random bytes) || ct || tag

   HMAC-SHA256 as a PRF makes the keystream indistinguishable
   under the standard HMAC assumptions, the per-blob random
   nonce means two protections of the same secret never share a
   keystream, and the tag is verified (hmac.compare_digest)
   BEFORE any plaintext is produced — a tampered or truncated
   blob raises ValueError and yields nothing.
4. **none-plaintext** — if NO backend can protect (no keyring,
   not Windows, and the key file cannot be created or read):
   the blob is stored as-is and the envelope SAYS SO
   (backend name "none-plaintext"). This module never pretends
   a plaintext blob is encrypted; doctor.py reads
   ``active_backend_name()`` and warns in plain words when this
   is the active backend.

Envelope format (text-safe, one line, safe for .env values and
for whole-file storage of the token JSON):

    VYUKENC1:<backend>:<base64(payload)>

``protect_bytes`` returns that as bytes; ``protect_text`` /
``unprotect_text`` are the str twins. ``looks_protected`` detects
the envelope in str or bytes. ``unprotect_*`` dispatches on the
backend recorded in the envelope — a blob sealed by DPAPI is
opened by DPAPI even on a machine that would now choose the
machine key — and raises ValueError when the envelope is
malformed, the backend is unavailable, or authentication fails.
Callers decide what a failure means (config treats it as "key
not set"; google_services treats it as "not connected"); this
module never silently returns garbage or the ciphertext.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import os
from . import config_spec
import sys
from pathlib import Path

# Envelope marker. The trailing colon is part of the prefix so a
# value that merely starts with the word cannot false-positive.
ENVELOPE_PREFIX = "VYUKENC1:"

# Backend names as recorded in the envelope (and reported by
# active_backend_name() / doctor.py). These strings are a stored
# format — never rename one without a migration.
BACKEND_KEYRING = "keyring"
BACKEND_DPAPI = "dpapi"
BACKEND_MACHINE_KEY = "machine-key"
BACKEND_NONE = "none-plaintext"

_KEYRING_SERVICE = "VYUK-secrets"

# Platform seam for tests: the DPAPI branch keys off this flag,
# so a test can monkeypatch _IS_WINDOWS plus _dpapi_protect /
# _dpapi_unprotect and exercise the dpapi dispatch on any OS.
_IS_WINDOWS = sys.platform.startswith("win")

_KEY_FILENAME = ".vyuk_secret_key"
_KEY_BYTES = 32
_NONCE_BYTES = 16
_TAG_BYTES = 32  # full HMAC-SHA256 output

# Test/configuration seams (module-level on purpose, the house
# style for seams in this codebase): a forced backend and an
# explicit key directory. Both default to None = automatic.
_FORCED_BACKEND: str | None = None
_KEY_DIR_OVERRIDE: Path | None = None
_key_cache: dict[str, bytes] = {}


# ---------------------------------------------------------------------------
# Seams
# ---------------------------------------------------------------------------

def set_key_dir(path) -> None:
    """Points the machine-key file at ``path`` (a directory), or
    back to automatic resolution with None. The env var
    VYUK_SECRET_KEY_DIR is consulted when no setter override is
    in force. Changing the directory drops the cached key, so a
    test that points this at a temp dir gets a fresh key file."""
    global _KEY_DIR_OVERRIDE
    _KEY_DIR_OVERRIDE = Path(path) if path is not None else None
    _key_cache.clear()


def force_backend(name: str | None) -> None:
    """Forces protect_bytes() to a single named backend (or None
    for the automatic order). A test seam — production code never
    calls this; it exists so a suite can prove the machine-key
    construction on a machine that also has keyring/DPAPI."""
    global _FORCED_BACKEND
    if name is not None and name not in (
            BACKEND_KEYRING, BACKEND_DPAPI, BACKEND_MACHINE_KEY,
            BACKEND_NONE):
        raise ValueError(f"unknown secrets backend: {name!r}")
    _FORCED_BACKEND = name


def _key_dir() -> Path:
    """Where the machine key file lives: setter override first,
    then VYUK_SECRET_KEY_DIR, then the package root (beside
    .env) — deliberately NOT data/ (see the module docstring)."""
    if _KEY_DIR_OVERRIDE is not None:
        return _KEY_DIR_OVERRIDE
    env_dir = config_spec.env("VYUK_SECRET_KEY_DIR").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    return Path(__file__).resolve().parent.parent


def _key_path() -> Path:
    return _key_dir() / _KEY_FILENAME


# ---------------------------------------------------------------------------
# Backend 1: keyring (try-import; absent on a stock install)
# ---------------------------------------------------------------------------

def _keyring_module():
    """The keyring module when importable, else None. Imported
    per call (cheap once in sys.modules) so installing the
    package mid-process — or a test faking it — is picked up."""
    try:
        import keyring
    except Exception:  # noqa: BLE001 — absent/broken = not this backend
        return None
    return keyring


def _keyring_protect(data: bytes) -> bytes:
    """Stores the blob in the OS credential store and returns
    the reference id as the payload. Raises on any keyring
    failure so protect_bytes falls through to the next backend."""
    kr = _keyring_module()
    if kr is None:
        raise RuntimeError("keyring is not importable")
    ref = base64.urlsafe_b64encode(os.urandom(18)).decode("ascii")
    kr.set_password(_KEYRING_SERVICE, ref,
                    base64.b64encode(data).decode("ascii"))
    return ref.encode("ascii")


def _keyring_unprotect(payload: bytes) -> bytes:
    kr = _keyring_module()
    if kr is None:
        raise ValueError("blob was protected with keyring, "
                         "which is not available now")
    ref = payload.decode("ascii")
    stored = kr.get_password(_KEYRING_SERVICE, ref)
    if stored is None:
        raise ValueError("keyring entry for this blob is missing")
    return base64.b64decode(stored.encode("ascii"), validate=True)


# ---------------------------------------------------------------------------
# Backend 2: Windows DPAPI via ctypes (monkeypatchable seam)
# ---------------------------------------------------------------------------

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


def _dpapi_protect(data: bytes) -> bytes:
    """CryptProtectData with UI forbidden: seals ``data`` to the
    current Windows user. Module-level seam — tests replace this
    (and _dpapi_unprotect) with a fake to prove the dispatch."""
    if not _IS_WINDOWS:
        raise OSError("DPAPI is only available on Windows")
    import ctypes.wintypes as wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buf = ctypes.create_string_buffer(data, max(len(data), 1))
    in_blob = _DataBlob(len(data),
                        ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_ubyte)))
    out_blob = _DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    """CryptUnprotectData — the mirror of _dpapi_protect. DPAPI
    blobs carry their own integrity check, so a tampered blob
    fails inside Windows and surfaces here as an OSError, which
    unprotect_bytes re-raises as ValueError."""
    if not _IS_WINDOWS:
        raise OSError("DPAPI is only available on Windows")
    import ctypes.wintypes as wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buf = ctypes.create_string_buffer(data, max(len(data), 1))
    in_blob = _DataBlob(len(data),
                        ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_ubyte)))
    out_blob = _DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


# ---------------------------------------------------------------------------
# Backend 3: machine key (pure stdlib — HMAC-CTR + encrypt-then-MAC)
# ---------------------------------------------------------------------------

def _load_or_create_key() -> bytes:
    """The 32-byte machine key, creating the key file on first
    use. Creation is O_EXCL so two first runs cannot race two
    different keys into existence (the loser re-reads the
    winner's file). Raises OSError when the location is not
    usable at all — protect_bytes then falls to none-plaintext
    rather than pretending."""
    path = _key_path()
    cache_key = str(path)
    if cache_key in _key_cache:
        return _key_cache[cache_key]
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = None
    if raw is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        candidate = os.urandom(_KEY_BYTES)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                         0o600)
        except FileExistsError:
            raw = path.read_bytes()  # lost the race — use theirs
        else:
            try:
                os.write(fd, candidate)
                os.fsync(fd)
            finally:
                os.close(fd)
            raw = candidate
    if len(raw) != _KEY_BYTES:
        # Tolerate a base64-written key file (an admin may have
        # provisioned one by hand); anything else is corrupt and
        # must not silently become a weak key.
        try:
            decoded = base64.b64decode(raw.strip(), validate=True)
        except Exception:  # noqa: BLE001
            decoded = b""
        if len(decoded) != _KEY_BYTES:
            raise ValueError(
                f"machine key file {path} is not a {_KEY_BYTES}-byte key")
        raw = decoded
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # best-effort tightening; never a failure mode
    _key_cache[cache_key] = raw
    return raw


def _machine_key_available() -> bool:
    """Non-creating availability probe for active_backend_name():
    the key file already exists, or its directory exists / can be
    created in a writable parent. (protect_bytes still verifies
    for real by loading the key and falls through on failure.)"""
    path = _key_path()
    if path.exists():
        return True
    directory = path.parent
    if directory.is_dir():
        return os.access(directory, os.W_OK)
    parent = directory.parent
    return parent.is_dir() and os.access(parent, os.W_OK)


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """HMAC-CTR keystream: block i = HMAC-SHA256(key, nonce ||
    i as 8-byte big-endian), concatenated and truncated."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(
            key, nonce + counter.to_bytes(8, "big"),
            hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def _machine_key_protect(data: bytes) -> bytes:
    key = _load_or_create_key()
    nonce = os.urandom(_NONCE_BYTES)
    stream = _keystream(key, nonce, len(data))
    ct = bytes(b ^ s for b, s in zip(data, stream))
    tag = hmac.new(key, b"tag" + nonce + ct, hashlib.sha256).digest()
    return nonce + ct + tag


def _machine_key_unprotect(payload: bytes) -> bytes:
    if len(payload) < _NONCE_BYTES + _TAG_BYTES:
        raise ValueError("protected blob is truncated")
    nonce = payload[:_NONCE_BYTES]
    tag = payload[-_TAG_BYTES:]
    ct = payload[_NONCE_BYTES:-_TAG_BYTES]
    key = _load_or_create_key()
    expected = hmac.new(key, b"tag" + nonce + ct,
                        hashlib.sha256).digest()
    # Encrypt-then-MAC: authenticate FIRST; no plaintext exists
    # until the tag verifies.
    if not hmac.compare_digest(tag, expected):
        raise ValueError(
            "protected blob failed authentication (tampered, or "
            "the machine key changed)")
    stream = _keystream(key, nonce, len(ct))
    return bytes(b ^ s for b, s in zip(ct, stream))


# The DPAPI entries resolve through lambdas on purpose: the dict
# is built at import time, but _dpapi_protect / _dpapi_unprotect
# are a documented monkeypatch seam — a static dict would capture
# the original functions and silently bypass a patched seam.
_PROTECTORS = {
    BACKEND_KEYRING: _keyring_protect,
    BACKEND_DPAPI: lambda data: _dpapi_protect(data),
    BACKEND_MACHINE_KEY: _machine_key_protect,
    BACKEND_NONE: lambda data: data,
}
_UNPROTECTORS = {
    BACKEND_KEYRING: _keyring_unprotect,
    BACKEND_DPAPI: lambda payload: _dpapi_unprotect(payload),
    BACKEND_MACHINE_KEY: _machine_key_unprotect,
    BACKEND_NONE: lambda payload: payload,
}


def _keyring_usable() -> bool:
    """True only when keyring imports AND the backend it selected
    can actually store a secret. Importability alone is not
    usability: on a machine with no Secret Service (or no
    Windows vault) keyring imports fine but get_keyring()
    returns the fail backend, whose every operation raises
    NoKeyringError — reporting "keyring" as the active backend
    then would be a lie (B-D10, 2026-11-09: proved in .venv-qt
    with keyring installed but no D-Bus session — the name said
    "keyring" while protect_bytes sealed with the machine key).
    ``viable`` is never consulted: the fail backend reports
    viable True. Unprotecting an old keyring envelope does NOT
    use this check (see _keyring_unprotect) — a stored blob
    must still be attempted even when the current backend is
    unusable, and it raises ValueError then, as before."""
    kr = _keyring_module()
    if kr is None:
        return False
    try:
        backend = kr.get_keyring()
    except Exception:  # noqa: BLE001 — no backend at all
        return False
    try:
        from keyring.backends import fail as _fail
    except Exception:  # noqa: BLE001 — cannot identify: do not trust
        return False
    return not isinstance(backend, _fail.Keyring)


def _candidate_backends() -> list[str]:
    """The backend(s) protect_bytes should attempt, in order.
    A backend whose precondition visibly cannot hold (keyring
    not importable, not Windows) is skipped without an attempt;
    a backend that FAILS mid-protect falls through to the next
    one in protect_bytes itself."""
    if _FORCED_BACKEND is not None:
        return [_FORCED_BACKEND]
    order = []
    if _keyring_usable():
        order.append(BACKEND_KEYRING)
    if _IS_WINDOWS:
        order.append(BACKEND_DPAPI)
    order.append(BACKEND_MACHINE_KEY)
    return order


def active_backend_name() -> str:
    """The backend protect_bytes() WOULD use on this machine
    right now: "keyring" / "dpapi" / "machine-key" /
    "none-plaintext". doctor.py reports this verbatim — and
    warns in plain words when it is "none-plaintext"."""
    if _FORCED_BACKEND is not None:
        return _FORCED_BACKEND
    if _keyring_usable():
        return BACKEND_KEYRING
    if _IS_WINDOWS:
        return BACKEND_DPAPI
    if _machine_key_available():
        return BACKEND_MACHINE_KEY
    return BACKEND_NONE


# ---------------------------------------------------------------------------
# Envelope API
# ---------------------------------------------------------------------------

def looks_protected(data) -> bool:
    """True when ``data`` (str or bytes) carries the VYUKENC1
    envelope — how readers tell a protected value from legacy
    plaintext that predates v5.47 (and should be migrated)."""
    if isinstance(data, bytes):
        return data.startswith(ENVELOPE_PREFIX.encode("ascii"))
    if isinstance(data, str):
        return data.startswith(ENVELOPE_PREFIX)
    return False


def protect_bytes(data: bytes) -> bytes:
    """Protects ``data`` with the best available backend and
    returns the envelope as bytes:
    b"VYUKENC1:" + backend + b":" + base64(payload).
    Never raises for backend failures — every candidate is tried
    in order and the final fallback is an honestly-labelled
    none-plaintext envelope."""
    data = bytes(data)
    for backend in _candidate_backends():
        try:
            payload = _PROTECTORS[backend](data)
        except Exception:  # noqa: BLE001 — fall through, never pretend
            continue
        return (ENVELOPE_PREFIX.encode("ascii")
                + backend.encode("ascii") + b":"
                + base64.b64encode(payload))
    payload = _PROTECTORS[BACKEND_NONE](data)
    return (ENVELOPE_PREFIX.encode("ascii")
            + BACKEND_NONE.encode("ascii") + b":"
            + base64.b64encode(payload))


def unprotect_bytes(blob: bytes) -> bytes:
    """Reverses protect_bytes, dispatching on the backend named
    in the envelope. Raises ValueError when the envelope is
    malformed, names an unknown backend, the named backend is
    unavailable, or authentication fails — callers must treat a
    raise as "no usable secret", never as partial plaintext."""
    if isinstance(blob, str):
        blob = blob.encode("utf-8")
    prefix = ENVELOPE_PREFIX.encode("ascii")
    if not blob.startswith(prefix):
        raise ValueError("not a VYUK protected blob (no envelope)")
    rest = blob[len(prefix):]
    backend_raw, sep, payload_b64 = rest.partition(b":")
    if not sep:
        raise ValueError("malformed VYUK protected blob (no backend)")
    try:
        backend = backend_raw.decode("ascii")
    except UnicodeDecodeError:
        raise ValueError("malformed VYUK protected blob (backend)")
    opener = _UNPROTECTORS.get(backend)
    if opener is None:
        raise ValueError(f"unknown secrets backend in blob: {backend!r}")
    try:
        payload = base64.b64decode(payload_b64, validate=True)
    except Exception:  # noqa: BLE001
        raise ValueError("malformed VYUK protected blob (base64)")
    try:
        return opener(payload)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 — one failure shape for callers
        raise ValueError(
            f"could not unprotect blob with backend {backend!r}: {exc}")


def protect_text(text: str) -> str:
    """protect_bytes for str secrets (the .env / token-file
    shape): returns the one-line envelope text."""
    return protect_bytes(text.encode("utf-8")).decode("ascii")


def unprotect_text(text: str) -> str:
    """unprotect_bytes for str: a value WITHOUT the envelope is
    legacy plaintext and passes through unchanged (callers use
    looks_protected to spot it and migrate); an enveloped value
    must unprotect cleanly or raise ValueError."""
    if not looks_protected(text):
        return text
    return unprotect_bytes(text.strip().encode("ascii")).decode("utf-8")


__all__ = [
    "ENVELOPE_PREFIX",
    "BACKEND_KEYRING",
    "BACKEND_DPAPI",
    "BACKEND_MACHINE_KEY",
    "BACKEND_NONE",
    "set_key_dir",
    "force_backend",
    "active_backend_name",
    "looks_protected",
    "protect_bytes",
    "unprotect_bytes",
    "protect_text",
    "unprotect_text",
]
