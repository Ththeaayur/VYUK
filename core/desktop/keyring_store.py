"""B-D10 (2026-11-09) keyring secrets store — the ONE dict-returning
wrapper a driver calls for NAMED secrets (API keys, tokens,
credentials) kept in the OS credential store.

THE GAP (proved first, file/line evidence)
------------------------------------------
Secrets storage ALREADY exists in the tree — this batch builds
only the remainder:

* ``core/secrets_store.py`` (v5.47, programme item 14 — B-T4.21 +
  D-C5) is an authenticated-encryption ENVELOPE module:
  ``protect_bytes`` / ``protect_text`` seal a blob with the best
  available backend and return a one-line ``VYUKENC1:<backend>:
  <base64>`` envelope; ``unprotect_*`` dispatches on the backend
  recorded in the envelope. Backends, in order: (1) **keyring** —
  the blob lives in the OS credential store under a RANDOM
  reference id and the envelope carries only that id
  (``_keyring_protect``, secrets_store.py:174); (2) **Windows
  DPAPI** via ctypes (``_dpapi_protect``); (3) **machine key** —
  pure-stdlib HMAC-CTR + encrypt-then-MAC, the 32-byte key in a
  0o600 file ``.vyuk_secret_key`` in the package root, deliberately
  NOT data/; (4) **none-plaintext** — honestly labelled, never
  pretending to be encrypted. Callers: ``core/config.py``
  (``SECRET_ENV_KEYS``: GEMINI_API_KEY, GROQ_API_KEY —
  ``save_api_key`` at config.py:565-571 writes the key through
  ``_write_env`` as an envelope) and
  ``core/google_services_token.py`` (the Google OAuth token file's
  CONTENT is an envelope). What it does NOT have:
  1. NO keyring dependency — the keyring backend is a dormant
     try-import ("keyring is NOT a dependency of this package
     today, so on a stock install this branch is a clean
     try-import that falls through — it lights up by itself if
     a future build ships keyring", module docstring). On a
     stock install the active backend is DPAPI on Windows and
     the machine key elsewhere. B-D10 IS the build that ships
     keyring, so the OS-store backend lights up first.
  2. NO named-secret store — a blob is stored under a RANDOM
     reference id in the single fixed service ``"VYUK-secrets"``
     (``_KEYRING_SERVICE``, secrets_store.py); there is no
     set/get/delete/list by (service, username) anywhere in the
     tree. A driver that wants "the Gemini key" cannot address
     it by name, rotate it by name, or delete it by name — it
     can only seal a blob and keep the envelope.
  3. NO list surface — neither secrets_store nor keyring's own
     API enumerates credentials; a driver cannot ask "which
     secrets do I hold for this service".
  4. NO usability check on the keyring branch — ``_keyring_module``
     only proves the package IMPORTS. On a Linux machine with
     keyring installed but no Secret Service, the import
     succeeds, ``active_backend_name()`` reports "keyring", and
     ``protect_bytes`` then falls through to the machine key
     (proved in .venv-qt, 2026-11-09: active name "keyring",
     envelope backend "machine-key"). The name lied about the
     backend in use. B-D10 fixes that in secrets_store.py
     (``_keyring_usable``: importable AND the selected backend
     is not the fail backend) — a 2-call-site fix, because
     declaring keyring is what exposes the lie.

  What is genuinely missing, and what this module adds, is the
  named-secret primitive: set / get / delete / list by (service,
  username) against the real keyring OS store, with the
  no-leak rules below. The envelope machinery is NOT changed by
  this batch (its behaviour is theirs; it keeps sealing blobs
  exactly as before — only the keyring-usability check is
  corrected, see above). Boundary: B11's ``run_goal``
  persistence is for GOAL STEPS; this wrapper is for SECRETS.
  A secret is never a goal step and a goal step is never a
  secret. No existing credential is migrated automatically —
  the .env keys and the Google token stay exactly where they
  are; a migration is a separate decision for the parent.

LOPHOLE FAMILY — "a secret that leaks, or is read by the wrong
caller, or is lost"
---------------------------------------------------------------
* A secret written to a plaintext file (data/, config, logs).
* A secret that appears in a log line, an error message, or a
  test fixture.
* A secret read by any caller (no service/username scoping).
* A secret that survives after deletion (a stale copy in a file
  or a cache).
* A secret that cannot be rotated (no update path, or an update
  that leaves the old value behind).

ONE design closes the class: every named secret goes through
this module; a secret is identified by (service, username)
(never by a bare name alone); the store is the OS credential
store — Windows Credential Manager via keyring's
WinVaultKeyring backend (DPAPI-encrypted, per-user), Secret
Service on a Linux desktop that has one — and the wrapper keeps
NO plaintext copy anywhere (no file, no cache, no log: this
module has no logging call, no print, and no file IO at all);
a secret is never logged, never in an error message, never in
a test fixture (tests use fake backends + fake secrets, and the
fake asserts the value appears in no reason string); a delete
removes the entry from the store AND verifies the removal (a
deleted secret never reads back); a set overwrites atomically
and verifies by reading back (a rotated secret leaves no old
value behind, and a set that does not read back is a failure,
never a success).

Rejected alternatives (recorded):
* Plaintext files (data/, config, .env) — the thing this batch
  replaces for named secrets. (The existing .env keys stay
  where they are until a migration is decided; this wrapper
  does not write them.)
* Logging a secret, even partially — a masked value in a log
  is still a leak vector. (This module logs nothing at all.)
* Unscoped reads — any caller reading any secret. (Every call
  carries its (service, username); there is no "get the secret"
  without naming which one.)
* Delete-without-verification — a stale copy survives. (delete
  reads back after removing; a still-present entry is a
  failure.)
* Set-without-read-back — a write that silently did not land.
  (set reads back and compares; a mismatch is a failure.)
* A names-only index file for list() — a file that can hold a
  stale name after an out-of-band delete, and a second source
  of truth that can drift from the store. (list() enumerates
  the OS store itself on Windows; elsewhere it refuses
  honestly.)
* A home-grown file store with the keyring name on it — the
  engine here IS real keyring (``keyring.set_password`` /
  ``get_password`` / ``delete_password`` against the backend
  keyring selects); only LISTING is done by a direct store
  call, because keyring's API has no enumeration (see below).

DEPENDENCY (proposed — parent rules)
------------------------------------
* keyring 25.7.0 (MIT — licence read from the 25.7.0 PyPI
  wheel's LICENSE file, "MIT License, Copyright (c) 2025
  <copyright holders>", + METADATA "License-Expression: MIT",
  2026-11-09; commercial use permitted). 25.7.0 is the latest
  on PyPI; requires Python >= 3.9. LAZY-imported
  (function-level static imports, the sympy pattern); absence
  degrades every secret call to an honest engine_unavailable,
  nothing else. NOTE on the round-6 record ("MIT + PSF"): the
  wheels say MIT + BSD-3, not PSF — the PSF half was a
  pywin32 / pywin32-ctypes confusion (see below); the record
  is corrected here.
* Transitives, all read from their wheels 2026-11-09, no GPL
  anywhere: jaraco.classes 3.4.0 (MIT), jaraco.functools 4.6.0
  (MIT), jaraco.context 6.1.2 (MIT), more-itertools 11.2.1
  (MIT); Linux-only: SecretStorage 3.5.0 (BSD-3-Clause),
  jeepney 0.9.0 (MIT); Windows-only: pywin32-ctypes 0.2.3
  (BSD-3-Clause, Copyright (c) 2014 Enthought, Inc. — the
  library keyring's Windows backend uses for CredWrite /
  CredRead / CredDelete). ``keyrings.alt`` is NOT used (no
  alt backend is needed: the Windows vault backend ships
  inside keyring itself, and Secret Service ships inside
  keyring on Linux).
* Weight, measured 2026-11-09 from the installed Qt venv
  (du -sb): keyring 201,023 B + jaraco 105,784 B +
  more-itertools 517,380 B + SecretStorage 72,833 B + jeepney
  354,233 B = 1,251,253 B (~1.2 MB) on Linux including the
  Linux-only backends. The Windows shipped set is keyring +
  jaraco + more-itertools + pywin32-ctypes, unpacked 990,090 B =
  ~967 KB of code — under 1 MB. (Parent correction 2026-11-09:
  the worker's "~855 KB" used the pywin32-ctypes WHEEL size
  (30,756 B) instead of its unpacked size (165,903 B).)
  (more-itertools, a jaraco.functools dependency, is the biggest
  single piece.)
* Installed in .venv-qt only (25.7.0); system python3 has no
  keyring (PEP 668 blocks pip there; --break-system-packages
  was NOT used) — the wrapper degrades there to
  engine_unavailable.

THE STORE (which backend, on which platform)
--------------------------------------------
keyring selects the backend itself (``keyring.get_keyring()``):
on Windows the WinVaultKeyring backend (Windows Credential
Manager, DPAPI-encrypted, per-user; persistence
CRED_PERSIST_ENTERPRISE, keyring's default); on a Linux
desktop with a Secret Service daemon the SecretService
backend; on a machine with no usable backend it returns the
fail backend (``keyring.backends.fail.Keyring``), whose every
operation raises NoKeyringError. ``viable`` does NOT detect
that — the fail backend reports viable True (proved in
.venv-qt, 2026-11-09: this Linux lab machine has no D-Bus
session, so get_keyring() returns the fail backend). This
module therefore treats a backend as usable ONLY when it is
not the fail backend (``isinstance`` check) — importability
alone is never enough. When no backend is usable the wrapper
degrades to engine_unavailable; it NEVER falls back to a
plaintext file (a silent plaintext fallback would be the
leak this batch exists to close).

LISTING (why it is a direct store call)
---------------------------------------
keyring's API has no enumeration: KeyringBackend offers
get_password / set_password / delete_password /
get_credential only, and WinVaultKeyring adds nothing (proved
by reading keyring 25.7.0's backend.py and
backends/Windows.py). list() therefore enumerates the same
store keyring's Windows backend writes to, via the same
library it uses: win32cred.CredEnumerate() from
pywin32-ctypes (preferred, exactly as keyring's Windows
backend imports it) or pywin32 as a fallback — with NO
filter, then exact-match filtering in Python. A Filter
matches target names by PREFIX, so a compound target
(``alice@VYUK-gemini``) never matches a "VYUK-gemini"
filter, and only an unfiltered enumeration sees it (proved
by the test suite's fake win32cred, which enforces the same
prefix semantics). A credential counts iff its TargetName
is the plain service name OR the compound name keyring's
Windows backend uses for a second username under one service
(``{username}@{service}``, WinVaultKeyring._compound_name)
— a neighbouring service such as "VYUK-gemini2" never leaks
into "VYUK-gemini"'s list. Only UserNames are returned, never
secrets — the credential blobs are never read. On a platform
where win32cred cannot be imported
(this Linux lab machine included — its import fails inside
cffi's C parser, which is why the import is guarded by a
broad except, not ImportError alone) list() refuses honestly
instead of guessing. The Windows enumeration path cannot run
on this lab machine; it is a field leg, owed, not this batch.

SERVICE NAMESPACING
-------------------
Every service the driver names is stored under "VYUK-<service>"
in the OS store, so VYUK's entries are identifiable in the
shared vault and list() can filter them. The existing
"VYUK-secrets" service (secrets_store.py's envelope blobs) is
a different service string and is never touched by this
module. Returned dicts always report the caller's unprefixed
service name.
"""

# This module has no logging call, no print, and no file IO.
# A secret must have nowhere to leak to: no log line, no error
# message, no file. (The test suite pins this: it greps this
# file for logging/print/open and asserts the fake secret
# appears in no reason string the wrapper produces.)

__all__ = [
    "engine_available",
    "engine_status",
    "set_secret",
    "get_secret",
    "delete_secret",
    "list_secrets",
    "set_backend",
    "reset_backend",
    "KeyringStore",
]

_SERVICE_PREFIX = "VYUK-"


class _EngineUnavailable(Exception):
    """keyring (or a usable backend for it) is not available in
    this interpreter."""


# -- lazy keyring import (sympy pattern) ------------------------------
_KR = None  # cached keyring module, or False when unavailable


def _load_keyring():
    """Import keyring once; return the module.

    Raises _EngineUnavailable when keyring cannot be imported.
    Importability alone does NOT mean a secret can be stored —
    see _usable_backend(): a machine with no backend returns
    the fail backend, whose every operation raises.
    """
    global _KR
    if _KR is None:
        try:
            import keyring as _keyring_mod
        except Exception as e:  # noqa: BLE001 — absent/broken = unavailable
            _KR = False
            raise _EngineUnavailable(
                "keyring is not installed in this interpreter "
                f"({e}); named secrets degrade to engine_unavailable"
            ) from e
        _KR = _keyring_mod
    if _KR is False:
        raise _EngineUnavailable(
            "keyring is not installed in this interpreter; "
            "named secrets degrade to engine_unavailable"
        )
    return _KR


def _usable_backend(keyring_mod):
    """The backend keyring selected, or raise _EngineUnavailable.

    The fail backend (``keyring.backends.fail.Keyring``) is what
    keyring returns when no usable backend exists — its
    ``viable`` property is True even then, so viability is
    never consulted; only the isinstance check counts.
    """
    try:
        backend = keyring_mod.get_keyring()
    except Exception as e:  # noqa: BLE001 — no backend at all
        raise _EngineUnavailable(
            "keyring has no usable backend on this machine "
            f"({e}); named secrets degrade to engine_unavailable"
        ) from e
    try:
        from keyring.backends import fail as _fail_mod
    except Exception as e:  # noqa: BLE001 — cannot identify: do not trust
        raise _EngineUnavailable(
            "keyring's backend cannot be identified on this "
            f"machine ({e}); named secrets degrade to "
            "engine_unavailable"
        ) from e
    if isinstance(backend, _fail_mod.Keyring):
        raise _EngineUnavailable(
            "keyring has no usable backend on this machine "
            "(the fail backend was selected — no Secret Service "
            "or Windows vault is available); named secrets "
            "degrade to engine_unavailable"
        )
    return backend


# -- validation ---------------------------------------------------------
def _refuse(reason):
    return {"ok": False, "reason": reason}


def _validate_name(value, what):
    if not isinstance(value, str):
        return f"{what} must be a string"
    if not value.strip():
        return f"{what} must not be empty"
    if "\x00" in value:
        return f"{what} must not contain a NUL byte"
    return None


def _validate_secret(secret):
    if not isinstance(secret, str):
        return "secret must be a string (keyring stores text)"
    if not secret:
        return "secret must not be empty (an empty value is not a secret)"
    if "\x00" in secret:
        return "secret must not contain a NUL byte"
    return None


def _target_service(service):
    return _SERVICE_PREFIX + service


# -- real backend (keyring) ---------------------------------------------
class _RealBackend:
    """Thin adapter over real keyring. The OS store is the only
    copy: this adapter keeps no cache, no file, no log."""

    def __init__(self):
        kr = _load_keyring()
        _usable_backend(kr)  # raises _EngineUnavailable when unusable
        self._kr = kr

    def set_password(self, service, username, secret):
        self._kr.set_password(service, username, secret)

    def get_password(self, service, username):
        return self._kr.get_password(service, username)

    def delete_password(self, service, username):
        self._kr.delete_password(service, username)

    def list_usernames(self, service):
        """Usernames stored under ``service`` — Windows only.

        Enumerates the same store keyring's Windows backend
        writes to, via win32cred.CredEnumerate (the same library
        it uses) — with NO filter, then exact-match filtering
        in Python. The filter is deliberately NOT passed: a
        Filter matches target names by PREFIX, so a compound
        target (``alice@VYUK-gemini``) never matches a
        "VYUK-gemini" filter — only an unfiltered enumeration
        sees it. Each credential counts iff its TargetName is
        the plain service name OR the compound name keyring's
        Windows backend uses for a second username under one
        service (``{username}@{service}``). Only usernames are
        returned — the credential blobs are never read.
        """
        try:
            from win32ctypes.pywin32 import win32cred
        except Exception:  # noqa: BLE001 — cffi fails on Linux, not ImportError
            try:
                import win32cred
            except Exception as e:  # noqa: BLE001 — no Windows vault here
                raise _EngineUnavailable(
                    "listing secrets needs the Windows Credential "
                    f"Manager (win32cred is not available here: {e})"
                ) from e
        try:
            enumerate_fn = win32cred.CredEnumerate
        except AttributeError as e:
            raise _EngineUnavailable(
                "listing secrets needs CredEnumerate, which this "
                f"win32cred does not offer ({e})"
            ) from e
        creds = enumerate_fn()
        names = set()
        for cred in creds or []:
            target = cred.get("TargetName") or ""
            username = cred.get("UserName") or ""
            if not username:
                continue
            if target == service or target == f"{username}@{service}":
                names.add(username)
        return sorted(names)


# -- the wrapper ---------------------------------------------------------
class KeyringStore:
    """One named-secret store. ``backend=None`` builds the real
    keyring backend (lazy import; engine_unavailable when the
    library or a usable backend is missing). A test backend may
    be injected; the wrapper's validation, identity, and
    reporting rules are identical either way.

    A secret is identified by (service, username) — never by a
    bare name alone. Services are namespaced under "VYUK-" in
    the OS store (see the module docstring).
    """

    def __init__(self, backend=None):
        self._backend = backend  # None -> build the real one lazily

    # -- internals ------------------------------------------------------
    def _ensure_backend(self):
        if self._backend is None:
            self._backend = _RealBackend()
        return self._backend

    def _engine_or_none(self):
        """The backend, or None + the engine_unavailable dict
        when keyring (or a usable backend) is missing."""
        try:
            return self._ensure_backend(), None
        except _EngineUnavailable as e:
            return None, {"ok": False, "reason": "engine_unavailable",
                          "detail": str(e)}

    # -- public API ------------------------------------------------------
    def set_secret(self, service, username, secret):
        """Store (or rotate) a secret. A set that does not read
        back identical is a failure, never a success — and the
        returned dict never carries the secret."""
        err = _validate_name(service, "service")
        if err:
            return _refuse(err)
        err = _validate_name(username, "username")
        if err:
            return _refuse(err)
        err = _validate_secret(secret)
        if err:
            return _refuse(err)
        backend, failure = self._engine_or_none()
        if failure is not None:
            return failure
        target = _target_service(service)
        try:
            backend.set_password(target, username, secret)
        except Exception as e:  # noqa: BLE001 — NoKeyringError included
            if type(e).__name__ == "NoKeyringError":
                return {"ok": False, "reason": "engine_unavailable",
                        "detail": str(e)}
            return _refuse(f"could not store the secret for "
                           f"'{service}' / '{username}': {e}")
        try:
            read_back = backend.get_password(target, username)
        except Exception as e:  # noqa: BLE001 — NoKeyringError included
            if type(e).__name__ == "NoKeyringError":
                return {"ok": False, "reason": "engine_unavailable",
                        "detail": str(e)}
            return _refuse(f"could not verify the stored secret for "
                           f"'{service}' / '{username}': {e}")
        if read_back != secret:
            return _refuse(f"the secret for '{service}' / '{username}' "
                           "did not read back after storing — not stored")
        return {"ok": True, "service": service, "username": username}

    def get_secret(self, service, username):
        """Read a secret. A missing entry is an honest refusal —
        never a default, never an empty string. The secret is
        returned in the dict (that is what get is FOR); it is
        never logged and never appears in any error message."""
        err = _validate_name(service, "service")
        if err:
            return _refuse(err)
        err = _validate_name(username, "username")
        if err:
            return _refuse(err)
        backend, failure = self._engine_or_none()
        if failure is not None:
            return failure
        target = _target_service(service)
        try:
            value = backend.get_password(target, username)
        except Exception as e:  # noqa: BLE001 — NoKeyringError included
            if type(e).__name__ == "NoKeyringError":
                return {"ok": False, "reason": "engine_unavailable",
                        "detail": str(e)}
            return _refuse(f"could not read the secret for "
                           f"'{service}' / '{username}': {e}")
        if value is None:
            return _refuse(f"no secret is stored for '{service}' / "
                           f"'{username}'")
        return {"ok": True, "service": service, "username": username,
                "secret": value}

    def delete_secret(self, service, username):
        """Remove a secret AND verify the removal. A deleted
        secret never reads back; a still-present entry is a
        failure, never a success. A missing entry is an honest
        refusal, never a silent no-op."""
        err = _validate_name(service, "service")
        if err:
            return _refuse(err)
        err = _validate_name(username, "username")
        if err:
            return _refuse(err)
        backend, failure = self._engine_or_none()
        if failure is not None:
            return failure
        target = _target_service(service)
        try:
            backend.delete_password(target, username)
        except Exception as e:  # noqa: BLE001 — PasswordDeleteError included
            if type(e).__name__ == "NoKeyringError":
                return {"ok": False, "reason": "engine_unavailable",
                        "detail": str(e)}
            if type(e).__name__ == "PasswordDeleteError":
                return _refuse(f"no secret is stored for '{service}' / "
                               f"'{username}'")
            return _refuse(f"could not delete the secret for "
                           f"'{service}' / '{username}': {e}")
        try:
            still_there = backend.get_password(target, username)
        except Exception as e:  # noqa: BLE001 — NoKeyringError included
            if type(e).__name__ == "NoKeyringError":
                return {"ok": False, "reason": "engine_unavailable",
                        "detail": str(e)}
            return _refuse(f"could not verify the deletion for "
                           f"'{service}' / '{username}': {e}")
        if still_there is not None:
            return _refuse(f"the secret for '{service}' / '{username}' "
                           "is still stored after deletion — not deleted")
        return {"ok": True, "service": service, "username": username}

    def list_secrets(self, service):
        """Usernames stored under ``service`` — never the secrets
        themselves. Windows only (the OS store there enumerates);
        elsewhere an honest refusal, never a guess."""
        err = _validate_name(service, "service")
        if err:
            return _refuse(err)
        backend, failure = self._engine_or_none()
        if failure is not None:
            return failure
        target = _target_service(service)
        try:
            names = backend.list_usernames(target)
        except _EngineUnavailable as e:
            return {"ok": False, "reason": "engine_unavailable",
                    "detail": str(e)}
        except Exception as e:  # noqa: BLE001 — NoKeyringError included
            if type(e).__name__ == "NoKeyringError":
                return {"ok": False, "reason": "engine_unavailable",
                        "detail": str(e)}
            return _refuse(f"could not list the secrets for "
                           f"'{service}': {e}")
        return {"ok": True, "service": service, "usernames": names}


# -- module-level default engine (driver surface) -----------------------
_DEFAULT = None
_BACKEND_OVERRIDE = None


def _default_engine():
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = KeyringStore(backend=_BACKEND_OVERRIDE)
    return _DEFAULT


def engine_available():
    """True only when keyring imports AND a usable backend is
    selected (not the fail backend). Importability alone is
    never enough — see _usable_backend()."""
    try:
        kr = _load_keyring()
        _usable_backend(kr)
        return True
    except _EngineUnavailable:
        return False


def engine_status():
    """Honest engine report: availability, backend, reason.
    Never carries a secret."""
    try:
        kr = _load_keyring()
        backend = _usable_backend(kr)
        return {
            "ok": True,
            "available": True,
            "backend": type(backend).__module__ + "."
                       + type(backend).__name__,
            "reason": None,
        }
    except _EngineUnavailable as e:
        return {
            "ok": False,
            "available": False,
            "backend": None,
            "reason": str(e),
        }


def set_backend(backend):
    """Test seam (clipboard idiom): replace the backend the
    default engine uses. Resets the default engine so the next
    call builds a fresh one on the new backend."""
    global _DEFAULT, _BACKEND_OVERRIDE
    _BACKEND_OVERRIDE = backend
    _DEFAULT = None
    return {"ok": True}


def reset_backend():
    """Undo set_backend(): the default engine builds the real
    keyring backend again on next use."""
    global _DEFAULT, _BACKEND_OVERRIDE
    _BACKEND_OVERRIDE = None
    _DEFAULT = None
    return {"ok": True}


def set_secret(service, username, secret):
    return _default_engine().set_secret(service, username, secret)


def get_secret(service, username):
    return _default_engine().get_secret(service, username)


def delete_secret(service, username):
    return _default_engine().delete_secret(service, username)


def list_secrets(service):
    return _default_engine().list_secrets(service)
