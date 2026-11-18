import time
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_system6_helpers import (
    _NOT_WINDOWS,
    _clamp_int,
    _duration_words,
    _is_windows,
    _ps_events,
    _tail,
    _today_str,
)


# ---------------------------------------------------------------------------
# STARTUP TIME — boot history from the Diagnostics-Performance log
# ---------------------------------------------------------------------------


def _boot_events() -> tuple[list[dict], str]:
    """Boot records from the last 8 days, newest first:
    [{when, day, boot_ms, main_ms, post_ms}], plus an error tail
    ('' when the query itself worked). Event 100 of the
    Diagnostics-Performance log carries the durations as named
    EventData fields — read structurally from the event XML,
    never from localised message text."""
    command = (
        "$start = (Get-Date).AddDays(-8); "
        "Get-WinEvent -FilterHashtable "
        "@{LogName='Microsoft-Windows-Diagnostics-Performance"
        "/Operational'; Id=100; StartTime=$start} "
        "-ErrorAction SilentlyContinue | ForEach-Object { "
        "[xml]$x = $_.ToXml(); $vals = @{}; "
        "foreach ($n in @($x.Event.EventData.Data)) { "
        "$vals[$n.Name] = $n.InnerText }; "
        "$_.TimeCreated.ToString('yyyy-MM-dd HH:mm') + [char]0x1f "
        "+ $vals['BootDuration'] + [char]0x1f "
        "+ $vals['MainPathBootTime'] + [char]0x1f "
        "+ $vals['BootPostBootTime'] }"
    )
    rc, rows, err = _ps_events(command, timeout=60)
    boots: list[dict] = []
    for row in rows:
        if len(row) < 2:
            continue
        try:
            boot_ms = int(row[1])
        except (TypeError, ValueError):
            continue

        def _ms(idx: int):
            try:
                return int(row[idx])
            except (IndexError, TypeError, ValueError):
                return None

        boots.append({"when": row[0], "day": row[0][:10],
                      "boot_ms": boot_ms, "main_ms": _ms(2),
                      "post_ms": _ms(3)})
    return boots, ("" if rc == 0 else _tail(err))


def startup_time_report() -> str:
    """How long starting the PC took today vs the 7-day average."""
    if not _is_windows():
        return ("Boot timings are recorded in the Windows event "
                f"log — {_NOT_WINDOWS}")
    boots, err_tail = _boot_events()
    if not boots:
        detail = f" Windows said: {err_tail}" if err_tail else ""
        return ("I couldn't find any boot-time records from the "
                "last 8 days. The Diagnostics-Performance log may "
                "be disabled, may need an admin context to read, or "
                f"the PC may not have restarted in that time.{detail}")
    today = _today_str()
    todays = [b for b in boots if b["day"] == today]
    prior = [b for b in boots if b["day"] != today]
    lines: list[str] = []
    if todays:
        newest = todays[0]
        lines.append(
            f"Today's start-up took {_duration_words(newest['boot_ms'] / 1000)}"
            " in total.")
        if newest["main_ms"] is not None and newest["post_ms"] is not None:
            lines.append(
                "Of that, "
                f"{_duration_words(newest['main_ms'] / 1000)} was the "
                "boot itself and "
                f"{_duration_words(newest['post_ms'] / 1000)} was "
                "getting to a usable desktop after sign-in.")
    else:
        lines.append("No start-up has been recorded today — the PC "
                     "hasn't restarted since midnight, or the boot "
                     "predates today's log window.")
    if prior:
        avg_ms = sum(b["boot_ms"] for b in prior) / len(prior)
        lines.append(
            f"Over the last 7 days, {len(prior)} recorded "
            f"start(s) averaged {_duration_words(avg_ms / 1000)}.")
        if todays:
            diff_s = todays[0]["boot_ms"] / 1000 - avg_ms / 1000
            if abs(diff_s) >= 2:
                direction = "faster" if diff_s < 0 else "slower"
                lines.append(
                    f"So today was {_duration_words(abs(diff_s))} "
                    f"{direction} than the recent average.")
            else:
                lines.append("Today was right in line with the "
                             "recent average.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# BATTERY DRAIN DETECTIVE — a live CPU sample, honestly labelled
# ---------------------------------------------------------------------------


def _battery_status():
    """psutil's battery reading, or None (no battery / no psutil)."""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return None
    try:
        return psutil.sensors_battery()
    except Exception:  # noqa: BLE001
        return None


def _cpu_sample(seconds: float) -> list[tuple[str, float]]:
    """Total %CPU per process name over the window, heaviest first.
    A live sample of THIS window only — no history is kept."""
    import psutil

    procs = {}
    for proc in psutil.process_iter(["name"]):
        try:
            proc.cpu_percent(None)
            procs[proc.pid] = proc
        except Exception:  # noqa: BLE001 — vanished mid-scan
            continue
    time.sleep(seconds)
    totals: dict[str, float] = {}
    for proc in procs.values():
        try:
            pct = proc.cpu_percent(None)
            name = proc.name()
        except Exception:  # noqa: BLE001 — gone or denied: skip it
            continue
        if pct and pct > 0:
            totals[name] = totals.get(name, 0.0) + pct
    return sorted(totals.items(), key=lambda kv: kv[1], reverse=True)


def battery_drain_detective(seconds: int = 20) -> str:
    """Sample CPU per process and name the heaviest users."""
    secs = _clamp_int(seconds, 5, 60, 20)
    try:
        import psutil  # noqa: F401 — presence check before the wait
    except Exception:  # noqa: BLE001
        return ("Couldn't sample processes — the psutil package "
                "isn't available in this VYUK install.")
    battery = _battery_status()
    ranking = _cpu_sample(float(secs))
    if battery is None:
        head = ("This PC reports no battery (it's most likely a "
                "desktop), so this is just the current load — the "
                f"heaviest CPU users over the last {secs} seconds:")
    elif battery.power_plugged:
        head = (f"The PC is plugged in (battery at "
                f"{round(battery.percent)}%), so nothing is "
                "draining right now — but here's the current load, "
                f"the heaviest CPU users over the last {secs} "
                "seconds:")
    else:
        head = (f"On battery at {round(battery.percent)}%. The "
                "heaviest CPU users over the last "
                f"{secs} seconds — a live sample of right now, "
                "not a record of what drained it earlier:")
    if not ranking:
        lines = [head, "Nothing used measurable CPU in that window "
                       "— the machine was effectively idle."]
        return "\n".join(lines)
    lines = [head]
    for name, pct in ranking[:5]:
        lines.append(f"• {name} — about {pct:.0f}% CPU")
    lines.append("CPU is the main drain a sample like this can "
                 "see; screen brightness and radios draw power too "
                 "but don't show up per-process.")
    return "\n".join(lines)
