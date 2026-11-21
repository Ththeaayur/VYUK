"""Display, window layering and input — skills_network impl (A23).

Moved verbatim from core/skills_network.py (DISPLAY, WINDOWS and
INPUT sections). All platform imports here are function-local in the
original and stay function-local; only shared helpers are imported.
"""

# INTENTIONAL: shared names owned by skills_network_helpers (§2 P3(c)).
from .skills_network_helpers import _NOT_WINDOWS, _is_windows


def _monitor_details() -> list[dict]:
    """Enumerate monitors with rect/primary/device + current refresh rate.

    Local reimplementation (this module stays independent of
    skills_plus): EnumDisplayMonitors for geometry, EnumDisplaySettingsW
    per device for resolution + Hz. Caller must be on Windows.
    """
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

    monitors: list[dict] = []
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.POINTER(RECT), ctypes.c_double)

    def _collect(hmonitor, _hdc, _rect, _data):
        info = MONITORINFOEX()
        info.cbSize = ctypes.sizeof(MONITORINFOEX)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            entry = {"left": r.left, "top": r.top,
                     "width": r.right - r.left, "height": r.bottom - r.top,
                     "primary": bool(info.dwFlags & 1), "device": info.szDevice,
                     "hz": None}
            dm = DEVMODE()
            dm.dmSize = ctypes.sizeof(DEVMODE)
            if user32.EnumDisplaySettingsW(info.szDevice, -1, ctypes.byref(dm)):
                entry["width"] = int(dm.dmPelsWidth) or entry["width"]
                entry["height"] = int(dm.dmPelsHeight) or entry["height"]
                if dm.dmDisplayFrequency and dm.dmDisplayFrequency > 1:
                    entry["hz"] = int(dm.dmDisplayFrequency)
            monitors.append(entry)
        return 1

    user32.EnumDisplayMonitors(0, None, callback_type(_collect), 0)
    monitors.sort(key=lambda m: (not m["primary"], m["left"], m["top"]))
    for i, m in enumerate(monitors, start=1):
        m["index"] = i
    return monitors


def display_details() -> str:
    """Per-monitor resolution, refresh rate, size and position."""
    if not _is_windows():
        return f"Detailed display info uses Windows display APIs — {_NOT_WINDOWS}"
    try:
        monitors = _monitor_details()
    except Exception as e:  # noqa: BLE001
        return f"Could not read display details: {e}"
    if not monitors:
        return "No monitors were reported by Windows."
    lines = [f"Displays ({len(monitors)}):"]
    for m in monitors:
        tag = " (main screen)" if m["primary"] else ""
        hz = f" at {m['hz']} Hz" if m["hz"] else ""
        lines.append(f"• Monitor {m['index']}{tag}: {m['width']}×{m['height']}{hz} "
                     f"at ({m['left']}, {m['top']}) — {m['device']}")
    return "\n".join(lines)


def set_resolution(monitor_index: int, width: int, height: int, confirm: bool = False) -> str:
    """Change a monitor's resolution — tested with Windows before applying."""
    if not confirm:
        return ("Changing the resolution rearranges the desktop and needs "
                "confirm=true. Ask again with confirmation.")
    if not _is_windows():
        return f"Changing the resolution uses Windows display APIs — {_NOT_WINDOWS}"
    try:
        idx, w, h = int(monitor_index), int(width), int(height)
    except (TypeError, ValueError):
        return "Resolution needs a monitor number and pixel width/height, e.g. monitor 1, 1920×1080."
    if w < 640 or h < 480 or w > 16384 or h > 16384:
        return f"{w}×{h} is outside the sane range for a display resolution."
    try:
        monitors = _monitor_details()
    except Exception as e:  # noqa: BLE001
        return f"Could not read the monitor list: {e}"
    if idx < 1 or idx > len(monitors):
        return (f"There is no monitor {monitor_index} — this PC has "
                f"{len(monitors)} monitor(s). Use display_details to see them.")
    device = monitors[idx - 1]["device"]
    try:
        import ctypes

        user32 = ctypes.windll.user32
        dm = _devmode_for(user32, device)
        if dm is None:
            return f"Could not read the current settings of monitor {idx}."
        # The pre-change size is already in hand (audit m13): keep
        # it, so the receipt can state old → new and the user has
        # the numbers needed to revert.
        old_w, old_h = int(dm.dmPelsWidth), int(dm.dmPelsHeight)
        dm.dmPelsWidth, dm.dmPelsHeight = w, h
        dm.dmFields = 0x80000 | 0x100000  # DM_PELSWIDTH | DM_PELSHEIGHT
        CDS_TEST, CDS_UPDATEREGISTRY = 0x00000002, 0x00000001
        test = user32.ChangeDisplaySettingsExW(device, ctypes.byref(dm), None, CDS_TEST, None)
        if test != 0:  # DISP_CHANGE_SUCCESSFUL
            return (f"Monitor {idx} refused {w}×{h} in the pre-change test "
                    f"(Windows code {test}) — nothing was changed. "
                    "Try a resolution the screen lists as supported.")
        applied = user32.ChangeDisplaySettingsExW(
            device, ctypes.byref(dm), None, CDS_UPDATEREGISTRY, None)
        if applied != 0:
            return (f"Windows accepted the test but failed to apply {w}×{h} "
                    f"(code {applied}) — the resolution was not changed.")
    except Exception as e:  # noqa: BLE001
        return f"Could not change the resolution: {e}"
    # Read back (house pattern): re-read the device's current mode
    # and confirm it holds the new size before claiming it.
    confirm_note = ""
    try:
        import ctypes

        now = _devmode_for(ctypes.windll.user32, device)
        if now is not None:
            rw, rh = int(now.dmPelsWidth), int(now.dmPelsHeight)
            if (rw, rh) == (w, h):
                confirm_note = " (Confirmed by read-back.)"
            else:
                confirm_note = (f" Windows accepted the change, but the "
                                f"read-back shows {rw}×{rh} — the new "
                                "resolution may not have stuck.")
    except Exception:  # noqa: BLE001
        confirm_note = ""
    return (f"Monitor {idx}: {old_w}×{old_h} → {w}×{h}.{confirm_note} "
            "Windows may have rearranged open windows — ask me to "
            "move one if it's stranded.")


def _devmode_for(user32, device: str):
    """Current DEVMODE for a device (struct defined here to keep the
    display helpers self-contained)."""
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

    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    if user32.EnumDisplaySettingsW(device, -1, ctypes.byref(dm)):
        return dm
    return None


def _find_windows(title_substring: str):
    """ALL (hwnd, title) pairs whose title contains the substring, or
    ([], msg) when the query is empty or nothing matches. Callers
    that act on a window must handle the multi-match case (audit
    m4): silently acting on the first of several windows is how the
    wrong window gets moved, pinned or faded."""
    needle = (title_substring or "").strip().lower()
    if not needle:
        return [], "That needs part of a window's title, e.g. 'Chrome'."
    from .desktop.control import _enum_windows  # Phase 3 D4a: control hub moved to core/desktop/

    matches = [(h, t) for h, t in _enum_windows() if needle in t.lower()]
    if not matches:
        return [], f"No window found with title containing '{title_substring}'."
    return matches, ""


def _find_window(title_substring: str):
    """First (hwnd, title) whose title contains the substring, else (None, msg).

    Kept for compatibility; the acting tools use _pick_window, which
    refuses to guess between several matches."""
    matches, err = _find_windows(title_substring)
    if not matches:
        return None, err
    return matches[0], ""


def _pick_window(title_substring: str):
    """Resolve a title query to exactly ONE window, or explain why not.

    Returns ((hwnd, title), "") on success, else (None, message).
    A unique substring match proceeds; so does a single exact-title
    match among several substring matches; several matches with no
    exact one → refuse and list the titles, the uninstall_app
    multi-match posture."""
    matches, err = _find_windows(title_substring)
    if not matches:
        return None, err
    if len(matches) == 1:
        return matches[0], ""
    needle = (title_substring or "").strip().lower()
    exact = [m for m in matches if m[1].strip().lower() == needle]
    if len(exact) == 1:
        return exact[0], ""
    titles = "; ".join(f"'{t}'" for _h, t in matches[:8])
    extra = f" (and {len(matches) - 8} more)" if len(matches) > 8 else ""
    return None, (f"'{title_substring}' matches {len(matches)} windows: "
                  f"{titles}{extra}. Say the exact title so the wrong "
                  "window is never changed.")


def window_always_on_top(title_substring: str, on: bool = True) -> str:
    """Pin a window above all others, or unpin it."""
    if not _is_windows():
        return f"Window layering uses Windows APIs — {_NOT_WINDOWS}"
    try:
        match, err = _pick_window(title_substring)
        if match is None:
            return err
        hwnd, title = match
        import ctypes

        user32 = ctypes.windll.user32
        insert_after = -1 if on else -2  # HWND_TOPMOST / HWND_NOTOPMOST
        SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010
        if not user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0,
                                   SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE):
            return f"Windows refused to change the layering of '{title}'."
    except Exception as e:  # noqa: BLE001
        return f"Could not change the window's layering: {e}"
    # R1b: read the topmost style back — the SetWindowPos call
    # succeeding does not prove the style took.
    from . import tool_outcome as _outcome  # R1b: seam wiring

    topmost, evidence = _outcome.window_topmost(hwnd)
    state = "pinned on top of" if on else "unpinned from the top of"
    if topmost is None:
        return _outcome.unverified(
            f"'{title}' was asked to be {state} the other windows, "
            "but the layering could not be read back.",
            evidence)
    if topmost != on:
        return (f"Windows did not keep the layering change for "
                f"'{title}' — it still reads "
                f"{'topmost' if topmost else 'not topmost'}.")
    return _outcome.verified(
        f"'{title}' is now {state} the other windows.",
        evidence)


def window_opacity(title_substring: str, percent: int) -> str:
    """Make a window see-through (30–100%); 100 restores it fully."""
    try:
        pct = int(percent)
    except (TypeError, ValueError):
        return "Opacity needs a percentage between 30 and 100."
    if pct < 30:
        return ("Below 30% a window is effectively invisible and unusable — "
                "30 is the minimum. Use 100 to restore a window fully.")
    if pct > 100:
        return "Opacity can't exceed 100%."
    if not _is_windows():
        return f"Window opacity uses Windows APIs — {_NOT_WINDOWS}"
    try:
        match, err = _pick_window(title_substring)
        if match is None:
            return err
        hwnd, title = match
        import ctypes

        user32 = ctypes.windll.user32
        GWL_EXSTYLE, WS_EX_LAYERED, LWA_ALPHA = -20, 0x00080000, 0x00000002
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if pct == 100:
            if style & WS_EX_LAYERED:
                if not user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA):
                    return (f"Windows refused to restore the opacity of "
                            f"'{title}'.")
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style & ~WS_EX_LAYERED)
            # R1b: the restore clears the layered style — verify
            # the style is gone, not the alpha.
            from . import tool_outcome as _outcome  # R1b: seam wiring

            try:
                after = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            except Exception as e:  # noqa: BLE001 — unreadable window
                return _outcome.unverified(
                    f"'{title}' was asked to go back to fully opaque, "
                    "but the window style could not be read back.",
                    "could not read the window style: %s" % e)
            if after & WS_EX_LAYERED:
                return (f"Windows did not restore the opacity of "
                        f"'{title}' — it still reads as a layered window.")
            return _outcome.verified(
                f"'{title}' is back to fully opaque.",
                "window style reads without the layered flag")
        if not (style & WS_EX_LAYERED):
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
        alpha = round(pct * 255 / 100)
        if not user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA):
            return f"Windows refused to set the opacity of '{title}'."
    except Exception as e:  # noqa: BLE001
        return f"Could not set the window's opacity: {e}"
    # R1b: read the alpha back — allow ±1 for the 255-scale
    # rounding.
    from . import tool_outcome as _outcome  # R1b: seam wiring

    read_pct, evidence = _outcome.window_opacity_value(hwnd)
    if read_pct is None:
        return _outcome.unverified(
            f"'{title}' was asked to go to {pct}% opacity, but the "
            "opacity could not be read back.",
            evidence)
    if abs(read_pct - pct) > 1:
        return (f"Windows did not keep the opacity of '{title}' — "
                f"it reads at {read_pct}%, not {pct}%.")
    return _outcome.verified(
        f"'{title}' is now at {pct}% opacity.", evidence)


def mouse_settings(action: str = "get", speed: int | None = None) -> str:
    """Read or set the Windows mouse pointer speed (1 slow – 20 fast)."""
    act = (action or "get").strip().lower()
    if act not in ("get", "set"):
        return f"Unknown mouse action: '{action}'. Use 'get' or 'set'."
    if not _is_windows():
        return f"Mouse speed uses Windows system settings — {_NOT_WINDOWS}"
    import ctypes

    user32 = ctypes.windll.user32
    SPI_GETMOUSESPEED, SPI_SETMOUSESPEED = 0x0070, 0x0071
    try:
        if act == "get":
            value = ctypes.c_int(0)
            if not user32.SystemParametersInfoW(SPI_GETMOUSESPEED, 0, ctypes.byref(value), 0):
                return "Could not read the mouse speed."
            return f"Mouse pointer speed is {value.value} out of 20."
        try:
            new_speed = int(speed)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return "Setting the mouse speed needs a number from 1 (slow) to 20 (fast)."
        if new_speed < 1 or new_speed > 20:
            return "Mouse speed must be between 1 (slow) and 20 (fast)."
        # SPIF_UPDATEINIFILE (1) + SPIF_SENDCHANGE (2): apply, broadcast,
        # and persist to the user profile so it survives sign-out.
        if not user32.SystemParametersInfoW(
                SPI_SETMOUSESPEED, 0, ctypes.c_void_p(new_speed), 0x0001 | 0x0002):
            return "Windows refused the new mouse speed."
        verify = ctypes.c_int(0)
        user32.SystemParametersInfoW(SPI_GETMOUSESPEED, 0, ctypes.byref(verify), 0)
        if verify.value != new_speed:
            return "Windows didn't keep the new mouse speed."
        return f"Mouse pointer speed set to {new_speed} out of 20."
    except Exception as e:  # noqa: BLE001
        return f"Could not {act} the mouse speed: {e}"


def paste_text(text: str) -> str:
    """Put text on the clipboard and paste it (Ctrl+V) wherever the cursor is."""
    content = text if text is not None else ""
    if not str(content).strip():
        return "Nothing to paste: no text was given."
    # H20 (v5.45): a paste IS typing — the shared password rule in
    # core/input_guard runs before the keystroke is injected, the
    # same helper control.type_text and uia_type_text use.
    try:
        from . import input_guard

        _blocked = input_guard.typing_blocked_reason()
    except Exception:  # noqa: BLE001
        _blocked = None
    if _blocked:
        return _blocked
    original_len = len(str(content))
    content = str(content)[:5000]
    try:
        import pyperclip

        pyperclip.copy(content)
    except ImportError:
        # F-F203: nothing was pasted and nothing reached the
        # clipboard, so the verdict is failed, not a plain string
        # the receipts would read "succeeded". (The other two
        # branches stay plain strings — they name different
        # conditions, not this filed shape.)
        from . import tool_outcome as _outcome  # R1b: seam wiring
        return _outcome.failed(
            "Pasting isn't available in this build of VYUK — the clipboard part didn't make it into the package.",
            "clipboard part not in build")
    except Exception as e:  # noqa: BLE001 — headless / no clipboard mechanism
        return (f"Could not reach the clipboard to paste: {e}. "
                "A desktop session with a clipboard is needed.")
    try:
        from .desktop.control import press_hotkey  # Phase 3 D4a: control hub moved to core/desktop/

        result = press_hotkey("ctrl+v")
        # R1b: press_hotkey may now return an Outcome — read it
        # through str(...) for the substring checks.
        result_text = str(result)
        if "Could not" in result_text or "requires" in result_text:
            return (f"The text is on the clipboard, but the paste keystroke "
                    f"failed: {result_text}")
    except Exception as e:  # noqa: BLE001
        return f"The text is on the clipboard, but pasting failed: {e} — press Ctrl+V to use it."
    preview = content.replace("\n", " ")[:40]
    # R1b: the clipboard write is verified (R1a paste-back), but the
    # ctrl+v landing cannot be read back — mark it honestly
    # unverified.
    # B13: the vision landing check asks the B5 backend whether the
    # pasted text is on screen (Granite read). Where vision cannot
    # read, the honestly-unverified return stands exactly as before
    # (same message, same kind).
    from . import tool_outcome as _outcome  # R1b: seam wiring

    if original_len > len(content):
        return _outcome.hands_landing_check(
            "paste", content,
            f"Pasted the first {len(content):,} of {original_len:,} characters "
            f"starting with \"{preview}\" into the focused window — the remaining "
            f"{original_len - len(content):,} characters were not pasted. "
            "The pasted text also stays on the clipboard. (Sent as a paste "
            "keystroke; whether it landed was not confirmed.)")
    return _outcome.hands_landing_check(
        "paste", content,
        f"Pasted {len(content)} character(s) starting with \"{preview}\" "
        "into the focused window (the text also stays on the clipboard). "
        "(Sent as a paste keystroke; whether it landed was not confirmed.)")
