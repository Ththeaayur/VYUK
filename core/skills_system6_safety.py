from datetime import datetime
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_system6_helpers import (
    _NOT_WINDOWS,
    _clamp_int,
    _is_windows,
    _now,
    _ps_events,
    _ps_run,
    _tail,
)


# ---------------------------------------------------------------------------
# RESTORE POINT BEFORE CHANGES — create, then confirm from the list
# ---------------------------------------------------------------------------


def restore_point_before_changes(description: str = "",
                                 cancel_token=None) -> str:
    """Create a restore point via the system2 machinery, then
    confirm it from the restore-point list (verify after act).

    B20: ``cancel_token`` ends the Checkpoint-Computer run
    promptly on Stop; a cancelled run returns the cancelled
    DICT (see create_restore_point — a string return would
    wrap as success).
    """
    if not _is_windows():
        return (f"Restore points are a Windows feature — "
                f"{_NOT_WINDOWS}")
    from . import skills_system_monitor as sys2

    desc = sys2._clean_restore_description(
        description or "VYUK pre-change restore point "
        + datetime.now().strftime("%Y-%m-%d %H:%M"))
    started = _now()
    rc, out, err = _ps_run(
        f"Checkpoint-Computer -Description '{desc}'", timeout=180,
        cancel_token=cancel_token)
    if rc == -3:
        from .funnel.tool_result import cancelled_result
        return cancelled_result(
            f"Creating the restore point '{desc}' was cancelled at "
            "the user's request while it was still running — "
            "whether a point was created is unknown, so check "
            "the restore-point list before relying on one.")
    if rc != 0:
        detail = sys2._tail(err or out) or "Checkpoint-Computer failed."
        lowered = detail.lower()
        if ("access" in lowered and "denied" in lowered) \
                or "0x80041003" in lowered or "permission" in lowered \
                or "elevat" in lowered or "administrator" in lowered:
            return ("Couldn't create the restore point — it needs "
                    "an elevated/admin context, which VYUK "
                    f"isn't running in. Windows said: {detail}")
        if "already been created" in lowered \
                or "24 hours" in lowered or "0x800423f7" in lowered:
            return ("Couldn't create the restore point — Windows "
                    "only allows one restore point per 24 hours "
                    "and one was already made in that window. "
                    f"Windows said: {detail}")
        return f"Couldn't create the restore point: {detail}"
    newest = sys2._newest_restore_point()
    if newest is not None:
        seen_desc, seen_at = newest
        if seen_at >= started - 300 \
                and seen_desc.strip().lower() == desc.lower():
            when = datetime.fromtimestamp(seen_at).strftime(
                "%H:%M on %d %b %Y")
            return (f"Restore point ready and confirmed: "
                    f"'{desc}', created at {when}. I read the "
                    "restore-point list back and it's the newest "
                    "point there — restore to this one if the "
                    "changes go wrong.")
    return ("Checkpoint-Computer reported success, but I couldn't "
            "confirm a new point with that description in the "
            "restore-point list afterwards, so treat the creation "
            "as UNCONFIRMED — don't rely on it yet.")


# ---------------------------------------------------------------------------
# APP CRASH DIGEST — Application-log errors grouped by app/source
# ---------------------------------------------------------------------------


def _crash_events(days: int) -> tuple[list[list[str]], str]:
    """Raw event rows [when, provider, id, first-data-value] from
    the Application log, plus an error tail. The first EventData
    value of the classic crash event (ID 1000) is positionally
    the faulting application's name — read from the event XML
    structure, never from localised message text."""
    command = (
        f"$start = (Get-Date).AddDays(-{days}); "
        "Get-WinEvent -FilterHashtable @{LogName='Application'; "
        "Level=@(1,2); StartTime=$start} -MaxEvents 300 "
        "-ErrorAction SilentlyContinue | ForEach-Object { "
        "[xml]$x = $_.ToXml(); $first = ''; "
        "if ($x.Event.EventData -and $x.Event.EventData.Data) { "
        "$first = @($x.Event.EventData.Data)[0].InnerText }; "
        "$_.TimeCreated.ToString('yyyy-MM-dd HH:mm') + [char]0x1f "
        "+ $_.ProviderName + [char]0x1f + $_.Id + [char]0x1f "
        "+ $first }"
    )
    rc, rows, err = _ps_events(command, timeout=90)
    return rows, ("" if rc == 0 else _tail(err))


def _group_crashes(rows: list[list[str]]) -> list[dict]:
    """Group event rows into [{name, count, latest, kind}] —
    kind 'app' for ID-1000 faulting-application names, 'source'
    for provider names. Worst first."""
    groups: dict[str, dict] = {}
    for row in rows:
        if len(row) < 3:
            continue
        when, provider, event_id = row[0], row[1], row[2]
        first = row[3].strip() if len(row) > 3 else ""
        if event_id == "1000" and first:
            name = first.replace("/", "\\").split("\\")[-1].strip()
            kind = "app"
        else:
            name = provider.strip() or "Unknown source"
            kind = "source"
        if not name:
            continue
        key = (kind, name.lower())
        group = groups.setdefault(
            key, {"name": name, "kind": kind, "count": 0,
                  "latest": when})
        group["count"] += 1
        if when > group["latest"]:
            group["latest"] = when
    return sorted(groups.values(),
                  key=lambda g: (g["count"], g["latest"]),
                  reverse=True)


def app_crash_digest(days: int = 7) -> str:
    """Group the last days' application crashes/errors by app."""
    span = _clamp_int(days, 1, 30, 7)
    if not _is_windows():
        return ("Application crash records live in the Windows "
                f"event log — {_NOT_WINDOWS}")
    rows, err_tail = _crash_events(span)
    if not rows:
        if err_tail:
            return (f"Couldn't read the Application event log: "
                    f"{err_tail}")
        return (f"No application crashes or errors were recorded "
                f"in the last {span} day{'s' if span != 1 else ''} "
                "— either it's been a quiet week or the log "
                "doesn't reach that far back.")
    groups = _group_crashes(rows)
    total = sum(g["count"] for g in groups)
    lines = [f"Application crashes and errors in the last {span} "
             f"day{'s' if span != 1 else ''} — {total} event(s) "
             f"across {len(groups)} app(s)/source(s), worst "
             "first:"]
    for group in groups[:8]:
        when = group["latest"]
        try:
            when_words = datetime.strptime(
                when, "%Y-%m-%d %H:%M").strftime("%d %b at %H:%M")
        except ValueError:
            when_words = when
        if group["kind"] == "app":
            verb = (f"crashed {group['count']} time"
                    f"{'s' if group['count'] != 1 else ''}")
        else:
            verb = (f"logged {group['count']} error"
                    f"{'s' if group['count'] != 1 else ''}")
        lines.append(f"• {group['name']} — {verb} "
                     f"(most recently {when_words})")
    if len(groups) > 8:
        lines.append(f"…and {len(groups) - 8} more source(s) with "
                     "fewer events.")
    return "\n".join(lines)
