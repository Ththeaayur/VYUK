"""LA-3.5 / LA-3.6 documents vault (v5.70, Life Admin track, plan §5.15).

ONE vault substrate for paperwork artefacts (rides LA-3.8 + LA-4.12):
vault-stored File No, DOB, and other paperwork reference values,
encrypted at rest, handed back to the USER only.

FENCES, enforced by construction and pinned by
~/workspace/code-lab/test_v570_la_vault.py:

  * PARALLEL STORE (INV-8) -- this vault lives in its OWN file
    (``docvault_v570.json`` under DATA_DIR by default, or any
    caller-supplied path). It never writes to ``assistant.db``
    and never mutates the frozen memory schema. It never touches
    the tree's ``data/`` directory on its own: the default path
    is used only when the caller passes no path.
  * ENCRYPTION AT REST IS THE ITEM, NOT AN OPTION -- ciphertext
    is produced with ``cryptography`` Fernet + PBKDF2HMAC
    (SHA-256), LAZY-imported inside ``_load_crypto``. If the
    library is absent, the vault REFUSES to store or retrieve
    (``VaultUnavailable``) -- fail-closed. There is NEVER a
    plaintext fallback and NEVER a home-rolled cipher. The key
    is derived from a caller-supplied passphrase that is never
    stored, never written to the vault file, and never logged
    (this module imports no logging module and prints nothing).
    An integrity digest (SHA-256 over the raw ciphertext bytes)
    is stored beside every entry and verified BEFORE any
    decryption; a mismatch raises ``VaultTampered``.
  * THE VAULT MUST NOT ABSORB PORTAL PASSWORDS -- ``store``
    raises ``VaultRefused`` on any password-shaped entry, by
    the explicit rule in ``_is_portal_password_entry``: an entry
    is refused when (a) it is explicitly flagged as a portal
    credential (``is_portal_password`` / ``portal_credential``
    true), or (b) its kind or label contains "password" /
    "passwd", or (c) it carries any password-shaped key (a key
    containing "password", "passwd", or "pwd", e.g. "password",
    "portal_password", "portal_pwd"), or (d) it carries a
    "portal" (or "site") field together with a password-shaped
    key. The report's own §12 rejects absorbing portal
    passwords; this rule makes that rejection code, not policy.
  * NEVER AUTO-TYPES INTO PORTALS -- this module contains NO
    typing or automation function and imports NO input-injection
    library (no pyautogui, no uia typing, no keyboard/mouse
    injection of any kind). ``retrieve`` returns the value to
    the caller (the user reads it and types it themselves in
    their own browser); nothing here sends a keystroke anywhere.
    Vault-stored File No + DOB are NEVER auto-typed into a
    portal, by the absence of any code path that could do so.
  * NO NETWORK I/O. NO TELEMETRY. This module imports no
    socket / urllib / requests / http client, no subprocess,
    and no telemetry exporter. Values never leave the device
    through this module; there is no upload, send, or sync path.

Standard library only at import time; ``cryptography`` is lazy.
No new tool declarations.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re

__all__ = [
    "VAULT_FILENAME",
    "VAULT_VERSION",
    "VaultError",
    "VaultUnavailable",
    "VaultRefused",
    "VaultTampered",
    "default_vault_path",
    "is_portal_password_entry",
    "store",
    "retrieve",
    "list_entries",
    "delete_entry",
    "refuse_portal_automation",
]

VAULT_FILENAME = "docvault_v570.json"
VAULT_VERSION = 1

# PBKDF2 work factor for the passphrase-derived key.
_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16

# Explicit password-shape rule (see module docstring). Any entry
# matching any clause is refused by store(), never stored.
_PASSWORD_WORD_RE = re.compile(r"passw(or)?d", re.IGNORECASE)
_PASSWORD_KEY_RE = re.compile(r"passw(or)?d|pwd", re.IGNORECASE)


class VaultError(Exception):
    """Base class for vault failures."""


class VaultUnavailable(VaultError):
    """The cryptography library is absent: fail-closed refusal."""


class VaultRefused(VaultError):
    """The entry or action is refused by a vault fence."""


class VaultTampered(VaultError):
    """Stored ciphertext failed its integrity digest or Fernet
    authentication: the entry is not returned."""


def default_vault_path() -> str:
    """Default vault file: DATA_DIR/docvault_v570.json, derived
    from this file's location (tree root / data), matching
    core/config.py's DATA_DIR = PROJECT_ROOT / "data". Used
    only when the caller passes no explicit path."""
    tree_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(tree_root, "data", VAULT_FILENAME)


def _load_crypto():
    """Lazy import of the encryption backend. Raises
    VaultUnavailable (fail-closed) when the library is absent,
    instead of ever falling back to plaintext."""
    try:
        from cryptography.fernet import Fernet, InvalidToken
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError as exc:
        raise VaultUnavailable(
            "documents vault refuses to operate without the "
            "'cryptography' library: install it (requirements.txt) "
            "or store nothing. No plaintext fallback exists."
        ) from exc
    return Fernet, InvalidToken, hashes, PBKDF2HMAC


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """PBKDF2HMAC-SHA256 passphrase -> Fernet key. The passphrase
    itself is never stored; only the salt lives in the file."""
    _Fernet, _InvalidToken, hashes, PBKDF2HMAC = _load_crypto()
    if not isinstance(passphrase, str) or not passphrase:
        raise VaultRefused("a non-empty passphrase is required.")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def is_portal_password_entry(entry) -> bool:
    """The explicit password-shape rule. Returns True when the
    entry must be refused as a portal credential (see module
    docstring for the four clauses). Non-dict entries are not
    password-shaped; store() rejects them separately."""
    if not isinstance(entry, dict):
        return False
    if entry.get("is_portal_password") or entry.get("portal_credential"):
        return True
    kind = str(entry.get("kind", ""))
    label = str(entry.get("label", ""))
    if _PASSWORD_WORD_RE.search(kind) or _PASSWORD_WORD_RE.search(label):
        return True
    keys = {str(k).lower() for k in entry}
    if any(_PASSWORD_KEY_RE.search(k) for k in keys):
        return True
    if ("portal" in keys or "site" in keys) and any(
        _PASSWORD_KEY_RE.search(k) for k in keys
    ):
        return True
    return False


def _load_file(vault_path: str) -> dict:
    """Read the vault file; a missing file is an empty vault.
    A malformed file is refused, never silently repaired."""
    if not os.path.exists(vault_path):
        return {"version": VAULT_VERSION, "salt": None, "entries": {}}
    try:
        with open(vault_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise VaultRefused(f"vault file is unreadable: {exc}.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        raise VaultRefused("vault file has an unexpected shape; refusing.")
    return data


def _save_file(vault_path: str, data: dict) -> None:
    directory = os.path.dirname(os.path.abspath(vault_path))
    os.makedirs(directory, exist_ok=True)
    tmp_path = vault_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    os.replace(tmp_path, vault_path)


def _get_salt(data: dict) -> bytes:
    """Salt for this vault file, generated once and stored
    (a salt is not a secret; it only defeats precomputation)."""
    raw = data.get("salt")
    if raw:
        try:
            return base64.b64decode(raw.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise VaultRefused("vault salt is corrupt; refusing.") from exc
    salt = os.urandom(_SALT_BYTES)
    data["salt"] = base64.b64encode(salt).decode("ascii")
    return salt


def store(vault_path, passphrase, entry_id, kind, label, value) -> dict:
    """Encrypt and store ONE paperwork artefact. ``value`` is a
    string (a File No, a reference number, a DOB the user chose
    to vault -- never a portal password: password-shaped entries
    raise VaultRefused and nothing is written). Returns metadata
    only ({ok, id, kind}) -- the stored value is never echoed
    back by store(). Raises VaultUnavailable when the
    cryptography library is absent: nothing is stored then."""
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise VaultRefused("entry_id must be a non-empty string.")
    if not isinstance(value, str):
        raise VaultRefused("vault values must be strings.")
    entry = {"kind": kind, "label": label, "value": value}
    if is_portal_password_entry(entry):
        raise VaultRefused(
            "refused: the vault must not absorb portal passwords "
            "(LA-2 fence). Store reference values only; keep "
            "portal passwords in your own password manager and "
            "sign in yourself.")
    path = vault_path or default_vault_path()
    Fernet, _InvalidToken, _hashes, _PBKDF2HMAC = _load_crypto()
    data = _load_file(path)
    key = _derive_key(passphrase, _get_salt(data))
    token = Fernet(key).encrypt(value.encode("utf-8"))
    data["entries"][entry_id] = {
        "kind": str(kind),
        "label": str(label),
        "ciphertext": base64.b64encode(token).decode("ascii"),
        "sha256": hashlib.sha256(token).hexdigest(),
    }
    _save_file(path, data)
    return {"ok": True, "id": entry_id, "kind": str(kind)}


def retrieve(vault_path, passphrase, entry_id) -> dict:
    """Decrypt ONE entry and return it TO THE USER ({id, kind,
    label, value}). The caller displays the value; this module
    never types it anywhere -- there is no automation path to
    type it with. Tampered ciphertext raises VaultTampered and
    returns nothing. Wrong passphrase raises VaultTampered (the
    Fernet token fails authentication) -- the module does not
    distinguish the two to the caller beyond that refusal."""
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise VaultRefused("entry_id must be a non-empty string.")
    path = vault_path or default_vault_path()
    Fernet, InvalidToken, _hashes, _PBKDF2HMAC = _load_crypto()
    data = _load_file(path)
    record = data["entries"].get(entry_id)
    if record is None:
        raise VaultRefused(f"no vault entry {entry_id!r}.")
    try:
        token = base64.b64decode(record["ciphertext"].encode("ascii"))
    except (ValueError, KeyError, TypeError) as exc:
        raise VaultTampered("stored entry is corrupt; refusing.") from exc
    if hashlib.sha256(token).hexdigest() != record.get("sha256"):
        raise VaultTampered(
            "integrity digest mismatch: stored ciphertext was "
            "altered; the entry is not returned.")
    try:
        plain = Fernet(_derive_key(passphrase, _get_salt(data))).decrypt(token)
    except InvalidToken as exc:
        raise VaultTampered(
            "authentication failed: wrong passphrase or altered "
            "ciphertext; the entry is not returned.") from exc
    return {"id": entry_id, "kind": record.get("kind", ""),
            "label": record.get("label", ""),
            "value": plain.decode("utf-8")}


def list_entries(vault_path) -> list[dict]:
    """Metadata ONLY: [{id, kind, label}] -- values are never
    listed, so a listing cannot leak a vaulted value. Listing
    needs no passphrase (it reveals nothing secret)."""
    path = vault_path or default_vault_path()
    data = _load_file(path)
    return [{"id": entry_id, "kind": rec.get("kind", ""),
             "label": rec.get("label", "")}
            for entry_id, rec in sorted(data["entries"].items())]


def delete_entry(vault_path, entry_id) -> dict:
    """Remove ONE entry. Returns {ok, deleted} metadata only."""
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise VaultRefused("entry_id must be a non-empty string.")
    path = vault_path or default_vault_path()
    data = _load_file(path)
    if entry_id not in data["entries"]:
        return {"ok": False, "deleted": False,
                "detail": f"no vault entry {entry_id!r}."}
    del data["entries"][entry_id]
    _save_file(path, data)
    return {"ok": True, "deleted": True, "id": entry_id}


def refuse_portal_automation(portal: str) -> dict:
    """Portal automation is REFUSED by design, for ANY portal
    name. This function takes NO credentials and there is no
    login, typing, or auto-fill function in this module: the
    assistant never types a vaulted File No, DOB, or anything
    else into a portal. Sign in yourself in your own browser;
    the vault hands values back to you, nothing more."""
    name = str(portal or "").strip() or "portal"
    return {
        "refused": True,
        "reason": (f"auto-typing into {name} is refused by design: "
                   "this module has no typing or automation path, "
                   "and vault-stored File No / DOB are never "
                   "auto-typed into portals. The vault returns "
                   "values to you; you enter them yourself in "
                   "your own browser."),
    }
