import re
import time
from datetime import datetime
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_system6_helpers import (
    _NOT_WINDOWS,
    _duration_words,
    _is_windows,
    _load_json,
    _now,
    _ps_events,
    _ps_run,
    _save_json,
    _state_path,
    _today_str,
)


# ---------------------------------------------------------------------------
# PERIPHERAL ROLL CALL — devices now vs the saved baseline
# ---------------------------------------------------------------------------

_PERIPHERAL_FILE = "peripherals_snapshot.json"


def _peripheral_path():
    return _state_path(_PERIPHERAL_FILE)


def _list_devices() -> list[dict] | None:
    """Present USB/Bluetooth devices [{name, kind}] via
    Get-PnpDevice; None when the query itself fails."""
    command = (
        "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue "
        "| Where-Object { $_.Class -in @('USB','Bluetooth') "
        "-and $_.FriendlyName } | ForEach-Object { "
        "$_.FriendlyName + [char]0x1f + $_.Class }"
    )
    rc, rows, _err = _ps_events(command, timeout=60)
    if rc != 0:
        return None
    devices: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not row or not row[0].strip():
            continue
        entry = {"name": row[0].strip(),
                 "kind": row[1].strip() if len(row) > 1 else ""}
        key = (entry["name"], entry["kind"])
        if key not in seen:
            seen.add(key)
            devices.append(entry)
    return devices


def _device_counts(devices: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for device in devices:
        counts[device["name"]] = counts.get(device["name"], 0) + 1
    return counts


def _diff_names(saved: dict, current: dict) -> tuple[list[str], list[str]]:
    """(missing, new) device names between two name→count maps."""
    missing = sorted(name for name in saved if name not in current)
    new = sorted(name for name in current if name not in saved)
    return missing, new


def peripheral_roll_call() -> str:
    """List connected USB/Bluetooth devices and diff against the
    last saved snapshot."""
    if not _is_windows():
        return ("The device roll call reads Windows' device "
                f"list (Get-PnpDevice) — {_NOT_WINDOWS}")
    devices = _list_devices()
    if devices is None:
        return ("Couldn't list the connected devices — "
                "Get-PnpDevice didn't answer. Nothing was saved.")
    current = _device_counts(devices)
    kinds: dict[str, str] = {}
    for device in devices:
        kinds.setdefault(device["name"], device["kind"])
    state = _load_json(_peripheral_path(), None)
    saved = None
    saved_date = ""
    if isinstance(state, dict) and isinstance(state.get("devices"), dict):
        saved = state["devices"]
        saved_date = str(state.get("date") or "")
    today = _today_str()
    lines = [f"Peripheral roll call — {len(devices)} connected "
             f"device(s), {len(current)} distinct name(s) "
             "(USB and Bluetooth):"]
    shown = sorted(current.items())
    for name, count in shown[:25]:
        suffix = f" ×{count}" if count > 1 else ""
        kind = f" ({kinds[name]})" if kinds.get(name) else ""
        lines.append(f"• {name}{suffix}{kind}")
    if len(shown) > 25:
        lines.append(f"…and {len(shown) - 25} more device "
                     "name(s).")
    # F-02: the save result used to be ignored, so the "I've saved
    # today's list as the baseline" line below could be false. Save
    # FIRST, then word the baseline lines from the actual result.
    saved_ok = _save_json(_peripheral_path(),
                          {"date": today, "devices": current})
    if saved is None:
        if saved_ok:
            lines.append("This is the first roll call — I've saved "
                         "today's list as the baseline, so from the "
                         "next run I can name what's missing or new.")
        else:
            lines.append("This is the first roll call — but I "
                         "couldn't save today's list as the baseline "
                         "(the write failed), so the next run will "
                         "treat it as a first run again.")
    else:
        missing, new = _diff_names(saved, current)
        when = ("earlier today" if saved_date == today
                else f"the last snapshot ({saved_date or 'earlier'})")
        if missing:
            lines.append(f"Missing since {when}: "
                         + ", ".join(missing) + ".")
        if new:
            lines.append(f"New since {when}: " + ", ".join(new) + ".")
        if not missing and not new:
            lines.append(f"Nothing has gone missing and nothing "
                         f"new has appeared since {when}.")
        if not saved_ok:
            lines.append("Note: today's list was not saved as the "
                         "new baseline — the write failed, so the "
                         "next roll call will compare against the "
                         "previous snapshot.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SCREEN TIME TODAY — an honest day counter fed by the idle probe
# ---------------------------------------------------------------------------

_SCREEN_TIME_FILE = "screen_time.json"


def _screen_time_path():
    return _state_path(_SCREEN_TIME_FILE)


def _idle_seconds():
    """Seconds since the last input, via the watchers' probe;
    None off Windows or on any failure."""
    try:
        from . import watchers

        return watchers._probe_idle_seconds()
    except Exception:  # noqa: BLE001
        return None


def _update_screen_counter() -> tuple[dict, float | None]:
    """Fold the time since the last check into today's counter:
    the credited active time for the gap is the wall-clock gap
    minus how long the user has currently been idle, so away
    time and sleep aren't counted. Returns (state, idle_now).
    A counter is only ever credited on a call — time while
    VYUK isn't running is simply unknown, and the wording
    says so."""
    path = _screen_time_path()
    now = _now()
    today = _today_str()
    idle = _idle_seconds()
    state = _load_json(path, None)
    if not isinstance(state, dict) or state.get("date") != today:
        previous = None
        if isinstance(state, dict) and state.get("date"):
            previous = {"date": state["date"],
                        "active_seconds":
                            float(state.get("active_seconds") or 0)}
        state = {"date": today, "started_at": now,
                 "active_seconds": 0.0, "last_check": now,
                 "checks": 0, "previous_day": previous}
    else:
        wall = now - float(state.get("last_check") or now)
        if wall > 0:
            wall = min(wall, 12 * 3600)
            if idle is not None:
                credit = min(wall, max(0.0, wall - float(idle)))
                state["active_seconds"] = (
                    float(state.get("active_seconds") or 0) + credit)
    state["last_check"] = now
    state["checks"] = int(state.get("checks") or 0) + 1
    # F-02: the save result used to be ignored, so the caller
    # reported a counter value that was never persisted. Return
    # the save status as a third element so the caller can say so.
    saved_ok = _save_json(path, state)
    return state, idle, saved_ok


def screen_time_today() -> str:
    """Today's tracked active-use estimate from the day counter."""
    state, idle, saved_ok = _update_screen_counter()
    started = datetime.fromtimestamp(
        float(state.get("started_at") or _now())).strftime("%H:%M")
    active_s = float(state.get("active_seconds") or 0)
    previous = state.get("previous_day")
    prev_line = ""
    if isinstance(previous, dict) and previous.get("active_seconds"):
        prev_line = (f" The previous tracked day "
                     f"({previous.get('date')}) totalled "
                     f"{_duration_words(float(previous['active_seconds']))} "
                     "of active use.")
    # F-02: when the counter update was not persisted, say so on
    # every branch — otherwise the reported value reads as kept.
    save_note = ("" if saved_ok else
                 " Note: this update was not saved (the write "
                 "failed), so the counter on disk is unchanged and "
                 "the next check will start from the last saved "
                 "state.")
    if idle is None:
        return ("I can't read this system's idle time (that "
                "reading is Windows-only), so I can't separate "
                "active use from the PC simply being on — no "
                "active minutes have been credited. The counter "
                f"itself has been kept since {started} today and "
                f"holds {_duration_words(active_s)}." + prev_line
                + save_note)
    if int(state.get("checks") or 0) <= 1 and active_s < 60:
        return ("I've only just started counting — the screen-"
                f"time counter began at {started} today, so the "
                "tracked active time so far is "
                f"{_duration_words(active_s)}, with no earlier "
                "data to add. Ask again later and the estimate "
                "will have grown; it only counts while VYUK "
                "is running." + prev_line + save_note)
    away = ""
    if idle >= 120:
        away = (f" Right now you've been away from the keyboard "
                f"for {_duration_words(float(idle))}.")
    return (f"Active use today: {_duration_words(active_s)} "
            f"(counting since {started}, an estimate from idle "
            "readings — it only covers time while VYUK "
            f"has been running).{away}" + prev_line + save_note)


# ---------------------------------------------------------------------------
# SAFE EJECT ALL — eject every removable drive, verify each left
# ---------------------------------------------------------------------------


def _removable_drives() -> list[dict] | None:
    """Removable (DriveType 2) drives [{letter, label}]; None
    when the query itself fails."""
    command = (
        "Get-CimInstance Win32_LogicalDisk | "
        "Where-Object { $_.DriveType -eq 2 } | ForEach-Object { "
        "$_.DeviceID + [char]0x1f + $_.VolumeName }"
    )
    rc, rows, _err = _ps_events(command, timeout=60)
    if rc != 0:
        return None
    drives = []
    for row in rows:
        if row and re.fullmatch(r"[A-Za-z]:", row[0].strip()):
            drives.append({"letter": row[0].strip(),
                           "label": row[1].strip()
                           if len(row) > 1 else ""})
    return drives


def _eject_drive(letter: str) -> None:
    """Ask the shell to eject one drive (Shell.Application verb).
    Best-effort by design — the caller verifies by listing the
    drives again afterwards."""
    if not re.fullmatch(r"[A-Za-z]:", letter or ""):
        return
    command = ("(New-Object -ComObject Shell.Application)"
               f".NameSpace(17).ParseName('{letter}\\')"
               ".InvokeVerb('Eject')")
    _ps_run(command, timeout=30)


def safe_eject_all() -> str:
    """Eject all removable drives, then verify each one left."""
    if not _is_windows():
        return (f"Drive ejection here uses the Windows shell — "
                f"{_NOT_WINDOWS}")
    drives = _removable_drives()
    if drives is None:
        return ("Couldn't list the removable drives — nothing "
                "was ejected.")
    if not drives:
        return ("No removable (USB) drives are plugged in — "
                "nothing to eject.")
    for drive in drives:
        _eject_drive(drive["letter"])
    time.sleep(1.0)
    remaining = _removable_drives()
    remaining_letters = ({d["letter"] for d in remaining}
                         if remaining is not None else set())
    lines: list[str] = []
    ejected = 0
    for drive in drives:
        label = f" ({drive['label']})" if drive["label"] else ""
        if remaining is not None \
                and drive["letter"] not in remaining_letters:
            ejected += 1
            lines.append(f"• {drive['letter']}{label} — ejected "
                         "safely.")
        elif remaining is None:
            lines.append(f"• {drive['letter']}{label} — eject was "
                         "asked, but I couldn't re-check the "
                         "drive list to confirm it left.")
        else:
            lines.append(f"• {drive['letter']}{label} — wouldn't "
                         "eject; it's likely still in use by a "
                         "program. Close anything using it and "
                         "try again.")
    head = (f"Ejected {ejected} of {len(drives)} removable "
            f"drive{'s' if len(drives) != 1 else ''}:"
            if ejected else "No drive could be ejected:")
    return "\n".join([head, *lines])
