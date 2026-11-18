"""
Desktop & personalisation skills for VYUK (v5.4, Pack A) — a second
desktop pack alongside core/skills_plus.py: wallpaper, dark mode,
keep-awake, virtual desktops, Settings pages, clipboard clearing and
toast notifications.

Same rules as the other packs: plain functions returning
human-readable strings; every platform-specific dependency (winreg,
ctypes, PowerShell) is reached lazily and guarded with try/except,
so this module imports cleanly on any OS — on non-Windows systems
the Windows-only skills return a clear fallback string instead of
crashing. Effects are verified where Windows lets VYUK verify them
(dark mode is read back after writing; the wallpaper write is
confirmed in the registry) and reported as *sent*, never *done*,
where Windows gives no confirmation (virtual-desktop shortcuts).
The toast builder treats its title/message as hostile input: both
are XML-escaped for the toast payload and quote-doubled for the
PowerShell string, because a notification is an injection surface
like any other.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return sys.platform.startswith("win")


_NOT_WINDOWS = "this system is not Windows, so nothing was changed."


def _run(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run a command, capturing output. Returns (returncode, stdout, stderr);
    returncode is -1 when the command could not be started at all and -2 on
    timeout, with the reason in stderr. Never raises."""
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
    }
    if _is_windows():
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW — no console flash
    try:
        completed = subprocess.run(args, **kwargs)
        return completed.returncode, completed.stdout or "", completed.stderr or ""
    except FileNotFoundError:
        return -1, "", f"{args[0]} was not found on this PC."
    except subprocess.TimeoutExpired:
        return -2, "", f"{args[0]} took longer than {timeout} seconds and was stopped."
    except Exception as e:  # noqa: BLE001
        return -1, "", str(e)


def _tail(text: str, limit: int = 400) -> str:
    """The last few meaningful lines of a command's output, for honest reports."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    out = "\n".join(lines[-4:])
    return out[:limit]


# ---------------------------------------------------------------------------
# WALLPAPER — HKCU\Control Panel\Desktop + SystemParametersInfoW
# ---------------------------------------------------------------------------

# style -> (WallpaperStyle, TileWallpaper) registry strings, the values
# Windows itself writes for each Desktop background style.
_WALLPAPER_STYLES = {
    "fill": ("10", "0"),
    "fit": ("6", "0"),
    "stretch": ("2", "0"),
    "tile": ("0", "1"),
    "center": ("0", "0"),
    "span": ("22", "0"),
}

_DESKTOP_KEY = r"Control Panel\Desktop"
_SPI_SETDESKWALLPAPER = 20
_SPIF_SENDCHANGE = 0x01 | 0x02  # SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE


def set_wallpaper(path: str, style: str = "fill",
                  monitor: str | None = None) -> str | dict:
    """Set the desktop wallpaper image and how it fills the screen.

    ``monitor`` is None (the legacy path: one image on ALL
    monitors via SPI) or a monitor selector (``"primary"``,
    ``"left"``, ``"second monitor"``, a device name, or
    ``"all"`` — resolved against the CURRENT monitor table).
    A per-monitor set goes through IDesktopWallpaper and saves
    that monitor's previous first. The style is GLOBAL Windows
    state (one style for all monitors) and is written for the
    whole desktop either way. When the per-monitor API is
    unavailable the set falls back to SPI on all monitors and
    the reply says so.

    B1 (2026-11-06): every failure of the set itself returns a
    funnel-shaped failure dict, never a plain string — the
    funnel used to carry each of these as {"status":
    "success"}, so a routine recorded SUCCESS for a wallpaper
    that was never set. The message text is unchanged; only the
    envelope changes. Argument validation (non-Windows host,
    unknown style, empty path) keeps its plain-string reply.
    """
    if not _is_windows():
        return f"Setting the wallpaper needs Windows — {_NOT_WINDOWS}"
    wanted_style = (style or "fill").strip().lower()
    if wanted_style not in _WALLPAPER_STYLES:
        return ("Unknown wallpaper style: "
                f"'{style}'. Use one of: {', '.join(sorted(_WALLPAPER_STYLES))}.")
    raw = (path or "").strip()
    if not raw:
        return "Setting the wallpaper needs the path of an image file."
    image = Path(raw).expanduser()
    if not image.is_file():
        from .funnel import tool_result as _tr  # B1: failure shape
        return _tr.failure_result(
            _tr.WALLPAPER_NOT_SET_KIND,
            f"That wallpaper file doesn't exist: {raw}")
    from . import wallpaper as _wallpaper  # lazy: stdlib-only module
    from .funnel import tool_result as _tr  # B1: failure shape

    if monitor is not None:
        # Per-monitor (R4): the style is global, so it is
        # written first exactly as the legacy path writes it;
        # the image set (save-previous + SetWallpaper +
        # read-back, or the honest SPI fallback) is the shared
        # seam in core/wallpaper.py.
        style_value, tile_value = _WALLPAPER_STYLES[wanted_style]
        try:
            import winreg

            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                    _DESKTOP_KEY) as key:
                winreg.SetValueEx(key, "WallpaperStyle", 0,
                                  winreg.REG_SZ, style_value)
                winreg.SetValueEx(key, "TileWallpaper", 0,
                                  winreg.REG_SZ, tile_value)
        except OSError as e:
            return _tr.failure_result(
                _tr.WALLPAPER_NOT_SET_KIND,
                f"Could not save the wallpaper style in the registry: {e}")
        result = _wallpaper.set_wallpaper_for_monitors(
            str(image), monitor)
        if not result.get("applied"):
            return _tr.failure_result(
                _tr.WALLPAPER_NOT_SET_KIND,
                f"Wallpaper not set: {result.get('reason', '')}.")
        label = result.get("monitor") or str(monitor)
        suffix = (" (Per-monitor wallpaper was unavailable, so "
                  "this set ALL monitors instead.)"
                  if result.get("fell_back") else "")
        return (f"Wallpaper set to '{image}' on {label} "
                f"(style: {wanted_style}).{suffix}")
    # Revert batch (2026-11-05): one shared save-previous seam —
    # no wallpaper change without a recorded previous. The save
    # runs before any registry write; on failure nothing changes.
    saved = _wallpaper.save_previous_wallpaper()
    if not saved["saved"]:
        return _tr.failure_result(
            _tr.WALLPAPER_NOT_SET_KIND,
            "Could not save the current wallpaper before "
            "changing it (%s), so the desktop was left "
            "unchanged." % saved["reason"])
    import winreg

    style_value, tile_value = _WALLPAPER_STYLES[wanted_style]
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _DESKTOP_KEY) as key:
            winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ, style_value)
            winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ, tile_value)
    except OSError as e:
        return _tr.failure_result(
            _tr.WALLPAPER_NOT_SET_KIND,
            f"Could not save the wallpaper style in the registry: {e}")
    import ctypes

    try:
        ok = ctypes.windll.user32.SystemParametersInfoW(
            _SPI_SETDESKWALLPAPER, 0, str(image), _SPIF_SENDCHANGE)
    except Exception as e:  # noqa: BLE001
        return _tr.failure_result(
            _tr.WALLPAPER_NOT_SET_KIND,
            f"Windows could not apply the wallpaper: {e}")
    if not ok:
        return _tr.failure_result(
            _tr.WALLPAPER_NOT_SET_KIND,
            "Windows refused the new wallpaper (SystemParametersInfo "
            "reported failure), so the desktop was left unchanged.")
    # SPIF_UPDATEINIFILE makes Windows persist the choice in the same
    # Desktop key's Wallpaper value — read it back as confirmation.
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _DESKTOP_KEY) as key:
            current, _t = winreg.QueryValueEx(key, "Wallpaper")
    except OSError:
        current = ""
    if str(current).lower() != str(image).lower():
        return _tr.failure_result(
            _tr.WALLPAPER_NOT_SET_KIND,
            f"Windows accepted the wallpaper change for '{image}', but the "
            f"saved desktop setting now reads '{current or '(empty)'}' "
            "instead — check the desktop to see what actually applied.")
    return f"Wallpaper set to '{image}' (style: {wanted_style})."


# ---------------------------------------------------------------------------
# DARK MODE — HKCU ...\Themes\Personalize
# ---------------------------------------------------------------------------

_PERSONALIZE_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")


def _read_light_theme() -> int | None:
    """AppsUseLightTheme as an int (1 light / 0 dark), None if unreadable."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY) as key:
            data, _t = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(data)
    except OSError:
        return None


def toggle_dark_mode(mode: str = "toggle") -> str:
    """Switch Windows + apps between dark and light themes."""
    if not _is_windows():
        return f"Dark mode is a Windows theme setting — {_NOT_WINDOWS}"
    wanted = (mode or "toggle").strip().lower()
    if wanted not in ("dark", "light", "toggle"):
        return f"Unknown mode: '{mode}'. Use one of: dark, light, toggle."
    current = _read_light_theme()
    if wanted == "toggle":
        # An unreadable value means Windows is on its default: light.
        target_light = 0 if (current if current is not None else 1) == 1 else 1
    else:
        target_light = 1 if wanted == "light" else 0
    import winreg

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY) as key:
            winreg.SetValueEx(key, "AppsUseLightTheme", 0,
                              winreg.REG_DWORD, target_light)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0,
                              winreg.REG_DWORD, target_light)
    except OSError as e:
        return f"Could not change the theme setting: {e}"
    after = _read_light_theme()
    if after is None:
        return ("The theme values were written, but VYUK could not read the "
                "setting back to confirm the result.")
    if after != target_light:
        return (f"The theme was asked to switch, but the setting still reads "
                f"{'light' if after == 1 else 'dark'} — the change did not "
                "take effect.")
    return ("Light mode is now on (apps and Windows chrome follow it). "
            if after == 1 else
            "Dark mode is now on (apps and Windows chrome follow it).")


# ---------------------------------------------------------------------------
# KEEP AWAKE — SetThreadExecutionState from a dedicated daemon thread
# ---------------------------------------------------------------------------

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002
_AWAKE_FLAGS = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED

# Module-level keep-awake state: {active, until (epoch seconds or
# None for "until stopped"), thread, stop (threading.Event),
# applied (did the last SetThreadExecutionState assert actually
# take? True/False once the awake thread has reported, None until
# it does — keep_awake must never claim ON over a refused request)}.
_awake: dict = {"active": False, "until": None, "thread": None,
                "stop": None, "applied": None}


def _apply_execution_state(flags: int) -> bool:
    """One SetThreadExecutionState call. Windows-only by nature;
    returns False (never raises) anywhere it cannot be made. Split
    out so tests can record the calls instead of making them."""
    if not _is_windows():
        return False
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:  # noqa: BLE001
        return False


def _awake_loop(stop_event: threading.Event, until: float | None) -> None:
    """Thread body: assert the awake flags, re-assert every 30 s so
    the request survives, and clear them on the way out.
    SetThreadExecutionState is per-thread, so the asserting thread
    must be the one that stays alive — and the one that clears.
    Every assert's result is recorded in _awake["applied"] so the
    caller can tell a held request from a refused one."""
    _awake["applied"] = _apply_execution_state(_AWAKE_FLAGS)
    while not stop_event.is_set():
        if until is not None:
            remaining = until - time.time()
            if remaining <= 0:
                break
            wait_s = min(30.0, remaining)
        else:
            wait_s = 30.0
        if stop_event.wait(wait_s):
            break
        _awake["applied"] = _apply_execution_state(_AWAKE_FLAGS)
    _apply_execution_state(_ES_CONTINUOUS)  # release the requirement
    _awake["active"] = False
    _awake["until"] = None


def _engage_awake(minutes: int = 0) -> None:
    """Start (or re-arm) keep-awake. Pure state + thread management —
    the platform effect lives in _apply_execution_state, which is
    why this is exercisable off-Windows in tests."""
    _release_awake()
    until = time.time() + minutes * 60 if minutes and minutes > 0 else None
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_awake_loop, args=(stop_event, until),
        name="nova-keep-awake", daemon=True)
    _awake.update({"active": True, "until": until,
                   "thread": thread, "stop": stop_event, "applied": None})
    thread.start()


def _release_awake() -> None:
    """Stop keep-awake if it is on; harmless when it is already off."""
    stop_event = _awake.get("stop")
    thread = _awake.get("thread")
    if stop_event is not None:
        stop_event.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)
    _awake.update({"active": False, "until": None, "thread": None,
                   "stop": None, "applied": None})


def _awake_status() -> dict:
    """{active, remaining_seconds} — reconciles a thread that ended
    on its own (deadline reached) with the stored state."""
    thread = _awake.get("thread")
    if _awake.get("active") and thread is not None and not thread.is_alive():
        _awake.update({"active": False, "until": None,
                       "thread": None, "stop": None, "applied": None})
    remaining = None
    if _awake.get("active") and _awake.get("until") is not None:
        remaining = max(0, int(_awake["until"] - time.time()))
    return {"active": bool(_awake.get("active")), "remaining": remaining}


def keep_awake(action: str = "status", minutes: int = 0) -> str:
    """Stop this PC sleeping (and blanking its screen) for a while."""
    act = (action or "status").strip().lower()
    if act not in ("start", "stop", "status"):
        return f"Unknown keep-awake action: '{action}'. Use one of: start, stop, status."
    if act == "start":
        if not _is_windows():
            return f"Keep-awake uses a Windows power API — {_NOT_WINDOWS}"
        try:
            mins = int(minutes or 0)
        except (TypeError, ValueError):
            return "Keep-awake minutes must be a whole number (0 = until you say stop)."
        if mins < 0:
            return "Keep-awake minutes can't be negative (0 = until you say stop)."
        _engage_awake(mins)
        # The awake thread asserts immediately; give it a moment to
        # report whether Windows actually accepted the request. A
        # refused assert must never be reported as ON (audit m3).
        deadline = time.time() + 2.0
        while _awake.get("applied") is None and time.time() < deadline:
            time.sleep(0.02)
        if _awake.get("applied") is False:
            _release_awake()
            return ("Couldn't keep the PC awake — the system refused "
                    "the request (SetThreadExecutionState reported "
                    "failure), so nothing is holding this PC awake and "
                    "it will still sleep on its normal schedule.")
        if mins > 0:
            return (f"Keep-awake is ON for {mins} minute(s): this PC won't sleep "
                    "or blank its screen. It lasts while VYUK is running and "
                    "ends early if you say stop.")
        return ("Keep-awake is ON until you say stop: this PC won't sleep or "
                "blank its screen. It lasts while VYUK is running.")
    if act == "stop":
        was_active = _awake_status()["active"]
        _release_awake()
        if was_active:
            return ("Keep-awake is OFF — this PC will sleep and blank its "
                    "screen on its normal schedule again.")
        return "Keep-awake wasn't on, so nothing changed."
    status = _awake_status()
    if not status["active"]:
        return ("Keep-awake is off — this PC sleeps and blanks its screen on "
                "its normal schedule.")
    if _awake.get("applied") is False:
        return ("Keep-awake was switched on, but Windows refused the "
                "request (SetThreadExecutionState reported failure), "
                "so it is NOT actually holding this PC awake — it will "
                "still sleep on its normal schedule.")
    if status["remaining"] is not None:
        return (f"Keep-awake is ON, with about {status['remaining'] // 60 + 1} "
                "minute(s) left. It lasts while VYUK is running.")
    return ("Keep-awake is ON until you say stop. "
            "It lasts while VYUK is running.")


# ---------------------------------------------------------------------------
# VIRTUAL DESKTOPS — Win+Ctrl+arrow / Win+Ctrl+D, best-effort status
# ---------------------------------------------------------------------------

_VK_LWIN = 0x5B
_VK_CONTROL = 0x11
_VK_LEFT = 0x25
_VK_RIGHT = 0x27
_VK_D = 0x44
_KEYEVENTF_KEYUP = 0x0002

_VD_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\VirtualDesktops"


def _send_desktop_chord(key_code: int) -> None:
    """Win+Ctrl+<key>: modifiers down first, released last, so the
    chord reads exactly like the physical keystrokes.

    GAPS-17: was six deprecated keybd_event calls — the last
    input dispatch outside the shared hub. Same wire events
    (down LWIN, down CTRL, down key, up key, up CTRL, up LWIN),
    same order, through the hub's _sendinput_key."""
    from .desktop.cursor_input import _sendinput_key

    _sendinput_key(_VK_LWIN)
    _sendinput_key(_VK_CONTROL)
    _sendinput_key(key_code)
    _sendinput_key(key_code, key_up=True)
    _sendinput_key(_VK_CONTROL, key_up=True)
    _sendinput_key(_VK_LWIN, key_up=True)


def _virtual_desktop_status() -> str:
    """Best-effort desktop count from the (undocumented) registry
    state; the honest fallback whenever anything can't be read."""
    fallback = ("Windows doesn't officially expose virtual-desktop state, "
                "so I can't reliably say how many desktops there are or "
                "which one is active — switching and creating with "
                "next/previous/create still work.")
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _VD_KEY) as key:
            count = 0
            index = 0
            while True:
                try:
                    _name, data, _t = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if isinstance(data, (bytes, bytearray)) and len(data) == 16:
                    count += 1
        if count == 0:
            return fallback
    except OSError:
        return fallback
    current_note = ""
    try:
        import ctypes

        session_id = ctypes.c_ulong(0)
        ctypes.windll.kernel32.ProcessIdToSessionId(
            os.getpid(), ctypes.byref(session_id))
        session_key = (
            r"Software\Microsoft\Windows\CurrentVersion\Explorer"
            rf"\SessionInfo\{session_id.value}\VirtualDesktops")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, session_key) as key:
            data, _t = winreg.QueryValueEx(key, "CurrentVirtualDesktop")
        if isinstance(data, (bytes, bytearray)) and len(data) == 16:
            current_note = " The active desktop's id is recorded by Windows."
    except Exception:  # noqa: BLE001
        current_note = ""
    noun = "desktop" if count == 1 else "desktops"
    return (f"Windows' desktop bookkeeping shows {count} virtual {noun}."
            + current_note
            + " (This state is undocumented — treat the count as "
              "best-effort, not guaranteed.)")


def virtual_desktop(action: str) -> str:
    """Move between Windows virtual desktops, create one, or report."""
    if not _is_windows():
        return f"Virtual desktops are a Windows feature — {_NOT_WINDOWS}"
    act = (action or "").strip().lower()
    if act not in ("next", "previous", "create", "status"):
        return (f"Unknown virtual desktop action: '{action}'. "
                "Use one of: next, previous, create, status.")
    if act == "status":
        return _virtual_desktop_status()
    # Failsafe (audit M4): these chords are synthetic keystrokes, so
    # they honour the same kill-switch as every other input hand —
    # while the failsafe is tripped, ZERO key events are sent.
    from .desktop.control import _input_blocked  # Phase 3 D4a: control hub moved to core/desktop/

    blocked = _input_blocked()
    if blocked is not None:
        return blocked
    key_code, label = {
        "next": (_VK_RIGHT, "Win+Ctrl+Right — one desktop to the right"),
        "previous": (_VK_LEFT, "Win+Ctrl+Left — one desktop to the left"),
        "create": (_VK_D, "Win+Ctrl+D — a new desktop"),
    }[act]
    try:
        _send_desktop_chord(key_code)
    except Exception as e:  # noqa: BLE001
        return f"Could not send the virtual-desktop shortcut: {e}"
    return (f"Sent the {label} shortcut. Windows gives no confirmation "
            "back for desktop switches, so check the screen to see the "
            "result.")


# ---------------------------------------------------------------------------
# SETTINGS PAGES — ms-settings: URIs
# ---------------------------------------------------------------------------

_SETTINGS_PAGES = {
    "home": "ms-settings:",
    "bluetooth": "ms-settings:bluetooth",
    "wifi": "ms-settings:network-wifi",
    "network": "ms-settings:network",
    "display": "ms-settings:display",
    "sound": "ms-settings:sound",
    "apps": "ms-settings:appsfeatures",
    "personalization": "ms-settings:personalization",
    "update": "ms-settings:windowsupdate",
    "battery": "ms-settings:batterysaver",
    "storage": "ms-settings:storagesense",
    "privacy": "ms-settings:privacy",
    "accounts": "ms-settings:yourinfo",
    "time": "ms-settings:dateandtime",
    "gaming": "ms-settings:gaming-gamebar",
    "accessibility": "ms-settings:easeofaccess",
    "startup": "ms-settings:startupapps",
    "notifications": "ms-settings:notifications",
    "defaultapps": "ms-settings:defaultapps",
}


def open_settings_page(page: str = "home") -> str:
    """Open one of the Windows Settings pages."""
    if not _is_windows():
        return f"Windows Settings only exists on Windows — {_NOT_WINDOWS}"
    wanted = (page or "home").strip().lower()
    if wanted not in _SETTINGS_PAGES:
        return ("Unknown Settings page: "
                f"'{page}'. Use one of: {', '.join(sorted(_SETTINGS_PAGES))}.")
    uri = _SETTINGS_PAGES[wanted]
    try:
        os.startfile(uri)  # noqa: S606 — a fixed ms-settings: URI from the map
    except Exception as e:  # noqa: BLE001
        return f"Could not open Settings at '{uri}': {e}"
    # R1b: the page was launched; no read-back exists for whether
    # it rendered — mark it honestly unverified.
    from . import tool_outcome as _outcome  # R1b: seam wiring

    return _outcome.unverified(
        "Opened Windows Settings → %s (%s). (Launch requested — "
        "whether the page rendered was not confirmed.)"
        % (wanted, uri),
        "no read-back exists for a launched Settings page")


# ---------------------------------------------------------------------------
# CLIPBOARD — clear it via the Win32 clipboard API
# ---------------------------------------------------------------------------


def clear_clipboard():
    """Empty the clipboard so whatever was copied is gone.

    R1a (tools loopholes, seam pass 2): the result is an Outcome —
    the empty claim was already verified in-handler by counting
    the formats left (verify-after-act #38); the seam now carries
    that verdict in the audited dict instead of a bare string.
    Refusal returns stay plain strings.
    """
    from . import tool_outcome as _outcome  # R1a: seam wiring

    if not _is_windows():
        return f"Clearing the clipboard this way needs Windows — {_NOT_WINDOWS}"
    import ctypes

    user32 = ctypes.windll.user32
    if not user32.OpenClipboard(None):
        return ("Could not open the clipboard — another app is holding it "
                "right now, so nothing was cleared. Try again in a moment.")
    try:
        if not user32.EmptyClipboard():
            return "Windows refused to empty the clipboard, so its contents are unchanged."
        # Verify-after-act (#38): count what is actually on
        # the clipboard now, while it is still open.
        remaining = user32.CountClipboardFormats()
    finally:
        user32.CloseClipboard()
    if remaining:
        return _outcome.failed(
            f"Windows emptied the clipboard, but {remaining} "
            "format(s) are present again already — another app "
            "may have re-populated it, so check before pasting.",
            "%d format(s) present after the clear" % remaining)
    return _outcome.verified(
        "Clipboard cleared — verified empty (no formats remain on it).",
        "0 clipboard formats remain")


# ---------------------------------------------------------------------------
# TOAST NOTIFICATIONS — PowerShell + Windows.UI.Notifications
# ---------------------------------------------------------------------------


def _xml_escape(text: str) -> str:
    """Escape text for XML content — & first, or the escapes themselves
    get escaped."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;"))


def _ps_single_quote(text: str) -> str:
    """Make text safe inside a single-quoted PowerShell string by
    doubling every single quote (PowerShell's only escape there)."""
    return text.replace("'", "''")


def _build_toast_command(title: str, message: str) -> str:
    """Build the PowerShell command that shows a toast. Pure function.

    Defence in depth on an injection surface: the title/message are
    XML-escaped for the toast payload FIRST, then the whole payload
    is quote-doubled for its single-quoted PowerShell string — either
    layer alone would hold; both are applied so neither has to."""
    xml = ('<toast><visual><binding template="ToastGeneric">'
           f"<text>{_xml_escape(title)}</text>"
           f"<text>{_xml_escape(message)}</text>"
           "</binding></visual></toast>")
    payload = _ps_single_quote(xml)
    return (
        "$ErrorActionPreference = 'Stop'; "
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, "
        "ContentType = WindowsRuntime] | Out-Null; "
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
        f"$xml.LoadXml('{payload}'); "
        "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml; "
        "[Windows.UI.Notifications.ToastNotificationManager]"
        "::CreateToastNotifier('VYUK').Show($toast)")


# #28: tags are VYUK's own grouping key — WinRT toasts
# offer a script no replace-by-tag API, so a same-tag show is
# recorded here and the result says plainly that it
# supersedes the earlier one (Windows may still list both
# in the Action Center).
_ACTIVE_TAGS: dict = {}


def notify_user(title: str, message: str, tag: str = "") -> str:
    """Show a Windows toast notification from VYUK"""
    if not _is_windows():
        return f"Toast notifications need Windows — {_NOT_WINDOWS}"
    heading = (title or "").strip()
    body = (message or "").strip()
    if not heading or not body:
        return "A notification needs both a title and a message."
    command = _build_toast_command(heading, body)
    rc, out, err = _run(
        ["powershell", "-NoProfile", "-Command", command], timeout=30)
    if rc != 0:
        detail = _tail(err or out)
        return ("The notification could not be shown"
                + (f": {detail}" if detail else
                   f" (PowerShell exited with code {rc})."))
    tag_key = (tag or "").strip().lower()
    # R1b: the toast was shown per the API; no read-back exists for
    # whether the user saw it — mark it honestly unverified.
    from . import tool_outcome as _outcome  # R1b: seam wiring

    if tag_key:
        earlier = _ACTIVE_TAGS.get(tag_key)
        _ACTIVE_TAGS[tag_key] = (heading, body)
        if earlier is not None:
            return _outcome.unverified(
                f"Notification shown: '{heading}' — it replaces "
                f"the earlier '{earlier[0]}' notification with "
                f"the tag '{tag_key}' (Windows may still list "
                "both in the Action Center). (Shown — whether you "
                "saw it was not confirmed.)",
                "no read-back exists for whether the user saw the toast")
        return _outcome.unverified(
            f"Notification shown: '{heading}' (tagged '{tag_key}'). "
            "(Shown — whether you saw it was not confirmed.)",
            "no read-back exists for whether the user saw the toast")
    return _outcome.unverified(
        f"Notification shown: '{heading}'. (Shown — whether you saw "
        "it was not confirmed.)",
        "no read-back exists for whether the user saw the toast")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Desktop & Personalisation (Pack A) tool on a
    ToolRegistry-like object."""
    registry.register(
        name="set_wallpaper",
        description="Set the Windows desktop wallpaper to an image file, choosing how it fills the screen: fill (default, crops to fit), fit (whole picture, bars if needed), stretch, tile, center, or span (across monitors). The style is saved in the registry and the change is verified by reading the saved setting back. Optional monitor names ONE screen (primary, left, right, second monitor, a device name like DISPLAY2, or all) — without it the image is set on all monitors at once; with it, only that screen changes and the others are left exactly as they are. The style is global Windows state and applies to the whole desktop either way.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file, e.g. 'C:\\Users\\me\\Pictures\\beach.jpg'"},
                "style": {"type": "string", "enum": ["fill", "fit", "stretch", "tile", "center", "span"], "description": "How the picture fills the screen (default fill)"},
                "monitor": {"type": "string", "description": "Optional: which screen to change — 'primary', 'left', 'right', 'second monitor', a device name like 'DISPLAY2', or 'all'. Without it the image is set on all monitors at once."},
            },
            "required": ["path"],
        },
        handler=set_wallpaper,
    )
    registry.register(
        name="toggle_dark_mode",
        description="Switch Windows' theme between dark and light — both the system chrome and apps that follow the system setting. mode is 'dark', 'light' or 'toggle' (default: flip whichever is on now). The result is the mode read back from the registry after the change.",
        parameters={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["dark", "light", "toggle"], "description": "'dark', 'light' or 'toggle' (default toggle)"},
            },
            "required": [],
        },
        handler=toggle_dark_mode,
    )
    registry.register(
        name="keep_awake",
        description="Keep this PC awake — no sleeping and no screen blanking — while VYUK is running. action 'start' turns it on (optionally for a number of minutes, 0 = until stopped), 'stop' turns it off, 'status' (default) reports whether it's on and how long is left.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "stop", "status"], "description": "'start', 'stop' or 'status' (default status)"},
                "minutes": {"type": "integer", "description": "For 'start': how long to stay awake, in minutes (0 = until stopped)"},
            },
            "required": [],
        },
        handler=keep_awake,
    )
    registry.register(
        name="virtual_desktop",
        description="Work with Windows virtual desktops (Task View desktops): 'next' and 'previous' send the Win+Ctrl+arrow shortcuts, 'create' sends Win+Ctrl+D for a new desktop. Windows gives no confirmation for switches. 'status' reports the desktop count from Windows' own bookkeeping, which is undocumented and best-effort.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["next", "previous", "create", "status"], "description": "'next', 'previous', 'create' or 'status'"},
            },
            "required": ["action"],
        },
        handler=virtual_desktop,
    )
    registry.register(
        name="open_settings_page",
        description="Open a Windows Settings page by name: home, bluetooth, wifi, network, display, sound, apps, personalization, update, battery, storage, privacy, accounts, time, gaming, accessibility, startup, notifications or defaultapps. It only opens the page — dedicated toggles live in tools like toggle_dark_mode and set_screen_timeout.",
        parameters={
            "type": "object",
            "properties": {
                "page": {"type": "string", "enum": ["home", "bluetooth", "wifi", "network", "display", "sound", "apps", "personalization", "update", "battery", "storage", "privacy", "accounts", "time", "gaming", "accessibility", "startup", "notifications", "defaultapps"], "description": "Which Settings page to open (default home)"},
            },
            "required": [],
        },
        handler=open_settings_page,
    )
    registry.register(
        name="clear_clipboard",
        description="Clear the clipboard, so whatever was last copied (text, image, files) is gone and pasting does nothing until something new is copied. The result verifies the clipboard is actually empty afterwards.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=clear_clipboard,
    )
    registry.register(
        name="notify_user",
        description="Show a Windows toast notification with a title and a message — for getting the user's attention when they're not looking at VYUK The result only claims the notification showed when Windows accepted it. Pass the same tag on a later call to mark the new notification as replacing the earlier one with that tag.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Notification heading, e.g. 'Download finished'"},
                "message": {"type": "string", "description": "Notification body text"},
                "tag": {"type": "string", "description": "Optional grouping tag, e.g. 'download': a later call with the same tag is reported as replacing the earlier notification"},
            },
            "required": ["title", "message"],
        },
        handler=notify_user,
    )
