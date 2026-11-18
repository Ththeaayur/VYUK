# Split from core/skills_plus.py (Phase A batch A12): moved verbatim.
from pathlib import Path
import fnmatch
import os
import shlex
import shutil
import subprocess
import time

# INTENTIONAL: names shared by several impl groups live in the
# helpers module (plan section 2 P3(c)); imported here so each
# moved function resolves its bare names in this module.
from .skills_plus_helpers import _NOT_WINDOWS, _human_size, _is_windows

# ---------------------------------------------------------------------------
# APPS — what's installed, and removing an app through its own uninstaller
# ---------------------------------------------------------------------------

_UNINSTALL_KEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)


def _installed_apps() -> list[dict]:
    """Read the Windows Uninstall registry keys (HKLM 64/32-bit + HKCU).

    Returns dicts: name, version, publisher, uninstall (string),
    quiet_uninstall (string). Update rollups (ParentKeyName) and
    hidden system components are skipped — they aren't apps a user
    means when they say "uninstall X". Caller must be on Windows.
    """
    import winreg

    apps: list[dict] = []
    seen: set[str] = set()
    roots = [(winreg.HKEY_LOCAL_MACHINE, path) for path in _UNINSTALL_KEYS]
    roots.append((winreg.HKEY_CURRENT_USER, _UNINSTALL_KEYS[0]))
    for root, path in roots:
        try:
            key = winreg.OpenKey(root, path)
        except OSError:
            continue
        with key:
            index = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                try:
                    sub = winreg.OpenKey(key, sub_name)
                except OSError:
                    continue
                with sub:
                    def _value(name):
                        try:
                            val, _ = winreg.QueryValueEx(sub, name)
                            return val
                        except OSError:
                            return None

                    display = _value("DisplayName")
                    if not display or not str(display).strip():
                        continue
                    if _value("ParentKeyName") is not None:
                        continue  # an update/patch entry, not the app
                    if _value("SystemComponent") == 1:
                        continue  # hidden OS component
                    display = str(display).strip()
                    dedupe = display.lower()
                    if dedupe in seen:
                        continue
                    seen.add(dedupe)
                    apps.append({
                        "name": display,
                        "version": str(_value("DisplayVersion") or "").strip(),
                        "publisher": str(_value("Publisher") or "").strip(),
                        "uninstall": str(_value("UninstallString") or "").strip(),
                        "quiet_uninstall": str(_value("QuietUninstallString") or "").strip(),
                    })
    apps.sort(key=lambda a: a["name"].lower())
    return apps


def list_installed_apps(search: str = "", limit: int = 30) -> str:
    """List installed desktop apps from winget's installed list.

    B-D8r (2026-11-09): routes through core/desktop/app_management.py
    list_installed() — the registry read is replaced by winget's own
    list, so the rows are the apps winget manages (name, version,
    package id). Winget reports no publisher, so the "by <publisher>"
    tail is gone; the package id takes its place (it is what
    uninstall_app needs). Search, limit, sorting and the
    "Showing X of N" tail are unchanged.
    """
    from .desktop import app_management as am
    from .skills_system_apps_startup import _engine_unavailable_prose

    result = am.list_installed()
    if not result.get("ok"):
        reason = result.get("reason") or ""
        unavailable = _engine_unavailable_prose(
            reason,
            "winget (the Windows Package Manager) isn't available on this PC, "
            "so the installed-apps list can't be read.")
        if unavailable is not None:
            return unavailable
        return reason
    apps = result.get("apps") or []
    apps = sorted(apps, key=lambda a: (a.get("name") or "").lower())
    needle = (search or "").strip().lower()
    if needle:
        apps = [a for a in apps if needle in (a.get("name") or "").lower()]
    if not apps:
        if needle:
            return f"No installed app has '{search}' in its name."
        return "No installed apps were found."
    try:
        cap = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        cap = 30
    shown = apps[:cap]
    lines = []
    for a in shown:
        bits = [a.get("name") or ""]
        if a.get("version"):
            bits.append(f"v{a['version']}")
        line = "• " + " — ".join(bits)
        if a.get("id"):
            line += f" (package {a['id']})"
        lines.append(line)
    suffix = f"\nShowing {len(shown)} of {len(apps)} — narrow it with a search word or raise the limit." if len(apps) > cap else ""
    return f"Installed apps ({len(apps)}):\n" + "\n".join(lines) + suffix


# --- Gaps batch (P1-12): uninstall argv parsing + validation ---
# A registry uninstall string is untrusted input: it used to be
# handed to a subprocess shell, so a string like
# `... & malicious` would have executed the second command.
# It is now parsed into argv, validated, and launched with
# shell=False — no shell ever interprets it.

# Executables that are shells/interpreters, never uninstallers.
# A registry string pointing at one of these is a smuggling
# vector and is refused outright.
_UNINSTALL_SHELL_EXECUTABLES = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "wscript.exe",
    "cscript.exe", "mshta.exe", "rundll32.exe", "regsvr32.exe",
    "bash.exe", "sh.exe",
}

# Metacharacters that only make sense to a shell. They are
# refused OUTSIDE double quotes only: a quoted path like
# "C:\Apps\Rock & Roll\uninstall.exe" is data, and with
# shell=False it is never interpreted — refusing it would break
# legitimate uninstallers.
_UNINSTALL_META_OUTSIDE_QUOTES = ("&", "|", ";", "<", ">", "^", "`", "$(")


def _strip_quoted_spans(command: str) -> str:
    """Return the command with double-quoted spans removed, so
    metacharacter checks see only unquoted text."""
    out = []
    in_quotes = False
    for ch in command:
        if ch == '"':
            in_quotes = not in_quotes
            continue
        if not in_quotes:
            out.append(ch)
    return "".join(out)


def parse_uninstall_command(command: str) -> list:
    """Parse a registry uninstall string into an argv list.

    Raises ValueError on: an empty string, or a shell
    metacharacter outside quotes. Quoted spans are respected
    (Windows double-quote rules), so paths with spaces and
    with & inside quotes parse as single tokens.
    """
    if not command or not str(command).strip():
        raise ValueError("empty uninstall command")
    raw = str(command)
    unquoted = _strip_quoted_spans(raw)
    for meta in _UNINSTALL_META_OUTSIDE_QUOTES:
        if meta in unquoted:
            raise ValueError(
                f"uninstall command refused: shell metacharacter "
                f"{meta!r} outside quotes")
    argv = []
    for token in shlex.split(raw, posix=False):
        if len(token) >= 2 and token.startswith('"') and token.endswith('"'):
            argv.append(token[1:-1])
        else:
            argv.append(token)
    if not argv or not argv[0]:
        raise ValueError("uninstall command has no executable")
    return argv


def validate_uninstall_target(argv: list) -> str:
    """Validate a parsed uninstall argv. Returns the exact-target
    display string (Windows-correct quoting via list2cmdline).

    Refuses: a shell/interpreter executable; a path executable
    that does not exist as a file; a bare name that is neither
    on PATH nor the well-known msiexec (Windows' own installer
    service — the canonical `msiexec /X{GUID}` uninstall form).
    """
    exe = argv[0]
    base = exe.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    if base in _UNINSTALL_SHELL_EXECUTABLES:
        raise ValueError(
            f"uninstall executable refused: {base!r} is a "
            f"shell/interpreter, not an uninstaller")
    if "\\" in exe or "/" in exe:
        if not os.path.isfile(exe):
            raise ValueError(f"uninstall executable not found: {exe!r}")
    elif base not in ("msiexec.exe", "msiexec"):
        if shutil.which(exe) is None:
            raise ValueError(
                f"uninstall executable not found on PATH: {exe!r}")
    return subprocess.list2cmdline(argv)


def preview_uninstall_target(command: str) -> dict:
    """Parse + validate a registry uninstall string WITHOUT
    launching anything. Returns {"ok": True, "argv": [...],
    "display": "<exact target>"} or {"ok": False, "reason": ...}.
    Callers show `display` for confirmation before running."""
    try:
        argv = parse_uninstall_command(command)
        display = validate_uninstall_target(argv)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "argv": argv, "display": display}


def uninstall_app(name: str, confirm: bool = False, cancel_token=None) -> str:
    """Uninstall an app — winget first, the registry uninstaller only as a narrow fallback.

    B-D8r (2026-11-09): routes through core/desktop/app_management.py —
    resolve() turns the name into ONE package id (an ambiguous exact
    name refuses, listing candidates), then remove() runs
    ``winget uninstall --id <id> --exact`` under the wrapper's
    deny-list (winget itself + runtimes/frameworks refused) and
    verifies the package is GONE from winget's installed list. The
    removal is therefore reported as removed (verified), not merely
    "launched" as the old registry path could only say.

    FALLBACK (exactly when it fires): only when the wrapper resolved
    the name to one package AND remove() reports that package is NOT
    in winget's installed list (detail "not installed") — i.e. winget
    knows the package but does not manage this install — AND the
    registry has exactly one matching app with a usable uninstall
    string. Then the old registry path runs inline (argv parse +
    validation, shell=False launch, "launched" receipt — the P1-12
    pin still holds: preview_uninstall_target + shell=False are in
    this function's source). The fallback never fires for a denied
    id (the deny-list is checked before the installed-list check),
    never for an ambiguous name (resolve refuses first), and never
    for a search error. An app winget's catalogue does not list at
    all is refused with a pointer to Windows Settings > Apps.

    B20: ``cancel_token`` ends the removal promptly on Stop; a
    cancelled removal returns the cancelled DICT, not a string.
    """
    if not confirm:
        return (f"Uninstalling '{name}' removes the app from this PC and needs "
                "confirm=true. Ask again with confirmation.")
    wanted = (name or "").strip()
    if not wanted:
        return "Uninstall needs the name of an app (see list_installed_apps)."
    from .desktop import app_management as am
    from .skills_system_apps_startup import _engine_unavailable_prose

    resolved = am.resolve(wanted)
    if not resolved.get("ok"):
        reason = resolved.get("reason") or ""
        unavailable = _engine_unavailable_prose(
            reason,
            "winget (the Windows Package Manager) isn't available on this PC, "
            "so apps can't be removed this way.")
        if unavailable is not None:
            return unavailable
        if "different packages" in reason:
            # The wrapper's refusal says "nothing was installed" —
            # install-flavoured prose; the uninstall wording names
            # the same refusal with the right verb.
            candidates = resolved.get("candidates") or []
            cap = 8
            lines = [f"• {c['name']} (package {c['id']}" +
                     (f", from {c['source']}" if c.get("source") else "") + ")"
                     for c in candidates[:cap]]
            suffix = (f"\n... and {len(candidates) - cap} more"
                      if len(candidates) > cap else "")
            return (f"'{wanted}' is the exact name of {len(candidates)} "
                    "different packages, so nothing was removed — "
                    "removing the first would be a guess. Pick one "
                    "by its package id:\n" + "\n".join(lines) + suffix)
        if reason.startswith("No exact match"):
            candidates = resolved.get("candidates") or []
            cap = 8
            lines = [f"• {c['name']} (package {c['id']}" +
                     (f", from {c['source']}" if c.get("source") else "") + ")"
                     for c in candidates[:cap]]
            suffix = (f"\n... and {len(candidates) - cap} more"
                      if len(candidates) > cap else "")
            return (f"No exact match for '{wanted}', so nothing was removed. "
                    "Did you mean one of these?\n" + "\n".join(lines) + suffix
                    + "\nAsk again with the exact name or package id to remove it."
                    + "\nIf the app isn't listed by winget, remove it in Windows Settings > Apps.")
        return reason
    package = resolved["package"]
    package_id = package["id"]
    package_name = package["name"]
    result = am.remove(package_id, confirm=confirm, cancel_token=cancel_token)
    if result.get("ok"):
        version = result.get("version") or ""
        return (f"Removed '{result.get('name') or package_name}' (package {package_id}"
                + (f", version {version}" if version else "")
                + ") — confirmed gone from winget's installed list.")
    if result.get("state") == "cancelled":
        from .funnel.tool_result import cancelled_result
        return cancelled_result(result.get("reason") or "")
    reason = result.get("reason") or ""
    if "needs confirm=true" in reason:
        # Defensive: confirm was already checked above, so the
        # wrapper should never refuse here — but if it does, this
        # tool's own sentence names the app, not the id.
        return (f"Uninstalling '{package_name}' removes the app from this PC and needs "
                "confirm=true. Ask again with confirmation.")
    unavailable = _engine_unavailable_prose(
        reason,
        "winget (the Windows Package Manager) isn't available on this PC, "
        "so apps can't be removed this way.")
    if unavailable is not None:
        return unavailable
    if result.get("detail") == "not installed":
        # FALLBACK (see the docstring): winget knows the package but
        # does not manage this install — the registry may still have
        # an uninstaller for it. The registry is read here only; a
        # registry that cannot be read simply means no fallback (the
        # wrapper's "not installed" verdict stands).
        try:
            apps = _installed_apps()
        except Exception:  # noqa: BLE001
            apps = []
        lowered = package_name.lower()
        exact = [a for a in apps if a["name"].lower() == lowered]
        matches = exact or [a for a in apps if lowered in a["name"].lower()]
        if not matches:
            return reason
        if len(matches) > 1:
            names = ", ".join(a["name"] for a in matches[:10])
            return (f"'{package_name}' matches more than one app ({names}). "
                    "Say the exact name so the wrong app is never removed.")
        app = matches[0]
        quiet = app["quiet_uninstall"]
        command = quiet or app["uninstall"]
        if not command:
            return (f"'{app['name']}' has no uninstaller on record in the registry, "
                    "so it can't be removed this way — it may be a portable app "
                    "(just delete its folder) or a Store app (use Windows Settings).")
        if not quiet and "msiexec" in command.lower() and "/i" in command.lower():
            # An MSI "modify" string (/I{GUID}) becomes a real uninstall (/X{GUID}).
            command = command.replace("/I{", "/X{").replace("/i{", "/X{")
        # Gaps batch (P1-12): a registry string is NEVER handed to a
        # shell. Parse it into argv, validate the executable, and
        # launch the list with shell=False. The exact target is shown
        # in the launch receipt below (and preview_uninstall_target
        # exposes it for confirmation before running).
        preview = preview_uninstall_target(command)
        if not preview["ok"]:
            return (f"Uninstaller for '{app['name']}' was refused: "
                    f"{preview['reason']} — the registry uninstall "
                    f"string is not a safe uninstaller command.")
        argv = preview["argv"]
        try:
            creationflags = 0
            if _is_windows():
                creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(
                argv, shell=False, creationflags=creationflags,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:  # noqa: BLE001
            return f"Could not start the uninstaller for '{app['name']}': {e}"
        # Receipt wording (audit m11): Popen only LAUNCHES the
        # uninstaller — detached, with its output discarded — so the
        # receipt must say "launched", never imply the removal has
        # completed. Verification is the next list_installed_apps call.
        # The receipt names the EXACT target (executable + arguments)
        # that was launched — the human sees what actually ran.
        fallback_note = (f"'{app['name']}' isn't managed by winget, so I used the "
                         "uninstaller Windows has on record for it. ")
        if quiet:
            return (fallback_note
                    + f"Uninstaller launched for '{app['name']}' — exact target: "
                    f"{preview['display']} — its quiet "
                    "uninstaller is running in the background and finishes "
                    "on its own. Ask me to list the installed apps in a few "
                    "seconds to confirm it's gone.")
        return (fallback_note
                + f"Uninstaller launched for '{app['name']}' — exact target: "
                f"{preview['display']} — it finishes in "
                "its own window, so follow its prompts on screen to "
                "complete the removal.")
    return reason

# ---------------------------------------------------------------------------
# DOCUMENTS — print a file, read a PDF's words
# ---------------------------------------------------------------------------


def print_file(path: str):
    """Send a file to the default printer via the Windows 'print' verb.

    Fix 7 (tools loopholes): ``os.startfile(..., "print")`` succeeding
    only means the print verb was launched — not that a job queued.
    The result is an Outcome: verified only when a job for this file
    is actually in the print queue (core.tool_outcome.
    print_job_queued). When the queue cannot be read, or no job
    appears, the result says so honestly instead of claiming the
    file printed.
    """
    if not path or not str(path).strip():
        return "Printing needs the path of a file."
    target = Path(str(path)).expanduser()
    if not target.exists():
        return f"File not found: {target}"
    if not target.is_file():
        return f"That's a folder, not a file — printing needs a file: {target}"
    if not _is_windows() or not hasattr(os, "startfile"):
        return ("Printing uses the Windows 'print' action for the file type — "
                f"{_NOT_WINDOWS}")
    try:
        os.startfile(str(target), "print")  # noqa: S606 — Windows-only verb, guarded above
    except Exception as e:  # noqa: BLE001
        return (f"Windows could not print '{target.name}': {e}. "
                "Some file types have no print action — open the file and print from its app instead.")
    from . import tool_outcome as _outcome  # Fix 7: verify a queued job
    _ok, _evidence = _outcome.print_job_queued(target.name)
    if _ok:
        return _outcome.verified(
            "Sent '%s' to the default printer — %s."
            % (target.name, _evidence),
            _evidence)
    if "no print-job read-back" in _evidence:
        return _outcome.unverified(
            "Sent '%s' to the default printer. I couldn't confirm a "
            "job reached the queue on this system, so if nothing "
            "prints, check the printer is on and has paper."
            % target.name,
            _evidence)
    return _outcome.unverified(
        "Sent '%s' to the print action, but %s — check the printer "
        "is on and has paper." % (target.name, _evidence),
        _evidence)


def read_pdf_text(path: str, max_pages: int = 10,
                  start_page: int = 1) -> str:
    """Extract the text of a PDF (~6000 chars): max_pages pages
    starting at start_page (1-based, default the first page)."""
    if not path or not str(path).strip():
        return "Reading a PDF needs the path of a file."
    target = Path(str(path)).expanduser()
    if not target.exists():
        return f"File not found: {target}"
    if not target.is_file():
        return f"That's a folder, not a file: {target}"
    try:
        from pypdf import PdfReader
    except ImportError:
        return ("Reading PDFs isn't available in this build of VYUK — "
                "the PDF part didn't make it into the package.")
    try:
        reader = PdfReader(str(target))
    except Exception as e:  # noqa: BLE001
        return f"That file couldn't be opened as a PDF: {e}"
    if getattr(reader, "is_encrypted", False):
        return "That PDF is password-protected, so its text can't be read."
    total_pages = len(reader.pages)
    try:
        pages_to_read = max(1, min(int(max_pages), 50))
    except (TypeError, ValueError):
        pages_to_read = 10
    try:
        first_page = max(1, int(start_page))
    except (TypeError, ValueError):
        first_page = 1
    if first_page > total_pages:
        return (f"'{target.name}' has {total_pages} page(s) — there "
                f"is no page {first_page} to start from.")
    window = reader.pages[first_page - 1:
                          first_page - 1 + pages_to_read]
    last_page = first_page - 1 + len(window)
    chunks: list[str] = []
    for page in window:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 — one bad page shouldn't sink the rest
            chunks.append("")
    text = "\n".join(chunks).strip()
    if not text:
        if first_page > 1:
            return (f"Pages {first_page}–{last_page} of "
                    f"'{target.name}' have no readable text — they "
                    "may be scanned images rather than real text "
                    "(OCR would be needed).")
        return (f"'{target.name}' has no readable text — it may be scanned images "
                "rather than real text (OCR would be needed).")
    limit = 6000
    notes = []
    if len(text) > limit:
        text = text[:limit]
        notes.append(f"showing the first {limit} characters")
    if first_page > 1:
        notes.append(f"read pages {first_page}–{last_page} of "
                     f"{total_pages} pages")
    elif total_pages > pages_to_read:
        notes.append(f"read {pages_to_read} of {total_pages} pages")
    suffix = f"\n... [truncated — {'; '.join(notes)}]" if notes else ""
    return f"Text of '{target.name}' ({total_pages} page(s)):\n{text}{suffix}"

# ---------------------------------------------------------------------------
# FIND — search for files by name across the usual places
# ---------------------------------------------------------------------------

_FIND_ROOTS = {
    "home": lambda: Path.home(),
    "desktop": lambda: Path.home() / "Desktop",
    "documents": lambda: Path.home() / "Documents",
    "downloads": lambda: Path.home() / "Downloads",
}
_FIND_SKIP_DIRS = {
    "AppData", ".git", "node_modules", "__pycache__", "$Recycle.Bin",
    "System Volume Information", ".venv", "venv", ".cache",
}
# Tool-managed homes and caches, skipped by the same policy as
# _FIND_SKIP_DIRS: nothing a user means when they ask VYUK to find
# a file lives in a package manager's home, an editor's state, or a
# test-runner's cache. (Field-check fix #33: the old set knew only
# the exact names ".venv"/"venv", so every real environment —
# ".venv-qt", "mic_field_venv", "cad_env" — was walked in full.)
_FIND_SKIP_DIR_NAMES = {
    ".npm", ".cargo", ".rustup", ".bun", ".conda", ".mamba",
    ".vscode", ".vscode-insiders", ".idea",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", ".tox", ".nox",
}
# A rare pattern over a big home walked the whole tree (75–88 s in
# the round-2 field check, field fix #33): the walk now stops at
# this time budget and says so honestly in the scope line, instead
# of making a voice turn wait for the whole disk. The budget is
# far inside the funnel's standard deadline (120 s) and the N1
# sweep's 15 s watchdog.
_FIND_TIME_BUDGET_S = 10.0


def _is_policy_skip_dir(name: str) -> bool:
    """True when a folder is a tool-managed tree, never user files.

    Exact names first (both skip sets), then shapes: any virtual
    environment (".venv-*" / "*-venv" / "*_venv"), any installed
    package tree ("site-packages", "dist-packages", "*.dist-info",
    "*.egg-info"). A folder a user made for their own files is
    never matched by these shapes — the shapes all name a tool's
    own storage, the same principle as the ".git"/"node_modules"
    skips that predate this function.
    """
    if name in _FIND_SKIP_DIRS or name in _FIND_SKIP_DIR_NAMES:
        return True
    if name.startswith(".venv-") or name.startswith(".venv_"):
        return True
    if name.endswith("_venv") or name.endswith("-venv"):
        return True
    if name in ("site-packages", "dist-packages"):
        return True
    if name.endswith(".dist-info") or name.endswith(".egg-info"):
        return True
    return False
_FIND_MAX_DEPTH = 10
_FIND_EXPLICIT_MAX_DEPTH = 25


def find_files(name_pattern: str, root: str = "home", limit: int = 20,
               min_size_kb: int = 0, max_size_kb: int = 0,
               newest_first: bool = False) -> str:
    """Find files by name pattern under home/desktop/documents/downloads or a folder.

    The walk is bounded three ways: it stops at the result limit, at
    the depth limit, or at a 10 s time budget (see _FIND_TIME_BUDGET_S),
    whichever comes first — a rare pattern over a big home used to
    walk the whole tree (75–88 s in the round-2 field check) and the
    scope line says which bound stopped it. Folders are visited in a
    fixed order (visible folders before hidden ones, alphabetical
    within each group), so the same question returns the same first
    files instead of filesystem order. Tool-managed trees are skipped
    by policy (see _is_policy_skip_dir): a file inside one is not
    found, and the scope line says which folder names were skipped.

    The optional size filters (in KB, by real file size) and the
    newest-first ordering need every match collected before anything
    can be filtered or sorted, so that path walks the whole scope,
    drops what the filters exclude (the result says how many) and
    only then applies the limit — subject to the same time budget.
    """
    pattern = (name_pattern or "").strip()
    if not pattern:
        return "Finding files needs a name or pattern, like 'report' or '*.pdf'."
    try:
        min_bytes = max(0, int(min_size_kb)) * 1024
    except (TypeError, ValueError):
        min_bytes = 0
    try:
        max_bytes = max(0, int(max_size_kb)) * 1024
    except (TypeError, ValueError):
        max_bytes = 0
    if isinstance(newest_first, str):
        newest = newest_first.strip().lower() in ("true", "1", "yes")
    else:
        newest = bool(newest_first)
    size_filter = min_bytes > 0 or max_bytes > 0
    extended = size_filter or newest
    if not any(ch in pattern for ch in "*?["):
        pattern = f"*{pattern}*"
    root_key = (root or "home").strip().lower()
    explicit_root = root_key not in _FIND_ROOTS
    if root_key in _FIND_ROOTS:
        base = _FIND_ROOTS[root_key]()
        root_label = root_key
    else:
        base = Path(root).expanduser()
        root_label = str(base)
    # Broad named roots keep the conservative scan budget; a folder the
    # caller explicitly supplied gets the deeper budget from the audit.
    max_depth = _FIND_EXPLICIT_MAX_DEPTH if explicit_root else _FIND_MAX_DEPTH
    if not base.exists():
        return f"That place doesn't exist on this PC: {base}"
    if not base.is_dir():
        return f"That's a file, not a folder to search in: {base}"
    try:
        cap = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        cap = 20
    lowered = pattern.lower()
    results: list[Path] = []
    skipped_dir_count = 0
    skipped_dir_names: set[str] = set()
    depth_limited_dir_count = 0
    stopped_at_result_limit = False
    stopped_at_time_budget = False
    dirs_searched = 0
    walk_deadline = time.monotonic() + _FIND_TIME_BUDGET_S
    for dirpath, dirnames, filenames in os.walk(base):
        if time.monotonic() >= walk_deadline:
            stopped_at_time_budget = True
            break
        dirs_searched += 1
        depth = len(Path(dirpath).relative_to(base).parts) if Path(dirpath) != base else 0
        policy_skipped = [d for d in dirnames if _is_policy_skip_dir(d)]
        skipped_dir_count += len(policy_skipped)
        skipped_dir_names.update(policy_skipped)
        dirnames[:] = [d for d in dirnames if not _is_policy_skip_dir(d)]
        # A directory that IS a Python environment (a venv marker file
        # or a conda-meta folder) is a tool-managed tree, not user
        # files: do not descend into it. Its own top-level files are
        # still matched. The base itself is exempt — an explicit root
        # is a folder the caller chose, even if it is an environment.
        if depth and ("pyvenv.cfg" in filenames or "conda-meta" in dirnames):
            skipped_dir_count += 1
            skipped_dir_names.add(Path(dirpath).name)
            dirnames[:] = []
        # Fixed order, user files first: visible folders before hidden
        # ones, alphabetical within each group. os.walk order is
        # filesystem order, so without this the same question could
        # return a different first-five on every run — and a time
        # budget would cut the search at a different place each time.
        dirnames.sort(key=lambda d: (d.startswith("."), d.lower()))
        if depth >= max_depth:
            depth_limited_dir_count += len(dirnames)
            dirnames[:] = []
        for i, filename in enumerate(filenames):
            if i and i % 256 == 0 and time.monotonic() >= walk_deadline:
                stopped_at_time_budget = True
                break
            if fnmatch.fnmatchcase(filename.lower(), lowered):
                results.append(Path(dirpath) / filename)
                if not extended and len(results) >= cap:
                    stopped_at_result_limit = True
                    break
        if stopped_at_result_limit or stopped_at_time_budget:
            break
    skipped_names = ", ".join(sorted(skipped_dir_names)) or "none encountered"
    if stopped_at_time_budget:
        coverage = (f"stopped at the {_FIND_TIME_BUDGET_S:g} s time budget "
                    f"after searching {dirs_searched} folder(s)")
        budget_note = (" — the rest of the root was not searched, so a "
                       "matching file may exist beyond where the search "
                       "stopped; narrow the root (Desktop, Documents, "
                       "Downloads, or a folder) or the name to search it "
                       "fully.")
    elif stopped_at_result_limit:
        coverage = "stopped early at the result limit after searching"
        budget_note = ""
    else:
        coverage = "searched"
        budget_note = ""
    scope = (
        f"Search scope: {coverage} root {base} (requested root: {root_label}) "
        f"for pattern '{pattern}' up to {max_depth} levels deep; "
        f"{skipped_dir_count} folder(s) were skipped by policy ({skipped_names}), and "
        f"{depth_limited_dir_count} folder(s) at the depth limit were not descended into, "
        "so files deeper than that may exist."
        f"{budget_note}"
    )
    if extended:
        kept: list[tuple[Path, int | None, float]] = []
        dropped = 0
        for hit in results:
            try:
                stat = hit.stat()
                size: int | None = stat.st_size
                mtime = stat.st_mtime
            except OSError:
                size, mtime = None, 0.0
            if size_filter and (size is None or size < min_bytes
                                or (max_bytes and size > max_bytes)):
                dropped += 1
                continue
            kept.append((hit, size, mtime))
        if newest:
            kept.sort(key=lambda item: (-item[2], item[0].name.lower()))
        else:
            kept.sort(key=lambda item: item[0].name.lower())
        if not kept:
            if results and size_filter:
                return (f"No files matching '{name_pattern}' under {root_label} "
                        f"passed the size filter — it dropped all "
                        f"{dropped} match(es). {scope}")
            return (f"No files matching '{name_pattern}' within {max_depth} levels "
                    f"under {root_label}. {scope}")
        truncated = len(kept) > cap
        shown = kept[:cap]
        filter_note = ""
        if size_filter:
            bits = []
            if min_bytes:
                bits.append(f"at least {_human_size(min_bytes)}")
            if max_bytes:
                bits.append(f"at most {_human_size(max_bytes)}")
            filter_note = (f"Size filter ({', '.join(bits)}) dropped "
                           f"{dropped} of {len(results)} matching file(s).")
        ext_lines = []
        for hit, size, _mtime in shown:
            shown_size = _human_size(size) if size is not None else "?"
            ext_lines.append(f"• {hit} ({shown_size})")
        more = (" (showing the first "
                f"{cap} — narrow the name or the filters to see more)"
                if truncated else "")
        body = (f"Found {len(shown)} file(s) matching '{name_pattern}' "
                f"under {root_label}{more}:\n" + "\n".join(ext_lines))
        if filter_note:
            body += f"\n{filter_note}"
        return body + f"\n{scope}"
    if not results:
        return (f"No files matching '{name_pattern}' within {max_depth} levels "
                f"under {root_label}. {scope}")
    results.sort(key=lambda p: p.name.lower())
    lines = []
    for hit in results:
        try:
            size = _human_size(hit.stat().st_size)
        except OSError:
            size = "?"
        lines.append(f"• {hit} ({size})")
    more = " (stopped at the result limit — narrow the name to see more)" if stopped_at_result_limit else ""
    return (f"Found {len(results)} file(s) matching '{name_pattern}' under {root_label}{more}:\n"
            + "\n".join(lines) + f"\n{scope}")
