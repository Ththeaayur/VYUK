"""control_files concern extracted from core/control.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/control."""

import os
from .. import config_spec
import shutil
import threading
from pathlib import Path
from .. import tool_outcome as _outcome  # R1a: seam wiring (verify-after-act)
from .control_base import (_DATA_DIR, _is_windows, _routed_backend, _truncate, _input_blocked)


_TRASH_MANIFEST_NAME = "trash_manifest.json"


_TRASH_STAMP_NAME = ".last_purge"


_TRASH_LOCK = threading.Lock()


def _trash_root() -> Path:
    """The ONE central trash root (v5.48, programme item 27).

    Resolution order: the VYUK_TRASH_DIR env override (tests and
    portable installs) — else, on Windows, %LOCALAPPDATA%/VYUK/
    trash — else $XDG_DATA_HOME/VYUK/trash or ~/.local/share/VYUK/
    trash — with the package data/ dir's .trash as the final
    fallback when none of the home-based roots can be created.
    The root is created on demand."""
    override = config_spec.env("VYUK_TRASH_DIR").strip()
    if override:
        root = Path(override).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        return root
    candidates: list[Path] = []
    if _is_windows():
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if local_appdata:
            candidates.append(Path(local_appdata) / "VYUK" / "trash")
        candidates.append(
            Path.home() / "AppData" / "Local" / "VYUK" / "trash")
    else:
        xdg = os.environ.get("XDG_DATA_HOME", "").strip()
        if xdg:
            candidates.append(Path(xdg).expanduser() / "VYUK" / "trash")
        candidates.append(Path.home() / ".local" / "share" / "VYUK" / "trash")
    candidates.append(_DATA_DIR / ".trash")
    for root in candidates:
        try:
            root.mkdir(parents=True, exist_ok=True)
            return root
        except OSError:
            continue
    # Every candidate refused: let the last one's error speak.
    root = candidates[-1]
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_trash_manifest(root: Path) -> list:
    """Manifest entries (list of dicts) for `root`; a missing or
    corrupt manifest reads as empty — the trash files themselves
    are never discarded over a bookkeeping failure."""
    import json

    try:
        data = json.loads(
            (root / _TRASH_MANIFEST_NAME).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/corrupt = empty
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict)]


def _write_trash_manifest(root: Path, entries: list) -> None:
    """Rewrite the manifest atomically (tmp + replace), like
    core/config's .env writes: a crash mid-write must not lose
    the recovery index."""
    import json

    tmp = root / (_TRASH_MANIFEST_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=1)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, root / _TRASH_MANIFEST_NAME)


def _append_trash_manifest(root: Path, entry: dict) -> None:
    """Best-effort manifest append: the file is ALREADY safe in
    the trash root when this runs, so a bookkeeping failure must
    never turn a completed rescue into a reported failure (which
    would mislead the caller into retrying over the new file)."""
    try:
        with _TRASH_LOCK:
            entries = _read_trash_manifest(root)
            entries.append(entry)
            _write_trash_manifest(root, entries)
    except Exception:  # noqa: BLE001 — bookkeeping only, see docstring
        pass


def _trash_entry_size(item: Path) -> int:
    """Byte size of a file, or the summed content size of a
    folder; 0 when it cannot be read."""
    try:
        if item.is_dir() and not item.is_symlink():
            total = 0
            for dirpath, _dirnames, filenames in os.walk(item):
                for fname in filenames:
                    try:
                        total += (Path(dirpath) / fname).stat().st_size
                    except OSError:
                        pass
            return total
        return item.stat().st_size
    except OSError:
        return 0


def _move_to_central_trash(item: Path, kind: str) -> tuple:
    """Move `item` into the central trash root under a timestamped
    unique name, record it in the manifest, and return
    (new_path, manifest_entry) (v5.48, programme item 27).

    Raises on any move failure, with the item left exactly where
    it was — the pre-v5.48 rescue contract, unchanged. Runs the
    at-most-daily purge check FIRST (see the section header)."""
    import datetime

    _maybe_daily_purge()
    root = _trash_root()
    size = _trash_entry_size(item)
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S-%f")
    target = root / f"{item.stem}_{stamp}{item.suffix}"
    counter = 1
    while target.exists():
        counter += 1
        target = root / f"{item.stem}_{stamp}_{counter}{item.suffix}"
    moved = Path(shutil.move(str(item), str(target)))
    entry = {
        "id": moved.name,
        "original_path": str(item),
        "trashed_name": moved.name,
        "deleted_at": now.isoformat(),
        "size": size,
        "kind": kind,
    }
    _append_trash_manifest(root, entry)
    return moved, entry


def _maybe_daily_purge() -> None:
    """The at-most-daily automatic purge (v5.48, programme item
    27d): when the trash root's .last_purge stamp is missing or
    older than 24 h, run purge_central_trash() and restamp. The
    stamp is written BEFORE the purge runs, so even a failing
    purge cannot retry-stampede on every rescue. Never raises —
    a purge problem must never block a rescue or a delete."""
    import time

    try:
        root = _trash_root()
        stamp_file = root / _TRASH_STAMP_NAME
        try:
            last = stamp_file.stat().st_mtime
        except OSError:
            last = 0.0
        if time.time() - last < 86400.0:
            return
        try:
            stamp_file.write_text(
                time.strftime("%Y-%m-%dT%H:%M:%S%z"), encoding="utf-8")
        except OSError:
            pass
        purge_central_trash()
    except Exception:  # noqa: BLE001 — see docstring
        pass


def purge_central_trash(max_age_days: float = 30) -> int:
    """Remove central-trash entries older than ``max_age_days``
    and prune them from the manifest; return how many entries
    were removed (v5.48, programme item 27). Ages come from the
    manifest's deleted_at, NOT file mtimes (a copied or touched
    trash file must not gain a new lease on life). An entry whose
    file is already gone is pruned and counted; an entry whose
    file cannot be removed is kept for the next purge. Malformed
    or undated entries are kept — purging on a guess could
    destroy the only copy of something."""
    import datetime

    try:
        days = float(max_age_days)
    except (TypeError, ValueError):
        raise ValueError("max_age_days must be a number of days") from None
    if days < 0:
        raise ValueError("max_age_days must be >= 0")
    root = _trash_root()
    with _TRASH_LOCK:
        entries = _read_trash_manifest(root)
        if not entries:
            return 0
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(days=days))
        kept: list = []
        removed = 0
        for entry in entries:
            try:
                deleted_at = datetime.datetime.fromisoformat(
                    str(entry.get("deleted_at", "")))
                if deleted_at.tzinfo is None:
                    deleted_at = deleted_at.replace(
                        tzinfo=datetime.timezone.utc)
            except (TypeError, ValueError):
                deleted_at = None
            if deleted_at is None or deleted_at >= cutoff:
                kept.append(entry)
                continue
            name = str(entry.get("trashed_name", ""))
            target = root / name if name and Path(name).name == name else None
            if target is not None and (target.exists()
                                       or target.is_symlink()):
                try:
                    if target.is_dir() and not target.is_symlink():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                except OSError:
                    kept.append(entry)
                    continue
            removed += 1
        if removed:
            try:
                _write_trash_manifest(root, kept)
            except Exception:  # noqa: BLE001 — files already gone;
                pass  # a stale manifest entry prunes next purge
        return removed


def restore_from_trash(identifier) -> str:
    """Restore one trashed item to its original path (v5.48,
    programme item 27). `identifier` is a manifest id (the
    trashed name) or the item's original path.

    Semantics (deliberately uniform, pinned by the v5.48 suite):
    a restore NEVER overwrites an existing file — not even for
    overwrite-rescue victims. The pre-v5.48 code had no restore
    path at all, so there is no clobbering precedent to match;
    refusing is the only behaviour that cannot destroy data the
    user has since created at that path. Legacy per-folder
    ``_nova_trash`` rescues (pre-v5.48) are also found: when the
    identifier is an original path with no central entry, the
    ``_nova_trash`` folder beside it is searched and its newest
    matching rescue restored."""
    ident = str(identifier or "").strip()
    if not ident:
        return "Restore needs a trash entry id or an original path."
    root = _trash_root()
    with _TRASH_LOCK:
        entries = _read_trash_manifest(root)
        entry = None
        for candidate in entries:
            if ident in (str(candidate.get("id", "")),
                         str(candidate.get("trashed_name", "")),
                         str(candidate.get("original_path", ""))):
                entry = candidate
                break
        if entry is not None:
            name = str(entry.get("trashed_name", ""))
            trashed = root / name if name and Path(name).name == name else None
            dest = Path(str(entry.get("original_path", "")))
            if trashed is None or not (trashed.exists()
                                       or trashed.is_symlink()):
                return (f"The trashed copy of {dest} is missing from "
                        f"{root} — it may already have been purged "
                        "or restored.")
            if dest.exists() or dest.is_symlink():
                return (f"Refused: {dest} already exists — restoring "
                        "would overwrite it. Move or rename it first, "
                        "then restore.")
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                moved = Path(shutil.move(str(trashed), str(dest)))
            except Exception as e:  # noqa: BLE001
                return f"Restore failed: {e}"
            entries.remove(entry)
            try:
                _write_trash_manifest(root, entries)
            except Exception:  # noqa: BLE001 — the file IS restored;
                pass  # a stale manifest entry is bookkeeping only
            return f"Restored {moved} from the central trash."
    # Legacy fallback: a pre-v5.48 per-folder _nova_trash rescue.
    original = Path(ident).expanduser()
    legacy_dir = original.parent / "_nova_trash"
    if legacy_dir.is_dir():
        matches = []
        for p in legacy_dir.iterdir():
            if p.name == original.name or (
                    p.stem.startswith(original.stem + "_")
                    and p.suffix == original.suffix):
                matches.append(p)
        if matches:
            chosen = max(matches,
                         key=lambda p: p.lstat().st_mtime)
            if original.exists() or original.is_symlink():
                return (f"Refused: {original} already exists — "
                        "restoring would overwrite it. Move or "
                        "rename it first, then restore.")
            try:
                moved = Path(shutil.move(str(chosen), str(original)))
            except Exception as e:  # noqa: BLE001
                return f"Restore failed: {e}"
            return (f"Restored {moved} from the legacy trash folder "
                    f"{legacy_dir}.")
    return (f"No trash entry found for '{ident}' — nothing was "
            "restored. It may already have been restored or purged.")


def _rescue_overwrite_victim(victim: Path) -> Path:
    """Move a file that is about to be overwritten into the
    CENTRAL trash (v5.48, programme item 27 — before v5.48, into
    the sibling ``_nova_trash/`` folder, v5.43 H7) under a
    timestamped name, record it in the central manifest, and
    return its new path. Signature and return are unchanged, so
    every existing caller (file_control here, sync_folder in
    core/skills_files_ops.py) routes centrally without its call site
    changing. Overwrite used to destroy the victim in place —
    delete went to trash but replacement did not — so an approved
    overwrite is recoverable exactly like a delete.

    Raises on any failure: the caller must then fail that item and
    leave the victim exactly where it was (a failed rescue must
    never become a destroyed file)."""
    moved, _entry = _move_to_central_trash(Path(victim),
                                           kind="overwrite-rescue")
    return moved


def purge_nova_trash(directory, max_age_days: float = 30) -> int:
    """Remove ``_nova_trash`` contents older than ``max_age_days``
    and return how many top-level entries were removed (v5.43, H7).

    ``directory`` may be the folder that CONTAINS a ``_nova_trash``
    folder, or a ``_nova_trash`` folder itself. Deliberately NOT
    wired to any schedule or automatic call — it runs only when
    something explicitly invokes it.

    Safety shape (why this is trivially safe to offer): it refuses
    to operate anywhere whose final component is not literally
    ``_nova_trash``; ages come from lstat, so a symlink is judged —
    and removed — as a link, never followed; only entries whose
    own modification time is older than the cutoff are touched."""
    import time

    try:
        days = float(max_age_days)
    except (TypeError, ValueError):
        raise ValueError("max_age_days must be a number of days") from None
    if days < 0:
        raise ValueError("max_age_days must be >= 0")
    base = Path(str(directory)).expanduser()
    trash = base if base.name == "_nova_trash" else base / "_nova_trash"
    if trash.name != "_nova_trash" or not trash.is_dir():
        return 0
    cutoff = time.time() - days * 86400.0
    removed = 0
    for entry in list(trash.iterdir()):
        try:
            if entry.lstat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def file_control(action: str, path: str, destination: str = "", pattern: str = "", confirm: bool = False, overwrite: bool = False):
    """File operations: list/find/copy/move/rename/create_folder/delete/info.

    v4.4 data-safety semantics (audit MAJOR): copy/move/rename never
    destroy an existing destination silently — collisions are refused
    up front with a clear message unless overwrite=true, batch work
    reports per-item outcomes, and replacements are disclosed.
    v5.43 (H7): an approved overwrite no longer destroys the victim
    either — it is rescued to trash first (see
    _rescue_overwrite_victim) and the result says where it went.
    v5.48 (programme item 27): the trash is now the ONE central
    root (_trash_root) with a manifest, for rescues and deletes
    alike — no more per-folder _nova_trash scatter.

    R1a (tools loopholes, seam pass 2): the mutating actions
    (copy/move/rename/create_folder/delete) return an Outcome —
    verified only when the world was re-read (destination bytes
    match, source gone after a move, trashed copy exists after a
    delete, folder exists after a create). Reads (list/find/info)
    and refusal returns stay plain strings, exactly as before.
    """
    action = (action or "").strip().lower()
    valid = ("list", "find", "copy", "move", "rename", "create_folder", "delete", "info")
    if action not in valid:
        return f"Unknown file action: '{action}'. Use one of: {', '.join(valid)}."
    if path is None or str(path) == "":
        return "File control needs a path."
    src = Path(str(path)).expanduser()
    try:
        if action == "list":
            if not src.exists():
                return f"Path not found: {src}"
            if src.is_file():
                return f"{src} is a file ({src.stat().st_size} bytes), not a folder."
            entries = sorted(src.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            if not entries:
                return f"{src} is empty."
            lines = []
            for entry in entries[:100]:
                kind = "DIR " if entry.is_dir() else "FILE"
                try:
                    size = entry.stat().st_size if entry.is_file() else 0
                except OSError:
                    size = 0
                lines.append(f"{kind} {entry.name}" + (f" ({size} bytes)" if kind == "FILE" else ""))
            suffix = f"\n... and {len(entries) - 100} more" if len(entries) > 100 else ""
            return f"Contents of {src} ({len(entries)} item(s)):\n" + "\n".join(lines) + suffix

        if action == "find":
            if not src.exists():
                return f"Path not found: {src}"
            if src.is_file():
                return f"{src} is a file, not a folder — 'find' searches inside a folder, so nothing was searched."
            glob_pattern = pattern or "*"
            # Keep the first 50 for display but COUNT the rest (up to
            # a scan cap) so the total is truthful; tolerate a folder
            # that errors mid-walk instead of discarding matches
            # already found (v4.4; the cap used to pose as the total).
            shown: list[Path] = []
            total = 0
            scan_error = None
            try:
                for hit in src.rglob(glob_pattern):
                    total += 1
                    if len(shown) < 50:
                        shown.append(hit)
                    if total >= 5000:
                        break
            except (OSError, PermissionError) as e:  # noqa: BLE001
                scan_error = e
            if total == 0 and scan_error is None:
                return f"No files matching '{glob_pattern}' under {src}."
            shown.sort(key=lambda p: str(p).lower())
            lines = [str(m) for m in shown]
            if total > 50:
                total_txt = "5000+" if total >= 5000 else str(total)
                header = f"Found {total_txt} match(es) for '{glob_pattern}' under {src} — showing the first 50:"
            else:
                header = f"Found {total} match(es) for '{glob_pattern}' under {src}:"
            note = f"\n(Search stopped early: {scan_error})" if scan_error is not None else ""
            return header + "\n" + "\n".join(lines) + note

        if action == "info":
            if not src.exists():
                return f"Path not found: {src}"
            st = src.stat()
            import datetime
            modified = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            if src.is_dir():
                # A folder's size is its CONTENT, not the directory
                # inode's byte size (v4.4 — the old number was the
                # inode, meaningless to every user who asked).
                total_size = 0
                n_files = 0
                n_dirs = 0
                walk_error = None
                for root, dirs, files in os.walk(src):
                    n_dirs += len(dirs)
                    for fname in files:
                        try:
                            total_size += (Path(root) / fname).stat().st_size
                            n_files += 1
                        except OSError as e:  # noqa: BLE001
                            walk_error = e
                size_line = (
                    f"Size: {total_size} bytes total across {n_files} file(s) "
                    f"in {n_dirs} subfolder(s)"
                )
                if walk_error is not None:
                    size_line += f" (partial — one item could not be read: {walk_error})"
                return f"{src}\nType: folder\n{size_line}\nModified: {modified}"
            return f"{src}\nType: file\nSize: {st.st_size} bytes\nModified: {modified}"

        if action == "create_folder":
            if src.exists() and not src.is_dir():
                return f"Cannot create a folder at {src}: a file already exists there."
            src.mkdir(parents=True, exist_ok=True)
            # R1a: verify the folder is really there — mkdir with
            # exist_ok=True returns quietly even when it did nothing.
            if src.is_dir():
                return _outcome.verified(
                    f"Folder ready: {src}",
                    "folder verified at %s" % src)
            return _outcome.failed(
                f"Could not create a folder at {src}: it is not a "
                "folder afterwards.",
                "folder missing after mkdir")

        if action in ("copy", "move", "rename"):
            if not destination:
                return f"File '{action}' needs a destination."
            if not src.exists():
                # F-F501 (same hole as delete): the act did not
                # happen, so the verdict is failed, not a plain
                # string the receipts would read "succeeded".
                return _outcome.failed(
                    f"Path not found: {src}", "source missing")
            dest = Path(str(destination)).expanduser()
            verb = {"copy": "Copied", "move": "Moved", "rename": "Renamed"}[action]
            src_is_dir = src.is_dir()
            if not src_is_dir and os.path.abspath(src) == os.path.abspath(dest):
                return f"Source and destination are the same file ({src}) — nothing to {action}."

            # Per-item plan (v4.4): a folder is walked file by file so
            # collisions are visible BEFORE anything is touched and
            # every item's outcome can be reported — one copytree call
            # used to silently merge/overwrite and dump raw errors.
            if src_is_dir:
                plan = []
                empty_dirs = []
                for root, dirs, files in os.walk(src):
                    if not dirs and not files:
                        empty_dirs.append(Path(root))
                    for fname in files:
                        s_file = Path(root) / fname
                        plan.append((s_file, dest / s_file.relative_to(src)))
            else:
                plan = [(src, dest)]
                empty_dirs = []

            if not plan:  # empty folder
                try:
                    dest.mkdir(parents=True, exist_ok=True)
                    for d in empty_dirs:
                        (dest / d.relative_to(src)).mkdir(parents=True, exist_ok=True)
                    if action in ("move", "rename"):
                        shutil.rmtree(src)
                    # R1a: verify the empty-folder act — the
                    # destination exists, and a move's source is gone.
                    if not dest.is_dir():
                        return _outcome.failed(
                            f"{verb} {src} -> {dest} (empty folder) — "
                            "but the destination folder is not there "
                            "afterwards.", "destination folder missing")
                    if action in ("move", "rename"):
                        gone, _gone = _outcome.file_absent(src)
                        if not gone:
                            return _outcome.failed(
                                f"{verb} {src} -> {dest} (empty folder) "
                                "— but the source folder is still "
                                "there.", "source folder still present")
                    return _outcome.verified(
                        f"{verb} {src} -> {dest} (empty folder).",
                        "empty folder verified at %s" % dest)
                except Exception as e:  # noqa: BLE001
                    return f"File operation '{action}' failed: {e}"

            collisions = [(s, d) for (s, d) in plan if d.exists()]
            if collisions and not overwrite:
                first = collisions[0][1]
                try:
                    first_size = first.stat().st_size
                    size_txt = f" ({first_size} bytes)"
                except OSError:
                    size_txt = ""
                return (
                    f"Refused: {action} would overwrite {len(collisions)} existing "
                    f"file(s) at the destination (first: {first}{size_txt}). "
                    "Nothing was changed. Ask again with overwrite=true to "
                    "replace them."
                )

            done = []       # (source, dest, bytes)
            failed = []     # (filename, short reason)
            replaced = 0
            rescued = []    # Paths overwrite victims were rescued to (H7)
            for s_file, d_file in plan:
                try:
                    d_file.parent.mkdir(parents=True, exist_ok=True)
                    was_existing = d_file.exists()
                    rescued_to = None
                    if was_existing and (d_file.is_file()
                                         or d_file.is_symlink()):
                        # v5.43 (H7): an approved overwrite no longer
                        # destroys the victim — rescue it FIRST
                        # (v5.48, programme item 27: into the central
                        # trash, see _rescue_overwrite_victim). If
                        # the rescue raises, this item fails below
                        # and the victim stays exactly where it was.
                        rescued_to = _rescue_overwrite_victim(d_file)
                        rescued.append(rescued_to)
                    # R1a: capture the source's evidence BEFORE the
                    # act — a move deletes the source, so after the
                    # move there is nothing left to compare against.
                    src_hash, _hash_reason = (None, "")
                    src_size = None
                    if action in ("move", "rename"):
                        src_hash, _hash_reason = _outcome.hash_file(s_file)
                        try:
                            src_size = s_file.stat().st_size
                        except OSError:
                            src_size = None
                    if action == "copy":
                        shutil.copy2(s_file, d_file)
                    else:
                        if was_existing and rescued_to is None:
                            # Approved replacement of something that is
                            # not a rescuable file (e.g. a folder in the
                            # way — unlink raises and the item fails, as
                            # before H7). Remove first so move behaves
                            # identically on Windows/POSIX.
                            d_file.unlink()
                        shutil.move(str(s_file), str(d_file))
                    # R1a: verify-after-act, per item. A copy is
                    # byte-compared against the surviving source; a
                    # move/rename is hash-compared against the
                    # pre-move capture (size-compared when the hash
                    # could not be read) AND the source must be gone.
                    # A verification failure is a failed item, never
                    # a success line — no rollback is attempted (a
                    # rollback is a second act with its own risks;
                    # the receipt names the item and the reason).
                    if action == "copy":
                        ok, evidence = _outcome.file_copied(s_file, d_file)
                    else:
                        if src_hash:
                            ok, evidence = _outcome.file_matches_hash(
                                d_file, src_hash)
                        elif src_size is not None:
                            try:
                                dst_size = d_file.stat().st_size
                            except OSError:
                                dst_size = -1
                            if dst_size == src_size:
                                ok, evidence = True, (
                                    "%d bytes at %s (hash unreadable "
                                    "before the move, size compared)"
                                    % (dst_size, d_file))
                            else:
                                ok, evidence = False, (
                                    "%s holds %d bytes, not the %d moved"
                                    % (d_file, dst_size, src_size))
                        else:
                            ok, evidence = True, (
                                "source hash and size unreadable before "
                                "the move; absence check only")
                        if ok:
                            gone, _gone = _outcome.file_absent(s_file)
                            if not gone:
                                ok, evidence = (
                                    False,
                                    "the source is still at %s after "
                                    "the move" % s_file)
                    if not ok:
                        failed.append((s_file.name,
                                       "verification failed: %s" % evidence))
                        continue
                    if was_existing:
                        replaced += 1
                    try:
                        size = d_file.stat().st_size
                    except OSError:
                        size = 0
                    done.append((s_file, d_file, size))
                except Exception as e:  # noqa: BLE001 — per-item: collect, don't dump
                    failed.append((s_file.name, str(e)[:120]))
            if action == "copy":
                for d in empty_dirs:
                    try:
                        (dest / d.relative_to(src)).mkdir(parents=True, exist_ok=True)
                    except Exception as e:  # noqa: BLE001
                        failed.append((d.name, str(e)[:120]))
            elif src_is_dir and not failed:
                # Every file moved out — drop the emptied source tree.
                shutil.rmtree(src, ignore_errors=True)

            if not src_is_dir and len(plan) == 1 and done and not failed:
                msg = f"{verb} {done[0][0]} -> {done[0][1]}"
                if replaced:
                    msg += " — replaced the existing file at the destination"
                    if rescued:
                        msg += (f" (the previous file was moved to "
                                f"{rescued[0]}, where it can be recovered)")
                # R1a: the single-file success line is now a verified
                # Outcome — the per-item loop above byte/hash-checked
                # it before recording it as done.
                return _outcome.verified(
                    msg + ".",
                    "%d bytes verified at %s" % (done[0][2], done[0][1]))
            total_bytes = sum(sz for _s, _d, sz in done)
            parts = [
                f"{verb} {len(done)} of {len(plan)} file(s) ({total_bytes} bytes) "
                f"from {src} to {dest}."
            ]
            if replaced:
                parts.append(f"Replaced {replaced} existing file(s).")
            if rescued:
                shown_rescue = ", ".join(str(p) for p in rescued[:3])
                more_rescue = (f" and {len(rescued) - 3} more"
                               if len(rescued) > 3 else "")
                parts.append(
                    f"The previous file(s) were moved to the central "
                    f"trash folder ({_trash_root()}) before being "
                    f"replaced, so they can be recovered: "
                    f"{shown_rescue}{more_rescue}.")
            if failed:
                shown_fail = "; ".join(f"{name} ({err})" for name, err in failed[:5])
                more = f"; and {len(failed) - 5} more" if len(failed) > 5 else ""
                parts.append(f"FAILED {len(failed)} item(s): {shown_fail}{more}.")
                if not done:
                    parts.append("Nothing was transferred.")
            # R1a: the folder summary is a verified Outcome only when
            # every item landed and verified; any failed item makes
            # the whole return a failed Outcome (the message already
            # names the failures — it never reads as a clean success).
            summary = " ".join(parts)
            if failed:
                return _outcome.failed(
                    summary,
                    "%d of %d item(s) failed" % (len(failed), len(plan)))
            return _outcome.verified(
                summary,
                "%d of %d file(s) verified at %s"
                % (len(done), len(plan), dest))

        if action == "delete":
            if not confirm:
                return (
                    f"Blocked by VYUK's trust layer: deleting '{src}' needs confirm=true. "
                    "Even then it is moved to the central trash folder, not permanently deleted."
                )
            if not src.exists():
                # F-F203: the delete did not happen, so the verdict
                # is failed, not a plain string the receipts would
                # read "succeeded". (The list/find/info not-found
                # branches above stay plain strings — they are
                # reads, and a read that finds nothing is not a
                # failed act.)
                return _outcome.failed(
                    f"Path not found: {src}", "source missing")
            # v5.48 (programme item 27): deletes land in the ONE
            # central trash root under a timestamped unique name,
            # with a manifest entry (see _move_to_central_trash) —
            # the per-folder _nova_trash scatter is retired. The
            # microsecond timestamp + counter unique-ify keeps the
            # v4.4 guarantee: the target provably does not exist,
            # so shutil.move can never nest this item inside an
            # earlier trashed one.
            moved_to, _trash_entry = _move_to_central_trash(
                src, kind="delete")
            # R1a: verify the delete — the source must be gone and
            # the trashed copy must exist. A trash move that raised
            # already returned as a failure above; this catches the
            # case where it returned quietly without landing.
            gone, _gone = _outcome.file_absent(src)
            if gone and Path(moved_to).exists():
                return _outcome.verified(
                    f"Moved to trash folder (recoverable): {moved_to}",
                    "source gone; trashed copy at %s" % moved_to)
            return _outcome.failed(
                f"Delete of {src} could not be confirmed: the source "
                "is still there, or the trashed copy is missing.",
                "source present" if not gone else "trashed copy missing")

        return f"Unhandled file action: {action}"
    except Exception as e:  # noqa: BLE001
        return f"File operation '{action}' failed: {e}"
