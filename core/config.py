"""
Configuration manager for Voice AI Assistant.
Loads environment variables and user settings.

Structure (GAPS-21, flaw C-V6): this module is the composition
seam — AppConfig, the singleton, the save entry points and the
migration runner. The schema lives in core/config_spec.py
(GAPS-10) and the .env persistence half — load_env_file,
_write_env, the secret-envelope protection, the stored-value
readers, the secret-migration flush — lives in
core/config_env.py. The moved names are re-exported here, so
every existing `from core.config import ...` keeps working.

Import-time side effects are the deliberate contract, not an
accident: importing this module loads .env (via config_env),
runs the versioned migration, and builds the singleton, so
`from core.config import config` works anywhere with the
migrated values. 100+ import sites rely on that; removing the
load would not remove the need, only move it to every caller.
"""

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from core.config_env import (  # noqa: F401 — re-exported for compat
    _PENDING_SECRET_MIGRATION,
    _PROCESS_ENV_KEYS,
    _flush_secret_migration,
    _protect_env_value,
    _read_env_file_dict,
    _stored_env_file_value,
    _unprotect_env_value,
    _validate_env_pair,
    _write_env,
    env_file_diagnostics,
    load_env_file,
)
from core.config_spec import (
    CONFIG_VERSION,
    CONFIG_VERSION_KEY,
    ConfigSpec,
    _env_bool_value,
    _env_float_value,
    _env_int_value,
    build_spec,
    env,
    ENV_ALIAS,
    FIELD_FOR_ENV_KEY,
    migrate_stored,
)

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "assistant.db"


def _env_float(key: str, default: float) -> float:
    """Reads a float setting from the environment, falling back to
    `default` when the variable is unset or unparseable — a typo in
    .env must never take the assistant down at import time.
    (GAPS-10: the coercion lives in core/config_spec.py now; this
    is a thin wrapper keeping the (key, default) signature.)"""
    return _env_float_value(os.getenv(key, ""), default)


def _env_int(key: str, default: int) -> int:
    """_env_float's integer sibling — same defensive contract.
    (GAPS-10: thin wrapper over core/config_spec.)"""
    return _env_int_value(os.getenv(key, ""), default)


def _env_bool(key: str, default: bool) -> bool:
    """Reads a boolean setting from the environment: 1/true/yes/on
    (any case) is True, 0/false/no/off is False, and anything else —
    unset or a typo — falls back to `default`, so a garbled .env
    line can never flip a feature by accident.
    (GAPS-10: thin wrapper over core/config_spec.)"""
    return _env_bool_value(os.getenv(key, ""), default)


# NOTE (GAPS-10): _provenance_lock_default lived here; its logic is
# _provenance_lock_value in core/config_spec.py now (the
# SESSION_PROVENANCE_LOCK / SPEAKER_LOCK alias resolution, applied
# by build_spec).


class AppConfig(ConfigSpec):
    """The app's configuration, backed by the versioned spec
    (core/config_spec.py, GAPS-10). The fields and their defaults
    live in ConfigSpec; this class is the compatibility layer: it
    keeps the AppConfig name, the env-reading construction
    (``AppConfig()`` reads the environment, as the old
    ``default_factory`` fields did), and the behaviour methods
    (speaker_lock alias, save_api_key, selected_provider*). The
    browser attach / debug fields (GAPS-3, Chrome 136 — from
    Chrome 136 the debug port is ignored on the default profile)
    are among those that live in ConfigSpec now."""

    def __init__(self, **overrides: Any) -> None:
        built = build_spec()
        for f in fields(built):
            setattr(self, f.name, getattr(built, f.name))
        known = {f.name for f in fields(built)}
        for key, value in overrides.items():
            if key not in known:
                raise TypeError(f"AppConfig has no field {key!r}")
            setattr(self, key, value)

    # v5.48 (programme item 24): deprecated read/write alias for
    # the field above. gui/main_window.py's Settings toggle still
    # reads and assigns config.speaker_lock this wave (its move to
    # the new name is the GUI builder's item); the alias keeps that
    # working against the renamed field. Remove once nothing reads
    # the old name.
    @property
    def speaker_lock(self) -> bool:
        """Deprecated alias of session_provenance_lock (v5.48,
        programme item 24)."""
        return self.session_provenance_lock

    @speaker_lock.setter
    def speaker_lock(self, value: bool) -> None:
        self.session_provenance_lock = bool(value)

    def save_api_key(self, key: str):
        self.api_key = key
        # v5.47 (programme item 16): the write goes FIRST — a value
        # _write_env rejects (a newline) must never land in
        # os.environ or on this object on its way to failing.
        _write_env({"GEMINI_API_KEY": key})
        os.environ["GEMINI_API_KEY"] = key

    # -- Phase 0 (fourth audit C01): the credential belongs to the
    # SELECTED provider, not to a fixed field. The GUI connect
    # gate used to check config.api_key (Gemini) for every cloud
    # brain, so a valid Groq-only setup was refused before the
    # provider ever started. These methods are the single seam:
    # the gate, and any future preflight, must resolve through
    # them instead of naming a key field directly. The resolution
    # mirrors brain.py's dispatch (engine local → local; else
    # cloud_provider groq → groq, anything else → gemini) and
    # window_brain._brain_engine's normalization (anything not
    # 'local' is a cloud engine).
    def selected_provider(self) -> str:
        """'local' | 'groq' | 'gemini' — the provider a connect
        would actually use for this config."""
        engine = str(getattr(self, "brain_engine", "gemini")
                     or "gemini").strip().lower()
        if engine == "local":
            return "local"
        provider = str(getattr(self, "cloud_provider", "gemini")
                       or "gemini").strip().lower()
        return "groq" if provider == "groq" else "gemini"

    def selected_provider_key_field(self) -> str | None:
        """The config field holding the selected provider's key,
        or None when no key is required (local)."""
        provider = self.selected_provider()
        if provider == "groq":
            return "groq_api_key"
        if provider == "gemini":
            return "api_key"
        return None

    def selected_provider_key_present(self) -> bool:
        """True when the selected provider can start: local always
        (no key), a cloud provider only when its own key field is
        non-blank. A whitespace-only key counts as absent."""
        field = self.selected_provider_key_field()
        if field is None:
            return True
        return bool(str(getattr(self, field, "") or "").strip())

    def missing_provider_key_message(self) -> str:
        """Plain-words missing-key notice naming the provider and
        its key. This is a KEY-ABSENCE refusal only — it must
        never be confused with other connect refusals (a worker
        already alive, a frozen brain falling back to local)."""
        provider = self.selected_provider()
        if provider == "groq":
            return ("Groq key missing — add your Groq key in Settings "
                    "to use the Groq cloud brain. Or switch her brain "
                    "to Local — no key, no account.")
        if provider == "gemini":
            return ("Gemini key missing — add your free Gemini key in "
                    "Settings to use the Gemini cloud brain. Or switch "
                    "her brain to Local — no key, no account.")
        return ""


def _sync_singleton(key: str) -> None:
    """GAPS-21 (C-V1): after a successful save, the running
    session's config singleton takes the saved value — through
    the spec's own live read, so the coercion (and the
    provenance-lock alias rule) is the spec's, not a second copy.
    Before this the sync was every caller's own hand-set line, and
    any caller that forgot (w10_wiring's settings import) left
    the singleton stale until restart. During the import-time
    migration the singleton does not exist yet — the sync is a
    no-op then, and the singleton is built from the migrated file
    afterwards. A failed sync never fails a save."""
    singleton = globals().get("config")
    if singleton is None:
        return
    canonical = ENV_ALIAS.get(key, key)
    field_name = FIELD_FOR_ENV_KEY.get(canonical)
    if field_name is None:
        return
    try:
        setattr(singleton, field_name, env(canonical))
    except Exception:  # noqa: BLE001 — a failed sync never fails a save
        pass


def save_env_setting(key: str, value: str):
    """Persists one KEY=value pair to .env and exports it into os.environ.

    Generalises AppConfig.save_api_key(), so any setting
    (BRAIN_ENGINE, OLLAMA_MODEL, …) can be saved durably the same
    way: update the existing line in place when the key is already
    present in .env, append it otherwise. The write itself goes
    through _write_env() and is atomic (v5.22).

    v5.47 (programme item 16): the write goes FIRST and the
    os.environ export second — _write_env validates single-line
    and its ValueError propagates to the caller unchanged (this
    is a programmatic API; callers that surface strings to the
    user, like the Settings GUI's _persist_config_setting, already
    guard their calls), and a rejected value must never be left
    sitting in os.environ as if it had been saved.

    GAPS-21 (C-V1): the write is one operation now — after the
    file and os.environ agree, the singleton takes the saved value
    too (_sync_singleton). Callers no longer need their own
    hand-set line (the existing ones are harmless duplication).
    """
    _write_env({key: value})
    os.environ[key] = value
    _sync_singleton(key)


def _run_config_migrations() -> None:
    """The versioned migration runner (GAPS-10, flaw #2). Reads the
    stored .env dict, detects its version (VYUK_CONFIG_VERSION,
    default 0 = pre-spec), applies the registered steps in order,
    and writes back only what changed, plus the version stamp —
    through save_env_setting, so the file and os.environ stay in
    step. A fresh install (no .env file) has nothing to migrate
    and nothing is written. Never raises: a failed migration
    leaves the file exactly as it was and is retried on the next
    load (the same best-effort contract as _flush_secret_migration).
    """
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    stored = _read_env_file_dict()
    try:
        from_version = int(str(stored.get(CONFIG_VERSION_KEY, "0")).strip())
    except (TypeError, ValueError):
        from_version = 0
    if from_version >= CONFIG_VERSION:
        return
    try:
        migrated, new_version = migrate_stored(
            stored, from_version, set(_PROCESS_ENV_KEYS))
    except Exception:  # noqa: BLE001 — migration is best-effort
        return
    changed = {k: v for k, v in migrated.items() if stored.get(k) != v}
    changed[CONFIG_VERSION_KEY] = str(new_version)
    try:
        for key, value in changed.items():
            save_env_setting(key, value)
    except Exception:  # noqa: BLE001 — migration is best-effort
        pass


# GAPS-10: the versioned migration must land before the singleton
# below is constructed, so it reads the migrated value.
_run_config_migrations()

config = AppConfig()

# v5.47 (programme item 14): complete any secret migration that
# the import-time load_env_file() queued. The flush lives in
# core/config_env; this is the earliest it can run after the
# singleton exists (load_env_file deferred it at import time).
_flush_secret_migration()
