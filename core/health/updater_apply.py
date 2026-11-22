"""Apply journal + startup swap — split from updater.py (B24)."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from ..version import VERSION, parse_version, version_newer
from .updater_manifest import _read_manifest_map, _verify_staged_manifest  # INTENTIONAL: shared helpers
from .updater_paths import _APPLIED_NAME, _BACKUP_DIRNAME, _JOURNAL_NAME, _MANIFEST_NAME, _MANIFEST_SIG_NAME, _VERSION_REL, _data_dir, _log, _now_iso, _package_root, _read_version_file, _sha256_of, _staging_dir  # INTENTIONAL: shared helpers/constants
from .updater_pending import _read_marker  # INTENTIONAL: shared helper
from .updater_protect import _is_protected_rel, _protected_in_inventory  # INTENTIONAL: shared helpers


def _journal_path() -> Path:
    # Lives in data/, NOT in the staging dir: _clear_staging()
    # must never silently remove the crash record, and data/ is
    # never part of a backup or a staged tree.
    return _data_dir() / _JOURNAL_NAME


def _write_journal(record: dict) -> None:
    path = _journal_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_journal() -> dict | None:
    try:
        record = json.loads(
            _journal_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/unreadable = no journal
        return None
    return record if isinstance(record, dict) else None


def _remove_journal() -> None:
    try:
        _journal_path().unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 — cleanup must never break apply
        pass


def _restore_from_backup(root: Path, backup_dir: Path,
                         inventory: list[str]) -> list[str]:
    """Restores every backed-up file over the live tree and deletes
    files the update introduced (in the inventory, not in the
    backup). Protected credential paths and data/ are never
    touched — they are never in a backup or an inventory, and the
    guards below double-enforce that. Returns failure strings
    (empty = clean). Never raises."""
    failures: list[str] = []
    backed: set[str] = set()
    for path in backup_dir.rglob("*"):
        if path.is_file():
            rel = path.relative_to(backup_dir).as_posix()
            backed.add(rel)
            if _is_protected_rel(rel) or rel == "data" or \
                    rel.startswith("data/"):
                continue
            dst = root / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dst)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{rel}: {exc}")
    for rel in inventory:
        if rel in backed:
            continue
        if _is_protected_rel(rel) or rel == "data" or \
                rel.startswith("data/"):
            continue
        try:
            (root / rel).unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{rel}: {exc}")
    return failures


def _perform_rollback(journal: dict) -> dict:
    """Completes a rollback requested by rollback_to_previous()
    (loophole batch 6, #9). Restores the journal's backup over
    the live tree with the journal's inventory (files the update
    introduced are deleted; user files created since are never
    in the inventory, so they are untouched), then verifies the
    version file reads the target build. On verified success the
    backup copy and the applied record are removed — the backup
    IS the live tree now, so keeping it would waste a tree copy
    and let the same rollback be requested twice. On any
    failure the journal is still removed (a failed rollback must
    not loop at every startup) but the backup and record are
    KEPT, so the rollback can be retried or done by hand, and
    the failure is logged + reported instead of silent. Never
    raises."""
    version = journal.get("version")
    from_version = journal.get("from_version")
    root = _package_root()
    if not version or parse_version(version) is None:
        _remove_journal()
        _log("rollback request has no valid target version — "
             "journal discarded, nothing changed")
        return {"state": "error",
                "message": "the rollback request is malformed — "
                           "nothing was changed"}
    backup_rel = journal.get("backup_dir")
    backup_dir = _data_dir() / backup_rel \
        if isinstance(backup_rel, str) and backup_rel else None
    if backup_dir is None or not backup_dir.is_dir():
        _remove_journal()
        _log(f"rollback to {version} cannot run: its backup is "
             f"missing")
        return {"state": "error", "version": version,
                "message": "the previous build's backup is missing "
                           "— the install may have been cleaned; "
                           "reinstall VYUK by hand to be safe"}
    inventory = journal.get("inventory")
    if not isinstance(inventory, list):
        inventory = []
    failures = _restore_from_backup(root, backup_dir,
                                    [str(r) for r in inventory])
    if failures:
        _remove_journal()
        _log(f"rollback to {version} was incomplete: {failures}")
        return {"state": "error", "version": version,
                "message": "the rollback was incomplete — "
                           "reinstall VYUK by hand to be safe"}
    live_version = _read_version_file(root / _VERSION_REL)
    if live_version != version:
        _remove_journal()
        _log(f"rollback to {version} restored files but the "
             f"version file reads {live_version!r}")
        return {"state": "error", "version": version,
                "message": "the rollback restored files but the "
                           "version still reads differently — "
                           "reinstall VYUK by hand to be safe"}
    _clear_staging()
    _remove_journal()
    shutil.rmtree(backup_dir, ignore_errors=True)
    try:
        (_data_dir() / _APPLIED_NAME).unlink(missing_ok=True)
    except OSError:
        pass
    _log(f"rolled back {from_version} -> {version}; restart to "
         f"run the previous build")
    return {"state": "rolled_back", "version": version,
            "from": from_version,
            "detail": "the previous build was restored — restart "
                      "VYUK to run it"}


def _recover_interrupted(journal: dict) -> dict:
    """Recovers from a previous apply that started swapping and
    never finished cleanly. Two phases, and they must not be
    confused: phase "done" means the swap completed and verified
    and only cleanup was interrupted — rolling back a completed
    update would be a downgrade, so it is cleaned up and reported
    as applied. Any other phase means the tree may be mixed: the
    pre-update tree is restored from the journal's backup, the
    staged update is discarded, and the state is "rolled_back".
    A missing backup is an error that touches nothing — the tree
    may be mixed-version and only a manual reinstall is safe."""
    version = journal.get("version")
    from_version = journal.get("from_version")
    root = _package_root()
    if journal.get("phase") == "done":
        _clear_staging()
        _remove_journal()
        _log(f"previous update to {version} had completed before "
             f"startup was interrupted; staging cleaned up")
        return {"state": "applied", "from": from_version, "to": version,
                "detail": "a previous update had completed before "
                          "startup was interrupted; staging cleaned up"}
    backup_rel = journal.get("backup_dir")
    backup_dir = _data_dir() / backup_rel \
        if isinstance(backup_rel, str) and backup_rel else None
    if backup_dir is None or not backup_dir.is_dir():
        _log(f"interrupted update to {version} cannot be recovered: "
             f"its backup is missing")
        return {"state": "error", "version": version,
                "message": "a previous update was interrupted and "
                           "its backup is missing — the install may "
                           "be mixed-version; reinstall VYUK by hand "
                           "to be safe"}
    inventory = journal.get("inventory")
    if not isinstance(inventory, list):
        inventory = []
    failures = _restore_from_backup(root, backup_dir, inventory)
    _clear_staging()
    _remove_journal()
    if failures:
        _log(f"interrupted update to {version} rollback was "
             f"incomplete: {failures}")
        return {"state": "error", "version": version,
                "message": "a previous update was interrupted and "
                           "its automatic rollback was incomplete — "
                           "reinstall VYUK to be safe"}
    _log(f"interrupted update to {version} rolled back to "
         f"{from_version}")
    return {"state": "rolled_back", "version": version,
            "reason": "the previous update was interrupted "
                      "partway; the previous build was restored and "
                      "the staged update discarded"}


# ---------------------------------------------------------------------------
# apply_staged() — the startup swap
# ---------------------------------------------------------------------------


def _clear_staging() -> None:
    shutil.rmtree(_staging_dir(), ignore_errors=True)


def _verify_staging(marker: dict) -> str | None:
    """None when the staged tree matches the marker inventory
    exactly, else the discrepancy. A staged file deleted or altered
    after staging must abort the apply (see apply_staged)."""
    tree = _staging_dir() / marker["version"]
    if not tree.is_dir():
        return "the staged tree is missing"
    inventory = marker.get("files")
    if not isinstance(inventory, dict) or not inventory:
        return "the staging marker has no file inventory"
    on_disk: dict[str, Path] = {}
    for path in tree.rglob("*"):
        if path.is_file():
            on_disk[path.relative_to(tree).as_posix()] = path
    if set(on_disk) != set(inventory):
        missing = sorted(set(inventory) - set(on_disk))
        extra = sorted(set(on_disk) - set(inventory))
        detail = []
        if missing:
            detail.append(f"missing: {', '.join(missing[:3])}")
        if extra:
            detail.append(f"unexpected: {', '.join(extra[:3])}")
        return "staged tree does not match its inventory (" + \
            "; ".join(detail) + ")"
    for rel, sha in inventory.items():
        if _sha256_of(on_disk[rel]) != sha:
            return f"staged file {rel!r} no longer matches its hash"
    return None


def _writable_probe(root: Path) -> bool:
    probe = root / ".nova_update_probe"
    try:
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def apply_staged() -> dict:
    """Applies a staged update over the package tree. Called once
    at startup from main.py, before the main window constructs.
    Never raises; on any mid-swap failure the pre-update tree is
    restored from the backup and the state is "rolled_back".

    States: "none" (nothing valid staged), "applied",
    "rolled_back", "needs_manual" (the app directory isn't
    writable — e.g. a frozen install in a protected location — so
    a human must reinstall/update by hand), "error".
    """
    try:
        return _apply_staged()
    except Exception as exc:  # noqa: BLE001 — startup must survive us
        _log(f"apply failed unexpectedly: {exc}")
        return {"state": "error", "message": str(exc)[:300]}


def _backup_ignore(dirpath, names, root=None):
    """The shutil.copytree ignore callable for the pre-apply
    backup (v5.47, programme item 14 — refactored from a closure
    inside _apply_staged to this module-level function so the
    exclusion contract is directly testable).

    Always skipped: __pycache__ everywhere, and data/ at the
    package root (user data never enters a tree backup).

    v5.47 addition: at the package root, ".env" and every
    ".env.*" file are ALSO skipped — before this fix the backup
    copied the live .env (with the user's API keys in it, however
    they are stored) into data/update_backup/<version>/.env on
    EVERY update, quietly multiplying the secret's copies. A
    root-level google_token.json is skipped for the same reason
    if one ever exists there (the token normally lives in data/,
    which is already excluded wholesale)."""
    skipped = {"__pycache__"}
    root_path = (Path(root).resolve() if root is not None
                 else _package_root().resolve())
    try:
        here = Path(dirpath).resolve()
    except Exception:  # noqa: BLE001 — an unresolvable dir is not root
        here = None
    if here is not None and here == root_path:
        skipped.add("data")
        for name in names:
            lowered = name.lower()
            if (lowered == ".env" or lowered.startswith(".env.")
                    or lowered == "google_token.json"
                    # v5.47 (programme item 14, coordinator follow-up):
                    # the secrets store's machine-key file lives at
                    # the package root beside .env. A backup that
                    # copied it would park the key next to the very
                    # ciphertexts it protects (and next to any backed
                    # -up protected token), silently gutting the
                    # machine-key store — so it never enters a backup.
                    or lowered == ".vyuk_secret_key"):
                skipped.add(name)
    return skipped & set(names)


def _apply_staged() -> dict:
    # 0) Recovery FIRST. A leftover journal means a previous apply
    # started swapping and never finished cleanly. This check runs
    # BEFORE the stale-staging check below — that check is exactly
    # what would otherwise discard the recovery: a crash after the
    # version file was copied makes the running version look
    # "new", so the staging would read as stale and the mixed
    # tree would be kept and reported as the new version.
    # A journal file that exists but does not parse is fail-closed:
    # the tree is not touched until a human intervenes.
    if _journal_path().is_file():
        journal = _read_journal()
        if journal is None:
            _log("apply journal exists but is unreadable — "
                 "refusing to touch the tree")
            return {"state": "error",
                    "message": "the update journal is unreadable — "
                               "the install may be mid-update; "
                               "reinstall VYUK by hand to be safe"}
        # Loophole batch 6 (#9): a rollback journal is a request
        # the user made from the About card, not an interrupted
        # apply — it completes here, at startup, before anything
        # else is touched.
        if journal.get("phase") == "rollback_requested":
            return _perform_rollback(journal)
        return _recover_interrupted(journal)
    marker = _read_marker()
    if marker is None:
        return {"state": "none"}
    version = marker["version"]
    root = _package_root()
    if not version_newer(version, VERSION):
        _clear_staging()
        _log(f"discarded stale staging for {version} "
             f"(running {VERSION})")
        return {"state": "none", "detail": "stale staging discarded"}
    problem = _verify_staging(marker)
    if problem is not None:
        _clear_staging()
        _log(f"update {version} aborted before apply: {problem}")
        return {"state": "rolled_back", "version": version,
                "reason": f"staging failed verification ({problem}); "
                          f"the live tree was not touched"}
    tree = _staging_dir() / version
    inventory = marker["files"]
    # Gate B (P0-3): a staged inventory naming a credential path
    # refuses the whole update before anything live is touched
    # (this catches a tampered marker, which extraction alone
    # cannot see — extraction rejects the same paths at stage time).
    bad = _protected_in_inventory(inventory)
    if bad:
        _clear_staging()
        _log(f"update {version} aborted before apply: staged "
             f"inventory names protected paths: {bad}")
        return {"state": "rolled_back", "version": version,
                "reason": "staged update names protected credential "
                          f"paths ({', '.join(bad)}); the live tree "
                          f"was not touched"}
    # Gate B (P0-4): the staged signed manifest must verify before
    # any live file changes — signature + manifest-listed hashes.
    mproblem = _verify_staged_manifest(tree)
    if mproblem is not None:
        _clear_staging()
        _log(f"update {version} aborted before apply: {mproblem}")
        return {"state": "rolled_back", "version": version,
                "reason": f"staged manifest failed verification "
                          f"({mproblem}); the live tree was not touched"}
    # Gate B (P0-4): a staged tree without a version file would
    # "apply" without ever bumping the version — refuse it.
    if _VERSION_REL not in inventory:
        _clear_staging()
        _log(f"update {version} aborted before apply: staged tree "
             f"carries no version file")
        return {"state": "rolled_back", "version": version,
                "reason": "staged update carries no version file; "
                          "the live tree was not touched"}
    if not _writable_probe(root):
        frozen = bool(getattr(sys, "frozen", False))
        _log(f"update {version} cannot apply: app directory not "
             f"writable (frozen={frozen})")
        return {"state": "needs_manual", "version": version,
                "message": "VYUK's install folder isn't writable, "
                           "so the staged update can't be applied "
                           "automatically — reinstall the newer "
                           "version by hand to update"}
    data_dir = _data_dir()
    backup_dir = data_dir / _BACKUP_DIRNAME / VERSION
    # 1) Back up the current tree (never data/, never __pycache__,
    # never the root .env / google_token.json — _backup_ignore,
    # v5.47 programme item 14).
    try:
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)

        shutil.copytree(
            root, backup_dir,
            ignore=lambda d, n: _backup_ignore(d, n, root=root),
            dirs_exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        _log(f"update {version}: backup failed, apply aborted: {exc}")
        return {"state": "error", "version": version,
                "message": f"couldn't back up the current build: "
                           f"{exc}"[:300]}
    # Gate B (P1-4): the previous manifest is the ownership
    # baseline. A missing previous manifest does not block the
    # update, but reconciliation is then skipped outright — no
    # file is ever deleted without positive ownership evidence.
    prev_manifest = _read_manifest_map(root)
    new_manifest = _read_manifest_map(tree)
    notes: list[str] = []
    if prev_manifest is None:
        notes.append("owned-file reconciliation skipped: the "
                     "previous install manifest is missing, so no "
                     "file could be proven stale — nothing was "
                     "removed")
    # Gate B (P1-4): a live file the previous manifest lists with
    # a different hash was modified by the user; a live file the
    # previous manifest never lists was created by the user and
    # the update also ships that path. Both are preserved and
    # reported, never silently overwritten. The version file and
    # the manifest files are always copied: the version file IS
    # the update's marker, and the manifest files are the new
    # trust anchor (a modified version file is reported in notes).
    preserved_modified: list[str] = []
    if prev_manifest is not None:
        for rel in sorted(inventory):
            if rel in (_VERSION_REL, _MANIFEST_NAME,
                       _MANIFEST_SIG_NAME):
                continue
            dst = root / rel
            if not dst.is_file():
                continue
            if rel in prev_manifest:
                if _sha256_of(dst) != prev_manifest[rel]:
                    preserved_modified.append(rel)
            else:
                preserved_modified.append(rel)
        if _VERSION_REL in prev_manifest:
            live_version = root / _VERSION_REL
            if live_version.is_file() and \
                    _sha256_of(live_version) != \
                    prev_manifest[_VERSION_REL]:
                notes.append("the live version file had been "
                             "modified since the previous manifest; "
                             "it was updated anyway — it is the "
                             "update's version marker")
    # Gate B (P0-4): the journal is written BEFORE any live file
    # changes, so a crash mid-swap is recoverable on next startup.
    journal = {"version": version, "from_version": VERSION,
               "backup_dir": f"{_BACKUP_DIRNAME}/{VERSION}",
               "inventory": sorted(inventory), "phase": "swapping"}
    try:
        _write_journal(journal)
    except Exception as exc:  # noqa: BLE001 — journal failure means
        # the swap must not start: an unjournaled swap is unrecoverable
        _log(f"update {version}: journal failed, apply aborted: {exc}")
        return {"state": "error", "version": version,
                "message": "couldn't record the update journal: "
                           f"{exc}"[:300]}
    # 2) Swap the staged files over the live tree — the version
    # file LAST (Gate B, P0-4). Until that copy lands, the running
    # version still says the old build, so a crash mid-swap can
    # never leave a mixed tree reporting the new version.
    try:
        ordered = [rel for rel in sorted(inventory)
                   if rel != _VERSION_REL]
        if _VERSION_REL in inventory:
            ordered.append(_VERSION_REL)
        for rel in ordered:
            if rel in preserved_modified:
                continue
            src = tree / rel
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        # Post-copy verification: every swapped file must match
        # the staged inventory before the update counts as applied
        # (preserved user-modified files are expected to differ
        # and are reported, not treated as failures).
        verify_failures = []
        for rel in sorted(inventory):
            if rel in preserved_modified:
                continue
            live = root / rel
            if not live.is_file() or _sha256_of(live) != inventory[rel]:
                verify_failures.append(rel)
        if verify_failures:
            raise OSError("post-copy verification failed for: " +
                          ", ".join(verify_failures[:3]))
    except Exception as exc:  # noqa: BLE001 — restore and report
        restore_failures = _restore_from_backup(
            root, backup_dir, sorted(inventory))
        _clear_staging()
        _remove_journal()
        if restore_failures:
            _log(f"update {version} FAILED mid-swap and rollback "
                 f"was incomplete: {restore_failures}")
            return {"state": "error", "version": version,
                    "message": "the update failed mid-apply and the "
                               "automatic rollback was incomplete — "
                               "reinstall VYUK to be safe"}
        _log(f"update {version} failed mid-swap ({exc}); "
             f"rolled back to {VERSION}")
        return {"state": "rolled_back", "version": version,
                "reason": f"apply failed partway ({exc}); the "
                          f"previous build was restored"}
    # 3) Reconciliation (Gate B, P1-4): remove files the previous
    # manifest owned that the new manifest no longer lists —
    # never user files, never user-modified files, never
    # protected/data paths, never a file the new inventory still
    # ships. A removal that fails is reported, not fatal: the
    # update itself succeeded, and rolling it back over one stale
    # file would be worse than leaving the file and saying so.
    removed: list[str] = []
    removal_skipped_modified: list[str] = []
    removal_failures: list[str] = []
    if prev_manifest is not None and new_manifest is not None:
        new_inventory = set(inventory)
        for rel in sorted(prev_manifest):
            if rel in new_manifest or rel in new_inventory:
                continue
            if _is_protected_rel(rel) or rel == "data" or \
                    rel.startswith("data/"):
                continue
            live = root / rel
            if not live.is_file():
                continue
            if _sha256_of(live) != prev_manifest[rel]:
                removal_skipped_modified.append(rel)
                continue
            try:
                live.unlink()
                removed.append(rel)
            except Exception as exc:  # noqa: BLE001
                removal_failures.append(f"{rel}: {exc}")
    # 4) Success: mark the journal done, record, clear staging,
    # remove the journal, report.
    journal["phase"] = "done"
    try:
        _write_journal(journal)
    except Exception:  # noqa: BLE001 — the swap already succeeded
        pass
    # Loophole batch 6 (#9): the applied record carries the
    # update's file inventory and backup location, so a later
    # rollback knows exactly which files the update introduced
    # (they are deleted on rollback) instead of guessing by
    # diffing the live tree against the backup — a guess would
    # delete user files whenever the backup is partial.
    record = {"from": VERSION, "to": version, "at": _now_iso(),
              "inventory": sorted(inventory),
              "backup_dir": f"{_BACKUP_DIRNAME}/{VERSION}"}
    try:
        applied_path = data_dir / _APPLIED_NAME
        tmp = applied_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        os.replace(tmp, applied_path)
    except Exception:  # noqa: BLE001 — the swap itself already succeeded
        pass
    _clear_staging()
    _remove_journal()
    _log(f"updated {VERSION} -> {version}")
    return {"state": "applied", "from": VERSION, "to": version,
            "preserved_modified": preserved_modified,
            "removed": removed,
            "removal_skipped_modified": removal_skipped_modified,
            "removal_failures": removal_failures,
            "notes": notes}
