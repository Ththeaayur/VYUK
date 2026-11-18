# Split from core/skills_plus.py (Phase A batch A12): moved verbatim.
from pathlib import Path
import subprocess

# INTENTIONAL: names shared by several impl groups live in the
# helpers module (plan section 2 P3(c)); imported here so each
# moved function resolves its bare names in this module.
from .skills_plus_helpers import _NOT_WINDOWS, _is_windows

# ---------------------------------------------------------------------------
# DRIVES — eject a removable drive safely
# ---------------------------------------------------------------------------


def eject_drive(drive: str) -> str:
    """Ask Windows to eject a removable drive (e.g. 'E' or 'E:')."""
    raw = (drive or "").strip()
    if not raw:
        return "Ejecting needs a drive, like 'E'."
    if not _is_windows():
        return f"Ejecting a drive the safe way is a Windows action here — {_NOT_WINDOWS}"
    letter = raw[0].upper()
    if not letter.isalpha():
        return f"'{drive}' doesn't look like a drive letter — try just 'E'."
    root = f"{letter}:\\"
    if not Path(root).exists():
        return f"There is no drive {letter}: right now."
    try:
        import ctypes

        # DRIVE_REMOVABLE = 2, DRIVE_CDROM = 5 can be ejected; fixed (3) can't.
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
        if drive_type == 3:
            return (f"{letter}: is an internal fixed drive — those can't be ejected. "
                    "If you meant a USB drive, check its letter in File Explorer.")
    except Exception:  # noqa: BLE001 — type check is advisory; the verb is the gate
        pass
    command = (
        "(New-Object -ComObject Shell.Application).NameSpace(17)"
        f".ParseName('{root}').InvokeVerb('Eject')"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=25,
        )
    except Exception as e:  # noqa: BLE001
        return f"Could not ask Windows to eject {letter}:: {e}"
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()[:200]
        return (f"Windows refused to eject {letter}:"
                + (f" — {detail}" if detail else "")
                + ". A program may still be using the drive; close it and try again.")
    # R1b: the eject verb is a request — poll until the drive
    # letter leaves, and say so honestly when it does not.
    from . import tool_outcome as _outcome  # R1b: seam wiring

    import time as _time

    for _attempt in range(4):
        if not _outcome.drive_present(letter):
            return _outcome.verified(
                "Ejected drive %s: safely — it is no longer listed."
                % letter,
                "drive %s: is gone after the eject request" % letter)
        _time.sleep(0.5)
    return _outcome.unverified(
        "Eject requested for drive %s:. The request was accepted, "
        "but the drive is still present — a program may still be "
        "using it. Wait for the 'safe to remove' note before "
        "unplugging it." % letter,
        "drive %s: still present 2 s after the eject request" % letter)

# ---------------------------------------------------------------------------
# MONITORS — list screens, move a window to another screen
# ---------------------------------------------------------------------------


def _monitor_rects() -> list[dict]:
    """Enumerate monitors via user32 (Windows): index, rect, primary, device."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFOEX(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    monitors: list[dict] = []

    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.POINTER(RECT), ctypes.c_double)

    def _collect(hmonitor, _hdc, _rect, _data):
        info = MONITORINFOEX()
        info.cbSize = ctypes.sizeof(MONITORINFOEX)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            monitors.append({
                "left": r.left, "top": r.top,
                "width": r.right - r.left, "height": r.bottom - r.top,
                "primary": bool(info.dwFlags & 1),
                "device": info.szDevice,
            })
        return 1

    user32.EnumDisplayMonitors(0, None, callback_type(_collect), 0)
    monitors.sort(key=lambda m: (not m["primary"], m["left"], m["top"]))
    for i, m in enumerate(monitors, start=1):
        m["index"] = i
    return monitors


def monitor_control(action: str, title_substring: str = "", monitor_index: int = 0) -> str:
    """List monitors, or move a window (by title) onto a chosen monitor."""
    act = (action or "").strip().lower()
    if act not in ("list", "move_window"):
        return f"Unknown monitor action: '{action}'. Use one of: list, move_window."
    if not _is_windows():
        return f"Monitor control uses Windows display APIs — {_NOT_WINDOWS}"
    try:
        monitors = _monitor_rects()
    except Exception as e:  # noqa: BLE001
        return f"Could not read the monitor list: {e}"
    if not monitors:
        return "No monitors were reported by Windows."
    if act == "list":
        lines = []
        for m in monitors:
            tag = " (main screen)" if m["primary"] else ""
            lines.append(
                f"• Monitor {m['index']}{tag}: {m['width']}×{m['height']} "
                f"at ({m['left']}, {m['top']}) — {m['device']}")
        return f"Monitors ({len(monitors)}):\n" + "\n".join(lines)
    # move_window
    needle = (title_substring or "").strip().lower()
    if not needle:
        return "Moving a window needs part of its title, e.g. 'Chrome'."
    try:
        target_index = int(monitor_index)
    except (TypeError, ValueError):
        target_index = 0
    if target_index < 1 or target_index > len(monitors):
        return (f"There is no monitor {monitor_index} — this PC has "
                f"{len(monitors)} monitor(s). Use monitor_control 'list' to see them.")
    target = monitors[target_index - 1]
    try:
        import ctypes

        from .desktop.control import _enum_windows  # Phase 3 D4a: control hub moved to core/desktop/

        # Identify before acting (audit m4): acting on the first of
        # several title matches can move a window the user didn't
        # mean. Unique match — or a single exact-title match —
        # proceeds; several matches refuse and list the titles.
        matches = [(h, t) for h, t in _enum_windows() if needle in t.lower()]
        if not matches:
            return f"No window found with title containing '{title_substring}'."
        exact = [m for m in matches if m[1].strip().lower() == needle]
        pool = exact or matches
        if len(pool) > 1:
            titles = "; ".join(f"'{t}'" for _h, t in pool[:8])
            extra = f" (and {len(pool) - 8} more)" if len(pool) > 8 else ""
            return (f"'{title_substring}' matches {len(pool)} windows: "
                    f"{titles}{extra}. Say the exact title so the wrong "
                    "window is never moved.")
        hwnd, title = pool[0]
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE — a minimized window has no real rect
        from ctypes import wintypes

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return f"Could not read the position of '{title}'."
        width, height = rect.right - rect.left, rect.bottom - rect.top
        x = target["left"] + 40
        y = target["top"] + 40
        if width >= target["width"]:
            x = target["left"]
        if height >= target["height"]:
            y = target["top"]
        SWP_NOSIZE, SWP_NOZORDER = 0x0001, 0x0004
        if not user32.SetWindowPos(hwnd, 0, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER):
            return f"Windows refused to move '{title}'."
        # R1b: read the rect back — SetWindowPos succeeding does
        # not prove the window landed on the target monitor.
        after = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(after)):
            from . import tool_outcome as _outcome  # R1b: seam wiring

            return _outcome.unverified(
                f"'{title}' was asked to move to monitor "
                f"{target_index}, but its position could not be read "
                "back.",
                "GetWindowRect failed after the move")
        cx = (after.left + after.right) // 2
        cy = (after.top + after.bottom) // 2
        if not (target["left"] <= cx < target["right"]
                and target["top"] <= cy < target["bottom"]):
            return (f"I asked Windows to move '{title}' to monitor "
                    f"{target_index}, but it actually sits at "
                    f"({after.left}, {after.top})–({after.right}, "
                    f"{after.bottom}), which is not on that screen — "
                    "the move did not take.")
    except Exception as e:  # noqa: BLE001
        return f"Could not move the window: {e}"
    # R1b: the rect read-back above confirmed the landing.
    from . import tool_outcome as _outcome  # R1b: seam wiring

    return _outcome.verified(
        f"Moved '{title}' to monitor {target_index} "
        f"({target['width']}×{target['height']}).",
        "window rect reads on the target monitor after the move")
