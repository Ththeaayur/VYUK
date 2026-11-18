import csv
import io
import time
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_system2_helpers import (
    _NOT_WINDOWS,
    _is_windows,
    _ps_structured,
    _run,
    _tail,
)


# ---------------------------------------------------------------------------
# RESTORE POINTS
# ---------------------------------------------------------------------------


def _clean_restore_description(text) -> str:
    """Sanitise a restore-point description for safe embedding in a
    PowerShell single-quoted string: quotes, backtick, dollar and every
    control character are stripped (so nothing can break out of the
    string or trigger interpolation), whitespace is collapsed and the
    result is capped at 200 characters."""
    raw = str(text or "")
    cleaned = "".join(
        ch for ch in raw
        if ch not in "\"'`$" and (ch.isprintable() or ch == " "))
    cleaned = " ".join(cleaned.split())[:200].strip()
    return cleaned or "VYUK restore point"


def _newest_restore_point() -> tuple[str, float] | None:
    """(description, created-epoch) of the newest restore point, or
    None when the list can't be read or holds no points. The
    CreationDate arrives as a WMI datetime string
    ('yyyyMMddHHmmss.ffffff+offset') — parsed positionally from its
    digit prefix, never from a localised label."""
    command = (
        "Get-ComputerRestorePoint | "
        "Sort-Object CreationDate -Descending | Select-Object -First 1 | "
        "ForEach-Object { $_.Description + [char]0x1f + $_.CreationDate }"
    )
    rc, rows, _err = _ps_structured(command, timeout=60)
    if rc != 0 or not rows or len(rows[0]) < 2:
        return None
    desc = rows[0][0].strip()
    digits = "".join(ch for ch in rows[0][1] if ch.isdigit())[:14]
    if len(digits) < 14:
        return None
    try:
        import datetime

        created = datetime.datetime.strptime(
            digits, "%Y%m%d%H%M%S").timestamp()
    except ValueError:
        return None
    return desc, created


def create_restore_point(description: str = "VYUK restore point",
                         cancel_token=None) -> str:
    """Create a Windows system restore point (needs an admin context).

    B20: ``cancel_token`` ends the Checkpoint-Computer run
    promptly on Stop; a cancelled run returns the cancelled
    DICT (a string return would wrap as success). Whether a
    point was actually created when stopped is unknown — the
    message says to check the restore-point list, never a
    claim either way.
    """
    desc = _clean_restore_description(description)
    if not _is_windows():
        return f"Restore points are a Windows feature — {_NOT_WINDOWS}"
    command = f"Checkpoint-Computer -Description '{desc}'"
    started = time.time()
    rc, out, err = _run(["powershell", "-NoProfile", "-Command", command],
                        timeout=180, cancel_token=cancel_token)
    if rc == -3:
        from .funnel.tool_result import cancelled_result
        return cancelled_result(
            f"Creating the restore point '{desc}' was cancelled at "
            "the user's request while it was still running — "
            "whether a point was created is unknown, so check "
            "the restore-point list before relying on one.")
    if rc == 0:
        # Verify (audit m8): Checkpoint-Computer's exit code alone is
        # the request being accepted — confirm a point carrying this
        # description actually appeared at the top of the list.
        newest = _newest_restore_point()
        if newest is not None:
            seen_desc, seen_at = newest
            fresh = seen_at >= started - 300  # slack for clock skew
            if fresh and seen_desc.strip().lower() == desc.lower():
                return (f"Restore point created and confirmed: '{desc}' "
                        "(it's the newest point in the list).")
        return (f"Restore point requested: '{desc}' — Checkpoint-Computer "
                "reported success, but I couldn't confirm a new point "
                "with that description in the restore-point list "
                "afterwards, so treat the creation as unconfirmed.")
    detail = _tail(err or out) or "Checkpoint-Computer failed."
    lowered = detail.lower()
    if ("access" in lowered and "denied" in lowered) or "0x80041003" in lowered \
            or "permission" in lowered or "elevat" in lowered \
            or "administrator" in lowered:
        return ("Couldn't create the restore point — it needs an "
                "elevated/admin context, which VYUK isn't running in. "
                f"Windows said: {detail}")
    if "already been created" in lowered or "24 hours" in lowered \
            or "0x800423f7" in lowered:
        return ("Couldn't create the restore point — Windows only allows "
                "one restore point per 24 hours and one was already made "
                f"in that window. Windows said: {detail}")
    return f"Couldn't create the restore point: {detail}"


# ---------------------------------------------------------------------------
# SCHEDULED TASKS
# ---------------------------------------------------------------------------


def _query_task_status(name: str) -> str | None:
    """The named task's Status column via schtasks /Query (CSV), or
    None when the query fails or its output can't be read. The CSV
    shape mirrors the list branch: name, next run, status."""
    rc, out, _err = _run(
        ["schtasks", "/Query", "/TN", name, "/FO", "CSV", "/NH"],
        timeout=60)
    if rc != 0 or not out.strip():
        return None
    try:
        for row in csv.reader(io.StringIO(out)):
            if len(row) >= 3 and row[0].strip().lower() == name.lower():
                return row[2].strip()
    except csv.Error:
        return None
    return None


def _parse_task_rows(out: str, include_microsoft: bool = False) -> list[tuple[str, str, str]]:
    """Parse `schtasks /Query /FO CSV /NH` output into
    (name, next-run, status) rows. Pure string work — no schtasks
    runs here. Built-in \\Microsoft\\ tasks are skipped unless
    include_microsoft is true; an unparseable answer yields []."""
    tasks: list[tuple[str, str, str]] = []
    try:
        for row in csv.reader(io.StringIO(out)):
            if len(row) < 3:
                continue
            tname = row[0].strip()
            if not tname:
                continue
            if not include_microsoft and tname.startswith("\\Microsoft\\"):
                continue
            tasks.append((tname, row[1].strip(), row[2].strip()))
    except csv.Error:
        return []
    return tasks


def task_scheduler_control(action: str = "list", name: str = "",
                           include_microsoft: bool = False) -> str:
    """List the user's scheduled tasks, or run/enable/disable one.
    include_microsoft (list only, default false) also shows the
    built-in \\Microsoft\\ tasks the list normally hides."""
    act = (action or "list").strip().lower()
    if act not in ("list", "run", "enable", "disable"):
        return (f"Unknown scheduled-task action: '{action}'. "
                "Use one of: list, run, enable, disable.")
    if not _is_windows():
        return f"Scheduled tasks are a Windows feature — {_NOT_WINDOWS}"
    if act == "list":
        rc, out, err = _run(["schtasks", "/Query", "/FO", "CSV", "/NH"],
                            timeout=60)
        if rc != 0:
            return (f"Couldn't list scheduled tasks: "
                    f"{_tail(err or out) or 'schtasks failed.'}")
        tasks = _parse_task_rows(out, include_microsoft=bool(include_microsoft))
        if not tasks:
            if out.strip():
                return ("Couldn't read schtasks' answer — it came back in "
                        "a shape VYUK doesn't recognise.")
            if include_microsoft:
                return "No scheduled tasks found at all (Microsoft tasks included)."
            return ("No user scheduled tasks found "
                    "(built-in Microsoft tasks are not shown).")
        cap = 30
        shown = tasks[:cap]
        if include_microsoft:
            lines = [f"Scheduled tasks ({len(tasks)} task(s), "
                     "Microsoft tasks included):"]
        else:
            lines = [f"Scheduled tasks ({len(tasks)} user task(s), "
                     "Microsoft tasks hidden):"]
        for tname, nxt, status in shown:
            lines.append(f"• {tname} — next: {nxt or '—'}, status: {status or '—'}")
        if len(tasks) > cap:
            lines.append(f"…showing the first {cap} of {len(tasks)} tasks.")
        return "\n".join(lines)
    wanted = (name or "").strip()
    if not wanted:
        return (f"Tell me which task to {act}, e.g. name='\\MyTask' — "
                "use action='list' to see the task names.")
    if act == "run":
        args = ["schtasks", "/Run", "/TN", wanted]
        done = f"Started scheduled task '{wanted}' (schtasks confirmed the run request)."
    elif act == "enable":
        args = ["schtasks", "/Change", "/TN", wanted, "/ENABLE"]
        done = None
    else:
        args = ["schtasks", "/Change", "/TN", wanted, "/DISABLE"]
        done = None
    rc, out, err = _run(args, timeout=60)
    if rc == 0 and act in ("enable", "disable"):
        # Verify (audit m7): /Change's exit code is the request being
        # accepted — read the task's Status back and report what it
        # actually says now.
        status = _query_task_status(wanted)
        verb = "Enabled" if act == "enable" else "Disabled"
        if status is None:
            return (f"{verb} scheduled task '{wanted}' (schtasks "
                    "accepted the change; I couldn't read its status "
                    "back to confirm).")
        is_disabled = status.strip().lower() == "disabled"
        if (act == "disable") == is_disabled:
            return (f"{verb} scheduled task '{wanted}' "
                    f"(confirmed — its status now reads '{status}').")
        return (f"schtasks accepted the {act} for '{wanted}', but its "
                f"status still reads '{status}' — the change didn't "
                "take.")
    if rc == 0:
        return done
    detail = _tail(out or err) or "schtasks failed."
    if "access is denied" in detail.lower():
        detail += " (This usually needs an elevated/admin context.)"
    return f"Couldn't {act} scheduled task '{wanted}': {detail}"


# ---------------------------------------------------------------------------
# PRINTERS
# ---------------------------------------------------------------------------


# One printer-list query shared by list_printers and the
# set_default_printer read-back: each row is name + \x1f + '1' when
# that printer is the one flagged default.
_PRINTER_LIST_PS = (
    "$defs = @(Get-CimInstance Win32_Printer | "
    "Where-Object { $_.Default } | Select-Object -ExpandProperty Name); "
    "Get-Printer | ForEach-Object { $_.Name + [char]0x1f + "
    "$(if ($defs -contains $_.Name) { '1' } else { '0' }) }"
)


def _default_printer_name() -> str | None:
    """The name the printer list currently flags as default, or None
    when the list can't be read or nothing is flagged."""
    rc, rows, _err = _ps_structured(_PRINTER_LIST_PS, timeout=60)
    if rc != 0:
        return None
    for row in rows:
        if len(row) > 1 and row[1] == "1" and row[0]:
            return row[0]
    return None


def list_printers() -> str:
    """Every installed printer, with the default marked. Read-only."""
    if not _is_windows():
        return f"Printer management here uses Windows printing — {_NOT_WINDOWS}"
    command = _PRINTER_LIST_PS
    rc, rows, err = _ps_structured(command, timeout=60)
    if rc != 0:
        return (f"Couldn't list printers: "
                f"{_tail(err) or 'the Windows print cmdlets failed.'}")
    printers = [(row[0], len(row) > 1 and row[1] == "1")
                for row in rows if row and row[0]]
    if not printers:
        return "No printers installed on this PC."
    lines = [f"Printers ({len(printers)}):"]
    for pname, is_default in printers:
        lines.append(f"• {pname}" + (" (default)" if is_default else ""))
    return "\n".join(lines)


def set_default_printer(name: str) -> str:
    """Make an installed printer the default via winspool.drv."""
    wanted = (name or "").strip()
    if not wanted:
        return ("Tell me which printer should become the default — "
                "use list_printers to see the installed names.")
    if not _is_windows():
        return f"Default-printer changes are a Windows feature — {_NOT_WINDOWS}"
    try:
        import ctypes

        spool = ctypes.WinDLL("winspool.drv", use_last_error=True)
        spool.SetDefaultPrinterW.argtypes = [ctypes.c_wchar_p]
        spool.SetDefaultPrinterW.restype = ctypes.c_bool
        ok = spool.SetDefaultPrinterW(wanted)
        if ok:
            # Verify (audit m5): the API's word alone isn't the
            # house standard — read the printer list back and report
            # the name it actually flags as default.
            actual = _default_printer_name()
            if actual is not None and actual.lower() == wanted.lower():
                return (f"Default printer is now '{wanted}' "
                        "(confirmed — it comes back flagged as the "
                        "default in the printer list).")
            if actual is not None:
                return (f"Windows accepted '{wanted}' as the default "
                        f"printer, but the printer list still flags "
                        f"'{actual}' as the default — the change "
                        "didn't take. Windows' 'let Windows manage "
                        "my default printer' setting can do this.")
            return (f"Default printer set to '{wanted}' — Windows "
                    "accepted the change, but I couldn't read the "
                    "printer list back to confirm it took.")
        code = ctypes.get_last_error()
        return (f"Windows refused to set '{wanted}' as the default printer "
                f"(error {code}). Check the name matches an installed "
                "printer exactly — use list_printers to see them.")
    except Exception as e:  # noqa: BLE001
        return f"Couldn't change the default printer: {e}"


# ---------------------------------------------------------------------------
# VPN — saved rasdial connections
# ---------------------------------------------------------------------------


def vpn_control(action: str = "status", name: str = "") -> str:
    """Status / connect / disconnect for saved Windows VPN connections."""
    act = (action or "status").strip().lower()
    if act not in ("status", "connect", "disconnect"):
        return (f"Unknown VPN action: '{action}'. "
                "Use one of: status, connect, disconnect.")
    if not _is_windows():
        return f"rasdial VPN connections are a Windows feature — {_NOT_WINDOWS}"
    if act == "status":
        rc, out, err = _run(["rasdial"], timeout=30)
        text = out or err
        if "no connections" in text.lower():
            return "No VPN connection is active right now."
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if "connected to" in text.lower():
            names = []
            seen_marker = False
            for ln in lines:
                if ln.lower().startswith("connected to"):
                    seen_marker = True
                    continue
                if seen_marker:
                    if ln.lower().startswith("command completed"):
                        break
                    names.append(ln)
            if names:
                return f"VPN connected: {', '.join(names)}."
        preview = "\n".join(lines[:5]) if lines else "no output"
        return ("Couldn't confidently read the VPN status from rasdial. "
                f"It said:\n{preview}")
    if act == "disconnect":
        rc, out, err = _run(["rasdial", "/disconnect"], timeout=30)
        text = out or err
        if "no connections" in text.lower():
            return "No VPN was connected, so nothing was disconnected."
        if rc == 0:
            return "VPN disconnected (rasdial confirmed)."
        return f"Couldn't disconnect the VPN: {_tail(text) or 'rasdial failed.'}"
    wanted = (name or "").strip()
    if not wanted:
        return ("Tell me which saved VPN connection to dial, "
                "e.g. name='Office VPN'. Only connections with saved "
                "credentials can connect — rasdial can't prompt here.")
    rc, out, err = _run(["rasdial", wanted], timeout=90)
    text = out or err
    if rc == 0:
        return (f"Connected to VPN '{wanted}' — rasdial completed the "
                "connection.")
    return (f"Couldn't connect to VPN '{wanted}': "
            f"{_tail(text) or 'rasdial failed.'}")


# ---------------------------------------------------------------------------
# DISPLAY — screen-off timeout via powercfg
# ---------------------------------------------------------------------------


def _read_video_idle_seconds() -> tuple[int, int] | None:
    """(ac_seconds, dc_seconds) for the display-off setting, read
    back with powercfg /query, or None when it can't be read.

    The query is scoped to the single VIDEOIDLE setting; its output
    lists several '…: 0x…' values (min, max, increment, then the two
    current indexes), so the AC and DC indexes are taken as the LAST
    two hex values — position, never a localised label."""
    import re

    rc, out, _err = _run(
        ["powercfg", "/query", "SCHEME_CURRENT", "SUB_VIDEO", "VIDEOIDLE"],
        timeout=30)
    if rc != 0:
        return None
    values = re.findall(r":\s*0x([0-9a-fA-F]+)", out or "")
    if len(values) < 2:
        return None
    try:
        return int(values[-2], 16), int(values[-1], 16)
    except ValueError:
        return None


def _timeout_phrase(seconds: int) -> str:
    """A stored timeout in seconds as the words the receipts use."""
    if seconds == 0:
        return "never turn off"
    if seconds % 60 == 0:
        return f"turn off after {seconds // 60} minute(s)"
    return f"turn off after {seconds} second(s)"


def set_screen_timeout(minutes: int, on: str = "both") -> str:
    """Set how long the display stays on before turning off."""
    try:
        mins = int(minutes)
    except (TypeError, ValueError):
        return (f"Screen timeout needs a whole number of minutes, "
                f"got '{minutes}'.")
    if mins < 0 or mins > 600:
        return ("Screen timeout must be between 0 and 600 minutes "
                f"(0 = never turn the screen off); got {mins}.")
    scope = (on or "both").strip().lower()
    if scope not in ("battery", "plugged", "both"):
        return (f"Unknown scope: '{on}'. "
                "Use 'battery', 'plugged' or 'both'.")
    if not _is_windows():
        return f"Screen timeout via powercfg is a Windows feature — {_NOT_WINDOWS}"
    targets = []
    if scope in ("battery", "both"):
        targets.append(("on battery", "monitor-timeout-dc"))
    if scope in ("plugged", "both"):
        targets.append(("when plugged in", "monitor-timeout-ac"))
    failures = []
    done_labels = []
    for label, setting in targets:
        rc, out, err = _run(["powercfg", "/change", setting, str(mins)],
                            timeout=30)
        if rc == 0:
            done_labels.append(label)
        else:
            failures.append(f"{label}: {_tail(err or out) or 'powercfg failed.'}")
    amount = "never turn off" if mins == 0 else f"turn off after {mins} minute(s)"
    if failures and not done_labels:
        return ("Couldn't set the screen timeout — "
                + "; ".join(failures))
    if failures:
        return (f"Screen set to {amount} {' and '.join(done_labels)}, but "
                "one change failed — " + "; ".join(failures))
    # Verify (audit m6): read the stored VIDEOIDLE values back and
    # report what Windows actually holds now, per scope changed.
    readback = _read_video_idle_seconds()
    if readback is None:
        return (f"Screen timeout set: {amount} ({' and '.join(done_labels)}). "
                "powercfg accepted the change (I couldn't read the value "
                "back to confirm).")
    ac_secs, dc_secs = readback
    wanted_secs = mins * 60
    per_scope = {"on battery": dc_secs, "when plugged in": ac_secs}
    mismatched = [
        f"{label} now reads {_timeout_phrase(per_scope[label])}"
        for label in done_labels if per_scope[label] != wanted_secs
    ]
    if mismatched:
        return (f"Screen timeout: powercfg accepted the change, but the "
                "read-back doesn't match — " + "; ".join(mismatched)
                + ". The change may not have taken.")
    return (f"Screen timeout set and confirmed: {amount} "
            f"({' and '.join(done_labels)}) — read back from Windows.")
