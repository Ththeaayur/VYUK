# SKILLS SPLIT PHASE A BATCH A21 INTENTIONAL: moved verbatim from
# core/skills_media.py (see that facade). Imports travel with the unit
# (plan SS2 P3); cross-part names imported with INTENTIONAL comments.

# INTENTIONAL (media split): shared names live in skills_media_helpers.
import hashlib
import os
from datetime import datetime
from pathlib import Path
from .skills_media_helpers import _NOT_WINDOWS, _existing_file, _human_size, _is_windows


_FOLDER_SKIP_DIRS = {
    "AppData", ".git", "node_modules", "__pycache__", "$Recycle.Bin",
    "System Volume Information", ".venv", "venv", ".cache",
}


_FOLDER_MAX_DEPTH = 12


def file_hash(path: str, algorithm: str = "sha256") -> str:
    """Compute a file's md5/sha1/sha256 hash (streamed, any file size)."""
    target = _existing_file(path)
    if isinstance(target, str):
        return target
    algo = (algorithm or "sha256").strip().lower()
    if algo not in ("md5", "sha1", "sha256"):
        return f"Unknown hash algorithm: '{algorithm}'. Use one of: md5, sha1, sha256."
    digest = hashlib.new(algo)
    try:
        with open(target, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as e:
        return f"Could not read '{target.name}' to hash it: {e}"
    return (f"{algo} of '{target.name}' ({_human_size(target.stat().st_size)}): "
            f"{digest.hexdigest()}")


def recent_files(limit: int = 15) -> str:
    """List the files Windows shows as recently used, newest first."""
    if not _is_windows():
        return ("The recent-files list is a Windows feature (the Recent folder) — "
                f"{_NOT_WINDOWS}")
    try:
        cap = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        cap = 15
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    recent_dir = Path(appdata) / "Microsoft" / "Windows" / "Recent"
    if not recent_dir.exists():
        return f"Windows has no Recent folder at {recent_dir} — nothing to list."
    try:
        entries = [p for p in recent_dir.iterdir() if p.is_file()]
    except OSError as e:
        return f"Could not read the Recent folder: {e}"
    if not entries:
        return "The Recent folder is empty — Windows hasn't tracked any recent files."
    entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    lines = []
    for entry in entries[:cap]:
        when = datetime.fromtimestamp(entry.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        name = entry.name[:-4] if entry.name.lower().endswith(".lnk") else entry.name
        lines.append(f"• {name} — {when}")
    return f"Recently used files (newest first, {len(lines)} of {len(entries)}):\n" + "\n".join(lines)


def folder_size(path: str, top: int = 5, by_extension: bool = False) -> str:
    """Total size of a folder, recursively, with its biggest subfolders.

    by_extension=true appends a "By type:" breakdown (the top 8 file
    extensions by bytes, with file counts), collected in the same
    single walk — the default output is unchanged.
    """
    try:
        top_n = max(1, min(int(top), 20))
    except (TypeError, ValueError):
        top_n = 5
    if isinstance(by_extension, str):
        want_types = by_extension.strip().lower() in ("true", "1", "yes")
    else:
        want_types = bool(by_extension)
    if not path or not str(path).strip():
        return "Folder size needs the path of a folder."
    base = Path(str(path)).expanduser()
    if not base.exists():
        return f"Folder not found: {base}"
    if not base.is_dir():
        return (f"That's a file, not a folder: {base} "
                f"({_human_size(base.stat().st_size)}).")
    total = 0
    file_count = 0
    unreadable_files = 0
    skipped_folder_count = 0
    skipped_folder_names: set[str] = set()
    depth_limited_folder_count = 0
    per_child: dict[str, int] = {}
    ext_bytes: dict[str, int] = {}
    ext_counts: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(base):
        current = Path(dirpath)
        depth = len(current.relative_to(base).parts) if current != base else 0
        policy_skipped = [d for d in dirnames if d in _FOLDER_SKIP_DIRS]
        skipped_folder_count += len(policy_skipped)
        skipped_folder_names.update(policy_skipped)
        dirnames[:] = [d for d in dirnames if d not in _FOLDER_SKIP_DIRS]
        if depth >= _FOLDER_MAX_DEPTH:
            depth_limited_folder_count += len(dirnames)
            dirnames[:] = []
        for filename in filenames:
            item = current / filename
            try:
                size = item.stat().st_size
            except OSError:
                unreadable_files += 1
                continue
            total += size
            file_count += 1
            if want_types:
                ext = Path(filename).suffix.lower() or "(no extension)"
                ext_bytes[ext] = ext_bytes.get(ext, 0) + size
                ext_counts[ext] = ext_counts.get(ext, 0) + 1
            if depth == 0:
                per_child["(files in the folder itself)"] = (
                    per_child.get("(files in the folder itself)", 0) + size)
            else:
                top = current.relative_to(base).parts[0]
                per_child[top] = per_child.get(top, 0) + size
    skipped_names = ", ".join(sorted(skipped_folder_names))
    policy_clause = f"{skipped_folder_count} folder(s) skipped by policy"
    if skipped_names:
        policy_clause += f" ({skipped_names})"
    exclusion_note = (
        f"{policy_clause}, {depth_limited_folder_count} folder(s) beyond "
        f"depth {_FOLDER_MAX_DEPTH} not counted"
    )
    if unreadable_files:
        exclusion_note += f", and {unreadable_files} file(s) couldn't be read"
    if file_count == 0:
        return (f"'{base.name}' contains no readable counted files (counted total 0 bytes; "
                f"{exclusion_note}).")
    biggest = sorted(per_child.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    lines = []
    for name, size in biggest:
        share = (size / total * 100) if total else 0.0
        lines.append(f"• {name}: {_human_size(size)} ({share:.0f}%)")
    if len(per_child) > top_n:
        lines.append(f"… and {len(per_child) - top_n} more "
                     f"subfolder(s) not shown "
                     f"(showing top {top_n} of {len(per_child)}).")
    result = (f"'{base}' totals {_human_size(total)} across {file_count} file(s); "
              f"{exclusion_note}.\n"
              "Biggest counted inside it:\n" + "\n".join(lines))
    if want_types and ext_bytes:
        top_ext = sorted(ext_bytes.items(), key=lambda kv: kv[1],
                         reverse=True)
        ext_lines = [f"• {ext}: {_human_size(size)} "
                     f"({ext_counts[ext]} file(s))"
                     for ext, size in top_ext[:8]]
        if len(top_ext) > 8:
            ext_lines.append(f"… and {len(top_ext) - 8} more type(s) "
                             "not shown.")
        result += "\nBy type:\n" + "\n".join(ext_lines)
    return result


_RENAME_CAP = 200


def batch_rename(folder: str, find: str, replace: str, confirm: bool = False,
                 cancel_token=None) -> str:
    """Rename every file in a folder whose name contains `find`.

    Preview by default: returns the old → new plan with collisions
    flagged and renames NOTHING. With confirm=true, the non-colliding
    renames are executed. Extensions are only changed when `find`
    itself matches inside the extension.

    B20: ``cancel_token`` is checked per rename — a Stop pressed
    mid-batch ends it promptly and returns the cancelled DICT
    (a string return would wrap as success). Renames are
    atomic per file (os.rename), so a stopped batch leaves
    renamed files renamed and the rest untouched — the message
    names how many ran.
    """
    if not folder or not str(folder).strip():
        return "Batch rename needs a folder path."
    base = Path(str(folder)).expanduser()
    if not base.exists():
        return f"Folder not found: {base}"
    if not base.is_dir():
        return f"That's a file, not a folder: {base}"
    needle = find or ""
    if not needle:
        return "Batch rename needs the text to find in the file names."
    swap = replace or ""
    try:
        files = sorted((p for p in base.iterdir() if p.is_file()),
                       key=lambda p: p.name.lower())
    except OSError as e:
        return f"Could not list the folder: {e}"
    plan: list[tuple[Path, str]] = []
    matched_count = 0
    for item in files:
        stem, ext = item.stem, item.suffix
        if needle in ext:
            new_name = item.name.replace(needle, swap)
        elif needle in stem:
            new_name = stem.replace(needle, swap) + ext
        else:
            continue
        if new_name and new_name != item.name:
            matched_count += 1
            if len(plan) < _RENAME_CAP:
                plan.append((item, new_name))
    if not plan:
        return f"No file names in {base} contain '{needle}' — nothing to rename."
    cap_note = ""
    if matched_count > len(plan):
        cap_note = (f" {matched_count} matched; planning the first {_RENAME_CAP} "
                    "— narrow the pattern to see and act on the rest.")
    # Collision detection: a target that already exists (and isn't the
    # file itself) or two files mapping to the same new name.
    targets: dict[str, Path] = {}
    collisions: dict[str, str] = {}
    for src, new_name in plan:
        key = new_name.lower()
        dest = base / new_name
        if key in targets:
            collisions[src.name] = f"'{new_name}' is also the target of '{targets[key].name}'"
        elif dest.exists() and dest.resolve() != src.resolve():
            collisions[src.name] = f"'{new_name}' already exists"
        else:
            targets[key] = src
    lines = []
    for src, new_name in plan:
        flag = f"  ⚠ SKIPPED — {collisions[src.name]}" if src.name in collisions else ""
        lines.append(f"• {src.name} → {new_name}{flag}")
    runnable = [(s, n) for s, n in plan if s.name not in collisions]
    if not confirm:
        return (f"Batch rename preview in {base} — {len(plan)} file(s) would be renamed"
                f" ({len(collisions)} blocked by name collisions).{cap_note} "
                "NOTHING was renamed.\n"
                + "\n".join(lines)
                + "\nCall again with confirm=true to rename the unblocked files.")
    renamed = 0
    errors = []
    for src, new_name in runnable:
        # B20: the per-rename yield point — a Stop pressed
        # mid-batch ends the loop here. Each os.rename is
        # atomic, so renamed files are renamed and the rest
        # are untouched; the message names how many ran.
        if cancel_token is not None and cancel_token.cancelled:
            from .funnel.tool_result import cancelled_result
            return cancelled_result(
                f"Renaming in {base} was cancelled at the user's "
                f"request after {renamed} file(s) renamed — the "
                "rest were not renamed.")
        try:
            os.rename(src, base / new_name)
            renamed += 1
        except OSError as e:
            errors.append(f"{src.name}: {e}")
    result = (f"Renamed {renamed} file(s) in {base}; "
              f"{len(collisions)} skipped (name collisions).{cap_note}")
    if errors:
        result += f" {len(errors)} failed: " + "; ".join(errors[:5])
    return result + "\n" + "\n".join(lines)


# SKILLS SPLIT PHASE A BATCH A21 INTENTIONAL: handlers keep their public
# module path (core.skills_media) so the override allow-list in
# core/funnel/tools_registry.py — which matches on handler.__module__ —
# keeps working with NO change to that file. The facade is the module's
# public identity; the impl modules are its private home.
file_hash.__module__ = "core.skills_media"
recent_files.__module__ = "core.skills_media"
folder_size.__module__ = "core.skills_media"
batch_rename.__module__ = "core.skills_media"
