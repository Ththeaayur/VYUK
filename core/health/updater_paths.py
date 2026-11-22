"""Paths, constants, log + hash helpers — split from updater.py (B24)."""
from __future__ import annotations

import hashlib
import os
import re
from .. import config_spec
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths. The package root is derived from this file (core/ sits directly
# under it), exactly how config.py builds DATA_DIR. NOVA_APP_ROOT is a
# test/diagnostic seam (the NOVA_ASSETS_DIR precedent in local_assets):
# when set, it replaces the derived root.
# ---------------------------------------------------------------------------

_STAGING_DIRNAME = "update_staging"
_MARKER_NAME = "staged.json"
_APPLIED_NAME = "update_applied.json"
_BACKUP_DIRNAME = "update_backup"
_CHECK_TIMEOUT_S = 8
_DOWNLOAD_TIMEOUT_S = 30
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Gate B (master audit P0-3/P1-4/P0-4) — updater correctness.
_JOURNAL_NAME = "update_apply.json"
_VERSION_REL = "core/version.py"
_MANIFEST_NAME = "install_manifest.json"
_MANIFEST_SIG_NAME = "install_manifest.json.sig"
# Credential-shaped basenames that must never arrive via an update
# payload and never be overwritten or deleted by apply/rollback.
# ".env.example" is the SHIPPED TEMPLATE and is explicitly NOT
# protected — every other ".env.*" is treated as a credential file
# (a broad ".env.*" rejection would refuse our own release zips,
# which carry .env.example at the tree root).
_PROTECTED_BASENAMES = {".env", "google_token.json", ".vyuk_secret_key"}


def _package_root() -> Path:
    override = config_spec.env("NOVA_APP_ROOT").strip()
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parent.parent.parent  # Phase 3 H1: module moved one level deeper (core/health/); package root stays at tree root


def _data_dir() -> Path:
    return _package_root() / "data"


def _staging_dir() -> Path:
    return _data_dir() / _STAGING_DIRNAME


def _marker_path() -> Path:
    return _staging_dir() / _MARKER_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(message: str) -> None:
    """One timestamped line in data/activity.log — the codebase's
    convention (see core/confirm_gate.py). Never raises."""
    try:
        log_path = _data_dir() / "activity.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat(timespec="seconds")
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{timestamp} | updater | {message}\n")
    except Exception:  # noqa: BLE001 — logging must never break an update
        pass


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _sha256_of(path: Path) -> str:
    """Streaming SHA-256 hex digest — 1 MB chunks, constant memory
    (same pattern as local_assets.sha256_of)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _read_version_file(path: Path) -> str | None:
    """The VERSION string from a version.py file, or None when
    the file is missing or unparseable. Rollback reads the
    BACKUP's version file (loophole batch 6, #9) — the directory
    name is never trusted as the version; a backup whose version
    file is missing or garbled is not a rollback target."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"""^VERSION\s*=\s*["']([^"']+)["']""",
                      text, re.M)
    if match is None:
        return None
    return match.group(1)
