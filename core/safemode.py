"""Wave 10 (v5.65) Leaf 3 — PF-11 safe mode + repair (STAGED, unwired).

Safe mode is a degraded-but-alive configuration the coordinator
reads at startup (later wiring): no watchers, no telemetry-adjacent
extras, the default (local, builtin) brain, and a minimal UI. This
module exposes the flag and the minimal configuration; it does not
itself restart anything, and no repair function here restarts
either — every repair returns a structured result.

Safe-mode flag sources (either one enables safe mode):
- environment: ``VYUK_SAFE_MODE`` set to ``1``/``true``/``yes``
  (case-insensitive); any other value, including an empty one,
  does not enable it.
- a data/ flag file: ``safe_mode.flag`` inside the data directory
  (presence enables it; its contents are ignored, so a truncated
  write cannot silently disable it).

Minimal configuration (``minimal_config()``) — the contract the
coordinator applies:
- ``brain_engine``: ``"local"`` — the default brain (config.py:
  since v2.7 the default is "local"; the cloud brain is opt-in).
- ``local_backend``: ``"builtin"`` — the zero-setup default backend.
- ``watchers_enabled``: False and ``watchers``: [] — no watchers.
- ``telemetry_enabled``: False and ``telemetry_adjacent_extras``:
  [] — no telemetry-adjacent extras.
- ``ui``: ``{"mode": "minimal", "animations": False,
  "extras": []}`` — minimal UI.
- ``default_model_tier``: ``"balanced"`` — the RAM-neutral tier
  (local_assets: Balanced is the fallback tier when RAM is
  unknown); safe mode never forces a download.

Repair actions (pure functions over explicit paths):
- ``reset_settings(settings_path, defaults)`` — writes ``defaults``
  as JSON, keeping a backup copy of the previous file (when one
  existed) next to it. Returns a structured result; never deletes
  the old file outright. (The live app persists settings through
  .env + QSettings; the coordinator bridges this file-based reset
  to those stores — this module deliberately does not touch
  .env or QSettings, so it works with system python, no Qt.)
- ``verify_install(tree_root, manifest_path)`` — hash-checks tree
  files against ``install_manifest.json`` (the shipped format:
  {"files": {POSIX relative path -> SHA-256 hex}, "format": 1},
  see core/health/integrity.py). Reports ``ok``/``mismatched``/``missing``
  /``unreadable``/``extra``. It does NOT verify the manifest
  signature — that is core/health/integrity.py's job; a manifest whose
  authenticity is in doubt must be checked there first. Paths
  escaping the tree are never followed.
- ``restore_data(bundle_path, target_data_dir, settings_store=None)``
  — restores a PF-27 backup bundle via core/backup.py's
  format (defined once there, imported here, never duplicated).

Stdlib only. Product VERSION is untouched (still 5.64.1); this
module carries no product-version constant of its own.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone

from .backup import restore_bundle as _restore_bundle

SAFE_MODE_ENV = "VYUK_SAFE_MODE"
SAFE_MODE_FLAG_FILENAME = "safe_mode.flag"
DEFAULT_MODEL_TIER = "balanced"


def is_safe_mode(data_dir: str | None = None,
                 env: dict | None = None) -> bool:
    """True when safe mode is requested by env or by flag file.

    ``env`` defaults to ``os.environ``; pass an explicit mapping in
    tests. A flag file enables safe mode by mere presence.
    """
    mapping = os.environ if env is None else env
    value = str(mapping.get(SAFE_MODE_ENV, "") or "").strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if data_dir is not None:
        flag = os.path.join(data_dir, SAFE_MODE_FLAG_FILENAME)
        if os.path.isfile(flag):
            return True
    return False


def safe_mode_sources(data_dir: str | None = None,
                      env: dict | None = None) -> dict:
    """Which flag sources fired: {"env": bool, "flag_file": bool}."""
    mapping = os.environ if env is None else env
    value = str(mapping.get(SAFE_MODE_ENV, "") or "").strip().lower()
    flag = False
    if data_dir is not None:
        flag = os.path.isfile(
            os.path.join(data_dir, SAFE_MODE_FLAG_FILENAME))
    return {"env": value in ("1", "true", "yes"), "flag_file": flag}


def minimal_config() -> dict:
    """The safe-mode configuration dict (fresh copy per call)."""
    return {
        "brain_engine": "local",
        "local_backend": "builtin",
        "default_model_tier": DEFAULT_MODEL_TIER,
        "watchers_enabled": False,
        "watchers": [],
        "telemetry_enabled": False,
        "telemetry_adjacent_extras": [],
        "ui": {"mode": "minimal", "animations": False, "extras": []},
    }


def reset_settings(settings_path: str, defaults: dict) -> dict:
    """Restore default settings, keeping a backup of the old file.

    Returns {"ok", "settings_path", "backup_path", "keys_restored",
    "previous_existed", "error"}. ``backup_path`` is None when no
    previous file existed. The old file is copied, never deleted
    outright; a failure to write the new file leaves the old file
    (and its backup, if made) in place and reports ``ok: False``.
    ``defaults`` must be JSON-serialisable — a ValueError is
    raised rather than writing a lossy file.
    """
    result: dict = {"ok": False, "settings_path": settings_path,
                    "backup_path": None, "keys_restored": 0,
                    "previous_existed": False, "error": None}
    try:
        blob = json.dumps(defaults, indent=2, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"defaults are not JSON-serialisable: {exc}") from exc
    parent = os.path.dirname(os.path.abspath(settings_path))
    if os.path.isfile(settings_path):
        result["previous_existed"] = True
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = f"{settings_path}.pre-reset-{stamp}"
        try:
            os.makedirs(parent, exist_ok=True)
            shutil.copyfile(settings_path, backup_path)
            result["backup_path"] = backup_path
        except OSError as exc:
            result["error"] = f"backup-failed: {exc}"
            return result
    try:
        os.makedirs(parent, exist_ok=True)
        with open(settings_path, "w", encoding="utf-8") as fh:
            fh.write(blob + "\n")
    except OSError as exc:
        result["error"] = f"write-failed: {exc}"
        return result
    result["ok"] = True
    result["keys_restored"] = len(defaults)
    return result


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside_tree(tree_root: str, rel: str) -> str | None:
    """Absolute path of ``rel`` inside ``tree_root``, or None."""
    root = os.path.abspath(tree_root)
    candidate = os.path.abspath(os.path.join(root, rel))
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    return candidate


def verify_install(tree_root: str, manifest_path: str) -> dict:
    """Hash-check tree files against install_manifest.json.

    Returns {"ok", "checked", "mismatched", "missing",
    "unreadable", "extra", "error"}: ``ok`` is True only when
    there are no mismatches, no missing files and no unreadable
    files. ``extra`` lists tree *.py files absent from the
    manifest — informational only (new leaf files land here
    until the next packaging run regenerates the manifest);
    extras do NOT fail the check. A manifest that cannot be
    read or parsed reports ``error`` and ``ok: False``.
    """
    result: dict = {"ok": False, "checked": 0, "mismatched": [],
                    "missing": [], "unreadable": [], "extra": [],
                    "error": None}
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        files = manifest["files"]
        if not isinstance(files, dict):
            raise ValueError("manifest 'files' is not a mapping")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["error"] = f"manifest-unreadable: {exc}"
        return result
    expected: set[str] = set()
    for rel in sorted(files):
        expected.add(rel)
        path = _inside_tree(tree_root, rel)
        if path is None:
            result["unreadable"].append(rel)
            continue
        if not os.path.isfile(path):
            result["missing"].append(rel)
            continue
        try:
            actual = _sha256_file(path)
        except OSError as exc:
            result["unreadable"].append(f"{rel}: {exc}")
            continue
        result["checked"] += 1
        if actual != files[rel]:
            result["mismatched"].append(rel)
    # Extra *.py files in the tree (same sweep the manifest covers).
    for root, _dirs, names in os.walk(tree_root):
        for name in names:
            if not name.endswith(".py"):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, tree_root).replace(os.sep, "/")
            if rel not in expected:
                result["extra"].append(rel)
    result["extra"].sort()
    result["ok"] = (not result["mismatched"] and not result["missing"]
                    and not result["unreadable"]
                    and result["error"] is None)
    return result


def restore_data(bundle_path: str, target_data_dir: str,
                 settings_store=None, passphrase: str | None = None
                 ) -> dict:
    """Restore a PF-27 backup bundle (see core/backup.py).

    The bundle format is defined once in backup and imported
    here — never duplicated. The report is backup's restore
    report verbatim (``applied``/``missing``/``extra``/``failed``,
    plus ``settings`` for the caller to apply, or applied to
    ``settings_store`` when one is supplied). Nothing here
    restarts the app: the coordinator applies the report and
    decides what restarts, if anything.

    Loophole batch 3 (#10): ``passphrase`` is the bundle's
    backup passphrase — without it (or for a legacy plain
    bundle, or when the integrity check fails) restore refuses
    and applies nothing.
    """
    return _restore_bundle(bundle_path, target_data_dir,
                           settings_store=settings_store,
                           passphrase=passphrase)
