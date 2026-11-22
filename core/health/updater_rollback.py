"""Rollback + discard seams — split from updater.py (loophole
batch 6, #9).

The update machinery (signed feed, hash-verified download,
journaled apply, restore-from-backup on failure) already
existed; what was missing was any way to UNDO an update that
applied cleanly but turned out bad, or to throw away a staged
update without restarting. Both seams are user-facing only —
no funnel tool, skill or voice path calls them (the voice path
to the About card is clicks, never a direct updater call).

Design, two halves:
* Request (here, cheap filesystem reads + one journal write):
  rollback_available() scans data/update_backup/ for a backup
  whose version file reads an older build than the running one
  and reports it; rollback_to_previous() records the request as
  a journal (phase "rollback_requested") and returns
  "rollback_pending" — it touches NO live file. The restore
  itself runs inside apply_staged() at next startup, exactly
  where the apply swap already runs, because rolling back a
  running app means overwriting the files of the running
  process. discard_staged_update() removes the staging dir +
  marker immediately (nothing live is staged yet, so there is
  nothing to protect) and reports what it discarded.
* Perform (updater_apply._perform_rollback, called from
  apply_staged when it sees a rollback journal): restores the
  backup over the live tree with the update's inventory (files
  the update introduced are deleted), verifies the version file
  reads the target build, and only then drops the backup copy
  and the applied record — a failed rollback keeps both, so it
  can be retried or done by hand.

The inventory comes from the applied record
(data/update_applied.json), which _apply_staged now writes
with the update's file list. When the record is missing or
carries no inventory (an older build's apply, or a crash
between swap and record), the rollback is DEGRADED: it
restores the backed-up files and deletes nothing, and says so
in the returned detail — files the update introduced stay in
place rather than being guessed at. Guessing the inventory by
diffing the live tree against the backup would delete user
files whenever the backup is partial, so it is never done.
"""
from __future__ import annotations

import json

from ..version import VERSION, parse_version, version_newer
from .updater_apply import _clear_staging, _journal_path, _write_journal
from .updater_paths import _APPLIED_NAME, _BACKUP_DIRNAME, _VERSION_REL, _data_dir, _log, _read_version_file
from .updater_pending import _read_marker


def _read_applied() -> dict | None:
    """The applied-update record, or None when absent/corrupt."""
    try:
        record = json.loads(
            (_data_dir() / _APPLIED_NAME).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — absent/corrupt = no record
        return None
    return record if isinstance(record, dict) else None


def rollback_available() -> dict:
    """The previous build that can be restored, or a no-availability
    report. Scans data/update_backup/ for a backup whose
    core/version.py reads a build strictly OLDER than the running
    one; the newest such backup wins. A backup whose version file
    is missing or garbled is skipped, never trusted by directory
    name. Returns {"available": bool, "version": str | None,
    "from_version": str, "backup_dir": str | None}."""
    backup_root = _data_dir() / _BACKUP_DIRNAME
    best: tuple[tuple[int, ...], str, object] | None = None
    if backup_root.is_dir():
        try:
            children = sorted(backup_root.iterdir())
        except OSError:
            children = []
        for child in children:
            if not child.is_dir():
                continue
            ver = _read_version_file(child / _VERSION_REL)
            parsed = parse_version(ver) if ver else None
            if parsed is None:
                continue
            if not version_newer(VERSION, ver):
                continue
            if best is None or parsed > best[0]:
                best = (parsed, ver, child)
    if best is None:
        return {"available": False, "version": None,
                "from_version": VERSION, "backup_dir": None}
    return {"available": True, "version": best[1],
            "from_version": VERSION, "backup_dir": str(best[2])}


def rollback_to_previous() -> dict:
    """Request a rollback to the previous build. Writes a
    rollback journal and returns {"state": "rollback_pending",
    "version", "from_version", "detail"} — the restore itself
    runs inside apply_staged() at next startup (see the module
    docstring: a running app must not overwrite its own files).
    The About card tells the user to restart. Refuses when
    there is nothing to roll back to, when an interrupted
    apply's journal is still pending recovery, or when a newer
    update is already staged (discard it or restart to install
    it first — a rollback and a staged update cannot both be
    pending). Never raises: failures return an error dict."""
    try:
        avail = rollback_available()
        if not avail.get("available"):
            return {"state": "none",
                    "message": "no previous build is available to "
                               "roll back to — no update backup was "
                               "found"}
        version = str(avail["version"])
        if _journal_path().is_file():
            return {"state": "error", "version": version,
                    "message": "a previous update was interrupted — "
                               "restart VYUK once to let it recover "
                               "before rolling back"}
        if _read_marker() is not None:
            return {"state": "error", "version": version,
                    "message": "a newer update is already downloaded "
                               "— discard it or restart to install it "
                               "before rolling back"}
        applied = _read_applied()
        inventory = applied.get("inventory") \
            if isinstance(applied, dict) else None
        degraded = not isinstance(inventory, list)
        if degraded:
            inventory = []
        journal = {"phase": "rollback_requested",
                   "version": version, "from_version": VERSION,
                   "backup_dir": f"{_BACKUP_DIRNAME}/{version}",
                   "inventory": sorted(str(r) for r in inventory)}
        try:
            _write_journal(journal)
        except Exception as exc:  # noqa: BLE001 — journal failure
            # means the request must not be reported as pending
            return {"state": "error", "version": version,
                    "message": f"couldn't record the rollback "
                               f"request: {exc}"[:300]}
        _log(f"rollback to {version} requested; it completes at "
             f"next startup")
        detail = ("restart VYUK to complete the rollback to the "
                  "previous build")
        if degraded:
            detail += (" (the update's file list is unavailable, so "
                       "files the update introduced will stay in "
                       "place — only the previous build's files "
                       "are restored)")
        return {"state": "rollback_pending", "version": version,
                "from_version": VERSION, "detail": detail}
    except Exception as exc:  # noqa: BLE001 — a rollback request
        return {"state": "error",  # must never break Settings
                "message": f"couldn't request the rollback: {exc}"}


def discard_staged_update() -> dict:
    """Throw away a staged (downloaded, not yet installed) update:
    removes the whole staging dir including its marker, so
    pending_update() reports nothing afterwards and the next
    startup installs nothing. Returns {"state": "discarded",
    "version"} or {"state": "none"} when nothing was staged.
    Never raises."""
    try:
        marker = _read_marker()
        if marker is None:
            _clear_staging()
            return {"state": "none",
                    "message": "no staged update to discard"}
        version = marker.get("version")
        _clear_staging()
        _log(f"discarded staged update {version}")
        return {"state": "discarded", "version": version}
    except Exception as exc:  # noqa: BLE001 — discarding must
        return {"state": "error",  # never break Settings
                "message": f"couldn't discard the staged update: "
                           f"{exc}"}
