"""v5.72 (Business track, wave 18, BZ-7.6) — identity owned by
Windows profiles.

BZ-7.6 is the one multi-user item that is INV-13-compatible as
written: VYUK mints no user accounts of its own. Identity comes
from the operating system's profile — on Windows, the signed-in
Windows user (USERNAME + USERPROFILE); on POSIX, the login user
(USER/LOGNAME + HOME, getpass fallback). The app reads identity
afresh on every call; it never stores a password, token, or
credential, and it never invents a second user.

What this module does NOT do:
  - No SSO, no SCIM, no roles (BZ-2 — parked behind the
    INV-13/INV-8 amendment shape, which is the parent's call).
  - No multi-principal sessions, no guest/team scopes (BZ-7
    remainder — parked for the same reason).
  - No profile switching: one OS profile = one identity. A
    different Windows user signing in is a different identity
    by construction, not a session switch inside VYUK.

Stdlib only. No network. No funnel registration. No writes
except where the caller explicitly asks for a path (none here:
this module only resolves).
"""

from __future__ import annotations

import getpass
import os
import platform


class IdentityError(ValueError):
    """Raised when no OS identity can be resolved (fail-closed)."""


def _env(*names):
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return ""


def resolve_identity(env=None, system=None) -> dict:
    """Resolve the current OS-profile identity.

    ``env`` / ``system`` are injectable for tests only; production
    callers pass nothing and get the real OS values. Returns
    {username, profile_path, platform, source}. Raises
    IdentityError when nothing resolves — never invents a name.
    """
    environ = os.environ if env is None else env
    sysname = platform.system() if system is None else system

    def _get(*names):
        for name in names:
            value = environ.get(name)
            if value and isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    if sysname == "Windows":
        username = _get("USERNAME")
        profile = _get("USERPROFILE")
        source = "windows-profile"
    else:
        username = _get("USER", "LOGNAME")
        profile = _get("HOME")
        source = "posix-profile"
    if not username and env is None:
        # Production path only: the real OS is the fallback.
        # An injected env IS the whole world for that call, so
        # a missing name there must fail closed, not leak the
        # real environment past the injection.
        try:
            username = getpass.getuser().strip()
        except Exception:  # noqa: BLE001 — no identity available
            username = ""
    if not username:
        raise IdentityError(
            "no OS-profile username resolves (USERNAME/USER/LOGNAME "
            "all empty and getpass failed); identity is owned by "
            "the OS profile, never invented")
    return {
        "username": username,
        "profile_path": profile,
        "platform": sysname,
        "source": source,
    }


def is_windows_profile(env=None, system=None) -> bool:
    """True when identity comes from a Windows profile."""
    try:
        return resolve_identity(env=env, system=system)["platform"] == "Windows"
    except IdentityError:
        return False


def profile_store_path(app_dir_name: str, env=None, system=None) -> str:
    """A per-profile store path for an app directory name, under
    the OS profile (Windows: %USERPROFILE%\\<name>; POSIX:
    $HOME/.<name>). Resolves only — creates nothing."""
    ident = resolve_identity(env=env, system=system)
    base = ident["profile_path"]
    if not base:
        raise IdentityError("OS profile path is unknown; cannot "
                            "resolve a per-profile store path")
    name = (app_dir_name or "").strip().strip("/\\")
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise IdentityError("app_dir_name must be a single plain "
                            "directory name, got %r" % app_dir_name)
    if ident["platform"] == "Windows":
        return base.rstrip("\\") + "\\" + name
    return base.rstrip("/") + "/." + name
