"""Staged-manifest verification — split from updater.py (B24)."""
from __future__ import annotations

import json
from pathlib import Path
from .updater_paths import _MANIFEST_NAME  # INTENTIONAL: shared constant


def _read_manifest_map(root: Path) -> dict | None:
    """The {rel: sha} files map of the signed install manifest at
    `root`, or None when the manifest is missing, unreadable, or
    malformed. Never raises. The signature itself is NOT checked
    here: for a staged tree it is checked by
    _verify_staged_manifest before any live change; for the live
    tree the manifest is only a removal baseline, and a live
    manifest that fails verification is already reported loudly
    by integrity.check_and_log at startup."""
    try:
        manifest = json.loads(
            (root / _MANIFEST_NAME).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/unreadable = no map
        return None
    files = manifest.get("files") if isinstance(manifest, dict) \
        else None
    return files if isinstance(files, dict) else None


def _verify_staged_manifest(tree: Path) -> str | None:
    """None when the staged tree's signed install manifest verifies
    (signature over the exact manifest bytes + every
    manifest-listed file present with a matching hash), else a
    plain-words problem. Runs BEFORE any live file changes. A
    staged tree without a usable signed manifest is refused, not
    applied on trust: every release zip we ship carries the
    manifest + signature, so a missing one means the payload is
    not one of ours. The integrity import is lazy so this module
    keeps its import order at startup (main.py imports the
    updater before QApplication exists)."""
    try:
        from .integrity import verify_install_tree
    except Exception as exc:  # noqa: BLE001
        return ("staged manifest could not be verified "
                f"(integrity module unavailable: {exc})")
    try:
        result = verify_install_tree(tree)
    except Exception as exc:  # noqa: BLE001 — verify never raises,
        # but a failure here must still refuse the update, not pass it
        return f"staged manifest verification failed: {exc}"
    if result.get("ok"):
        return None
    problems = result.get("problems") or ["unknown verification failure"]
    return ("staged install manifest does not verify: " +
            "; ".join(problems[:3]))
