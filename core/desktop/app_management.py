"""B-D8 (2026-11-09) app management via winget — the ONE dict-returning
wrapper a driver calls to install, update, remove, and list Windows
apps through the Windows Package Manager (winget.exe).

THE GAP (proved first, file/line evidence)
------------------------------------------
Winget machinery ALREADY exists in the tree — this batch builds only
the remainder. What exists (all human-string tool handlers, all
registered, all untouched by this batch):

* ``winget_install`` (core/skills_system.py:85, implemented in
  core/skills_system_apps_startup.py:233) — exact id-or-name match
  only, confirm required, verifies the install landed via
  ``winget list --id`` (v5.9, m10). Honest, as far as it goes.
* ``list_app_updates`` (skills_system.py:99, apps_startup.py:322)
  — pending updates, read from ``winget upgrade``.
* ``winget_upgrade_all`` (skills_system.py:104, apps_startup.py:365)
  — updates EVERYTHING winget manages, with a per-package
  before/after receipt. The ONLY update path.
* ``uninstall_app`` (core/skills_plus.py:66, implemented in
  core/skills_plus_apps_files.py:222) — registry-based: it launches
  the uninstaller Windows has on record for an app name. It is NOT
  a winget uninstall, and it has no deny-list.
* ``list_installed_apps`` (skills_plus.py:54, apps_files.py:86) —
  registry-based listing. It is NOT a ``winget list``.

What does NOT exist, and is the remainder this module builds:

1. NO single-app upgrade. The only update path updates everything —
   a user who asked for one app gets every app updated.
2. NO winget uninstall. Removal goes through a registry uninstall
   string (an untrusted command line, however carefully parsed) and
   nothing refuses a system/protected package.
3. NO dict-returning wrapper a driver can call. Every existing
   function returns a human sentence; a driver cannot tell success
   from failure without parsing prose.
4. NO explicit search surface for the driver. ``_winget_search``
   (apps_startup.py:156) is a private helper, not a driver call.
5. NO ``winget list`` surface for the driver. ``_winget_installed_row``
   (apps_startup.py:186) probes one id; there is no installed-list
   call that returns rows.
6. An AMBIGUOUS-EXACT-NAME hole in ``winget_install``: it takes the
   FIRST exact id-or-name match (apps_startup.py:276-280). Two
   packages sharing one exact Name (different Ids) install the
   first, silently. The tool is NOT changed by this batch — its
   behaviour is theirs — but this wrapper never takes a first
   match: an ambiguous exact name refuses, listing candidates.

The existing tools are NOT changed by this batch (their behaviour
is theirs). This wrapper is the new driver surface; the tools keep
working as they are. B-D8r (2026-11-09) did the rewire the last
paragraph anticipated: winget_install, list_app_updates,
winget_upgrade_all, uninstall_app and list_installed_apps now call
this module (the B-D4r shape) — the tools keep their registrations
and prose shapes, and the wrapper's resolve/deny-list/confirm rules
are unchanged. ``list_updates()`` was added here in B-D8r (the
pending-updates surface list_app_updates routes through).

LOPHOLE FAMILY — "an install that installs the wrong app, or
installs without the user knowing"
------------------------------------------------------------
* An install that resolves the WRONG package (a name that matches
  several packages, or a typo that installs a lookalike).
* An install that proceeds WITHOUT confirmation (the user never
  said yes to THAT app).
* An update that updates the WRONG app — or updates everything
  when the user asked for one.
* A remove that removes the WRONG app — or a system app.
* An install that reports SUCCESS when it failed (winget's exit
  code / output misread).

ONE design closes the class: every app action goes through this
module; a package is identified by its winget PACKAGE ID (never
by a bare name alone — a name is a search hint, resolved by
``resolve()``; an exact id match wins, an exact name match must
be UNIQUE, anything ambiguous REFUSES listing candidates); every
install/update/remove requires the caller to pass ``confirm=True``
— exactly True, not merely truthy — and the wrapper refuses
without it (the driver gets the user's approval first through
the funnel; the wrapper enforces it, it never installs on its
own initiative); every remove checks a deny-list of
system/protected packages BEFORE any winget call; update-all is
a SEPARATE explicit function — ``update()`` never passes
``--all``; every result reports what winget actually did (the
exit code, the package id, the version that landed or was
removed, read from winget's own lists — a failed install is an
honest failure, never a success claim; an install winget claims
but the installed list does not show is reported unconfirmed,
never as landed).

Rejected alternatives (recorded):
* Bare-name identity — a name matches several packages; the
  first match wins silently. (Identity is the package id;
  ``resolve()`` refuses ambiguity.)
* Install-without-confirmation — the user never said yes to
  THAT app. (``confirm=True`` is enforced here, not assumed.)
* Update-all-by-default — updates everything when the user
  asked for one. (``update()`` takes one id; ``update_all()``
  is a separate, explicit call.)
* Remove-without-deny-list — a system app removed. (The
  deny-list is checked before any winget call.)
* Exit-code-only verdicts — winget's exit 0 is its claim, not
  proof. (Every install/update/remove is verified against
  winget's own lists afterwards; the installed list is the
  verdict — the v5.9, m10 posture, kept here.)
* A home-grown downloader with the winget name on it — the app
  management IS winget (real winget.exe calls via subprocess;
  no installer is ever downloaded by this module itself).

DEPENDENCY (PROPOSED — parent rules)
------------------------------------
winget-cli, MIT — licence read from the GitHub API repo metadata
for microsoft/winget-cli 2026-11-09 by this batch (license:
{"key": "mit", "name": "MIT License", "spdx_id": "MIT"}) +
the round-6 research record ("MIT (GitHub licence field +
LICENSE file)"). There is NO pip package and NO pin: winget.exe
is a Windows system tool — it ships with Windows 10/11 as App
Installer, and this module shells out to it via subprocess.
Nothing is downloaded, vendored, or installed by this module.
WEIGHT: 0 new bytes shipped — the wrapper is pure Python on the
standard library (subprocess) plus the tree's own winget
parsers; winget.exe itself is part of Windows, not of VYUK.
The parent rules on the system-tool dependency, not a pip one.
``winget.exe`` is absent from this Linux lab machine (``which
winget`` finds nothing; no ``winget`` module in either
interpreter) — the lab runs the logic against an injected fake
backend (see below); the real-Windows leg (a real
install/update/remove on his machine) is a field leg, owed,
not this batch.

LICENCE TRACEABILITY (the apps, not the tool)
---------------------------------------------
winget-cli itself is MIT. The APPS it installs carry their own
licences, and this wrapper NEVER presents an installed app as
licence-cleared. Every install/update result records the
package id AND the source it came from (``{"package_id",
"source", "version"}``), so the licence question for each app
is answerable later from the record — the record is the
trace, not a clearance.

APPROVAL BOUNDARY
-----------------
Installs, updates, and removes are destructive. The wrapper
never acts on its own initiative: it requires ``confirm=True``
on every mutating call, and the driver must obtain the user's
approval through the funnel (C-14/D4) before passing it. A
tool that installed on a bare utterance would be a hard
violation; this wrapper is driver machinery, not a voice tool,
and it enforces the confirmation itself. No new tool is added
by this batch (registry stays 656).

BACKEND PROTOCOL (what a backend must provide)
----------------------------------------------
A backend is any object with these methods; the real one is
``_WingetBackend`` (winget.exe via the tree's ``_run``), the
tests and the prover inject a fake with the same shape:

* ``which()`` -> bool. True when winget.exe is on PATH.
* ``run(args, timeout_s, cancel_token=None)`` -> (rc, out, err).
  rc is -1 when the command could not start, -2 on timeout,
  -3 when the caller's cancel token stopped it (the B20 shape).

A backend never raises for a failed winget call — it returns
the (rc, out, err) triple, and the wrapper turns it into an
honest failure dict. The winget table parsers are the tree's
own (``_winget_rows`` / ``_winget_bare_message`` from
core/skills_system_apps_startup.py) — locale-proof, shared,
not duplicated here.

A result dict is {"ok", "package_id", "name", "version",
"source", "state", "reason", "detail"}. ``state`` is one of
"installed" / "updated" / "up_to_date" / "removed" /
"cancelled" / "failed". ``ok`` is True only when winget's own
lists confirm the outcome. ``source`` is winget's source for
the package (usually "winget") — the licence trace, never a
clearance.
"""

from __future__ import annotations

import shutil

from ..skills_system_apps_startup import _winget_bare_message, _winget_rows
from ..skills_system_helpers import _NOT_WINDOWS, _is_windows, _run, _tail

ENGINE = "winget"

# Timeouts mirror the existing tool handlers (skills_system_apps_startup).
_SEARCH_TIMEOUT_S = 90
_LIST_TIMEOUT_S = 120
_MUTATE_TIMEOUT_S = 900

# --- Deny-list: system/protected packages a remove must never touch ---
# These are frameworks/runtimes other apps depend on, or winget
# itself — removing one breaks apps, not just the package. Checked
# BEFORE any winget call, by package id. Deliberately NOT denied:
# the Microsoft Store (Microsoft.WindowsStore) — it is an app, not
# a framework; a user may choose to remove it, and winget can do
# so without breaking the OS. Deliberately NOT a broad
# "Microsoft.Windows.*" prefix — that would block removing ordinary
# user apps (Photos, Notepad, Calculator) that happen to share the
# prefix. The list is runtimes + winget itself, nothing else.
_DENIED_EXACT = {
    "Microsoft.DesktopAppInstaller",  # winget itself — removing it breaks this wrapper
}
_DENIED_PREFIXES = (
    "Microsoft.WindowsAppRuntime.",  # Windows App Runtime (WinUI apps depend on it)
    "Microsoft.VCLibs.",  # Visual C++ runtime
    "Microsoft.UI.Xaml.",  # WinUI framework
    "Microsoft.NET.Native.Framework.",  # .NET Native framework
    "Microsoft.NET.Native.Runtime.",  # .NET Native runtime
)


class _EngineUnavailable(Exception):
    """winget.exe is not available for app management."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def _denied_reason(package_id: str) -> str | None:
    """The deny-list refusal for a package id, or None when it may be removed."""
    lowered = (package_id or "").strip().lower()
    for exact in _DENIED_EXACT:
        if lowered == exact.lower():
            return (
                f"'{package_id}' is winget itself — removing it would break "
                "app management on this PC, so it is refused"
            )
    for prefix in _DENIED_PREFIXES:
        if lowered.startswith(prefix.lower()):
            return (
                f"'{package_id}' is a system runtime/framework other apps "
                "depend on, so it is refused — removing it would break "
                "apps, not just the package"
            )
    return None


def _clean_version(row: dict) -> str:
    """The version from a winget row: the first token of the slice
    (winget sometimes prints a Match column after Version)."""
    tokens = (row.get("Version") or "").split()
    return tokens[0] if tokens else ""


def _package_dict(row: dict) -> dict:
    """One package as the wrapper reports it — id + source + version."""
    return {
        "name": row.get("Name", ""),
        "id": row.get("Id", ""),
        "version": _clean_version(row),
        "source": row.get("Source", ""),
    }


class _WingetBackend:
    """The REAL backend: winget.exe via the tree's _run (subprocess,
    no console flash on Windows, B20 cancellation). No state."""

    def which(self) -> bool:
        return shutil.which("winget") is not None

    def run(self, args, timeout_s: int, cancel_token=None):
        return _run(args, timeout=timeout_s, cancel_token=cancel_token)


def engine_available() -> bool:
    """True when winget.exe is usable on this machine (Windows +
    winget on PATH). False on the Linux lab — and that is an honest
    answer, not a skip."""
    return _is_windows() and shutil.which("winget") is not None


def engine_status() -> dict:
    """Honest engine report: {"available", "engine", "reason"}."""
    if not _is_windows():
        return {
            "available": False,
            "engine": ENGINE,
            "reason": f"engine_unavailable: winget is the Windows package manager — {_NOT_WINDOWS}",
        }
    if shutil.which("winget") is None:
        return {
            "available": False,
            "engine": ENGINE,
            "reason": (
                "engine_unavailable: winget (the Windows Package Manager) "
                "isn't available on this PC — it normally ships with "
                "Windows 10/11 as 'App Installer'"
            ),
        }
    return {"available": True, "engine": ENGINE, "reason": None}


class AppManagement:
    """One winget session, one wrapper, every app action verified.

    A package is named by its winget PACKAGE ID. ``resolve()`` turns
    a search hint (a name or an id) into a unique id; install takes
    the hint through ``resolve()``; update and remove take an id and
    check it against the installed list. Nothing here installs on
    its own initiative: every mutating call requires
    ``confirm=True`` — exactly True, not merely truthy."""

    def __init__(self, *, backend=None):
        self._backend = backend

    # -- engine ----------------------------------------------------

    def engine_available(self) -> bool:
        if self._backend is not None:
            return True  # an injected backend IS the engine for tests
        return engine_available()

    def engine_status(self) -> dict:
        if self._backend is not None:
            return {
                "available": True,
                "engine": ENGINE,
                "reason": None,
                "note": "fake backend (tests)",
            }
        return engine_status()

    def _backend_or_real(self):
        if self._backend is not None:
            return self._backend
        if not _is_windows():
            raise _EngineUnavailable(
                f"winget is the Windows package manager — {_NOT_WINDOWS}"
            )
        if shutil.which("winget") is None:
            raise _EngineUnavailable(
                "winget (the Windows Package Manager) isn't available "
                "on this PC — it normally ships with Windows 10/11 "
                "as 'App Installer'"
            )
        return _WingetBackend()

    # -- result shapes ----------------------------------------------

    @staticmethod
    def _fail(reason: str, package_id=None, name=None, version="",
              source="", state="failed", detail="") -> dict:
        return {
            "ok": False,
            "package_id": package_id,
            "name": name,
            "version": version,
            "source": source,
            "state": state,
            "reason": reason,
            "detail": detail,
        }

    @staticmethod
    def _ok(package_id, name, version, source, state, detail="") -> dict:
        return {
            "ok": True,
            "package_id": package_id,
            "name": name,
            "version": version,
            "source": source,
            "state": state,
            "reason": "",
            "detail": detail,
        }

    # -- winget reads (through the backend) --------------------------

    def _search_rows(self, be, query: str) -> tuple[list[dict], str]:
        """(rows, error) for a winget search — the existing helper's
        flow, stricter on one point: an answer winget's parsers
        cannot read is a FAILURE, never an empty result. The
        existing ``_winget_search`` treats no-rows + rc 0 as
        nothing-found; this wrapper only reads nothing-found from
        winget's own no-results message or its bare nothing-to-do
        answer."""
        rc, out, err = be.run(
            ["winget", "search", "--query", query,
             "--accept-source-agreements"],
            _SEARCH_TIMEOUT_S,
        )
        if rc == -1:
            return [], err or "winget could not be started."
        if "No package found" in out:
            return [], ""
        rows, readable = _winget_rows(
            out, "search", ["Name", "Id", "Version", "Source"])
        if not rows and rc != 0:
            return [], _tail(err or out) or "winget search failed."
        if not rows and not readable and not _winget_bare_message(out):
            return [], (
                "winget's search answer couldn't be read, so nothing "
                "can be claimed about it — winget said:\n"
                + (_tail(err or out) or "no details were printed.")
            )
        for row in rows:
            row["Version"] = _clean_version(row)
        return rows, ""

    def _installed_row(self, be, package_id: str) -> tuple[dict | None, bool]:
        """(row, readable) for one installed package id — the
        existing ``_winget_installed_row``'s flow, tri-state:
        readable False means winget's list could not be read at
        all, and callers must report that, never read it as
        "not installed"."""
        rc, out, _err = be.run(
            ["winget", "list", "--id", package_id, "--exact",
             "--accept-source-agreements"],
            _LIST_TIMEOUT_S,
        )
        if rc != 0:
            return None, False
        if "No installed package" in out:
            # winget's own "no such package" answer — readable,
            # genuinely absent, not an unreadable list.
            return None, True
        for kind, columns in (
            ("search", ["Name", "Id", "Version", "Source"]),
            ("upgrade", ["Name", "Id", "Version", "Available", "Source"]),
        ):
            rows, readable = _winget_rows(out, kind, columns)
            for row in rows:
                if row.get("Id", "").lower() == package_id.lower():
                    return row, True
            if readable:
                # A readable table with no matching row: the package
                # is not in the list. (The second shape is only
                # tried when the first parse read no table at all.)
                return None, True
        return None, False

    def _pending_updates(self, be) -> tuple[list[dict] | None, bool]:
        """(pending-update rows, winget-says-none-pending) — the
        existing ``_winget_pending_updates``'s flow. Rows are None
        when winget's answer couldn't be read — callers must report
        that, never read it as "no updates"."""
        rc, out, _err = be.run(
            ["winget", "upgrade", "--include-unknown",
             "--accept-source-agreements"],
            _LIST_TIMEOUT_S,
        )
        if rc == -1:
            return None, False
        if "No available upgrade" in out or "No installed package" in out:
            return [], True
        rows, readable = _winget_rows(
            out, "upgrade",
            ["Name", "Id", "Version", "Available", "Source"])
        if rows:
            for row in rows:
                row["Version"] = _clean_version(row)
            return rows, False
        if not readable and rc == 0 and _winget_bare_message(out):
            return [], True
        return None, False

    # -- reads -------------------------------------------------------

    def search(self, query) -> dict:
        """Candidate packages for a name or id hint — a read-only
        step, safe before any confirmation. Returns {"ok",
        "candidates": [{name, id, version, source}], "reason"}."""
        wanted = (query or "").strip() if isinstance(query, str) else ""
        if not wanted:
            return {
                "ok": False, "candidates": [],
                "reason": "search needs the name or package id of an app, e.g. 'VLC' or 'VideoLAN.VLC'",
            }
        try:
            be = self._backend_or_real()
        except _EngineUnavailable as exc:
            return {"ok": False, "candidates": [], "reason": f"engine_unavailable: {exc.detail}"}
        rows, error = self._search_rows(be, wanted)
        if error:
            return {"ok": False, "candidates": [], "reason": error}
        if not rows:
            return {
                "ok": False, "candidates": [],
                "reason": f"winget has no package matching '{wanted}'. Check the name and try again.",
            }
        return {
            "ok": True,
            "candidates": [_package_dict(row) for row in rows],
            "reason": "",
        }

    def resolve(self, query) -> dict:
        """Turn a search hint into ONE package id — the identity
        rule. An exact id match wins (ids are unique); otherwise
        an exact name match must be UNIQUE; anything ambiguous or
        unmatched REFUSES, listing candidates. Returns {"ok",
        "package": {name, id, version, source} | None,
        "candidates": [...], "reason"}."""
        found = self.search(query)
        if not found["ok"]:
            return {
                "ok": False, "package": None,
                "candidates": found["candidates"],
                "reason": found["reason"],
            }
        rows = found["candidates"]
        wanted = (query or "").strip().lower()
        for row in rows:  # an exact id match is unique by construction
            if row["id"].lower() == wanted:
                return {"ok": True, "package": row, "candidates": [], "reason": ""}
        exact_names = [row for row in rows if row["name"].lower() == wanted]
        if len(exact_names) == 1:
            return {"ok": True, "package": exact_names[0], "candidates": [], "reason": ""}
        if len(exact_names) > 1:
            # Two packages sharing one exact Name (different Ids) —
            # the existing tool installs the FIRST; this wrapper
            # refuses. The user picks the id.
            cap = 8
            lines = [f"• {row['name']} (package {row['id']}" +
                     (f", from {row['source']}" if row.get("source") else "") + ")"
                     for row in exact_names[:cap]]
            suffix = (f"\n... and {len(exact_names) - cap} more"
                      if len(exact_names) > cap else "")
            return {
                "ok": False, "package": None,
                "candidates": exact_names,
                "reason": (
                    f"'{query}' is the exact name of {len(exact_names)} "
                    "different packages, so nothing was installed — "
                    "installing the first would be a guess. Pick one "
                    "by its package id:\n" + "\n".join(lines) + suffix
                ),
            }
        cap = 8
        lines = [f"• {row['name']} (package {row['id']}" +
                 (f", from {row['source']}" if row.get("source") else "") + ")"
                 for row in rows[:cap]]
        suffix = f"\n... and {len(rows) - cap} more" if len(rows) > cap else ""
        return {
            "ok": False, "package": None, "candidates": rows,
            "reason": (
                f"No exact match for '{query}', so nothing was installed. "
                "Did you mean one of these?\n" + "\n".join(lines) + suffix
                + "\nAsk again with the exact name or package id to install it."
            ),
        }

    def list_installed(self) -> dict:
        """The apps winget lists as installed — {"ok", "apps":
        [{name, id, version, available, source}], "reason"}.
        An unreadable answer is a failure, never an empty list."""
        try:
            be = self._backend_or_real()
        except _EngineUnavailable as exc:
            return {"ok": False, "apps": [], "reason": f"engine_unavailable: {exc.detail}"}
        rc, out, err = be.run(
            ["winget", "list", "--accept-source-agreements"],
            _LIST_TIMEOUT_S,
        )
        if rc == -1:
            return {"ok": False, "apps": [], "reason": err or "winget could not be started."}
        if rc != 0:
            return {
                "ok": False, "apps": [],
                "reason": "winget could not list installed apps:\n" + (_tail(err or out) or "no details were printed."),
            }
        rows, readable = _winget_rows(
            out, "search", ["Name", "Id", "Version", "Source"])
        if not rows:
            rows, readable = _winget_rows(
                out, "upgrade",
                ["Name", "Id", "Version", "Available", "Source"])
        if not rows and not readable:
            return {
                "ok": False, "apps": [],
                "reason": (
                    "winget's installed-apps answer couldn't be read, so "
                    "nothing can be claimed about it — winget said:\n"
                    + (_tail(err or out) or "no details were printed.")
                ),
            }
        apps = []
        for row in rows:
            apps.append({
                "name": row.get("Name", ""),
                "id": row.get("Id", ""),
                "version": _clean_version(row),
                "available": row.get("Available", ""),
                "source": row.get("Source", ""),
            })
        return {"ok": True, "apps": apps, "reason": ""}

    def list_updates(self) -> dict:
        """The apps winget says have a newer version available —
        {"ok", "updates": [{name, id, version, available, source}],
        "reason"}. An unreadable answer is a failure, never "up
        to date". (B-D8r: the pending-updates surface
        ``list_app_updates`` routes through; without it the tool
        would bypass this wrapper and call winget directly.)"""
        try:
            be = self._backend_or_real()
        except _EngineUnavailable as exc:
            return {"ok": False, "updates": [], "reason": f"engine_unavailable: {exc.detail}"}
        rows, _none_pending = self._pending_updates(be)
        if rows is None:
            return {
                "ok": False, "updates": [],
                "reason": "Couldn't read winget's answer, so I can't tell whether app updates are available.",
            }
        return {
            "ok": True,
            "updates": [{
                "name": row.get("Name", ""), "id": row.get("Id", ""),
                "version": _clean_version(row),
                "available": row.get("Available", ""),
                "source": row.get("Source", ""),
            } for row in rows],
            "reason": "",
        }

    # -- confirmation -------------------------------------------------

    @staticmethod
    def _need_confirm(confirm, what: str) -> str | None:
        """The confirmation refusal, or None when the caller passed
        exactly True. ``confirm=1`` or ``confirm="yes"`` is truthy
        but not an explicit yes — it refuses."""
        if confirm is True:
            return None
        return (
            f"{what} needs confirm=true — the user must approve this "
            "exact action first. Ask again with confirmation."
        )

    # -- mutating calls ------------------------------------------------

    def install(self, query, *, confirm=False, cancel_token=None) -> dict:
        """Install one app, resolved through ``resolve()`` first.

        The install itself is ``winget install --id <id> --exact``;
        winget's exit 0 is its claim, not proof — the installed
        list is read back, and only a row there reports the
        version that actually landed. The result records the
        package id AND the source (the licence trace — never a
        clearance of the app's own licence)."""
        resolved = self.resolve(query)
        if not resolved["ok"]:
            return self._fail(
                resolved["reason"],
                package_id=None, state="failed",
                detail="nothing was installed",
            )
        package = resolved["package"]
        package_id = package["id"]
        refusal = self._need_confirm(
            confirm,
            f"Installing '{package['name']}' (package {package_id}) downloads software onto this PC and",
        )
        if refusal:
            return self._fail(
                refusal, package_id=package_id, name=package["name"],
                version=package["version"], source=package["source"],
                detail="nothing was installed",
            )
        try:
            be = self._backend_or_real()
        except _EngineUnavailable as exc:
            return self._fail(
                f"engine_unavailable: {exc.detail}",
                package_id=package_id, name=package["name"],
                version=package["version"], source=package["source"],
                detail="nothing was installed",
            )
        rc, out, err = be.run(
            ["winget", "install", "--id", package_id, "--exact",
             "--accept-source-agreements", "--accept-package-agreements"],
            _MUTATE_TIMEOUT_S, cancel_token,
        )
        detail = _tail(out) or _tail(err)
        if rc == -3:
            # B20: stopped at the user's request — the package may
            # be partly installed; winget itself decides what a
            # re-run does, so the message names the check, never a
            # claim either way.
            return self._fail(
                f"Installing '{package['name']}' (package {package_id}) was "
                "cancelled at the user's request while it was still "
                "running — the package may be partly installed, so "
                "check winget's installed list before retrying.",
                package_id=package_id, name=package["name"],
                version=package["version"], source=package["source"],
                state="cancelled",
                detail="cancelled by the user",
            )
        if rc == -2:
            return self._fail(
                f"Installing '{package['name']}' (package {package_id}) timed out after "
                "15 minutes — it may still be running in the background; check before retrying.",
                package_id=package_id, name=package["name"],
                version=package["version"], source=package["source"],
                detail="timed out after 15 minutes",
            )
        if rc == 0:
            installed, readable = self._installed_row(be, package_id)
            if installed is not None:
                landed = _clean_version(installed)
                return self._ok(
                    package_id, package["name"], landed,
                    installed.get("Source", "") or package["source"],
                    "installed",
                    detail="confirmed in winget's installed list",
                )
            if not readable:
                return self._fail(
                    f"winget reported success installing '{package['name']}' "
                    f"(package {package_id}), but winget's installed list "
                    "couldn't be read, so the install couldn't be confirmed.",
                    package_id=package_id, name=package["name"],
                    version=package["version"], source=package["source"],
                    detail="installed list unreadable after a reported success",
                )
            return self._fail(
                f"winget reported success installing '{package['name']}' "
                f"(package {package_id}), but the package doesn't show in "
                "winget's installed list, so the install couldn't be "
                "confirmed. Check the app list before relying on it.",
                package_id=package_id, name=package["name"],
                version=package["version"], source=package["source"],
                detail="not in winget's installed list after a reported success",
            )
        return self._fail(
            f"winget could not install '{package['name']}' (package {package_id}). "
            f"winget said:\n{detail or 'no details were printed.'}",
            package_id=package_id, name=package["name"],
            version=package["version"], source=package["source"],
            detail=detail,
        )

    def update(self, package_id, *, confirm=False, cancel_token=None) -> dict:
        """Update ONE app — never everything. The id must be
        installed AND have an update pending (read from winget's
        own lists before the run); after the run the pending list
        is read again, and only an id no longer pending reports
        the new version that actually landed."""
        wanted = (package_id or "").strip() if isinstance(package_id, str) else ""
        if not wanted or " " in wanted:
            return self._fail(
                "update needs one winget package id (a single token, e.g. 'VideoLAN.VLC') — "
                "a name is a search hint, resolve it first",
                package_id=package_id if isinstance(package_id, str) else None,
                detail="nothing was updated",
            )
        refusal = self._need_confirm(
            confirm,
            f"Updating '{wanted}' downloads and installs a new version on this PC and",
        )
        if refusal:
            return self._fail(refusal, package_id=wanted, detail="nothing was updated")
        try:
            be = self._backend_or_real()
        except _EngineUnavailable as exc:
            return self._fail(
                f"engine_unavailable: {exc.detail}",
                package_id=wanted, detail="nothing was updated",
            )
        installed, readable = self._installed_row(be, wanted)
        if installed is None:
            if not readable:
                return self._fail(
                    f"winget's installed list couldn't be read, so whether "
                    f"'{wanted}' is installed can't be told — nothing was updated.",
                    package_id=wanted, detail="installed list unreadable",
                )
            return self._fail(
                f"'{wanted}' is not installed (winget's list has no such package id), "
                "so there is nothing to update.",
                package_id=wanted, detail="not installed",
            )
        old_version = _clean_version(installed)
        pending, _none = self._pending_updates(be)
        if pending is None:
            return self._fail(
                "winget's update list couldn't be read, so whether an update "
                f"is pending for '{wanted}' can't be told — nothing was updated.",
                package_id=wanted, name=installed.get("Name", ""),
                version=old_version, source=installed.get("Source", ""),
                detail="pending-update list unreadable",
            )
        if not any(row.get("Id", "").lower() == wanted.lower() for row in pending):
            return self._ok(
                wanted, installed.get("Name", ""), old_version,
                installed.get("Source", ""), "up_to_date",
                detail="winget reports no update pending for this package",
            )
        rc, out, err = be.run(
            ["winget", "upgrade", "--id", wanted, "--exact",
             "--accept-source-agreements", "--accept-package-agreements"],
            _MUTATE_TIMEOUT_S, cancel_token,
        )
        detail = _tail(out) or _tail(err)
        if rc == -3:
            return self._fail(
                f"Updating '{wanted}' was cancelled at the user's request while it was "
                "still running — it may have updated and it may not; run the update "
                "check to see what remains.",
                package_id=wanted, name=installed.get("Name", ""),
                version=old_version, source=installed.get("Source", ""),
                state="cancelled", detail="cancelled by the user",
            )
        if rc == -2:
            return self._fail(
                f"Updating '{wanted}' timed out after 15 minutes — it may have updated "
                "and it may not; run the update check to see what remains.",
                package_id=wanted, name=installed.get("Name", ""),
                version=old_version, source=installed.get("Source", ""),
                detail="timed out after 15 minutes",
            )
        after, _after_none = self._pending_updates(be)
        if after is not None:
            still = any(row.get("Id", "").lower() == wanted.lower() for row in after)
            if not still:
                landed, readable2 = self._installed_row(be, wanted)
                new_version = _clean_version(landed) if landed is not None else old_version
                if rc == 0:
                    return self._ok(
                        wanted, installed.get("Name", ""), new_version,
                        installed.get("Source", ""), "updated",
                        detail=(
                            f"updated {old_version} → {new_version}"
                            if new_version != old_version
                            else "updated; winget reports no update pending"
                        ),
                    )
                return self._fail(
                    f"winget's update of '{wanted}' finished with errors, but the "
                    "package is no longer pending an update — winget said:\n"
                    f"{detail or 'no details were printed.'}",
                    package_id=wanted, name=installed.get("Name", ""),
                    version=new_version, source=installed.get("Source", ""),
                    detail=detail,
                )
            return self._fail(
                f"winget's update of '{wanted}' did not complete — the package is "
                "still pending an update. winget said:\n"
                f"{detail or 'no details were printed.'}",
                package_id=wanted, name=installed.get("Name", ""),
                version=old_version, source=installed.get("Source", ""),
                detail=detail,
            )
        if rc == 0:
            return self._fail(
                f"winget reported success updating '{wanted}', but its update list "
                "couldn't be read afterwards, so the update couldn't be confirmed.",
                package_id=wanted, name=installed.get("Name", ""),
                version=old_version, source=installed.get("Source", ""),
                detail="pending-update list unreadable after a reported success",
            )
        return self._fail(
            f"winget could not update '{wanted}'. winget said:\n"
            f"{detail or 'no details were printed.'}",
            package_id=wanted, name=installed.get("Name", ""),
            version=old_version, source=installed.get("Source", ""),
            detail=detail,
        )

    def update_all(self, *, confirm=False, cancel_token=None) -> dict:
        """Update EVERYTHING winget manages — the ONLY update-all
        path, and a separate explicit call: ``update()`` never
        passes ``--all``. The receipt is per-package: the pending
        list is read before and after the run, and the result
        names what actually updated and what is still pending."""
        refusal = self._need_confirm(
            confirm,
            "Updating every app downloads and installs new versions across this PC and",
        )
        if refusal:
            return {
                "ok": False, "package_id": None, "name": None,
                "version": "", "source": "", "state": "failed",
                "reason": refusal, "detail": "nothing was updated",
                "updated": [], "still_pending": [],
            }
        try:
            be = self._backend_or_real()
        except _EngineUnavailable as exc:
            return {
                "ok": False, "package_id": None, "name": None,
                "version": "", "source": "", "state": "failed",
                "reason": f"engine_unavailable: {exc.detail}",
                "detail": "nothing was updated",
                "updated": [], "still_pending": [],
            }
        before, _before_none = self._pending_updates(be)
        if before is not None and not before:
            return {
                "ok": True, "package_id": None, "name": None,
                "version": "", "source": "", "state": "up_to_date",
                "reason": "",
                "detail": "Everything winget manages is already up to date — nothing was updated.",
                "updated": [], "still_pending": [],
            }
        rc, out, err = be.run(
            ["winget", "upgrade", "--all", "--accept-source-agreements",
             "--accept-package-agreements"],
            _MUTATE_TIMEOUT_S, cancel_token,
        )
        detail = _tail(out) or _tail(err)
        if rc == -3:
            return {
                "ok": False, "package_id": None, "name": None,
                "version": "", "source": "", "state": "cancelled",
                "reason": (
                    "The winget update run was cancelled at the user's "
                    "request while it was still running — some apps may "
                    "have updated and others not; run the update check "
                    "to see what remains."
                ),
                "detail": "cancelled by the user",
                "updated": [], "still_pending": [],
            }
        if rc == -2:
            return {
                "ok": False, "package_id": None, "name": None,
                "version": "", "source": "", "state": "failed",
                "reason": (
                    "The update run timed out after 15 minutes — some apps may have "
                    "updated and others not; run the update check to see what remains."
                ),
                "detail": "timed out after 15 minutes",
                "updated": [], "still_pending": [],
            }
        if before is not None:
            after, _after_none = self._pending_updates(be)
            if after is not None:
                remaining = {row["Id"].lower() for row in after}
                done = [row for row in before if row["Id"].lower() not in remaining]
                stuck = [row for row in before if row["Id"].lower() in remaining]
                updated = [{
                    "name": row.get("Name", ""), "id": row.get("Id", ""),
                    "version": _clean_version(row),
                    "available": row.get("Available", ""),
                    "source": row.get("Source", ""),
                } for row in done]
                still = [{
                    "name": row.get("Name", ""), "id": row.get("Id", ""),
                    "version": _clean_version(row),
                    "available": row.get("Available", ""),
                    "source": row.get("Source", ""),
                } for row in stuck]
                summary = (f"{len(done)} of {len(before)} pending app update(s) completed"
                           + ("; no updates remain" if not stuck else
                              f"; still pending (did not complete): {len(stuck)}"))
                if rc == 0:
                    return {
                        "ok": True, "package_id": None, "name": None,
                        "version": "", "source": "", "state": "updated",
                        "reason": "", "detail": f"winget update run finished — {summary}.",
                        "updated": updated, "still_pending": still,
                    }
                return {
                    "ok": False, "package_id": None, "name": None,
                    "version": "", "source": "", "state": "failed",
                    "reason": f"winget's update run finished with errors — {summary}. "
                              f"winget said:\n{detail or 'no details were printed.'}",
                    "detail": detail,
                    "updated": updated, "still_pending": still,
                }
        if rc == 0:
            return {
                "ok": False, "package_id": None, "name": None,
                "version": "", "source": "", "state": "failed",
                "reason": (
                    "winget's update run finished without errors, but its "
                    "package list couldn't be read before and after, so "
                    "individual results couldn't be confirmed.\n" + detail
                ).strip(),
                "detail": detail,
                "updated": [], "still_pending": [],
            }
        return {
            "ok": False, "package_id": None, "name": None,
            "version": "", "source": "", "state": "failed",
            "reason": f"winget's update run finished with errors. winget said:\n{detail or 'no details were printed.'}",
            "detail": detail,
            "updated": [], "still_pending": [],
        }

    def remove(self, package_id, *, confirm=False, cancel_token=None) -> dict:
        """Remove one app via ``winget uninstall --id <id> --exact``.

        The deny-list is checked FIRST, before any winget call;
        the id must be installed (read from winget's own list);
        after the run the installed list is read again, and only
        a package no longer listed reports the version that was
        removed."""
        wanted = (package_id or "").strip() if isinstance(package_id, str) else ""
        if not wanted or " " in wanted:
            return self._fail(
                "remove needs one winget package id (a single token, e.g. 'VideoLAN.VLC') — "
                "a name is a search hint, resolve it first",
                package_id=package_id if isinstance(package_id, str) else None,
                detail="nothing was removed",
            )
        denied = _denied_reason(wanted)
        if denied:
            return self._fail(
                denied, package_id=wanted, detail="nothing was removed",
            )
        refusal = self._need_confirm(
            confirm,
            f"Removing '{wanted}' uninstalls the app from this PC and",
        )
        if refusal:
            return self._fail(refusal, package_id=wanted, detail="nothing was removed")
        try:
            be = self._backend_or_real()
        except _EngineUnavailable as exc:
            return self._fail(
                f"engine_unavailable: {exc.detail}",
                package_id=wanted, detail="nothing was removed",
            )
        installed, readable = self._installed_row(be, wanted)
        if installed is None:
            if not readable:
                return self._fail(
                    f"winget's installed list couldn't be read, so whether "
                    f"'{wanted}' is installed can't be told — nothing was removed.",
                    package_id=wanted, detail="installed list unreadable",
                )
            return self._fail(
                f"'{wanted}' is not installed (winget's list has no such package id), "
                "so there is nothing to remove.",
                package_id=wanted, detail="not installed",
            )
        old_version = _clean_version(installed)
        name = installed.get("Name", "")
        source = installed.get("Source", "")
        rc, out, err = be.run(
            ["winget", "uninstall", "--id", wanted, "--exact",
             "--accept-source-agreements"],
            _MUTATE_TIMEOUT_S, cancel_token,
        )
        detail = _tail(out) or _tail(err)
        if rc == -3:
            return self._fail(
                f"Removing '{wanted}' was cancelled at the user's request while it was "
                "still running — the app may have been removed and it may not; "
                "check the installed list before retrying.",
                package_id=wanted, name=name, version=old_version,
                source=source, state="cancelled",
                detail="cancelled by the user",
            )
        if rc == -2:
            return self._fail(
                f"Removing '{wanted}' timed out after 15 minutes — it may still be "
                "running in the background; check before retrying.",
                package_id=wanted, name=name, version=old_version,
                source=source, detail="timed out after 15 minutes",
            )
        after, readable2 = self._installed_row(be, wanted)
        if after is None and readable2:
            if rc == 0:
                return self._ok(
                    wanted, name, old_version, source, "removed",
                    detail="confirmed gone from winget's installed list",
                )
            return self._fail(
                f"winget's removal of '{wanted}' finished with errors, but the "
                "package is no longer in the installed list — winget said:\n"
                f"{detail or 'no details were printed.'}",
                package_id=wanted, name=name, version=old_version,
                source=source, detail=detail,
            )
        if after is not None:
            return self._fail(
                f"winget's removal of '{wanted}' did not complete — the package is "
                "still in the installed list. winget said:\n"
                f"{detail or 'no details were printed.'}",
                package_id=wanted, name=name, version=old_version,
                source=source, detail=detail,
            )
        return self._fail(
            f"winget reported the removal of '{wanted}', but its installed list "
            "couldn't be read afterwards, so the removal couldn't be confirmed.",
            package_id=wanted, name=name, version=old_version,
            source=source, detail="installed list unreadable after the run",
        )


# -- module-level default (the driver calls these) -------------------

_default = AppManagement()


def set_app_backend(backend) -> None:
    """Test seam (clipboard idiom): inject a fake backend for the
    module-level functions. Production code never calls this."""
    global _default
    _default = AppManagement(backend=backend)


def reset_app_backend() -> None:
    """Restore the module-level default (real backend, lazy)."""
    global _default
    _default = AppManagement()


def engine_available_default() -> bool:
    return _default.engine_available()


def engine_status_default() -> dict:
    return _default.engine_status()


def search(query) -> dict:
    return _default.search(query)


def resolve(query) -> dict:
    return _default.resolve(query)


def list_installed() -> dict:
    return _default.list_installed()


def list_updates() -> dict:
    return _default.list_updates()


def install(query, *, confirm=False, cancel_token=None) -> dict:
    return _default.install(query, confirm=confirm, cancel_token=cancel_token)


def update(package_id, *, confirm=False, cancel_token=None) -> dict:
    return _default.update(package_id, confirm=confirm, cancel_token=cancel_token)


def update_all(*, confirm=False, cancel_token=None) -> dict:
    return _default.update_all(confirm=confirm, cancel_token=cancel_token)


def remove(package_id, *, confirm=False, cancel_token=None) -> dict:
    return _default.remove(package_id, confirm=confirm, cancel_token=cancel_token)
