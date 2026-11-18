"""Two-way folder sync (R9-4 mutagen PORT) — own code, no binary.

Mutagen (mutagen-io/mutagen) is the two-way, delta-based, watched
sync engine the round-9 scouting pointed at — but adopting its
official release binary is HARD-BLOCKED on licence, and the scout's
"MIT" was incomplete:

* The repo LICENSE file (read in full, master branch) says "all code
  in this repository is made available under the terms of the MIT
  License" — and then carves out: "All code that resides under the
  sspl directory is made available under the terms of the Server
  Side Public License", and "From Mutagen v0.17 onward, SSPL-licensed
  code is included by default in official Mutagen release builds."
  SSPL is copyleft, and copyleft material is excluded from the
  distributed product by the standing commercial rule — so the
  official v0.18.1 binary (which ships that SSPL code) cannot be
  adopted. The only clean adoption path is building mutagen from
  source with the --sspl flag omitted, which this lab cannot verify
  (no Go toolchain here) — that stays a parent decision, not this
  batch's.
* Weight, measured (not the scout's "~10 MB class"): the v0.18.1
  release tarballs are ~102 MB each, but that is mostly the agents
  bundle — the extracted mutagen binary itself is 13,193,368 B
  (~12.6 MB) and `mutagen version` prints 0.18.1. Weight did not
  decide this; the SSPL inclusion did.

What ships instead is the two-way sync the tree actually lacked,
as own stdlib code, on the tree's own one-way sync_folder patterns
(core/skills_files2_pdf_files.py): whole-file copy (no rsync-style
delta transfer — the docstring says so plainly), mtime+size fast
path with a sha256 tie-break, the same 5,000-file cap, symlinks
skipped never followed, overwrite victims rescued into the central
trash first (the same _rescue_overwrite_victim sync_folder uses),
copies verified by byte comparison and deletes verified by absence
(the R1a seam). What is NEW here is the second direction plus the
thing sync_folder has no concept of: CONFLICTS. A per-pair state
file (data/two_way_sync/<pair-hash>.json) records the last-synced
fingerprint of every file; a file that changed on BOTH sides since
the last sync, or changed on one side while deleted on the other,
is a conflict — it is reported by name, copied NEITHER way, and
left exactly as it was. Nothing in a conflict is ever silently
overwritten or deleted.

Scope, deliberately narrow:
- Local folder <-> folder only. Anything that looks remote
  ("://" or user@host:path) is refused. No network, no discovery.
- One-shot sync per call. NO watching, NO background thread, NO
  auto-start — continuous sync is a separate, larger step and is
  NOT built here.
- No delta transfer: whole files, like sync_folder.
- Mirror delete is OFF by default (sync_folder's rule: nothing is
  ever deleted unless mirror_delete=true) — and a mirror-deleted
  file is rescued into the central trash first, because it is the
  last copy; sync_folder's own mirror delete does not rescue, and
  this batch is deliberately stricter there.
- A dry run changes nothing on disk AND writes no state file — a
  dry run that established a baseline would make the next real run
  see "unchanged" everywhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import tool_outcome as _outcome

# Same bound as sync_folder's _SYNC_MAX_FILES
# (core/skills_files2_pdf_files.py:331): the tree caps a sync scan
# at 5,000 files a side, and this batch keeps that bound rather
# than inventing a new one.
_TWO_WAY_MAX_FILES = 5000

# Files bigger than this are never hashed for the state file — the
# size+mtime fast path decides them, and a copy is verified by byte
# comparison anyway. (There is no size cap on syncing itself.)
_STATE_HASH_CAP = 2 * 1024 * 1024 * 1024

_TREE_ROOT = Path(__file__).resolve().parent.parent
_STATE_DIR = _TREE_ROOT / "data" / "two_way_sync"

# A folder pair that looks remote. Local paths only: no network,
# no discovery, no SSH endpoint.
_REMOTE_RE = re.compile(r"^(?:[A-Za-z0-9_.-]+)@(?:[A-Za-z0-9_.-]+):")


def _state_dir() -> Path:
    return _STATE_DIR


def _pair_key(folder_a: str, folder_b: str) -> str:
    """Order-independent pair id: A<->B is the same pair as B<->A."""
    pair = sorted((folder_a, folder_b))
    return hashlib.sha256(
        "\x00".join(pair).encode("utf-8")).hexdigest()


def _state_path(folder_a: str, folder_b: str) -> Path:
    return _state_dir() / (_pair_key(folder_a, folder_b) + ".json")


def _load_state(folder_a: str, folder_b: str) -> dict | None:
    path = _state_path(folder_a, folder_b)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _content_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _fingerprint(path: Path) -> dict | None:
    """(size, mtime_ns, sha256) for a file — None if unreadable."""
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size > _STATE_HASH_CAP:
        # Too big to hash for the state: the size+mtime fast path
        # decides it, and copies are byte-verified anyway.
        return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                "sha256": ""}
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "sha256": _content_sha256(path) or ""}


def _matches_state(path: Path, entry: dict | None) -> bool:
    """True when `path` still holds the content the state recorded.

    Fast path first (size + mtime), sha256 only when they differ —
    a touch changes mtime without changing content, and that must
    not read as a change.
    """
    if not entry:
        return False
    try:
        stat = path.stat()
    except OSError:
        return False
    if (stat.st_size == entry.get("size")
            and stat.st_mtime_ns == entry.get("mtime_ns")):
        return True
    recorded = entry.get("sha256") or ""
    if not recorded:
        return False
    return _content_sha256(path) == recorded


def _scan(root: Path) -> tuple[dict[str, Path], int, bool]:
    """relpath -> file, skipping symlinks (never followed).

    The state dir itself is skipped when it sits inside a synced
    root — syncing the sync bookkeeping would corrupt it.
    Returns (files, symlinks_skipped, cap_hit).
    """
    files: dict[str, Path] = {}
    symlinks_skipped = 0
    cap_hit = False
    state_dir = _state_dir()
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        kept_dirs = []
        for dirname in dirnames:
            child = current / dirname
            if child.is_symlink():
                symlinks_skipped += 1
            elif child == state_dir or state_dir in child.parents:
                continue
            else:
                kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        for filename in filenames:
            if len(files) >= _TWO_WAY_MAX_FILES:
                cap_hit = True
                break
            item = current / filename
            if item.is_symlink():
                symlinks_skipped += 1
                continue
            files[item.relative_to(root).as_posix()] = item
        if cap_hit:
            dirnames[:] = []
    return files, symlinks_skipped, cap_hit


def _looks_remote(folder: str) -> bool:
    return "://" in folder or bool(_REMOTE_RE.match(folder.strip()))


def _rescue(victim: Path) -> Path:
    """Rescue an overwrite/delete victim into the central trash.

    Same helper sync_folder uses (core/desktop/control.py's
    _rescue_overwrite_victim, central trash root since v5.48).
    Imported lazily so the module stays importable without the
    desktop hub, and patchable in tests as two_way_sync._rescue.
    """
    from .desktop.control import _rescue_overwrite_victim

    return _rescue_overwrite_victim(victim)


def _remove_empty_dirs(root: Path) -> int:
    """Remove dirs emptied by mirror deletes, bottom-up, best-effort."""
    removed = 0
    for dirpath, dirnames, _filenames in os.walk(root, topdown=False):
        current = Path(dirpath)
        if current == root:
            continue
        try:
            if not os.listdir(current):
                current.rmdir()
                removed += 1
        except OSError:
            pass
    return removed


def sync_folders_two_way(folder_a: str = "", folder_b: str = "",
                         mirror_delete: bool = False,
                         dry_run: bool = False,
                         cancel_token=None) -> str:
    """Sync two local folders BOTH ways, with conflict detection.

    New on one side only -> copied to the other. Changed on one
    side only since the last sync -> copied over (the overwritten
    version is rescued into the central trash first). Changed on
    BOTH sides, or changed on one side while deleted on the other
    -> CONFLICT: reported by name, copied neither way, left exactly
    as it was. Deleted on one side while unchanged on the other ->
    mirror-deleted on the other side ONLY when mirror_delete=true
    (default false: reported, never touched), and rescued first
    because it is the last copy.

    The first sync for a pair has no baseline: files on one side
    only are copied across, files on both sides with different
    content are conflicts, and the state file is then written so
    the next run can tell a change from a deletion.

    A dry run changes nothing on disk AND writes no state file.
    A real run returns an Outcome: verified only when every copy
    byte-matched, every delete landed, and there were no conflicts
    — a conflict or an error makes it failed, with the message
    naming what did sync and what still needs the user's choice.
    """
    if not folder_a or not str(folder_a).strip():
        return "Two-way sync needs the first folder."
    if not folder_b or not str(folder_b).strip():
        return "Two-way sync needs the second folder."
    if _looks_remote(str(folder_a)) or _looks_remote(str(folder_b)):
        return ("Two-way sync is local folders only — a remote "
                "endpoint (a URL or user@host:path) is refused. "
                "Point it at two folders on this machine.")
    root_a = Path(str(folder_a)).expanduser()
    root_b = Path(str(folder_b)).expanduser()
    if not root_a.exists() or not root_a.is_dir():
        return f"The first folder was not found: {root_a}"
    if not root_b.exists() or not root_b.is_dir():
        return f"The second folder was not found: {root_b}"
    a_resolved = root_a.resolve()
    b_resolved = root_b.resolve()
    if a_resolved == b_resolved:
        return ("Both folders are the same folder — syncing a folder "
                "with itself would do nothing at best. Pick two "
                "different folders.")
    if a_resolved.is_relative_to(b_resolved) or \
            b_resolved.is_relative_to(a_resolved):
        return ("One folder is INSIDE the other — a two-way sync "
                "there would copy the copy into itself. Pick two "
                "separate folders.")

    state = _load_state(str(a_resolved), str(b_resolved))
    first_run = state is None
    state_files = (state or {}).get("files", {})

    files_a, skipped_a, cap_a = _scan(a_resolved)
    files_b, skipped_b, cap_b = _scan(b_resolved)

    copied_ab: list[str] = []   # A -> B
    copied_ba: list[str] = []   # B -> A
    deleted: list[str] = []
    would_delete: list[str] = []
    conflicts: list[str] = []
    up_to_date = 0
    errors: list[str] = []
    rescued: list[Path] = []
    copied_bytes = 0
    # relpath -> fingerprint to record in the new state. The rule:
    # agreed files (both sides, same content) are recorded; a
    # pending deletion and every open conflict KEEP their old entry,
    # so the next run re-detects them instead of mistaking them for
    # new files and silently resolving them by copying.
    new_state_files: dict[str, dict] = {}
    dirs_removed = 0

    def cancelled():
        return cancel_token is not None and cancel_token.cancelled

    def copy_one(src: Path, dst: Path, rel: str) -> bool:
        """Copy src -> dst with rescue + byte verification. True ok."""
        nonlocal copied_bytes
        if cancelled():
            return False
        try:
            if dst.is_dir() and not dst.is_symlink():
                errors.append(
                    f"{rel}: the other side has a folder there, "
                    "not a file — left alone")
                return False
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.is_file() or dst.is_symlink():
                    try:
                        rescued.append(_rescue(dst))
                    except Exception as e:  # noqa: BLE001 — per-item
                        errors.append(
                            f"{rel}: could not rescue the existing "
                            f"file before overwriting it ({e})")
                        return False
                shutil.copy2(src, dst)
                ok, evidence = _outcome.file_copied(src, dst)
                if not ok:
                    errors.append(
                        f"{rel}: verification failed: {evidence}")
                    return False
            copied_bytes += src.stat().st_size
            return True
        except OSError as e:
            errors.append(f"{rel}: {e}")
            return False

    def delete_one(victim: Path, rel: str) -> bool:
        """Mirror-delete one file, rescued first. True ok."""
        if cancelled():
            return False
        if not dry_run:
            try:
                rescued.append(_rescue(victim))
            except Exception as e:  # noqa: BLE001 — per-item
                errors.append(
                    f"{rel}: could not rescue the file before "
                    f"deleting it ({e})")
                return False
            # The rescue MOVED the file into the trash — it is
            # already gone from here. Verify the absence; do NOT
            # unlink again (the victim no longer exists at this
            # path, and unlinking would raise FileNotFoundError).
            gone, reason = _outcome.file_absent(victim)
            if not gone:
                errors.append(
                    f"delete {rel}: verification failed: {reason}")
                return False
        return True

    for rel in sorted(set(files_a) | set(files_b) | set(state_files)):
        if cancelled():
            from .funnel.tool_result import cancelled_result

            return cancelled_result(
                f"Two-way sync of {a_resolved} <-> {b_resolved} was "
                f"cancelled at the user's request after "
                f"{len(copied_ab) + len(copied_ba)} file(s) copied "
                f"and {len(deleted)} deleted — the rest did not run, "
                "and no state was written, so the next run starts "
                "from the same baseline.")
        in_a = rel in files_a
        in_b = rel in files_b
        entry = state_files.get(rel)
        if in_a and in_b:
            if first_run:
                hash_a = _content_sha256(files_a[rel])
                hash_b = _content_sha256(files_b[rel])
                if hash_a is not None and hash_a == hash_b:
                    up_to_date += 1
                    fp = _fingerprint(files_a[rel])
                    if fp:
                        new_state_files[rel] = fp
                else:
                    conflicts.append(
                        f"{rel}: both sides have it with different "
                        "content (first sync, no baseline — pick one "
                        "side's version, or rename one)")
                continue
            changed_a = not _matches_state(files_a[rel], entry)
            changed_b = not _matches_state(files_b[rel], entry)
            if not changed_a and not changed_b:
                up_to_date += 1
                if entry:
                    new_state_files[rel] = entry
            elif changed_a and not changed_b:
                if copy_one(files_a[rel], files_b[rel], rel):
                    copied_ab.append(rel)
                    fp = _fingerprint(files_a[rel])
                    if fp:
                        new_state_files[rel] = fp
            elif changed_b and not changed_a:
                if copy_one(files_b[rel], files_a[rel], rel):
                    copied_ba.append(rel)
                    fp = _fingerprint(files_b[rel])
                    if fp:
                        new_state_files[rel] = fp
            else:
                hash_a = _content_sha256(files_a[rel])
                hash_b = _content_sha256(files_b[rel])
                if hash_a is not None and hash_a == hash_b:
                    up_to_date += 1
                    fp = _fingerprint(files_a[rel])
                    if fp:
                        new_state_files[rel] = fp
                else:
                    conflicts.append(
                        f"{rel}: changed on BOTH sides since the "
                        "last sync — left exactly as it was on both "
                        "sides; pick one version, or rename one")
                    # Keep the old baseline: dropping it would make
                    # one side look "new" next run and silently
                    # resolve the conflict by copying.
                    if entry:
                        new_state_files[rel] = entry
        elif in_a and not in_b:
            if first_run:
                if copy_one(files_a[rel], b_resolved / rel, rel):
                    copied_ab.append(rel)
                    fp = _fingerprint(files_a[rel])
                    if fp:
                        new_state_files[rel] = fp
                continue
            if entry and _matches_state(files_a[rel], entry):
                # Unchanged on A, gone on B: B deleted it.
                if mirror_delete:
                    if delete_one(files_a[rel], rel):
                        deleted.append(rel)
                else:
                    would_delete.append(rel)
                    # Keep the old baseline: without it the next
                    # run would mistake this pending deletion for
                    # a new file on A and copy it back to B.
                    new_state_files[rel] = entry
            elif entry:
                conflicts.append(
                    f"{rel}: changed on the first folder but deleted "
                    "on the second — left exactly as it was; restore "
                    "it on the second side, or delete it on the first")
                # Keep the old baseline: dropping it would make the
                # changed side look "new" next run and resurrect the
                # file on the side that deleted it.
                new_state_files[rel] = entry
            else:
                # Never synced: new on A.
                if copy_one(files_a[rel], b_resolved / rel, rel):
                    copied_ab.append(rel)
                    fp = _fingerprint(files_a[rel])
                    if fp:
                        new_state_files[rel] = fp
        elif in_b and not in_a:
            if first_run:
                if copy_one(files_b[rel], a_resolved / rel, rel):
                    copied_ba.append(rel)
                    fp = _fingerprint(files_b[rel])
                    if fp:
                        new_state_files[rel] = fp
                continue
            if entry and _matches_state(files_b[rel], entry):
                # Unchanged on B, gone on A: A deleted it.
                if mirror_delete:
                    if delete_one(files_b[rel], rel):
                        deleted.append(rel)
                else:
                    would_delete.append(rel)
                    # Keep the old baseline (see the A-side twin
                    # above): a pending deletion is not a new file.
                    new_state_files[rel] = entry
            elif entry:
                conflicts.append(
                    f"{rel}: changed on the second folder but deleted "
                    "on the first — left exactly as it was; restore "
                    "it on the first side, or delete it on the second")
                # Keep the old baseline (see the A-side twin above).
                new_state_files[rel] = entry
            else:
                # Never synced: new on B.
                if copy_one(files_b[rel], a_resolved / rel, rel):
                    copied_ba.append(rel)
                    fp = _fingerprint(files_b[rel])
                    if fp:
                        new_state_files[rel] = fp
        # else: in the state, on neither side — deleted on both.
        # Nothing to do, and the state entry is dropped by the
        # rebuild below.

    if cancelled():
        # Cancel can land during the final item, after the loop's
        # per-iteration check — never report a cancelled run as a
        # completed sync, and never write state for it.
        from .funnel.tool_result import cancelled_result

        return cancelled_result(
            f"Two-way sync of {a_resolved} <-> {b_resolved} was "
            f"cancelled at the user's request after "
            f"{len(copied_ab) + len(copied_ba)} file(s) copied "
            f"and {len(deleted)} deleted — the rest did not run, "
            "and no state was written, so the next run starts "
            "from the same baseline.")

    if not dry_run:
        for root in (a_resolved, b_resolved):
            dirs_removed += _remove_empty_dirs(root)
        state_path = _state_path(str(a_resolved), str(b_resolved))
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({
                "folders": sorted((str(a_resolved), str(b_resolved))),
                "files": new_state_files,
                "last_sync": datetime.now(timezone.utc).isoformat(),
                "last_conflicts": conflicts,
                "last_summary": {
                    "copied_a_to_b": len(copied_ab),
                    "copied_b_to_a": len(copied_ba),
                    "deleted": len(deleted),
                    "up_to_date": up_to_date,
                    "errors": len(errors),
                },
            }, indent=2, sort_keys=True), encoding="utf-8")
        except OSError as e:
            errors.append(f"could not write the sync state file ({e})")

    verb = "Would sync" if dry_run else "Synced"
    lines = [
        f"{'Dry run — nothing was copied or deleted. ' if dry_run else ''}"
        f"{verb} {a_resolved} <-> {b_resolved}: "
        f"{len(copied_ab)} file(s) {'would go' if dry_run else 'copied'} "
        f"first -> second, {len(copied_ba)} {'would go' if dry_run else 'copied'} "
        f"second -> first, {up_to_date} already in step."
    ]
    if first_run:
        lines.append("First sync for this pair — the baseline is "
                     f"{'NOT written (dry run)' if dry_run else 'now recorded'}, "
                     "so the next run can tell a change from a deletion.")
    if copied_ab:
        shown = ", ".join(copied_ab[:5])
        more = (f" (+{len(copied_ab) - 5} more)"
                if len(copied_ab) > 5 else "")
        lines.append(f"First -> second: {shown}{more}")
    if copied_ba:
        shown = ", ".join(copied_ba[:5])
        more = (f" (+{len(copied_ba) - 5} more)"
                if len(copied_ba) > 5 else "")
        lines.append(f"Second -> first: {shown}{more}")
    lines.append(f"Total data {'to copy' if dry_run else 'copied'}: "
                 f"{copied_bytes / 1024:.1f} KB.")
    if deleted:
        lines.append(
            f"Mirror delete ON: {'would remove' if dry_run else 'removed'} "
            f"{len(deleted)} file(s) deleted on one side and unchanged "
            f"on the other"
            + (f" and {dirs_removed} emptied folder(s)."
               if dirs_removed else "."))
    if would_delete:
        lines.append(
            f"{len(would_delete)} file(s) were deleted on one side and "
            "are unchanged on the other — left UNTOUCHED, because "
            "mirror_delete is off. Nothing is ever deleted unless "
            "mirror_delete=true.")
    if rescued:
        shown = ", ".join(str(p) for p in rescued[:3])
        more = (f" (+{len(rescued) - 3} more)"
                if len(rescued) > 3 else "")
        lines.append(
            f"{len(rescued)} existing file(s) were replaced or deleted "
            "— the previous version(s) were moved to the central "
            f"trash folder first, so they can be recovered: {shown}{more}.")
    if conflicts:
        shown = "; ".join(conflicts[:3])
        more = (f" (+{len(conflicts) - 3} more conflict(s))"
                if len(conflicts) > 3 else "")
        lines.append(
            f"{len(conflicts)} CONFLICT(S) — nothing was overwritten "
            f"or deleted for these, they were left exactly as they "
            f"were: {shown}{more}")
    skipped = skipped_a + skipped_b
    if skipped:
        lines.append(f"{skipped} symlink(s) were skipped, not followed.")
    if cap_a or cap_b:
        lines.append(
            "The scan stopped at the 5,000-file cap on one side — "
            "files past it were not compared or copied.")
    if errors:
        lines.append(f"{len(errors)} error(s): " + "; ".join(errors[:3]))
    result = "\n".join(lines)
    if dry_run:
        return result  # a dry run changes nothing — plain string
    if errors or conflicts:
        return _outcome.failed(
            result,
            "%d error(s) and %d conflict(s) — the folders are not "
            "fully in step" % (len(errors), len(conflicts)))
    return _outcome.verified(
        result,
        "%d file(s) copied and verified, %d already in step"
        % (len(copied_ab) + len(copied_ba), up_to_date))


def two_way_sync_status() -> str:
    """List every known two-way sync pair and its last run.

    Pure read of the state files in data/two_way_sync/ — it never
    touches the synced folders themselves.
    """
    state_dir = _state_dir()
    if not state_dir.is_dir():
        return ("No two-way sync pairs yet — run sync_folders_two_way "
                "once on two folders to start one.")
    pairs = []
    for path in sorted(state_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        folders = data.get("folders", [])
        summary = data.get("last_summary", {})
        conflicts = data.get("last_conflicts", [])
        pairs.append(
            f"{' <-> '.join(folders) or path.name}: "
            f"{len(data.get('files', {}))} file(s) in step, last sync "
            f"{data.get('last_sync', 'unknown')}, last run copied "
            f"{summary.get('copied_a_to_b', 0)} + "
            f"{summary.get('copied_b_to_a', 0)}, deleted "
            f"{summary.get('deleted', 0)}"
            + (f", {len(conflicts)} conflict(s) still open: "
               + ", ".join(conflicts[:3])
               if conflicts else ", no conflicts open"))
    if not pairs:
        return ("No two-way sync pairs yet — run sync_folders_two_way "
                "once on two folders to start one.")
    return ("Two-way sync pairs:\n" + "\n".join(pairs))
