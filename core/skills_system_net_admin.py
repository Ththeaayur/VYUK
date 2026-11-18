"""
System skills VIII for VYUK (v5.31 wave-2 pack): three
small read-only inspections that answer questions the earlier
system packs don't cover — what's actually in the hosts file,
whether PATH has gone stale, and whether the clock is really
being kept in sync. Nothing in this pack changes any system
state; each tool just reads and reports, in plain words, and
says so honestly when a reading can't be taken.

The Windows surface sits behind module-level seams (_hosts_path
for the file location, _sync_query for the sync command, the
family's _run channel from skills_system_monitor) so tests can fake it;
hosts_view and path_audit are plain file/environment reads and
work the same on Linux. The w32tm/timedatectl output parsers are
pure functions of the command text.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) —
this module never touches the registry singleton itself.
"""

import ipaddress
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers (each pack carries its own copies — the house convention)
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _tail(text: str, limit: int = 300) -> str:
    """The last meaningful line of a command's output, for reports."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1][:limit] if lines else ""


def _run_command(args: list[str], timeout: int = 20):
    """Run a command through the family's runner (skills_system_monitor
    ._run: returncode, stdout, stderr; -1 when the command could
    not be started at all, -2 on timeout). Never raises."""
    from . import skills_system_monitor

    return skills_system_monitor._run(args, timeout=timeout)


# ---------------------------------------------------------------------------
# HOSTS VIEW — the active name→address overrides, comments aside
# ---------------------------------------------------------------------------


def _hosts_path() -> Path:
    """Where the hosts file lives on this platform."""
    if _is_windows():
        root = os.environ.get("SystemRoot") or r"C:\Windows"
        return Path(root) / "System32" / "drivers" / "etc" / "hosts"
    return Path("/etc/hosts")


def _parse_hosts(text: str) -> list[tuple[str, list[str]]]:
    """Active (address, names) mappings from hosts-file text:
    blank lines and comments skipped, inline comments stripped,
    lines whose first token isn't an IP address ignored."""
    mappings: list[tuple[str, list[str]]] = []
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            ipaddress.ip_address(parts[0])
        except ValueError:
            continue
        mappings.append((parts[0], parts[1:]))
    return mappings


def hosts_view() -> str:
    """List the hosts file's active mappings."""
    path = _hosts_path()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return (f"There's no hosts file at {path} — that's "
                "unusual; name lookups are going entirely to DNS. "
                "Nothing was changed.")
    except OSError as exc:
        return (f"I couldn't read the hosts file at {path} "
                f"({exc.strerror or exc}). Nothing was changed.")
    mappings = _parse_hosts(text)
    if not mappings:
        return (f"The hosts file at {path} holds no active "
                "mappings — every line in it is a comment or "
                "blank, so all name lookups go to DNS.")
    lines = [f"The hosts file ({path}) has {len(mappings)} active "
             f"mapping{'s' if len(mappings) != 1 else ''}:"]
    for address, names in mappings[:40]:
        lines.append(f"• {address} → {', '.join(names)}")
    if len(mappings) > 40:
        lines.append(f"…and {len(mappings) - 40} more "
                     "mapping(s).")
    lines.append("These are local overrides: a name listed here "
                 "resolves to that address instead of whatever "
                 "DNS says.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PATH AUDIT — stale, duplicated and relative PATH entries
# ---------------------------------------------------------------------------


def _path_entries() -> list[str]:
    """The PATH entries, in order (the seam tests can fake)."""
    return os.environ.get("PATH", "").split(os.pathsep)


def _dup_key(entry: str) -> str:
    """Comparison key for duplicate detection: the normalised
    path, case-folded on Windows where the filesystem is."""
    key = os.path.normpath(entry)
    return key.lower() if _is_windows() else key


def path_audit() -> str:
    """Report PATH entries that don't exist, duplicates and
    relative entries."""
    entries = _path_entries()
    total = len(entries)
    if total == 0 or entries == [""]:
        return ("PATH is empty — it has no entries at all, which "
                "would stop most programs from being found by "
                "name.")
    empty = sum(1 for e in entries if not e.strip())
    named = [e for e in entries if e.strip()]
    missing: list[str] = []
    for entry in named:
        if not os.path.isdir(entry) and entry not in missing:
            missing.append(entry)
    relative = [e for e in named if not os.path.isabs(e)]
    counts: dict[str, int] = {}
    display: dict[str, str] = {}
    for entry in named:
        key = _dup_key(entry)
        counts[key] = counts.get(key, 0) + 1
        display.setdefault(key, entry)
    duplicates = [(display[key], counts[key]) for key in counts
                  if counts[key] > 1]

    if not missing and not duplicates and not relative and not empty:
        return (f"PATH checks out: all {total} entries exist as "
                "folders, none are duplicated or relative — "
                "nothing stale to clean up.")
    lines = [f"PATH audit — {total} entr"
             f"{'ies' if total != 1 else 'y'}:"]
    if missing:
        lines.append(f"• {len(missing)} entr"
                     f"{'ies' if len(missing) != 1 else 'y'} "
                     "don't exist as folders (stale — left "
                     "behind by uninstalled or moved programs):")
        for entry in missing[:10]:
            lines.append(f"  – {entry}")
        if len(missing) > 10:
            lines.append(f"  …and {len(missing) - 10} more.")
    if duplicates:
        for entry, count in duplicates:
            lines.append(f"• Duplicated: {entry} appears "
                         f"{count} times.")
    if relative:
        lines.append("• Relative entries (they point wherever "
                     "the calling program happens to be "
                     "running): " + ", ".join(relative))
    if empty:
        lines.append("• One entry is empty, which some programs "
                     "treat as 'the current folder'."
                     if empty == 1 else
                     f"• {empty} entries are empty, which some "
                     "programs treat as 'the current folder'.")
    lines.append("I haven't changed PATH — this is a report "
                 "only; the stale entries are safe to remove in "
                 "the system settings if nothing points there "
                 "on purpose.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# TIME SYNC STATUS — is the clock actually being kept right?
# ---------------------------------------------------------------------------


def _sync_query():
    """The platform's clock-sync reading as (rc, stdout, stderr)
    — the seam tests fake for the Windows wording. Windows asks
    w32tm; elsewhere timedatectl (whose 'System clock
    synchronized' line is the standard Linux reading)."""
    if _is_windows():
        return _run_command(["w32tm", "/query", "/status"],
                            timeout=20)
    return _run_command(["timedatectl"], timeout=15)


def _fields(text: str) -> dict[str, str]:
    """'Key: value' lines of a command's output, keys lowered."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip().lower()] = value.strip()
    return out


def _w32tm_report(rc: int, out: str, err: str) -> str:
    lowered = (out or "").lower()
    if "0x80070426" in lowered \
            or "service has not been started" in lowered:
        return ("The Windows Time service isn't running, so this "
                "PC's clock isn't syncing with any time server — "
                "it drifts on its own until the service is "
                "started again (Settings → Time & language → "
                "Date & time).")
    if rc != 0 or "error occurred" in lowered:
        detail = _tail(err or out) or "w32tm didn't answer."
        return (f"Couldn't read the clock-sync status — w32tm "
                f"said: {detail}")
    fields = _fields(out)
    last = fields.get("last successful sync time", "")
    source = fields.get("source", "")
    if not last or last.lower().startswith("unspecified") \
            or "local cmos" in source.lower():
        return ("This PC's clock has never successfully synced "
                "with a time server — it's running on the "
                "computer's own hardware (CMOS) clock, so if "
                "that's drifted, nothing is correcting it. "
                "Check Date & time settings and that the "
                "Windows Time service is running.")
    src = f", from {source}" if source else ""
    return (f"The clock is being kept in sync. Last successful "
            f"sync: {last}{src}.")


def _timedatectl_report(rc: int, out: str, err: str) -> str:
    if rc == -1:
        return ("Couldn't check clock sync — timedatectl isn't "
                "available on this system, and there's no other "
                "standard reading to take, so I won't guess.")
    if rc != 0:
        detail = _tail(err or out) or "timedatectl didn't answer."
        return (f"Couldn't read the clock-sync status — "
                f"timedatectl said: {detail}")
    fields = _fields(out)
    synced = fields.get("system clock synchronized", "").lower()
    ntp = fields.get("ntp service", "").lower()
    if synced == "yes":
        tail = (" The NTP service is active."
                if ntp == "active" else "")
        return ("The clock is being kept in sync — timedatectl "
                "reports the system clock as synchronized." + tail)
    if synced == "no":
        tail = (" The NTP service is inactive, which is why."
                if ntp == "inactive" else "")
        return ("The clock is NOT synchronized — timedatectl "
                "says so plainly, so the time may have drifted."
                + tail)
    return ("timedatectl answered but didn't state whether the "
            "clock is synchronized, so I won't guess either way.")


def time_sync_status() -> str:
    """Plain verdict on clock sync: last sync + source on
    Windows, the synchronized yes/no on Linux."""
    rc, out, err = _sync_query()
    if _is_windows():
        return _w32tm_report(rc, out, err)
    return _timedatectl_report(rc, out, err)


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (core/funnel/tools_registry.py wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every System V pack tool on a ToolRegistry-like
    object."""
    registry.register(
        name="hosts_view",
        description="Show the hosts file's active name-to-address mappings, with comments and blank lines skipped and a count of what's there. Use when a website resolves to the wrong place, a name is being blocked or redirected locally, or the user asks what the hosts file says. Read-only — nothing is edited.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=hosts_view,
    )
    registry.register(
        name="path_audit",
        description="Audit PATH for rot: entries that no longer exist on disk (the classic leftovers from uninstalled or moved programs), entries listed more than once, and relative entries. Use when a program 'isn't found' from one place but works from another, or for a general PATH tidy-up check. Read-only — it reports and never edits PATH.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=path_audit,
    )
    registry.register(
        name="time_sync_status",
        description="Say plainly whether this PC's clock is actually being kept in sync: on Windows, the last successful sync time and its source from w32tm — or that it has never synced, or that the Windows Time service isn't running; on Linux, timedatectl's synchronized verdict. Use when timestamps look wrong, meetings land at odd times, or sign-ins complain about clock skew. Read-only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=time_sync_status,
    )
