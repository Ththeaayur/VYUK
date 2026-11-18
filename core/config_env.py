"""The .env file's persistence half of VYUK's configuration
(GAPS-21, architecture-review flaw C-V6).

Before this module, core/config.py was schema + loader +
persistence + migration + singleton in one file: the .env loader,
the atomic writer, the secret-envelope protection, the stored-value
readers and the secret-migration flush all lived next to AppConfig
and the singleton. The schema already left for core/config_spec.py
(GAPS-10); this module takes the persistence half. What stays in
core/config.py is AppConfig, the singleton, the save entry points
(save_env_setting / save_api_key) and the migration runner — the
composition seam, not the file machinery.

PROJECT_ROOT is computed here with the same expression as
core/config.py (both files live in core/, so the value is
identical); the .env file is PROJECT_ROOT / ".env".

Import-time behaviour is unchanged: importing this module
captures the pre-.env process environment and loads .env into
os.environ, exactly as core/config.py did before the split. The
load is a merge, never an overwrite — a real process-environment
value wins over the file.

C-V8 diagnostics: load_env_file() records every key in the file
that VYUK does not know (see KNOWN_ENV_KEYS) into
_ENV_FILE_DIAGNOSTICS, readable through env_file_diagnostics().
A diagnostic is not a gate — an unknown key still loads exactly
as before; the record exists so a typo'd key is visible instead
of silently doing nothing. Only the key is recorded, never the
value.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.config_spec import (
    CONFIG_VERSION_KEY,
    ENV_ALIAS,
    FIELD_FOR_ENV_KEY,
)

# The same value as core/config.py's PROJECT_ROOT — both files
# live in core/, so parent.parent is the tree root either way.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The import-time value of PROJECT_ROOT, for _project_root()
# below.
_DEFAULT_ROOT = PROJECT_ROOT


def _project_root() -> Path:
    """The tree root for .env purposes (GAPS-21 fix, neighbour
    run 1). The .env location has ONE documented seam:
    core.config.PROJECT_ROOT — two pre-existing tests patch it
    (test_uis6's engine-persistence test, test_config_spec's
    migration-runner test) and DATA_DIR/DB_PATH derive from it.
    When this module was split out of core/config.py, the file
    functions read this module's own PROJECT_ROOT instead, so a
    patched config root no longer steered the write and the save
    landed in the real tree (neighbour run 1: a stray .env with
    BRAIN_ENGINE=local). So the root resolves from the loaded
    config module first (core.config, or
    voice_assistant.core.config in the app process — the lookup
    is lazy via sys.modules, so there is no import cycle), and
    only falls back to this module's own PROJECT_ROOT when no
    config module is loaded yet (the import-time load, when the
    config module is still mid-import and has no PROJECT_ROOT
    attribute). In production both agree, so the behaviour is
    unchanged; in tests, patching core.config.PROJECT_ROOT
    steers every file function, as before the split."""
    import sys

    for _name in ("core.config", "voice_assistant.core.config"):
        _mod = sys.modules.get(_name)
        if _mod is not None:
            _root = getattr(_mod, "PROJECT_ROOT", None)
            if _root is not None:
                return Path(_root)
    return PROJECT_ROOT

# The exact set of .env keys whose values are SECRETS (v5.47,
# programme item 14): these — and only these — are stored in .env
# as core/secrets_store envelopes instead of plaintext (see
# load_env_file's migration and _write_env's protection below).
# The set is deliberately explicit, not pattern-matched: a new
# secret key joins by being named here, and a key that merely
# SOUNDS secret but is an identifier (GOOGLE_CLIENT_ID — a public
# client identifier, not a credential) stays plaintext on purpose.
SECRET_ENV_KEYS = frozenset({
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "GOOGLE_CLIENT_SECRET",
})

# Secret keys still found in PLAINTEXT in .env at load time,
# queued by load_env_file() for migration (rewritten protected).
# The rewrite itself goes through _write_env(), which is defined
# further down this module, so the flush happens either at the end
# of a post-import load_env_file() call or from the hook at the
# bottom of core/config.py once the singleton exists.
_PENDING_SECRET_MIGRATION: dict[str, str] = {}

# Unknown .env keys recorded by the last load_env_file() call
# (GAPS-21, C-V8): [{kind, env_key, reason}]. Only the key is
# ever recorded, never the value.
_ENV_FILE_DIAGNOSTICS: list[dict[str, str]] = []

# Every key VYUK knows in a .env file: the spec's env keys, the
# legacy aliases, the version stamp, and the keys that are
# documented but not spec-owned — VYUK_SAFE_MODE (read by
# core/safemode.py, not a setting), NOVA_START_MINIMIZED (set by
# main.py from --minimized), and CHANNELS / CHUNK_SIZE (documented
# in .env.example; the voice path holds them as plain constants,
# so they are known-but-unread, not unknown).
KNOWN_ENV_KEYS = (
    frozenset(FIELD_FOR_ENV_KEY)
    | frozenset(ENV_ALIAS)
    | {CONFIG_VERSION_KEY, "VYUK_SAFE_MODE", "NOVA_START_MINIMIZED",
       "CHANNELS", "CHUNK_SIZE"}
)


def env_file_diagnostics() -> list[dict[str, str]]:
    """The unknown-key diagnostics recorded by the last
    load_env_file() call — [{kind, env_key, reason}]. Empty means
    every key in the file is one VYUK knows."""
    return [dict(d) for d in _ENV_FILE_DIAGNOSTICS]


def _secrets_module():
    """core.secrets_store, imported lazily — or None when it
    cannot be imported (this module is occasionally executed
    standalone, outside the package, by updater tests; .env
    handling must degrade to the pre-v5.47 plaintext behaviour
    there rather than fail to import at all)."""
    try:
        from . import secrets_store

        return secrets_store
    except Exception:  # noqa: BLE001 — standalone / broken package
        try:
            import importlib

            return importlib.import_module(
                "voice_assistant.core.secrets_store")
        except Exception:  # noqa: BLE001
            return None


def _protect_env_value(key: str, value: str) -> str:
    """The .env-file representation of a value being written for
    `key` (v5.47, programme item 14): secret keys are stored as a
    secrets_store envelope; everything else — and every value
    when no secrets backend can even be imported — is stored
    exactly as given. An already-enveloped value passes through
    untouched (re-saving a loaded value must not double-wrap)."""
    if key not in SECRET_ENV_KEYS or not value:
        return value
    store = _secrets_module()
    if store is None or store.looks_protected(value):
        return value
    return store.protect_text(value)


def _unprotect_env_value(key: str, value: str) -> str | None:
    """The usable value for a secret key read from .env (v5.47,
    programme item 14): a plaintext value passes through; an
    enveloped value is unprotected; an enveloped value that can
    no longer be unprotected (machine key lost/changed) returns
    None — the caller then treats the key as unset rather than
    handing the ciphertext to an API as if it were the key."""
    if key not in SECRET_ENV_KEYS:
        return value
    store = _secrets_module()
    if store is None or not store.looks_protected(value):
        return value
    try:
        return store.unprotect_text(value)
    except Exception:  # noqa: BLE001 — undecryptable = unset
        return None


# Simple .env loader to avoid strict dependency issues if python-dotenv is not installed
def load_env_file():
    env_path = _project_root() / ".env"
    _ENV_FILE_DIAGNOSTICS.clear()
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if not key:
                    continue
                # GAPS-21 (C-V8): an unknown key is recorded,
                # never silently accepted and never refused — the
                # record names the key only, never the value.
                if key not in KNOWN_ENV_KEYS:
                    _ENV_FILE_DIAGNOSTICS.append({
                        "kind": "unknown-key", "env_key": key,
                        "reason": f"{key!r} is not a VYUK setting"})
                # v5.47 (programme item 14): a secret key's stored
                # value is an envelope — the app must see the
                # PLAINTEXT in os.environ, never the envelope. A
                # secret still stored in plaintext is queued for
                # migration (the file is rewritten protected by
                # _flush_secret_migration) while its value loads
                # exactly as before.
                if key in SECRET_ENV_KEYS and val:
                    store = _secrets_module()
                    if store is not None and store.looks_protected(val):
                        unprotected = _unprotect_env_value(key, val)
                        if unprotected is None:
                            continue  # undecryptable: treat as unset
                        val = unprotected
                    elif store is not None:
                        _PENDING_SECRET_MIGRATION[key] = val
                if key not in os.environ:
                    os.environ[key] = val
    # When this module is fully loaded (any post-import call),
    # _flush_secret_migration exists and the migration lands now;
    # during the initial import it is deferred to the hook at the
    # bottom of core/config.py once _write_env exists there.
    flush = globals().get("_flush_secret_migration")
    if callable(flush):
        flush()


# GAPS-10: the keys that were in the real process environment
# BEFORE the .env file is read. A real env override wins over the
# file and is never migrated; the versioned migration runner
# (core/config.py) needs this set to tell the two apart. Captured
# here, before load_env_file() merges the file into os.environ.
_PROCESS_ENV_KEYS = frozenset(os.environ)

load_env_file()


def _validate_env_pair(key: str, value: str) -> None:
    """v5.47 (programme item 16): .env is a single-line format —
    a CR or LF in a key or value would let one saved setting
    inject whole new settings lines into the file (a pasted key
    carrying "\\nINJECTED=1" is the shape of the attack, an
    accidental multi-line paste is the shape of the accident).
    Reject loudly instead of silently stripping: a mangled key
    that quietly stops working is worse than an error the user
    can see and fix."""
    for label, text in (("key", key), ("value", value)):
        if "\n" in text or "\r" in text:
            raise ValueError(
                f"{label} {key!r} contains a newline; "
                ".env values must be single-line")


def _write_env(updates: dict[str, str]):
    """Applies `updates` to .env ATOMICALLY (v5.22, brain audit 2 #6).

    The old pattern truncated .env and wrote it back in place: a
    crash, kill or power cut mid-write — a laptop lid closed on the
    spot is the realistic one, and connect-time code saves settings
    too — could leave a torn or empty .env and lose EVERY saved key
    and preference with it. Now the new content is built in memory
    (existing lines keep their place and their exact text; a line
    whose key is being updated is replaced by the new value, and
    later duplicates of an updated key collapse away; keys not yet
    present append at the end), written to .env.tmp in the SAME
    directory, flushed + fsynced best-effort, then os.replace()d
    over .env. A same-directory rename is atomic, so any reader sees
    the whole old file or the whole new one — never half of either.

    v5.47: two contract additions. (Programme item 16) every key
    and value is validated single-line FIRST — a newline raises
    ValueError before the file is touched. (Programme item 14)
    values for SECRET_ENV_KEYS are stored as secrets_store
    envelopes; the `updates` the caller passes (and os.environ)
    always carry the plaintext, only the file representation is
    protected. Validation runs on the caller's plaintext, before
    protection, so the envelope can never smuggle a newline past
    the check (base64 contains none anyway).
    """
    for _k, _v in updates.items():
        _validate_env_pair(_k, _v)
    updates = {k: _protect_env_value(k, v) for k, v in updates.items()}
    env_path = _project_root() / ".env"
    lines: list[str] = []
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    new_lines: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        matched = None
        for key in updates:
            if stripped.startswith(f"{key}="):
                matched = key
                break
        if matched is None:
            new_lines.append(line)
        elif matched not in seen:
            new_lines.append(f"{matched}={updates[matched]}\n")
            seen.add(matched)
        # else: a duplicate of an updated key — it collapses away.
    for key, value in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={value}\n")

    tmp_path = env_path.with_name(env_path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass  # fsync is a durability nicety, never a failure mode
    os.replace(tmp_path, env_path)


def _flush_secret_migration() -> None:
    """Rewrites any .env secret keys load_env_file() found still
    in plaintext, protected (v5.47, programme item 14). Goes
    through _write_env, so the rewrite is atomic and every other
    line keeps its exact text. Never raises: a failed migration
    leaves the file exactly as it was (plaintext, working) and
    the migration is simply retried on the next load."""
    if not _PENDING_SECRET_MIGRATION:
        return
    pending = dict(_PENDING_SECRET_MIGRATION)
    _PENDING_SECRET_MIGRATION.clear()
    try:
        _write_env(pending)
    except Exception:  # noqa: BLE001 — migration is best-effort
        _PENDING_SECRET_MIGRATION.update(pending)


def _stored_env_file_value(key: str) -> str | None:
    """The value stored for `key` in the .env file itself — None
    when the file or the key is absent. (os.environ cannot answer
    this: load_env_file merges file values into it, mixing stored
    values with real process-environment overrides.)"""
    env_path = _project_root() / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, val = line.split("=", 1)
        if k.strip() == key:
            return val.strip().strip('"').strip("'")
    return None


def _read_env_file_dict() -> dict[str, str]:
    """The .env file as a plain dict (same parsing as
    _stored_env_file_value, all keys). Empty when the file is
    absent."""
    env_path = _project_root() / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, val = line.split("=", 1)
        k = k.strip()
        if k:
            out[k] = val.strip().strip('"').strip("'")
    return out
