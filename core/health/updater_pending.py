"""pending_update() — split from updater.py (B24)."""
from __future__ import annotations

import json
from ..version import VERSION, parse_version
from .updater_paths import _marker_path, _staging_dir  # INTENTIONAL: shared helpers


# ---------------------------------------------------------------------------
# pending_update() — what the Settings UI shows
# ---------------------------------------------------------------------------


def _read_marker() -> dict | None:
    try:
        marker = json.loads(
            _marker_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — absent/corrupt marker = nothing staged
        return None
    if not isinstance(marker, dict) or \
            parse_version(marker.get("version")) is None:
        return None
    return marker


def pending_update() -> dict | None:
    """The staged update awaiting the next restart, or None."""
    marker = _read_marker()
    if marker is None:
        return None
    tree = _staging_dir() / marker["version"]
    if not tree.is_dir():
        return None
    return {
        "state": "staged",
        "version": marker["version"],
        "notes": marker.get("notes", ""),
        "staged_at": marker.get("staged_at"),
        "current_version": VERSION,
    }
