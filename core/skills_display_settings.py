"""
Display depth skills for VYUK (v5.12, Pack O) — the two display
settings the older packs never touched: the primary display's
refresh rate and its screen rotation. core/skills_network.py
already reports per-monitor resolution + Hz (display_details) and
changes resolution (set_resolution); neither it nor display_control
(info / wallpaper / dark mode) touches frequency or orientation.

Same DEVMODE discipline as set_resolution (the v5.9 standard):
enumerate BEFORE changing and refuse what the display doesn't
offer — listing what it does offer; copy the current DEVMODE, set
only the fields being changed, apply with the same flags
set_resolution uses (CDS_UPDATEREGISTRY); then READ BACK the live
mode and report old → new from the read-back, never from the
request. A change Windows accepted but the read-back contradicts
is reported as not taken.

PRIMARY DISPLAY ONLY, for both tools — stated in the tool
descriptions and in every result. The platform work is factored
behind small seams (_primary_display, _read_current_mode,
_enumerate_modes, _apply_mode) and the decisions behind pure
helpers (_supported_rates, _pick_rate, _plan_rotation), so the
logic is testable off-Windows; the handlers return a clear
fallback string anywhere that isn't Windows.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import sys

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return sys.platform.startswith("win")


_NOT_WINDOWS = "this system is not Windows, so nothing was changed."

# Win32 display constants (winuser.h / wingdi.h).
_ENUM_CURRENT_SETTINGS = -1
_CDS_UPDATEREGISTRY = 0x00000001  # the apply flags set_resolution uses
_DM_DISPLAYORIENTATION = 0x00000080
_DM_PELSWIDTH = 0x00080000
_DM_PELSHEIGHT = 0x00100000
_DM_DISPLAYFREQUENCY = 0x00400000

# DEVMODE.dmDisplayOrientation values <-> the degrees the user means.
_ORIENT_TO_DEG = {0: 0, 1: 90, 2: 180, 3: 270}
_DEG_TO_ORIENT = {deg: code for code, deg in _ORIENT_TO_DEG.items()}
_LANDSCAPE_DEG = (0, 180)

# ChangeDisplaySettingsEx result codes, in plain words. 0 is success;
# anything else is reported to the user WITH its code, never hidden.
_DISP_CHANGE_MEANINGS = {
    1: "Windows says this change needs a restart before it takes effect",
    -1: "the change failed",
    -2: "that display mode isn't supported",
    -3: "the new setting couldn't be saved",
    -4: "Windows rejected the request's flags",
    -5: "Windows rejected a parameter of the request",
    -6: "the displays need to be set up differently for this",
}


def _change_failure(code: int, what: str) -> str:
    """Name a ChangeDisplaySettingsEx failure code plainly."""
    meaning = _DISP_CHANGE_MEANINGS.get(
        code, "Windows couldn't apply the change")
    return (f"Windows refused to change {what} (code {code} — {meaning}), "
            "so nothing was changed.")


# ---------------------------------------------------------------------------
# Pure planning helpers — no platform calls, fully testable
# ---------------------------------------------------------------------------


def _supported_rates(modes: list[dict], width: int, height: int,
                     bpp: int) -> list[int]:
    """Distinct refresh rates (Hz) a mode list offers at exactly this
    resolution and colour depth, sorted ascending. Frequencies
    below 30 are driver bookkeeping, not real rates (0/1 mean
    'adapter default'), so they never reach the user as choices."""
    rates = set()
    for m in modes:
        if (m.get("width") == width and m.get("height") == height
                and m.get("bpp") == bpp):
            hz = int(m.get("hz") or 0)
            if hz >= 30:
                rates.add(hz)
    return sorted(rates)


def _pick_rate(supported: list[int], requested: int) -> int | None:
    """The requested rate if the display offers it, else None — the
    caller refuses with the supported list, never substitutes."""
    return requested if requested in supported else None


def _format_rates(rates: list[int]) -> str:
    return ", ".join(str(r) for r in rates)


def _plan_rotation(cur_w: int, cur_h: int, cur_deg: int,
                   target_deg: int) -> tuple[int, int, int]:
    """Plan a rotation: (new_width, new_height, new_degrees).

    Moving between the landscape family (0°/180°) and the portrait
    family (90°/270°) swaps width and height — the panel physically
    turns — while moving inside a family keeps them. A target equal
    to the current orientation plans to the current values, which
    the caller reads as 'already there, make no call'."""
    cur_landscape = cur_deg in _LANDSCAPE_DEG
    target_landscape = target_deg in _LANDSCAPE_DEG
    if cur_landscape != target_landscape:
        return cur_h, cur_w, target_deg
    return cur_w, cur_h, target_deg


# ---------------------------------------------------------------------------
# Platform seams — the only functions that touch Win32
# ---------------------------------------------------------------------------


def _devmode_type():
    """The DEVMODE ctypes class — the same layout core/skills_network.py
    defines for its display helpers (kept local, as there, so the
    struct travels with the code that fills it)."""
    import ctypes
    from ctypes import wintypes

    class DEVMODE(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
            ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD),
            ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
            ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long),
            ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
            ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
            ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
            ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
            ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
            ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
            ("dmDisplayFlags", wintypes.DWORD), ("dmDisplayFrequency", wintypes.DWORD),
            ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
            ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
            ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
            ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD),
        ]

    return DEVMODE


def _user32():
    import ctypes

    return ctypes.windll.user32


def _primary_display() -> dict | None:
    """The primary monitor's entry ({device, width, height, hz, ...})
    from core/skills_network.py's enumeration — reused by import
    rather than re-implemented, so 'which display is primary' has
    exactly one definition in the app. None when it can't be read."""
    try:
        from .skills_network import _monitor_details

        monitors = _monitor_details()
    except Exception:  # noqa: BLE001
        return None
    for m in monitors:
        if m.get("primary"):
            return m
    return monitors[0] if monitors else None


def _current_devmode(device: str, user32):
    """A fresh DEVMODE holding the device's LIVE mode, or None."""
    import ctypes

    DEVMODE = _devmode_type()
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    if user32.EnumDisplaySettingsW(device, _ENUM_CURRENT_SETTINGS,
                                    ctypes.byref(dm)):
        return dm
    return None


def _mode_dict(dm) -> dict:
    return {
        "width": int(dm.dmPelsWidth),
        "height": int(dm.dmPelsHeight),
        "bpp": int(dm.dmBitsPerPel),
        "hz": int(dm.dmDisplayFrequency),
        "orientation": int(dm.dmDisplayOrientation),
    }


def _read_current_mode(device: str, user32=None) -> dict | None:
    """The device's live mode as a plain dict, or None."""
    user32 = user32 if user32 is not None else _user32()
    dm = _current_devmode(device, user32)
    return _mode_dict(dm) if dm is not None else None


def _enumerate_modes(device: str, user32=None) -> list[dict]:
    """Every mode the device's driver lists, as plain dicts. The
    index loop is capped so a driver that never says 'no more'
    can't spin forever."""
    import ctypes

    user32 = user32 if user32 is not None else _user32()
    DEVMODE = _devmode_type()
    modes: list[dict] = []
    for index in range(1024):
        dm = DEVMODE()
        dm.dmSize = ctypes.sizeof(DEVMODE)
        if not user32.EnumDisplaySettingsW(device, index, ctypes.byref(dm)):
            break
        modes.append(_mode_dict(dm))
    return modes


def _apply_mode(device: str, hz: int | None = None,
                orientation: int | None = None,
                width: int | None = None, height: int | None = None,
                user32=None) -> int:
    """Apply a change to the device: copy its current DEVMODE, set
    only the given fields (with their DM_* flags), and call
    ChangeDisplaySettingsExW with the same flags set_resolution
    uses. Returns the Windows result code (0 = applied)."""
    import ctypes

    user32 = user32 if user32 is not None else _user32()
    dm = _current_devmode(device, user32)
    if dm is None:
        return -1
    fields = 0
    if hz is not None:
        dm.dmDisplayFrequency = int(hz)
        fields |= _DM_DISPLAYFREQUENCY
    if orientation is not None:
        dm.dmDisplayOrientation = int(orientation)
        fields |= _DM_DISPLAYORIENTATION
    if width is not None:
        dm.dmPelsWidth = int(width)
        fields |= _DM_PELSWIDTH
    if height is not None:
        dm.dmPelsHeight = int(height)
        fields |= _DM_PELSHEIGHT
    dm.dmFields = fields
    return int(user32.ChangeDisplaySettingsExW(
        device, ctypes.byref(dm), None, _CDS_UPDATEREGISTRY, None))


# ---------------------------------------------------------------------------
# REFRESH RATE — enumerate, refuse the unoffered, apply, read back
# ---------------------------------------------------------------------------


def set_refresh_rate(hz: int) -> str:
    """Set the primary display's refresh rate to an offered value."""
    if not _is_windows():
        return (f"Changing the refresh rate uses Windows display APIs — "
                f"{_NOT_WINDOWS}")
    try:
        target = int(hz)
    except (TypeError, ValueError):
        return ("The refresh rate needs to be a whole number of hertz, "
                "e.g. 60, 120 or 144.")
    display = _primary_display()
    if display is None:
        return ("VYUK couldn't find the primary display, so nothing "
                "was changed.")
    device = display["device"]
    current = _read_current_mode(device)
    if current is None:
        return ("VYUK couldn't read the primary display's current "
                "mode, so nothing was changed.")
    cur_w, cur_h = current["width"], current["height"]
    rates = _supported_rates(
        _enumerate_modes(device), cur_w, cur_h, current["bpp"])
    if not rates:
        return (f"VYUK couldn't enumerate the refresh rates the primary "
                f"display offers at {cur_w}×{cur_h}, so nothing was "
                "changed.")
    if _pick_rate(rates, target) is None:
        return (f"The primary display doesn't offer {target} Hz at "
                f"{cur_w}×{cur_h} — it offers {_format_rates(rates)} Hz. "
                "Nothing was changed.")
    old_hz = current["hz"]
    if target == old_hz:
        return (f"The primary display is already running at {target} Hz "
                f"({cur_w}×{cur_h}) — nothing to change.")
    code = _apply_mode(device, hz=target)
    if code != 0:
        return _change_failure(code, "the refresh rate")
    after = _read_current_mode(device)
    if after is None:
        return (f"Windows accepted the change to {target} Hz, but VYUK "
                "couldn't read the display back to confirm it took.")
    if after["hz"] == target:
        return (f"Refresh rate: {old_hz} → {target} Hz on the primary "
                f"display ({cur_w}×{cur_h}, confirmed by read-back).")
    return (f"Windows accepted the change, but the primary display "
            f"still reads {after['hz']} Hz on read-back — the new rate "
            "didn't take.")


# ---------------------------------------------------------------------------
# SCREEN ROTATION — plan the swap, apply, read back orientation + size
# ---------------------------------------------------------------------------


def set_screen_rotation(degrees: int) -> str:
    """Rotate the primary display to 0 / 90 / 180 / 270 degrees."""
    if not _is_windows():
        return (f"Rotating the screen uses Windows display APIs — "
                f"{_NOT_WINDOWS}")
    try:
        target_deg = int(degrees)
    except (TypeError, ValueError):
        target_deg = -1
    if target_deg not in _DEG_TO_ORIENT:
        return ("Screen rotation needs one of: 0, 90, 180 or 270 "
                "degrees.")
    display = _primary_display()
    if display is None:
        return ("VYUK couldn't find the primary display, so nothing "
                "was changed.")
    device = display["device"]
    current = _read_current_mode(device)
    if current is None:
        return ("VYUK couldn't read the primary display's current "
                "mode, so nothing was changed.")
    cur_deg = _ORIENT_TO_DEG.get(current["orientation"], 0)
    new_w, new_h, _ = _plan_rotation(
        current["width"], current["height"], cur_deg, target_deg)
    if target_deg == cur_deg:
        return (f"The primary display is already rotated to "
                f"{target_deg}° — nothing was changed.")
    code = _apply_mode(device, orientation=_DEG_TO_ORIENT[target_deg],
                       width=new_w, height=new_h)
    if code != 0:
        return _change_failure(code, "the screen rotation")
    after = _read_current_mode(device)
    if after is None:
        return (f"Windows accepted the rotation to {target_deg}°, but "
                "VYUK couldn't read the display back to confirm it took.")
    after_deg = _ORIENT_TO_DEG.get(after["orientation"], 0)
    if after_deg == target_deg and (after["width"], after["height"]) == (new_w, new_h):
        return (f"Screen rotation: {cur_deg}° → {target_deg}° on the "
                f"primary display (now {new_w}×{new_h}, confirmed by "
                f"read-back). If it's hard to use like this, just ask "
                f"me to rotate it back to {cur_deg}° — the same tool "
                "changes it back.")
    return (f"Windows accepted the rotation, but the read-back shows "
            f"{after_deg}° at {after['width']}×{after['height']} — the "
            "rotation didn't take as asked, so check the screen before "
            "relying on it.")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Display depth (Pack O) tool on a
    ToolRegistry-like object."""
    registry.register(
        name="set_refresh_rate",
        description="Set the PRIMARY display's refresh rate (Hz). Only rates the display actually offers at its current resolution can be set — asking for an unoffered rate changes nothing and reports the rates that are offered (e.g. 'set the refresh rate to 144'). The change is applied to the primary display only and confirmed by reading the live rate back; the result reports old → new from that read-back.",
        parameters={
            "type": "object",
            "properties": {
                "hz": {"type": "integer", "description": "Target refresh rate in hertz, e.g. 60, 120, 144"},
            },
            "required": ["hz"],
        },
        handler=set_refresh_rate,
    )
    registry.register(
        name="set_screen_rotation",
        description="Rotate the PRIMARY display to 0, 90, 180 or 270 degrees (0 is the normal landscape orientation). Width and height swap automatically when rotating between landscape and portrait. The result is confirmed by reading the live orientation back. This same tool rotates the screen back — if a rotation makes the screen hard to use, ask to rotate to 0 to undo it. Primary display only.",
        parameters={
            "type": "object",
            "properties": {
                "degrees": {"type": "integer", "enum": [0, 90, 180, 270], "description": "Target orientation in degrees: 0, 90, 180 or 270"},
            },
            "required": ["degrees"],
        },
        handler=set_screen_rotation,
    )
