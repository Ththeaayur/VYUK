"""Part of the skills_system_inventory split (Phase A batch A28): see
skills_system_inventory.py (facade) for the pack docstring and registration.
Move only — bodies verbatim from the original module.
"""

import re


# INTENTIONAL: cross-part names — hardware functions resolve these bare
# names in their defining module's globals, so they are imported here,
# not relied on from the facade (bare-name rule, plan §2 P3).
from .skills_system3_helpers import (
    _NOT_WINDOWS,
    _bool_word,
    _field,
    _human_size,
    _is_windows,
    _ps_structured,
    _tail,
)


# ---------------------------------------------------------------------------
# HARDWARE — what this machine physically is (read-only)
# ---------------------------------------------------------------------------


def _format_hardware(rc: int, rows: list[list[str]], err: str) -> str:
    """Format structured hardware rows. Pure — tested directly."""
    gpus: list[str] = []
    board = ""
    bios = ""
    sticks: list[tuple[float, str]] = []
    cpu = ""
    for row in rows:
        tag = _field(row, 0)
        if tag == "GPU":
            name = _field(row, 1) or "unknown graphics adapter"
            vram = _field(row, 2)
            gpus.append(f"{name} ({vram} GB)" if vram and vram != "0"
                        else name)
        elif tag == "BOARD":
            product, maker = _field(row, 1), _field(row, 2)
            board = " ".join(p for p in (maker, product) if p)
        elif tag == "BIOS":
            version, date = _field(row, 1), _field(row, 2)
            bios = version + (f" ({date})" if date else "")
        elif tag == "RAM":
            try:
                gb = float(_field(row, 1))
            except ValueError:
                gb = 0.0
            sticks.append((gb, _field(row, 2)))
        elif tag == "CPU":
            cpu = _field(row, 1)
    if not (gpus or board or bios or sticks or cpu):
        if rc != 0:
            return (f"Couldn't read the hardware inventory: "
                    f"{_tail(err) or 'the CIM queries failed.'}")
        return "Couldn't read the hardware inventory — Windows returned nothing."
    lines = ["Hardware inventory:"]
    if cpu:
        lines.append(f"• CPU: {cpu}")
    for gpu in gpus:
        lines.append(f"• Graphics: {gpu}")
    if board:
        lines.append(f"• Motherboard: {board}")
    if bios:
        lines.append(f"• BIOS: {bios}")
    if sticks:
        total = sum(gb for gb, _ in sticks)
        per = ", ".join(
            f"{gb:g} GB" + (f" @ {speed}" if speed else "")
            for gb, speed in sticks)
        lines.append(f"• Memory: {len(sticks)} stick(s), {total:g} GB "
                     f"total ({per})")
    present = [name for name, ok in
               (("CPU", bool(cpu)), ("graphics", bool(gpus)),
                ("motherboard", bool(board)), ("BIOS", bool(bios)),
                ("memory", bool(sticks))) if not ok]
    if present:
        lines.append(f"(Couldn't read: {', '.join(present)}.)")
    if rc != 0:
        lines.append("(Some hardware queries reported errors; "
                     "the rest is shown above.)")
    return "\n".join(lines)


def hardware_info() -> str:
    """CPU, GPU, motherboard, BIOS and RAM sticks via CIM. Read-only."""
    if not _is_windows():
        return f"Hardware inventory via CIM is a Windows feature — {_NOT_WINDOWS}"
    sep = "[char]0x1f"
    command = "; ".join([
        "Get-CimInstance Win32_VideoController | ForEach-Object { "
        f"'GPU' + {sep} + $_.Name + {sep} + "
        "[math]::Round($_.AdapterRAM / 1GB, 1) }",
        "Get-CimInstance Win32_BaseBoard | ForEach-Object { "
        f"'BOARD' + {sep} + $_.Product + {sep} + $_.Manufacturer }}",
        "Get-CimInstance Win32_BIOS | ForEach-Object { "
        f"'BIOS' + {sep} + $_.SMBIOSBIOSVersion + {sep} + "
        "$(if ($_.ReleaseDate) { $_.ReleaseDate.ToString('yyyy-MM-dd') } "
        "else { '' }) }",
        "Get-CimInstance Win32_PhysicalMemory | ForEach-Object { "
        f"'RAM' + {sep} + [math]::Round($_.Capacity / 1GB, 1) + {sep} + "
        "$_.Speed }",
        "Get-CimInstance Win32_Processor | ForEach-Object { "
        f"'CPU' + {sep} + $_.Name }}",
    ])
    rc, rows, err = _ps_structured(command, timeout=90)
    return _format_hardware(rc, rows, err)


# ---------------------------------------------------------------------------
# DRIVERS — signed driver inventory (read-only)
# ---------------------------------------------------------------------------

_DRIVER_CAP = 40


def _format_drivers(rc: int, rows: list[list[str]], err: str,
                    filter_text: str = "",
                    limit: int = _DRIVER_CAP) -> str:
    """Format structured driver rows. Pure — tested directly."""
    drivers = []
    for row in rows:
        device = _field(row, 0)
        if not device:
            continue
        drivers.append((device, _field(row, 1), _field(row, 2)))
    if not drivers:
        if rc != 0:
            return (f"Couldn't list drivers: "
                    f"{_tail(err) or 'the driver query failed.'}")
        return "No signed drivers were returned by Windows."
    needle = (filter_text or "").strip().lower()
    total = len(drivers)
    matched = [d for d in drivers
               if not needle or needle in d[0].lower()]
    if not matched:
        return (f"No drivers match '{filter_text}' "
                f"(out of {total} signed drivers).")
    try:
        cap = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        cap = _DRIVER_CAP
    shown = matched[:cap]
    scope = f" matching '{filter_text}'" if needle else ""
    lines = [f"Signed drivers ({len(matched)} of {total}{scope}):"]
    for device, version, provider in shown:
        bits = device
        if version:
            bits += f" — v{version}"
        if provider:
            bits += f" ({provider})"
        lines.append(f"• {bits}")
    if len(matched) > cap:
        lines.append(f"…showing the first {cap} of "
                     f"{len(matched)} — pass part of a device name "
                     "as filter to narrow it down, or raise the limit.")
    return "\n".join(lines)


def list_drivers(filter: str = "", limit: int = 40,
                 cancel_token=None) -> str:
    """Signed drivers via Win32_PnpSignedDriver. Slow CIM class.

    B20: ``cancel_token`` ends the CIM query promptly on Stop;
    a cancelled run returns the cancelled DICT (a string
    return would wrap as success). The inventory is read-only,
    so a stop loses nothing — the list simply never arrives.
    """
    if not _is_windows():
        return f"Driver inventory via CIM is a Windows feature — {_NOT_WINDOWS}"
    command = (
        "Get-CimInstance Win32_PnpSignedDriver | ForEach-Object { "
        "$_.DeviceName + [char]0x1f + $_.DriverVersion + [char]0x1f + "
        "$_.DriverProviderName }"
    )
    rc, rows, err = _ps_structured(command, timeout=120,
                                   cancel_token=cancel_token)
    if rc == -3:
        from .funnel.tool_result import cancelled_result
        return cancelled_result(
            "Listing drivers was cancelled at the user's request "
            "while the inventory was still being read — no driver "
            "list was produced.")
    return _format_drivers(rc, rows, err, filter, limit)


# ---------------------------------------------------------------------------
# DRIVES — space per mounted drive (read-only, cross-platform)
# ---------------------------------------------------------------------------


def drive_info() -> str:
    """Every readable mounted drive with free/total space. Read-only."""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return ("Couldn't read drive information — the psutil package "
                "isn't available in this VYUK install.")
    seen: set[str] = set()
    mounts: list[str] = []
    for part in psutil.disk_partitions(all=False):
        if part.mountpoint and part.mountpoint not in seen:
            seen.add(part.mountpoint)
            mounts.append(part.mountpoint)
    lines: list[str] = []
    skipped = 0
    for mount in sorted(mounts):
        try:
            usage = psutil.disk_usage(mount)
        except (PermissionError, OSError):
            skipped += 1
            continue
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        lines.append(
            f"• {mount} — {_human_size(usage.free)} free of "
            f"{_human_size(usage.total)} ({usage.percent:.0f}% used)")
    if not lines:
        return ("Couldn't read any mounted drive — every mount was "
                "gone or off-limits by the time VYUK looked.")
    head = f"Drives ({len(lines)} readable):"
    if skipped:
        lines.append(f"…and {skipped} mount(s) couldn't be read "
                     "(permission denied or vanished).")
    return head + "\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# RESTORE POINTS — listing (read-only)
# ---------------------------------------------------------------------------

_RESTORE_CAP = 15


def _wmi_datetime(text: str) -> str:
    """'20261021153012.000000+330' → '2026-10-21 15:30'; '' if unparseable."""
    digits = "".join(ch for ch in (text or "") if ch.isdigit())
    if len(digits) < 12:
        return ""
    return (f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]} "
            f"{digits[8:10]}:{digits[10:12]}")


def _format_restore_points(rc: int, rows: list[list[str]],
                           err: str) -> str:
    """Format structured restore-point rows. Pure — tested directly.
    An empty success and a failure are deliberately different answers."""
    points = []
    for row in rows:
        raw_when = _field(row, 0)
        if not raw_when:
            continue
        points.append((raw_when, _wmi_datetime(raw_when), _field(row, 1)))
    if not points:
        if rc != 0:
            return (f"Couldn't list restore points: "
                    f"{_tail(err) or 'Get-ComputerRestorePoint failed.'}")
        return ("No restore points found on this PC — System Restore "
                "has none recorded (it may be turned off).")
    points.sort(key=lambda p: p[0], reverse=True)
    shown = points[:_RESTORE_CAP]
    lines = [f"Restore points ({len(points)}, newest first):"]
    for _raw, when, desc in shown:
        lines.append(f"• {when or 'unknown date'} — {desc or '(no description)'}")
    if len(points) > _RESTORE_CAP:
        lines.append(f"…showing the newest {_RESTORE_CAP} of {len(points)}.")
    return "\n".join(lines)


def list_restore_points() -> str:
    """Existing restore points, newest first. Read-only."""
    if not _is_windows():
        return f"Restore points are a Windows feature — {_NOT_WINDOWS}"
    command = (
        "Get-ComputerRestorePoint | ForEach-Object { "
        "$_.CreationTime + [char]0x1f + $_.Description }"
    )
    rc, rows, err = _ps_structured(command, timeout=60)
    return _format_restore_points(rc, rows, err)


# ---------------------------------------------------------------------------
# USERS — local accounts (read-only)
# ---------------------------------------------------------------------------


def _format_users(rows: list[list[str]]) -> str:
    """Format structured user rows. Pure — tested directly."""
    users = []
    for row in rows:
        name = _field(row, 0)
        if not name:
            continue
        users.append((name, _bool_word(_field(row, 1)),
                      _field(row, 2) == "1"))
    if not users:
        return "No local user accounts were returned by Windows."
    lines = [f"Local user accounts ({len(users)}):"]
    for name, disabled, is_me in sorted(users, key=lambda u: u[0].lower()):
        suffix = ""
        if is_me:
            suffix += " (you)"
        if disabled == "on":
            suffix += " — disabled"
        elif disabled == "unknown":
            suffix += " — disabled state unknown"
        lines.append(f"• {name}{suffix}")
    return "\n".join(lines)


def list_users() -> str:
    """Local Windows user accounts, current one marked. Read-only."""
    if not _is_windows():
        return f"Windows account listing has no meaning here — {_NOT_WINDOWS}"
    command = (
        "$me = $env:USERNAME; "
        "Get-CimInstance Win32_UserAccount "
        "-Filter 'LocalAccount=true' | ForEach-Object { "
        "$_.Name + [char]0x1f + $_.Disabled + [char]0x1f + "
        "$(if ($_.Name -eq $me) { '1' } else { '0' }) }"
    )
    rc, rows, err = _ps_structured(command, timeout=60)
    if not rows and rc != 0:
        return (f"Couldn't list user accounts: "
                f"{_tail(err) or 'the account query failed.'}")
    return _format_users(rows)


# ---------------------------------------------------------------------------
# FONTS — installed font families (read-only)
# ---------------------------------------------------------------------------

_FONT_CAP = 60
_FONT_SUFFIX_RE = re.compile(
    r"\s*\((?:TrueType|OpenType|Raster|Vector|Type 1)[^)]*\)\s*$",
    re.IGNORECASE)


def _clean_font_name(raw: str) -> str:
    """Strip the ' (TrueType)'-style suffixes from a Fonts value name."""
    return _FONT_SUFFIX_RE.sub("", (raw or "").strip()).strip()


def list_fonts(filter: str = "") -> str:
    """Installed font family names from the HKLM Fonts key. Read-only."""
    if not _is_windows():
        return f"The Windows font registry only exists on Windows — {_NOT_WINDOWS}"
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return "Couldn't read the font list — the registry isn't reachable."
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
    except OSError as e:
        return f"Couldn't open the Fonts registry key: {e}"
    names: list[str] = []
    try:
        count = winreg.QueryInfoKey(key)[1]
        for i in range(count):
            try:
                value_name = winreg.EnumValue(key, i)[0]
            except OSError:
                continue
            cleaned = _clean_font_name(value_name)
            if cleaned:
                names.append(cleaned)
    finally:
        winreg.CloseKey(key)
    names = sorted(set(names), key=str.lower)
    if not names:
        return "No fonts were returned from the registry."
    needle = (filter or "").strip().lower()
    matched = [n for n in names if not needle or needle in n.lower()]
    if not matched:
        return (f"No fonts match '{filter}' "
                f"(out of {len(names)} installed).")
    shown = matched[:_FONT_CAP]
    scope = f" matching '{filter}'" if needle else ""
    lines = [f"Installed fonts ({len(matched)} of {len(names)}{scope}):"]
    lines += [f"• {n}" for n in shown]
    if len(matched) > _FONT_CAP:
        lines.append(f"…showing the first {_FONT_CAP} of {len(matched)} "
                     "— pass part of a font name as filter to narrow it.")
    return "\n".join(lines)
