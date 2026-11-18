import re
import time
from datetime import date, datetime, time as dtime, timedelta
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_system6_helpers import (
    _NOT_WINDOWS,
    _clamp_int,
    _is_windows,
    _load_json,
    _now,
    _ps_events,
    _save_json,
    _state_path,
)


# ---------------------------------------------------------------------------
# WI-FI DEAD-SPOT MAP — a walk-around signal log, room by room
# ---------------------------------------------------------------------------

_WIFI_MAP_FILE = "wifi_dead_spots.json"


def _wifi_state_path():
    return _state_path(_WIFI_MAP_FILE)


def _signal_pct():
    """Current Wi-Fi signal as a percentage, via the network
    pack's netsh parse; None when it can't be read (not Windows,
    adapter off, not connected)."""
    if not _is_windows():
        return None
    from . import skills_network

    try:
        rc, out = skills_network._run(
            ["netsh", "wlan", "show", "interfaces"], timeout=15)
    except Exception:  # noqa: BLE001
        return None
    if rc != 0:
        return None
    for line in (out or "").splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip().lower() == "signal":
            match = re.search(r"(\d+)", value)
            if match:
                return int(match.group(1))
    return None


def _wifi_room_stats(readings: list[dict]) -> list[tuple[str, float, int, int]]:
    """(room, average, weakest, count) per room, weakest average
    first."""
    by_room: dict[str, list[int]] = {}
    for reading in readings:
        by_room.setdefault(reading["room"], []).append(reading["signal"])
    stats = [(room, sum(vals) / len(vals), min(vals), len(vals))
             for room, vals in by_room.items()]
    stats.sort(key=lambda s: s[1])
    return stats


def _wifi_report_lines(state: dict) -> list[str]:
    readings = state.get("readings") or []
    if not readings:
        return ["No readings yet — stand in a room and add one "
                "with the room's name."]
    lines = [f"Wi-Fi map — {len(readings)} reading(s):"]
    for room, avg, weakest, count in _wifi_room_stats(readings):
        lines.append(f"• {room} — average {round(avg)}%, weakest "
                     f"moment {weakest}% ({count} reading"
                     f"{'s' if count != 1 else ''})")
    weakest_room = _wifi_room_stats(readings)[0][0]
    lines.append(f"Weakest room: {weakest_room}.")
    if state.get("session_started"):
        lines.append("The walk is still running — add more rooms "
                     "or stop it when you're done.")
    return lines


def wifi_dead_spot_map(action: str = "report", room: str = "") -> str:
    """Walk-around Wi-Fi signal log: start / add_reading / stop /
    report."""
    act = (action or "report").strip().lower()
    if act not in ("start", "add_reading", "stop", "report"):
        return (f"Unknown Wi-Fi map action: '{action}'. Use "
                "'start', 'add_reading', 'stop' or 'report'.")
    state = _load_json(_wifi_state_path(),
                       {"session_started": None, "readings": []})
    if not isinstance(state, dict):
        state = {"session_started": None, "readings": []}
    readings = state.get("readings") or []

    if act == "start":
        fresh = {"session_started": _now(), "readings": []}
        # F-02: the save result used to be ignored, so a failed
        # write still reported a started (and cleared) walk. A
        # failed save leaves the previous map untouched, so say so.
        if not _save_json(_wifi_state_path(), fresh):
            return ("I couldn't start the Wi-Fi walk — saving the new "
                    "empty map failed, so the previous map (if any) "
                    "is untouched and no new walk started. Try again "
                    "and the walk will start once the save works.")
        cleared = (f" The previous map ({len(readings)} "
                   f"reading{'s' if len(readings) != 1 else ''}) "
                   "was cleared." if readings else "")
        return ("Wi-Fi dead-spot walk started." + cleared +
                " Stand in each room and add a reading with that "
                "room's name; I'll build the map from them.")

    if act == "add_reading":
        label = " ".join((room or "").split())[:40]
        if not label:
            return ("Tell me the room this reading is for — e.g. "
                    "add_reading with room 'Kitchen'.")
        signal = _signal_pct()
        if signal is None:
            return ("I couldn't read the Wi-Fi signal just now — "
                    "that reading needs Windows (netsh) and a "
                    "connected Wi-Fi adapter. No reading was "
                    "saved.")
        note = ""
        if not state.get("session_started"):
            state["session_started"] = _now()
            note = " (I started a new walk for you.)"
        readings.append({"room": label, "signal": signal,
                         "when": _now()})
        state["readings"] = readings
        # F-02: the save result used to be ignored and the branch
        # returned "Saved: ..." unconditionally. A failed save
        # means this reading was NOT kept — say so, with no
        # "Saved:" string anywhere in the failure message.
        if not _save_json(_wifi_state_path(), state):
            return (f"That reading wasn't recorded — saving the "
                    f"Wi-Fi map failed, so '{label}' at {signal}% "
                    f"was not kept. Try adding it again.")
        stats = _wifi_room_stats(readings)
        weakest = (f" Weakest so far: {stats[0][0]} at "
                   f"{round(stats[0][1])}% average."
                   if len(stats) > 1 else "")
        return (f"Saved: {label} — {signal}% "
                f"(reading #{len(readings)}).{weakest}{note}")

    if act == "stop":
        if not state.get("session_started"):
            return "No Wi-Fi walk is running right now."
        state["session_started"] = None
        # F-02: a failed save leaves the walk showing as running
        # on the next load (the file still holds session_started),
        # so "kept in the map" would be false — say so instead.
        if not _save_json(_wifi_state_path(), state):
            return ("I couldn't stop the walk cleanly — saving the "
                    "stopped state failed, so the walk will still "
                    "show as running next time. Try stopping it "
                    "again.")
        lines = [f"Walk stopped — {len(readings)} reading(s) kept "
                 "in the map."]
        lines.extend(_wifi_report_lines(state))
        return "\n".join(lines)

    return "\n".join(_wifi_report_lines(state))


# ---------------------------------------------------------------------------
# DISK WRITE HOGS — a few seconds of per-process write deltas
# ---------------------------------------------------------------------------


def _io_snapshot() -> tuple[dict, int]:
    """{pid: (name, write_bytes)} plus a count of processes that
    couldn't be read (permissions / vanished)."""
    import psutil

    snap: dict[int, tuple[str, int]] = {}
    skipped = 0
    for proc in psutil.process_iter(["name"]):
        try:
            counters = proc.io_counters()
            snap[proc.pid] = (proc.info.get("name") or proc.name(),
                              counters.write_bytes)
        except Exception:  # noqa: BLE001 — denied or gone: counted
            skipped += 1
    return snap, skipped


def _io_deltas(before: dict, after: dict) -> list[tuple[str, int]]:
    """Total bytes written per process name between two snapshots,
    heaviest first."""
    totals: dict[str, int] = {}
    for pid, (name, written) in after.items():
        if pid not in before:
            continue
        delta = written - before[pid][1]
        if delta > 0:
            totals[name] = totals.get(name, 0) + delta
    return sorted(totals.items(), key=lambda kv: kv[1], reverse=True)


def disk_write_hogs(seconds: int = 5) -> str:
    """Sample per-process disk writes for a few seconds."""
    secs = _clamp_int(seconds, 1, 15, 5)
    try:
        import psutil  # noqa: F401 — presence check before the wait
    except Exception:  # noqa: BLE001
        return ("Couldn't sample disk activity — the psutil "
                "package isn't available in this VYUK "
                "install.")
    before, skipped_before = _io_snapshot()
    time.sleep(float(secs))
    after, skipped_after = _io_snapshot()
    skipped = max(skipped_before, skipped_after)
    deltas = _io_deltas(before, after)
    head = (f"Disk writes over the last {secs} second"
            f"{'s' if secs != 1 else ''} — a short sample, not a "
            "history:")
    if not deltas:
        out = [head, "No process wrote measurably to disk in "
                     "that window."]
    else:
        total = sum(delta for _name, delta in deltas)
        lines = [head]
        for name, delta in deltas[:5]:
            lines.append(f"• {name} — wrote about "
                         f"{delta / (1024 * 1024):.1f} MB")
        lines.append(f"Total across all processes: about "
                     f"{total / (1024 * 1024):.1f} MB.")
        out = lines
    if skipped:
        out.append(f"({skipped} process(es) couldn't be read — "
                   "usually system processes owned by another "
                   "account.)")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# UPDATE RESTART PLANNER — propose only, never schedule or force
# ---------------------------------------------------------------------------


def _pending_updates() -> tuple[int, list[str]] | None:
    """(count, titles) of waiting software updates via the
    Windows Update session, or None when the check itself fails."""
    command = (
        "$s = New-Object -ComObject Microsoft.Update.Session; "
        "$r = $s.CreateUpdateSearcher().Search("
        "\"IsInstalled=0 and Type='Software'\"); "
        "$r.Updates | ForEach-Object { $_.Title }"
    )
    rc, rows, _err = _ps_events(command, timeout=180)
    if rc != 0:
        return None
    titles = [row[0].strip() for row in rows if row and row[0].strip()]
    return len(titles), titles


def _reboot_pending() -> bool:
    """Whether Windows has already flagged a restart as owed
    (component servicing or Windows Update) — the registry flags
    windows_update_status reads too."""
    if not _is_windows():
        return False
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return False
    for key_path in (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion"
            r"\Component Based Servicing\RebootPending",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion"
            r"\WindowsUpdate\Auto Update\RebootRequired"):
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
            key.Close()
            return True
        except OSError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return False


def _calendar_busy() -> list[tuple[datetime, datetime]] | None:
    """Busy (start, end) windows for the next 48 hours from
    Google Calendar, as naive local datetimes — or None when the
    calendar isn't connected or can't be read."""
    try:
        from . import google_services as gs

        if not gs.is_connected():
            return None
        svc = gs._calendar_service_or_none()
        if svc is None:
            return None
        now = datetime.now().astimezone()
        events = gs._fetch_events(svc, now, now + timedelta(hours=48))
        busy: list[tuple[datetime, datetime]] = []
        for event in events or []:
            parsed = gs._event_start(event)
            if not parsed:
                continue
            kind, begin, finish = parsed
            if kind == "allday":
                start = datetime(begin.year, begin.month, begin.day)
                busy.append((start, start + timedelta(days=1)))
            else:
                start = begin.replace(tzinfo=None)
                end = (finish.replace(tzinfo=None) if finish
                       else start + timedelta(hours=1))
                busy.append((start, end))
        return busy
    except Exception:  # noqa: BLE001 — calendar is best-effort here
        return None


def _propose_window(busy):
    """The first 2–4 AM slot in the next three days that starts in
    the future and (when `busy` is known) doesn't clash with an
    event. Returns (start, end) or None."""
    now = datetime.now()
    for offset in range(3):
        day = (now + timedelta(days=offset)).date()
        start = datetime.combine(day, dtime(2, 0))
        end = datetime.combine(day, dtime(4, 0))
        if end <= now:
            continue
        if busy is not None and any(
                start < busy_end and busy_start < end
                for busy_start, busy_end in busy):
            continue
        return start, end
    return None


def _window_words(start: datetime) -> str:
    today = date.today()
    if start.date() == today:
        label = "tonight"
    elif start.date() == today + timedelta(days=1):
        label = "tomorrow night"
    else:
        label = f"on {start:%A} night"
    return (f"{label} ({start:%a %d %b}) between 2:00 and 4:00 "
            "in the morning")


def update_restart_planner() -> str:
    """Check pending updates and propose (only propose) a restart
    window."""
    if not _is_windows():
        return ("Restart planning for Windows Update only "
                f"applies on Windows — {_NOT_WINDOWS}")
    pending = _pending_updates()
    reboot_owed = _reboot_pending()
    if pending is not None and pending[0] == 0 and not reboot_owed:
        return ("Nothing is waiting: no updates are pending and "
                "no restart is owed, so there's nothing to plan. "
                "I'll keep this tool for when there is.")
    lines: list[str] = []
    if pending is None:
        lines.append("I couldn't check the update queue itself "
                     "(the Windows Update session didn't answer), "
                     "so treat the count as unknown.")
    elif pending[0]:
        count, titles = pending
        lines.append(f"{count} update{'s are' if count != 1 else ' is'} "
                     "waiting to be installed:")
        for title in titles[:5]:
            lines.append(f"• {title}")
        if count > 5:
            lines.append(f"…and {count - 5} more.")
    if reboot_owed:
        lines.append("A restart is also already flagged as owed "
                     "— some earlier update won't finish until "
                     "this PC restarts.")
    busy = _calendar_busy()
    slot = _propose_window(busy)
    if slot is None:
        lines.append("Every early-morning slot I checked in the "
                     "next three days clashes with something or "
                     "has already passed — pick any night that "
                     "suits you; the early hours are the least "
                     "disruptive.")
    else:
        lines.append(f"Suggested restart window: "
                     f"{_window_words(slot[0])}.")
        if busy is None:
            lines.append("I couldn't check your calendar "
                         "(Google Calendar isn't connected or "
                         "didn't answer), so that's a generic "
                         "quiet-hours pick — move it if it "
                         "clashes with anything.")
        else:
            lines.append("I checked your calendar for the next "
                         "48 hours and that slot is clear.")
    lines.append("This is only a suggestion — I haven't scheduled "
                 "anything, and nothing will restart on its own "
                 "because of this.")
    return "\n".join(lines)
