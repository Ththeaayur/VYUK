"""Environment + registry + disk tools of the System & Apps pack.

Phase A batch A31 part 2 (skills split close-out): moved verbatim
from core/skills_system.py — env_var, registry_read, disk_health,
clean_temp_files, largest_files and find_duplicates, plus the
env/registry/scan helpers only they use. Behaviour unchanged.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

# INTENTIONAL (split A31 part 2): the shared helpers live in
# skills_system_helpers (plan P3(c)); this module imports them
# rather than duplicating them.
from .skills_system_helpers import (
    _NOT_WINDOWS, _human_size, _is_windows, _run, _tail)


# ---------------------------------------------------------------------------
# ENVIRONMENT + REGISTRY (user scope; registry is read-only by design)
# ---------------------------------------------------------------------------


_ENV_KEY = "Environment"


_SECRET_NAME_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH",
                      "CREDENTIAL", "PRIVATE")


def _env_value_shown(name: str, value: str) -> str:
    """How a variable's value may be shown (v5.30): values land in
    chat AND the activity log, so a secret-named variable (API keys,
    tokens, passwords…) is masked to its first/last 2 characters
    plus a length — a secret printed once is a secret leaked."""
    text = str(value)
    if any(part in (name or "").upper()
           for part in _SECRET_NAME_PARTS):
        if len(text) <= 4:
            return f"•••• (masked — {len(text)} characters)"
        return (f"{text[:2]}…{text[-2:]} "
                f"(masked — {len(text)} characters)")
    return text


def env_var(action: str = "list", name: str = "", value: str = "",
            confirm: bool = False) -> str:
    """List/get/set THIS USER's environment variables (HKCU\\Environment)."""
    act = (action or "list").strip().lower()
    if act not in ("list", "get", "set"):
        return f"Unknown environment action: '{action}'. Use one of: list, get, set."
    if act == "set" and not confirm:
        return ("Setting an environment variable changes what new programs see "
                "and needs confirm=true. Ask again with confirmation.")
    if not _is_windows():
        return f"User environment variables live in the Windows registry — {_NOT_WINDOWS}"
    import winreg

    try:
        if act == "list":
            entries: list[tuple[str, str]] = []
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ENV_KEY) as key:
                    index = 0
                    while True:
                        try:
                            vname, vdata, _t = winreg.EnumValue(key, index)
                        except OSError:
                            break
                        index += 1
                        entries.append((vname, str(vdata)))
            except OSError:
                pass
            if not entries:
                return "No user environment variables are set."
            cap = 50
            lines = []
            for n, v in sorted(entries)[:cap]:
                shown = _env_value_shown(n, v)
                if shown == v and len(v) > 120:
                    shown = f"{v[:120]}… [truncated: showing 120 of {len(v)} characters]"
                lines.append(f"• {n} = {shown}")
            suffix = f"\n... and {len(entries) - cap} more" if len(entries) > cap else ""
            return f"User environment variables ({len(entries)}):\n" + "\n".join(lines) + suffix
        var = (name or "").strip()
        if not var:
            return f"Give the variable name to {act}."
        if act == "get":
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ENV_KEY) as key:
                    data, _t = winreg.QueryValueEx(key, var)
                shown = _env_value_shown(var, str(data))
                if shown != str(data) and not confirm:
                    return (f"{var} = {shown}. The full value is "
                            "masked because the name suggests a "
                            "secret; ask again with confirm=true to "
                            "reveal it.")
                return f"{var} = {data}"
            except OSError:
                return f"The user environment variable '{var}' is not set."
        # set
        new_value = str(value or "")
        vtype = winreg.REG_EXPAND_SZ if "%" in new_value else winreg.REG_SZ
        old_value: str | None = None
        stored_value: str | None = None
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _ENV_KEY) as key:
            try:  # keep an existing variable's type (e.g. PATH stays expandable)
                old_raw, old_type = winreg.QueryValueEx(key, var)
                old_value = str(old_raw)
                vtype = old_type
            except OSError:
                pass
            winreg.SetValueEx(key, var, 0, vtype, new_value)
            # Read the value back before claiming anything: the write
            # counts only once the registry agrees it persisted, and
            # the receipt shows old -> new (v5.9, M3; the
            # fast_startup_control shape).
            try:
                stored_raw, _stored_type = winreg.QueryValueEx(key, var)
                stored_value = str(stored_raw)
            except OSError:
                stored_value = None
        try:  # tell running apps politely; harmless if it fails
            import ctypes

            result = ctypes.c_ulong(0)
            ctypes.windll.user32.SendMessageTimeoutW(
                0xFFFF, 0x001A, 0, "Environment", 2, 5000, ctypes.byref(result))
        except Exception:  # noqa: BLE001
            pass
        if stored_value is None:
            return (f"Tried to set the user environment variable '{var}', "
                    "but reading it back from the registry failed, so the "
                    "change couldn't be confirmed. Check it with "
                    "action='get' before relying on it.")
        if stored_value != new_value:
            return (f"The user environment variable '{var}' did not keep "
                    f"the value that was written: it now reads "
                    f"'{_env_value_shown(var, stored_value)}' instead of "
                    f"'{_env_value_shown(var, new_value)}'.")
        old_shown = (_env_value_shown(var, old_value)
                     if old_value is not None else "(not set before)")
        return (f"User environment variable set: {var}: {old_shown} -> "
                f"{_env_value_shown(var, new_value)} (read back from the registry to confirm). "
                "Programs pick it up the next time they start; VYUK and already-open "
                "apps keep the old value until restarted.")
    except OSError as e:
        return f"Could not {act} the environment variable: {e}"


_REG_ROOTS = {
    "HKCU": "HKEY_CURRENT_USER", "HKEY_CURRENT_USER": "HKEY_CURRENT_USER",
    "HKLM": "HKEY_LOCAL_MACHINE", "HKEY_LOCAL_MACHINE": "HKEY_LOCAL_MACHINE",
    "HKCR": "HKEY_CLASSES_ROOT", "HKEY_CLASSES_ROOT": "HKEY_CLASSES_ROOT",
}


_REG_TYPE_NAMES = {1: "text (REG_SZ)",
                   2: "expandable text (REG_EXPAND_SZ)",
                   3: "binary data (REG_BINARY)",
                   4: "32-bit number (REG_DWORD)",
                   7: "list of text lines (REG_MULTI_SZ)",
                   11: "64-bit number (REG_QWORD)"}


def _format_reg_value(data, vtype: int) -> str:
    if vtype == 3 and isinstance(data, (bytes, bytearray)):
        hexed = bytes(data[:32]).hex(" ")
        return f"{hexed}{' …' if len(data) > 32 else ''} ({len(data)} bytes)"
    if isinstance(data, (list, tuple)):
        return "; ".join(str(x) for x in data)[:200]
    text = str(data)
    if vtype == 2 and isinstance(data, str):
        # REG_EXPAND_SZ stores %VAR% references — show the
        # path they actually point at (#40), with the stored
        # form kept visible so nothing is hidden.
        expanded = re.sub(
            r"%([^%]+)%",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            data)
        if expanded != data:
            text = (f"{expanded} "
                    f"(stored with %VARIABLES% as '{data}')")
    if vtype in (4, 11) and isinstance(data, int):
        text = f"{data} (0x{data:x})"
    return text[:200]


def registry_read(path: str, value_name: str = "") -> str:
    """READ-ONLY registry lookup: one value, or a key's values listed."""
    if not _is_windows():
        return f"The registry only exists on Windows — {_NOT_WINDOWS}"
    raw = (path or "").strip()
    if not raw:
        return "Give a registry path, like 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion'."
    parts = [p for p in raw.replace("/", "\\").split("\\") if p != ""]
    if not parts or parts[0].upper() not in _REG_ROOTS:
        return ("The path must start with HKCU, HKLM or HKCR — for example "
                "'HKCU\\Software\\VYUK'.")
    import winreg

    root = getattr(winreg, _REG_ROOTS[parts[0].upper()])
    subkey = "\\".join(parts[1:])
    try:
        key = winreg.OpenKey(root, subkey, 0, winreg.KEY_READ)
    except OSError:
        return f"Registry key not found: {raw}"
    with key:
        wanted = (value_name or "").strip()
        if wanted:
            try:
                data, vtype = winreg.QueryValueEx(key, wanted)
            except OSError:
                return f"'{raw}' has no value named '{wanted}'."
            type_name = _REG_TYPE_NAMES.get(vtype, f"type {vtype}")
            return f"{raw} → {wanted} ({type_name}) = {_format_reg_value(data, vtype)}"
        try:
            subkey_count, value_count, _modified = winreg.QueryInfoKey(key)
        except OSError:
            subkey_count, value_count = 0, 0
        if value_count == 0:
            return (f"{raw} holds no values"
                    + (f" and has {subkey_count} subkey(s)." if subkey_count else "."))
        cap = 30
        lines = []
        for index in range(min(value_count, cap)):
            try:
                vname, vdata, vtype = winreg.EnumValue(key, index)
            except OSError:
                break
            type_name = _REG_TYPE_NAMES.get(vtype, f"type {vtype}")
            lines.append(f"• {vname or '(default)'} ({type_name}) = {_format_reg_value(vdata, vtype)}")
        suffix = f"\n... and {value_count - cap} more values" if value_count > cap else ""
        return (f"Values under {raw} ({value_count}"
                + (f", plus {subkey_count} subkey(s)" if subkey_count else "")
                + "):\n" + "\n".join(lines) + suffix)


# ---------------------------------------------------------------------------
# DISKS — physical health, temp cleanup, largest files, duplicates
# ---------------------------------------------------------------------------


def disk_health() -> str:
    """Physical disk model / type / health via PowerShell's Get-PhysicalDisk."""
    if not _is_windows():
        return f"Disk health reads Windows storage data — {_NOT_WINDOWS}"
    command = ("Get-PhysicalDisk | Select-Object FriendlyName, MediaType, "
               "HealthStatus, Size | ConvertTo-Json -Compress")
    rc, out, err = _run(
        ["powershell", "-NoProfile", "-Command", command], timeout=60)
    if rc != 0 or not out.strip():
        return ("Could not read disk health — the Windows storage cmdlet failed"
                + (f": {_tail(err or out)}" if (err or out).strip() else "."))
    try:
        parsed = json.loads(out)
    except ValueError:
        return "Could not read disk health — Windows returned data VYUK couldn't parse."
    disks = parsed if isinstance(parsed, list) else [parsed]
    if not disks:
        return "Windows reported no physical disks."
    lines = []
    for disk in disks:
        size = disk.get("Size") or 0
        size_text = _human_size(int(size)) if size else "unknown size"
        lines.append(
            f"• {disk.get('FriendlyName', 'disk')} — {disk.get('MediaType', 'unknown type')}, "
            f"{size_text}, health: {disk.get('HealthStatus', 'unknown')}")
    return "Physical disks:\n" + "\n".join(lines)


_TEMP_SKIP_AGE_SECONDS = 7 * 24 * 3600


def _temp_dir_paths() -> list[Path]:
    paths: list[Path] = []
    user_temp = os.environ.get("TEMP") or tempfile.gettempdir()
    paths.append(Path(user_temp))
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    paths.append(Path(system_root) / "Temp")
    unique: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _measure_temp_dirs(dirs: list[Path], now: float, skip_age_seconds: float | None = None) -> dict:
    """Walk temp dirs: totals overall and for files older than the threshold.
    Pure measurement — deletes nothing. Used by clean_temp_files and tests.
    skip_age_seconds defaults to _TEMP_SKIP_AGE_SECONDS (7 days); the
    (dirs, now) positional shape is unchanged for existing callers."""
    skip_age = _TEMP_SKIP_AGE_SECONDS if skip_age_seconds is None else skip_age_seconds
    stats = {"files": 0, "bytes": 0, "old_files": 0, "old_bytes": 0}
    for base in dirs:
        if not base.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for filename in filenames:
                try:
                    info = (Path(dirpath) / filename).stat()
                except OSError:
                    continue
                stats["files"] += 1
                stats["bytes"] += info.st_size
                if now - info.st_mtime > skip_age:
                    stats["old_files"] += 1
                    stats["old_bytes"] += info.st_size
    return stats


def clean_temp_files(confirm: bool = False, days: int = 7) -> str:
    """Report temp-folder junk, or (confirm=true) delete files older than `days` days."""
    if not _is_windows():
        return f"Temp cleanup here targets the Windows temp folders — {_NOT_WINDOWS}"
    import time

    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 7
    days = max(1, min(3650, days))
    skip_age = days * 24 * 3600
    dirs = _temp_dir_paths()
    now = time.time()
    stats = _measure_temp_dirs(dirs, now, skip_age)
    locations = " and ".join(str(d) for d in dirs)
    if not confirm:
        return (f"Temp folders ({locations}) hold {_human_size(stats['bytes'])} across "
                f"{stats['files']} file(s). Of that, {_human_size(stats['old_bytes'])} in "
                f"{stats['old_files']} file(s) is older than {days} "
                f"day{'s' if days != 1 else ''} and safe to clear. "
                "Nothing was deleted — ask again with confirm=true to clear the old files.")
    freed = 0
    removed = 0
    skipped = 0
    for base in dirs:
        if not base.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for filename in filenames:
                target = Path(dirpath) / filename
                try:
                    info = target.stat()
                except OSError:
                    continue
                if now - info.st_mtime <= skip_age:
                    continue
                try:
                    target.unlink()
                    freed += info.st_size
                    removed += 1
                except OSError:
                    skipped += 1  # locked or in use — left alone, by design
    return (f"Temp cleanup done: removed {removed} old file(s), freed "
            f"{_human_size(freed)}. {skipped} file(s) were in use or locked and "
            "were left alone.")


_SCAN_ROOTS = {
    "home": lambda: Path.home(),
    "desktop": lambda: Path.home() / "Desktop",
    "documents": lambda: Path.home() / "Documents",
    "downloads": lambda: Path.home() / "Downloads",
}


_SCAN_SKIP_DIRS = {
    "AppData", ".git", "node_modules", "__pycache__", "$Recycle.Bin",
    "System Volume Information", ".venv", "venv", ".cache",
}


_SCAN_MAX_DEPTH = 10


def _resolve_scan_root(root: str) -> tuple[Path | None, str]:
    key = (root or "home").strip().lower()
    if key in _SCAN_ROOTS:
        return _SCAN_ROOTS[key](), key
    return Path(root).expanduser(), str(root)


def _iter_scanned_files_with_stats(base: Path, cap: int) -> tuple[list[Path], dict]:
    """Files under base plus an honest account of the scan's limits.

    The returned stats distinguish reaching ``cap`` because another
    file existed beyond it from merely ending with exactly ``cap``
    files, and count both policy-skipped folders and folders that
    were not descended into because of the depth limit.
    """
    found: list[Path] = []
    stats = {
        "cap": cap,
        "cap_reached": False,
        "skipped_folder_count": 0,
        "skipped_folder_names": set(),
        "depth_limited_folder_count": 0,
    }
    for dirpath, dirnames, filenames in os.walk(base):
        current = Path(dirpath)
        depth = len(current.relative_to(base).parts) if current != base else 0
        policy_skipped = [d for d in dirnames if d in _SCAN_SKIP_DIRS]
        stats["skipped_folder_count"] += len(policy_skipped)
        stats["skipped_folder_names"].update(policy_skipped)
        dirnames[:] = [d for d in dirnames if d not in _SCAN_SKIP_DIRS]
        if depth >= _SCAN_MAX_DEPTH:
            stats["depth_limited_folder_count"] += len(dirnames)
            dirnames[:] = []
        for filename in filenames:
            if len(found) >= cap:
                stats["cap_reached"] = True
                stats["skipped_folder_names"] = sorted(stats["skipped_folder_names"])
                return found, stats
            found.append(current / filename)
    stats["skipped_folder_names"] = sorted(stats["skipped_folder_names"])
    return found, stats


def _iter_scanned_files(base: Path, cap: int) -> list[Path]:
    """Files under base (skip-set + depth cap), at most `cap` of them."""
    found, _stats = _iter_scanned_files_with_stats(base, cap)
    return found


def largest_files(root: str = "home", limit: int = 10) -> str:
    """The biggest files under a folder, biggest first."""
    base, label = _resolve_scan_root(root)
    if base is None or not base.exists():
        return f"That place doesn't exist on this PC: {root}"
    if not base.is_dir():
        return f"That's a file, not a folder to look in: {base}"
    try:
        cap = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        cap = 10
    scan_cap = 20000
    files, scan_stats = _iter_scanned_files_with_stats(base, scan_cap)
    sized: list[tuple[int, Path]] = []
    unreadable_files = 0
    for path in files:
        try:
            sized.append((path.stat().st_size, path))
        except OSError:
            unreadable_files += 1
            continue
    skipped_names = ", ".join(scan_stats["skipped_folder_names"])
    policy_clause = f"{scan_stats['skipped_folder_count']} folder(s) skipped by policy"
    if skipped_names:
        policy_clause += f" ({skipped_names})"
    if scan_stats["cap_reached"]:
        coverage = f"stopped at the {scan_cap:,}-file scan limit"
    else:
        coverage = "scan completed"
    scope = (
        f"Scan scope: {coverage}; scanned {len(files):,} file(s); {policy_clause}, "
        f"{scan_stats['depth_limited_folder_count']} folder(s) beyond depth "
        f"{_SCAN_MAX_DEPTH} not scanned"
    )
    if unreadable_files:
        scope += f"; {unreadable_files} file(s) couldn't be read"
    scope += "."
    if not sized:
        return f"No readable files found under {label}. {scope}"
    sized.sort(key=lambda item: item[0], reverse=True)
    lines = [f"• {_human_size(size)} — {path}" for size, path in sized[:cap]]
    return f"Largest files under {label}:\n{scope}\n" + "\n".join(lines)


def _hash_file(path: Path, limit: int | None = None) -> str:
    digest = hashlib.sha256()
    remaining = limit
    with path.open("rb") as handle:
        while True:
            want = 1024 * 256 if remaining is None else min(1024 * 256, remaining)
            if want <= 0:
                break
            chunk = handle.read(want)
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return digest.hexdigest()


def find_duplicates(folder: str, limit_groups: int = 10, min_size_kb: int = 0) -> str:
    """Find identical files in a folder: same size, then same content hash.
    min_size_kb (default 0 = no minimum) excludes smaller files from the
    scan; how many were excluded is stated in the result, mirroring the
    existing over-200 MB exclusion."""
    base = Path(folder or "").expanduser() if (folder or "").strip() else None
    if base is None:
        return "Finding duplicates needs a folder to look in."
    if not base.exists():
        return f"Folder not found: {base}"
    if not base.is_dir():
        return f"That's a file, not a folder: {base}"
    try:
        group_cap = max(1, min(int(limit_groups), 30))
    except (TypeError, ValueError):
        group_cap = 10
    try:
        min_kb = max(0, min(int(min_size_kb), 1_000_000))
    except (TypeError, ValueError):
        min_kb = 0
    min_bytes = min_kb * 1024
    scan_cap = 3000
    files = _iter_scanned_files(base, scan_cap)
    truncated = len(files) >= scan_cap
    sized: dict[int, list[Path]] = {}
    oversized_files = 0
    oversized_bytes = 0
    undersized_files = 0
    undersized_bytes = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if min_bytes and size < min_bytes:
            undersized_files += 1
            undersized_bytes += size
            continue
        if size > 200 * 1024 * 1024:
            oversized_files += 1
            oversized_bytes += size
        if size > 0:
            sized.setdefault(size, []).append(path)
    groups: list[tuple[int, int, list[Path]]] = []  # (wasted, size, paths)
    for size, paths in sized.items():
        if len(paths) < 2 or size > 200 * 1024 * 1024:
            continue
        # First pass: hash the first 64 KB. For files no bigger than
        # that, the partial hash already covers the whole file.
        partial: dict[str, list[Path]] = {}
        for path in paths:
            try:
                key = _hash_file(path, limit=64 * 1024)
            except OSError:
                continue
            partial.setdefault(key, []).append(path)
        for candidates in partial.values():
            if len(candidates) < 2:
                continue
            if size <= 64 * 1024:
                groups.append((size * (len(candidates) - 1), size, list(candidates)))
                continue
            # Second pass: full hash to confirm the big files truly match.
            full: dict[str, list[Path]] = {}
            for path in candidates:
                try:
                    key = _hash_file(path)
                except OSError:
                    continue
                full.setdefault(key, []).append(path)
            for same in full.values():
                if len(same) >= 2:
                    groups.append((size * (len(same) - 1), size, same))
    oversized_note = ""
    if oversized_files:
        oversized_note = (
            f" {oversized_files} file(s) over 200 MB ({_human_size(oversized_bytes)} "
            "total) were excluded from duplicate detection."
        )
    undersized_note = ""
    if undersized_files:
        undersized_note = (
            f" {undersized_files} file(s) under {min_kb} KB ({_human_size(undersized_bytes)} "
            "total) were excluded by the minimum size."
        )
    if not groups:
        note = f" (scan stopped at {scan_cap} files)" if truncated else ""
        return f"No duplicate files found under {base}{note}.{oversized_note}{undersized_note}"
    groups.sort(key=lambda g: g[0], reverse=True)
    total_wasted = sum(g[0] for g in groups)
    lines = [f"Found {len(groups)} duplicate group(s) under {base} — "
             f"{_human_size(total_wasted)} used by extra copies:"]
    for wasted, size, paths in groups[:group_cap]:
        lines.append(f"• {_human_size(size)} each, {len(paths)} copies "
                     f"({_human_size(wasted)} wasted):")
        for path in paths:
            lines.append(f"    {path}")
    if len(groups) > group_cap:
        lines.append(f"... and {len(groups) - group_cap} more group(s)")
    if truncated:
        lines.append(f"(Scan stopped at {scan_cap} files — narrow the folder to check the rest.)")
    if oversized_files:
        lines.append(
            f"Scope: {oversized_files} file(s) over 200 MB "
            f"({_human_size(oversized_bytes)} total) were excluded from duplicate detection.")
    if undersized_files:
        lines.append(
            f"Scope: {undersized_files} file(s) under {min_kb} KB "
            f"({_human_size(undersized_bytes)} total) were excluded by the minimum size.")
    lines.append("Nothing was deleted — use file_control to remove the copies you don't want.")
    return "\n".join(lines)
