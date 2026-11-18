"""Apps + startup tools of the System & Apps pack.

Phase A batch A31 part 1 (skills split): moved verbatim from
core/skills_system.py — winget_install, list_app_updates,
winget_upgrade_all, list_startup_programs and set_startup_program,
plus the winget/startup helpers only they use. Services-power and
env-disk tools stay in core/skills_system.py until batch 2.
Behaviour unchanged.
"""

import os
import re
import shutil
from pathlib import Path

# INTENTIONAL (split A31 part 1): the shared helpers live in
# skills_system_helpers (plan P3(c)); this module imports them
# rather than duplicating them.
from .skills_system_helpers import _NOT_WINDOWS, _is_windows, _run, _tail


# ---------------------------------------------------------------------------
# WINGET — search, install and update apps
# ---------------------------------------------------------------------------


def _parse_winget_table(text: str, columns: list[str]) -> list[dict]:
    """Parse a winget results table into row dicts keyed by `columns`.

    Winget pads its columns at fixed character offsets, so the reliable
    parse is: find the header line containing the column names, take each
    column's start offset from the header, then slice each data row by
    those offsets. Data rows end at the first blank line or summary line
    ("N upgrades available." / "The following packages..."). Pure
    function of the text — unit-tested with canned output.
    """
    lines = (text or "").splitlines()
    header_idx = None
    offsets: list[int] = []
    for i, line in enumerate(lines):
        if all(col in line for col in columns[:2]) and columns[0] in line:
            candidate = []
            ok = True
            for col in columns:
                pos = line.find(col)
                if pos < 0:
                    ok = False
                    break
                candidate.append(pos)
            if ok:
                header_idx = i
                offsets = candidate
                break
    if header_idx is None:
        return []
    rows: list[dict] = []
    for line in lines[header_idx + 1:]:
        if not line.strip():
            break
        if line.strip().startswith(("---", "The following", "No package")):
            if line.strip().startswith("---"):
                continue
            break
        if re.match(r"^\d+ upgrades? available", line.strip()):
            break
        if len(line) <= offsets[0]:
            continue
        row: dict[str, str] = {}
        for j, col in enumerate(columns):
            start = offsets[j]
            end = offsets[j + 1] if j + 1 < len(offsets) else None
            row[col] = line[start:end].strip() if end else line[start:].strip()
        if row.get(columns[0]) or row.get(columns[1] if len(columns) > 1 else ""):
            rows.append(row)
    return rows


_WINGET_SCHEMAS = {
    ("search", 4): ["Name", "Id", "Version", "Source"],
    ("search", 5): ["Name", "Id", "Version", "Match", "Source"],
    ("upgrade", 5): ["Name", "Id", "Version", "Available", "Source"],
}


def _parse_winget_table_struct(text: str, kind: str) -> list[dict] | None:
    """Parse a winget table by structure alone (locale-proof).

    Returns a list of row dicts keyed by the canonical English field
    names, [] for a readable table with no data rows, or None when no
    readable table is present at all — callers MUST treat None as
    "winget's answer couldn't be read", never as "no results". Pure
    function of the text — unit-tested with canned output, including
    a French-localized sample.
    """
    lines = (text or "").splitlines()
    sep_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (len(stripped) >= 20 and stripped.count("-") >= 20
                and set(stripped) <= {"-", " "}):
            sep_idx = i
            break
    if sep_idx is None or sep_idx == 0:
        return None
    header = lines[sep_idx - 1]
    # Column starts = the runs of label text separated by 2+ spaces.
    offsets = [m.start() for m in re.finditer(r"\S+(?: \S+)*", header)]
    fields = _WINGET_SCHEMAS.get((kind, len(offsets)))
    if fields is None:
        return None
    rows: list[dict] = []
    for line in lines[sep_idx + 1:]:
        if not line.strip():
            break
        row: dict[str, str] = {}
        for j, field in enumerate(fields):
            start = offsets[j]
            end = offsets[j + 1] if j + 1 < len(offsets) else None
            row[field] = line[start:end].strip() if end else line[start:].strip()
        # A data row must slice cleanly: a real package Id is a single
        # token with no spaces. Anything else is a footer/summary line
        # (or a table this parse can't trust) — the table is over.
        if not row.get("Id") or " " in row["Id"] or not row.get("Name"):
            break
        rows.append(row)
    return rows


def _winget_rows(text: str, kind: str, columns: list[str]) -> tuple[list[dict], bool]:
    """Best-effort winget rows: English header parse first (exact,
    proven), structural parse as the locale-proof fallback.

    Returns (rows, readable): readable is True when SOME parse read a
    table (even an empty one) — False means winget's answer couldn't
    be read and must be reported as such, never as an empty result."""
    rows = _parse_winget_table(text, columns)
    if rows:
        return rows, True
    struct = _parse_winget_table_struct(text, kind)
    if struct is None:
        return [], False
    return struct, True


def _winget_bare_message(text: str) -> bool:
    """True when winget's whole answer is a short bare message (1–2
    lines, no table). `winget upgrade` in ANY language answers a
    nothing-to-do run with one short line and a table otherwise, so a
    bare message after a successful run is winget's own 'nothing to
    report' — the only shape from which 'up to date' may be inferred
    without a parsed table."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return 0 < len(lines) <= 2 and len(text or "") <= 400


def _winget_search(query: str) -> tuple[list[dict], str]:
    """Run winget search; returns (rows, error_string)."""
    rc, out, err = _run(
        ["winget", "search", "--query", query, "--accept-source-agreements"],
        timeout=90,
    )
    if rc == -1:
        return [], err
    if "No package found" in out:
        return [], ""
    rows, _readable = _winget_rows(out, "search", ["Name", "Id", "Version", "Source"])
    if not rows and rc != 0:
        return [], _tail(err or out) or "winget search failed."
    # No rows with rc == 0: winget answered with its bare no-results
    # message (localized, so not matched by the English check above) —
    # that is a genuine "nothing found", in any language.
    for row in rows:
        # winget sometimes prints a "Match" column after Version; the
        # version itself is always the first token of that slice.
        tokens = row["Version"].split()
        row["Version"] = tokens[0] if tokens else ""
    return rows, ""


def _describe_package(row: dict) -> str:
    """'Name (package Id, from Source)' — how a resolved package is named."""
    return (f"'{row['Name']}' (package {row['Id']}"
            + (f", from {row['Source']}" if row.get("Source") else "") + ")")


def _winget_installed_row(package_id: str) -> dict | None:
    """The installed-list row for one package id (`winget list`), or
    None when the package doesn't appear or winget's list can't be
    read. Used to verify an install actually landed instead of
    trusting the installer's exit code (v5.9, m10)."""
    rc, out, _err = _run(
        ["winget", "list", "--id", package_id, "--exact",
         "--accept-source-agreements"],
        timeout=120,
    )
    if rc != 0:
        return None
    for kind, columns in (
        ("search", ["Name", "Id", "Version", "Source"]),
        # `winget list` grows an "Available" column when an update
        # exists for the package — same 5-column shape as `upgrade`.
        ("upgrade", ["Name", "Id", "Version", "Available", "Source"]),
    ):
        rows, _readable = _winget_rows(out, kind, columns)
        for row in rows:
            if row.get("Id", "").lower() == package_id.lower():
                return row
    return None


def _winget_pending_updates() -> tuple[list[dict] | None, bool]:
    """(pending-update rows, winget-says-none-pending) for the apps
    winget manages. Rows are None when winget's answer couldn't be
    read — callers must report that, never read it as "no updates"
    (the same posture as list_app_updates)."""
    rc, out, _err = _run(
        ["winget", "upgrade", "--include-unknown",
         "--accept-source-agreements"],
        timeout=120)
    if rc == -1:
        return None, False
    if "No available upgrade" in out or "No installed package" in out:
        return [], True
    rows, readable = _winget_rows(
        out, "upgrade", ["Name", "Id", "Version", "Available", "Source"])
    if rows:
        return rows, False
    if not readable and rc == 0 and _winget_bare_message(out):
        return [], True
    return None, False


def _engine_unavailable_prose(reason: str, no_winget: str) -> str | None:
    """The tool's own engine-unavailable prose for a wrapper
    ``engine_unavailable: ...`` reason, or None when the reason is
    not an engine failure. The wrapper decides the engine is
    unavailable; this only chooses WHICH of the tool's existing
    sentences says so (not-Windows vs winget missing). B-D8r."""
    if not reason.startswith("engine_unavailable:"):
        return None
    if not _is_windows():
        return f"winget is the Windows package manager — {_NOT_WINDOWS}"
    return no_winget


def winget_install(query: str, confirm: bool = False,
                   cancel_token=None) -> str:
    """Install an app with winget — exact matches only, never a fuzzy guess.

    B-D8r (2026-11-09): routes through core/desktop/app_management.py
    install() — the wrapper's resolve() refuses an ambiguous exact
    name (listing candidates) instead of taking the first match, so
    the AMBIGUOUS-EXACT-NAME hole (the old ``pick = row; break``)
    is closed in the voice path. The wrapper's read-back
    verification replaces this tool's own ``winget list --id`` check.

    The request is resolved against winget search FIRST (a read-only
    step, safe before confirmation): an exact package-id or name match
    is the only thing that may be installed. Anything else returns the
    candidate list and installs nothing, however the call was
    confirmed — the confirmation was given for the words the user
    said, not for a package they never saw named.

    B20: ``cancel_token`` ends the install promptly on Stop
    (the child is polled, not blocked on). A cancelled install
    returns the cancelled DICT, not a string — the funnel wraps
    a string return as success, so only the dict records the
    stop honestly. The package may be partly installed when
    stopped, so the message says to check the installed list
    before retrying (winget itself decides what a re-run does).
    """
    from .desktop import app_management as am

    wanted = (query or "").strip()
    if not wanted:
        return "Installing needs the name or package id of an app, e.g. 'VLC' or 'VideoLAN.VLC'."
    result = am.install(wanted, confirm=confirm, cancel_token=cancel_token)
    if result.get("state") == "cancelled":
        from .funnel.tool_result import cancelled_result
        return cancelled_result(result.get("reason") or "")
    if result.get("ok"):
        name = result.get("name") or ""
        package_id = result.get("package_id") or ""
        version = result.get("version") or ""
        return (f"Installed '{name}' (package {package_id}"
                + (f", version {version}" if version else "")
                + ") via winget — confirmed in winget's installed list.")
    reason = result.get("reason") or ""
    unavailable = _engine_unavailable_prose(
        reason,
        "winget (the Windows Package Manager) isn't available on this PC, "
        "so nothing can be installed this way. It normally ships with "
        "Windows 10/11 as 'App Installer', which this PC doesn't have.")
    if unavailable is not None:
        return unavailable
    if "needs confirm=true" in reason:
        # The wrapper's refusal names the package without its
        # source; this tool's own sentence keeps the
        # ", from <source>" the user has always heard.
        name = result.get("name") or ""
        package_id = result.get("package_id") or ""
        source = result.get("source") or ""
        return (f"Installing '{name}' (package {package_id}"
                + (f", from {source}" if source else "")
                + ") downloads software onto this PC and needs confirm=true. "
                "Ask again with confirmation.")
    return reason


def list_app_updates() -> str:
    """List apps that winget says have a newer version available.

    B-D8r (2026-11-09): routes through core/desktop/app_management.py
    list_updates() — the pending list is read by the wrapper, not by
    a winget call in this tool.

    "Up to date" is asserted ONLY on positive evidence: winget's own
    no-updates message (English marker, or its bare one-line answer in
    any language after a successful run) or a successfully parsed
    table. An answer that can't be parsed is reported as unreadable —
    never as "no updates" (v4.7: an empty parse is an error to
    report, never a fact to assert; winget localizes its headers, so
    the parse is structural, not keyed on English words)."""
    from .desktop import app_management as am

    result = am.list_updates()
    if not result.get("ok"):
        reason = result.get("reason") or ""
        unavailable = _engine_unavailable_prose(
            reason,
            "winget (the Windows Package Manager) isn't available on this PC, "
            "so update checks aren't possible.")
        if unavailable is not None:
            return unavailable
        return reason
    rows = result.get("updates") or []
    if not rows:
        return "Everything winget manages is up to date — no app updates available."
    cap = 40
    lines = []
    for row in rows[:cap]:
        lines.append(
            f"• {row['name']} — {row['version']} → {row['available']} ({row['id']})")
    suffix = f"\n... and {len(rows) - cap} more" if len(rows) > cap else ""
    return f"App updates available ({len(rows)}):\n" + "\n".join(lines) + suffix


def winget_upgrade_all(confirm: bool = False, cancel_token=None) -> str:
    """Update every app winget manages, in one go.

    B-D8r (2026-11-09): routes through core/desktop/app_management.py
    update_all() — the per-package before/after receipt is rendered
    here from the wrapper's updated/still_pending lists (the stuck
    apps are NAMED, as this tool has always reported them; the
    wrapper's summary only counts them).

    The receipt is per-package, not a blanket claim: the pending list
    is read before and after the run, and the result names what
    actually updated and what is still pending (v5.9, m10).

    B20: ``cancel_token`` ends the run promptly on Stop; a
    cancelled run returns the cancelled DICT (see
    winget_install — a string return would wrap as success).
    """
    from .desktop import app_management as am

    result = am.update_all(confirm=confirm, cancel_token=cancel_token)
    reason = result.get("reason") or ""
    if "needs confirm=true" in reason:
        return ("Updating every app downloads and installs new versions across "
                "this PC and needs confirm=true. Ask again with confirmation.")
    unavailable = _engine_unavailable_prose(
        reason,
        "winget (the Windows Package Manager) isn't available on this PC, "
        "so apps can't be updated this way.")
    if unavailable is not None:
        return unavailable
    if result.get("state") == "cancelled":
        # B20: stopped at the user's request — some apps may
        # have updated and others not; the pending list is
        # the honest measure, so the message names the check.
        from .funnel.tool_result import cancelled_result
        return cancelled_result(
            "The winget update run was cancelled at the user's "
            "request while it was still running — some apps may "
            "have updated and others not; run list_app_updates "
            "to see what remains.")
    if result.get("state") == "up_to_date":
        return (result.get("detail")
                or "Everything winget manages is already up to date — nothing was updated.")
    if result.get("detail") == "timed out after 15 minutes":
        return ("The update run timed out after 15 minutes — some apps may have "
                "updated and others not; run list_app_updates to see what remains.")
    updated = result.get("updated") or []
    still = result.get("still_pending") or []
    if result.get("ok") or reason.startswith(
            "winget's update run finished with errors — "):
        # The pre-run probe proved updates were pending, so the
        # nothing-to-do readings below don't apply — the outcome is
        # measured against the post-run list instead.
        before = len(updated) + len(still)
        summary = (f"{len(updated)} of {before} pending app update(s) "
                   "completed")
        if still:
            cap = 8
            names = ", ".join(row["name"] for row in still[:cap])
            if len(still) > cap:
                names += f", and {len(still) - cap} more"
            summary += (f"; still pending (did not complete): {names}")
        else:
            summary += "; no updates remain"
        summary += "."
        if result.get("ok"):
            return f"winget update run finished — {summary}"
        detail = result.get("detail") or ""
        return (f"winget's update run finished with errors — {summary} "
                f"winget said:\n{detail or 'no details were printed.'}")
    return reason


# ---------------------------------------------------------------------------
# STARTUP — what launches at sign-in, and switching it off reversibly
# ---------------------------------------------------------------------------


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


_DISABLED_KEY = r"Software\VYUK\DisabledStartup"


def _startup_folder(user: bool) -> Path:
    base = os.environ.get("APPDATA" if user else "ProgramData", "")
    return Path(base) / r"Microsoft\Windows\Start Menu\Programs\Startup"


def _disabled_folder() -> Path:
    base = os.environ.get("APPDATA", str(Path.home()))
    return Path(base) / "Nova" / "DisabledStartup"


def _read_run_values(root, path: str) -> list[tuple[str, str]]:
    """(name, command) pairs from a Run key; [] when the key is absent."""
    import winreg

    try:
        key = winreg.OpenKey(root, path)
    except OSError:
        return []
    entries: list[tuple[str, str]] = []
    with key:
        index = 0
        while True:
            try:
                name, value, _type = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            entries.append((name, str(value)))
    return entries


def _folder_items(folder: Path) -> list[str]:
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_file())


def _startup_inventory() -> list[dict]:
    """Every startup entry VYUK can see, tagged by source and state.
    Caller must be on Windows. Dicts: name, detail, source, enabled."""
    import winreg

    items: list[dict] = []
    for name, cmd in _read_run_values(winreg.HKEY_CURRENT_USER, _RUN_KEY):
        items.append({"name": name, "detail": cmd,
                      "source": "registry (this user)", "enabled": True})
    for name, cmd in _read_run_values(winreg.HKEY_LOCAL_MACHINE, _RUN_KEY):
        items.append({"name": name, "detail": cmd,
                      "source": "registry (all users)", "enabled": True})
    wow = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
    for name, cmd in _read_run_values(winreg.HKEY_LOCAL_MACHINE, wow):
        items.append({"name": name, "detail": cmd,
                      "source": "registry (all users, 32-bit)", "enabled": True})
    for name in _folder_items(_startup_folder(True)):
        items.append({"name": name, "detail": "shortcut/file in your Startup folder",
                      "source": "Startup folder (this user)", "enabled": True})
    for name in _folder_items(_startup_folder(False)):
        items.append({"name": name, "detail": "shortcut/file in the shared Startup folder",
                      "source": "Startup folder (all users)", "enabled": True})
    # Entries VYUK previously disabled (kept restorable).
    for name, cmd in _read_run_values(winreg.HKEY_CURRENT_USER, _DISABLED_KEY):
        items.append({"name": name, "detail": cmd,
                      "source": "registry (this user)", "enabled": False})
    for name in _folder_items(_disabled_folder()):
        items.append({"name": name, "detail": "shortcut/file kept by VYUK",
                      "source": "Startup folder (this user)", "enabled": False})
    items.sort(key=lambda i: (not i["enabled"], i["name"].lower()))
    return items


def list_startup_programs() -> str:
    """List everything that runs automatically when the user signs in."""
    if not _is_windows():
        return f"Startup programs are a Windows sign-in feature — {_NOT_WINDOWS}"
    try:
        items = _startup_inventory()
    except Exception as e:  # noqa: BLE001
        return f"Could not read the startup list: {e}"
    if not items:
        return "Nothing is set to run automatically at sign-in."
    lines = []
    for item in items:
        state = "" if item["enabled"] else " — DISABLED (by VYUK; can be re-enabled)"
        lines.append(f"• {item['name']} [{item['source']}] — {item['detail']}{state}")
    return (f"Startup programs ({len(items)}):\n" + "\n".join(lines)
            + "\nNote: a few system entries start from places this list doesn't "
              "cover (scheduled tasks, services).")


def set_startup_program(name: str, enabled: bool, confirm: bool = False) -> str:
    """Enable/disable one of *this user's* startup entries, reversibly.

    Disabling a registry entry moves its Run value into VYUK's backup
    key; disabling a Startup-folder item moves the file into VYUK's
    DisabledStartup folder. Enabling reverses the move exactly.
    All-users entries need an administrator and are refused politely.
    """
    verb = "Enabling" if enabled else "Disabling"
    if not confirm:
        return (f"{verb} the startup program '{name}' changes what runs at "
                "sign-in and needs confirm=true. Ask again with confirmation.")
    if not _is_windows():
        return f"Startup programs are a Windows sign-in feature — {_NOT_WINDOWS}"
    wanted = (name or "").strip().lower()
    if not wanted:
        return "Give the name of a startup program (see list_startup_programs)."
    import winreg

    try:
        if not enabled:
            # Registry (this user) → backup key.
            for entry_name, cmd in _read_run_values(winreg.HKEY_CURRENT_USER, _RUN_KEY):
                if entry_name.lower() == wanted:
                    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY) as run_key:
                        _data, vtype = winreg.QueryValueEx(run_key, entry_name)
                        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _DISABLED_KEY) as backup:
                            winreg.SetValueEx(backup, entry_name, 0, vtype, _data)
                        winreg.DeleteValue(run_key, entry_name)
                    return (f"'{entry_name}' will no longer start at sign-in. "
                            "VYUK kept its entry safely — ask to enable it to restore it exactly.")
            # Startup folder (this user) → VYUK's disabled folder.
            folder = _startup_folder(True)
            for filename in _folder_items(folder):
                if filename.lower() == wanted or Path(filename).stem.lower() == wanted:
                    target_dir = _disabled_folder()
                    target_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(folder / filename), str(target_dir / filename))
                    return (f"'{filename}' was moved out of your Startup folder, so it "
                            "won't run at sign-in. VYUK kept the file — ask to enable it to put it back.")
            # All-users entries → admin territory.
            admin_sources = (
                _read_run_values(winreg.HKEY_LOCAL_MACHINE, _RUN_KEY)
                + _read_run_values(winreg.HKEY_LOCAL_MACHINE,
                                   r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run")
            )
            if any(n.lower() == wanted for n, _c in admin_sources) or \
                    any(f.lower() == wanted for f in _folder_items(_startup_folder(False))):
                return (f"'{name}' starts for all users, so changing it needs an "
                        "administrator. VYUK runs as you — open Task Manager > Startup "
                        "apps as admin, or ask an admin to change it.")
            return (f"No startup program named '{name}' was found for your account. "
                    "Use list_startup_programs to see the exact names.")
        # Enabling: restore from VYUK's backups.
        for entry_name, cmd in _read_run_values(winreg.HKEY_CURRENT_USER, _DISABLED_KEY):
            if entry_name.lower() == wanted:
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _DISABLED_KEY) as backup:
                    _data, vtype = winreg.QueryValueEx(backup, entry_name)
                    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY) as run_key:
                        winreg.SetValueEx(run_key, entry_name, 0, vtype, _data)
                    winreg.DeleteValue(backup, entry_name)
                return f"'{entry_name}' will start at sign-in again — restored exactly as it was."
        disabled_dir = _disabled_folder()
        for filename in _folder_items(disabled_dir):
            if filename.lower() == wanted or Path(filename).stem.lower() == wanted:
                folder = _startup_folder(True)
                folder.mkdir(parents=True, exist_ok=True)
                shutil.move(str(disabled_dir / filename), str(folder / filename))
                return f"'{filename}' is back in your Startup folder and will run at sign-in."
        try:
            items = _startup_inventory()
        except Exception:  # noqa: BLE001
            items = []
        if any(i["name"].lower() == wanted and i["enabled"] for i in items):
            return f"'{name}' is already set to start at sign-in."
        return (f"VYUK has no disabled startup program named '{name}' to restore. "
                "Use list_startup_programs to see what exists.")
    except OSError as e:
        return f"Could not change the startup program '{name}': {e}"
    except Exception as e:  # noqa: BLE001
        return f"Could not change the startup program '{name}': {e}"
