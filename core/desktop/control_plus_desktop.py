"""Control Plus split (B26): control_plus_desktop.py -- moved verbatim from core/desktop/control_plus.py; see the facade for the split map."""

from .control_plus_helpers import _is_windows  # INTENTIONAL (B26: cross-part bare name; defined in control_plus_helpers)
from pathlib import Path
import time

def display_control(action: str, path: str = "", monitor: str | None = None):
    """Display actions: info, set_wallpaper, dark_on, dark_off.

    ``monitor`` names ONE screen for the set_wallpaper leg
    ("primary", "left", "second monitor", a device name, or
    "all"); without it the leg sets all monitors via SPI, as
    before. A per-monitor set goes through IDesktopWallpaper
    and saves that monitor's previous first (the shared seam
    in core/wallpaper.py); when the per-monitor API is
    unavailable it falls back to SPI on all monitors and the
    reply says so.

    R1a (tools loopholes, seam pass 2): the dark legs verify
    through the shared core.tool_outcome.registry_dword_equals
    helper and the set_wallpaper leg keeps its REG_SZ read-back
    (H10) with the success wrapped in verified() — the result is
    an Outcome on every leg that acts. Refusals and the read-only
    info leg stay plain strings.
    """
    action = (action or "").strip().lower()
    valid = ("info", "set_wallpaper", "dark_on", "dark_off")
    if action not in valid:
        return f"Unknown display action: '{action}'. Use one of: {', '.join(valid)}."
    if action == "info":
        try:
            from .screen_capture import list_monitors

            listed = list_monitors()
            if listed["ok"] and listed["monitors"]:
                primary = next(
                    (m for m in listed["monitors"] if m["primary"]),
                    listed["monitors"][0])
                return (f"Primary display resolution: {primary['width']} "
                        f"x {primary['height']} pixels.")
        except Exception:  # noqa: BLE001 — headless: try Windows metrics next
            pass
        if _is_windows():
            try:
                import ctypes

                user32 = ctypes.windll.user32
                return (f"Primary display resolution: "
                        f"{user32.GetSystemMetrics(0)} x {user32.GetSystemMetrics(1)} pixels.")
            except Exception as e:  # noqa: BLE001
                return f"Could not read the display size: {e}"
        return "Could not determine the display size — no display is available on this system."
    if action == "set_wallpaper":
        if not _is_windows():
            return "Setting wallpaper via SystemParametersInfo is only available on Windows — this system is not Windows."
        if not path or not str(path).strip():
            return "set_wallpaper needs the path to an image file."
        # B1 (2026-11-06): every failure below returns a
        # funnel-shaped failure dict, never a plain string — the
        # funnel used to carry each of these as {"status":
        # "success"}, so a routine recorded SUCCESS for a
        # wallpaper that was never set. The message text is
        # unchanged; only the envelope changes.
        from ..funnel import tool_result as _tr  # B1: failure shape
        image = Path(str(path)).expanduser()
        if not image.exists() or not image.is_file():
            return _tr.failure_result(
                _tr.WALLPAPER_NOT_SET_KIND,
                f"Wallpaper image not found: {image}")
        # R4: a monitor selector routes through the shared
        # per-monitor seam (save-previous + SetWallpaper +
        # read-back, or the honest SPI fallback); without one
        # this leg keeps its legacy SPI path below.
        if monitor is not None:
            from .. import wallpaper as _wallpaper  # lazy: stdlib-only

            result = _wallpaper.set_wallpaper_for_monitors(
                str(image), monitor)
            from .. import tool_outcome as _outcome  # R4: seam wiring

            if not result.get("applied"):
                return _tr.failure_result(
                    _tr.WALLPAPER_NOT_SET_KIND,
                    f"Wallpaper not set: {result.get('reason', '')}.")
            label = result.get("monitor") or str(monitor)
            suffix = (" (Per-monitor wallpaper was unavailable, "
                      "so this set ALL monitors instead.)"
                      if result.get("fell_back") else "")
            return _outcome.verified(
                f"Wallpaper set to {image} on {label} (saved setting "
                f"read back to confirm).{suffix}",
                "per-monitor wallpaper reads %s as expected" % image)
        # Revert batch (2026-11-05): one shared save-previous
        # seam — no wallpaper change without a recorded
        # previous. The save runs before the SPI call; on
        # failure nothing changes. (This leg writes no style —
        # it keeps the current style — so only the save is
        # shared, not set_wallpaper's style write.)
        from .. import wallpaper as _wallpaper  # lazy: stdlib-only

        saved = _wallpaper.save_previous_wallpaper()
        if not saved["saved"]:
            return _tr.failure_result(
                _tr.WALLPAPER_NOT_SET_KIND,
                "Could not save the current wallpaper before "
                "changing it (%s), so the desktop was left "
                "unchanged." % saved["reason"])
        try:
            import ctypes

            SPI_SETDESKWALLPAPER = 20
            SPIF_UPDATEINIFILE_SENDCHANGE = 3
            ok = ctypes.windll.user32.SystemParametersInfoW(
                SPI_SETDESKWALLPAPER, 0, str(image), SPIF_UPDATEINIFILE_SENDCHANGE)
            if not ok:
                return _tr.failure_result(
                    _tr.WALLPAPER_NOT_SET_KIND,
                    "Windows refused the wallpaper change "
                    "(SystemParametersInfo returned 0).")
        except Exception as e:  # noqa: BLE001
            return _tr.failure_result(
                _tr.WALLPAPER_NOT_SET_KIND,
                f"Could not set the wallpaper: {e}")
        # Read-back (v5.9, audit H10 — the skills_desktop_prefs.set_wallpaper
        # standard): SPIF_UPDATEINIFILE makes Windows persist the
        # choice in the Desktop key's Wallpaper value — read it back
        # instead of trusting the BOOL alone.
        current = ""
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Control Panel\Desktop") as key:
                current, _t = winreg.QueryValueEx(key, "Wallpaper")
        except Exception:  # noqa: BLE001 — read-back is best-effort
            current = ""
        if str(current).lower() != str(image).lower():
            # B1: the act was attempted and the world contradicts
            # the claim. R1a wrapped this in a failed Outcome,
            # but a failed Outcome still leaves the funnel as
            # {"status": "success", "outcome_verified": False} —
            # which a routine records as success. A funnel-shaped
            # failure dict is the honest envelope; the message is
            # unchanged.
            return _tr.failure_result(
                _tr.WALLPAPER_NOT_SET_KIND,
                f"Windows accepted the wallpaper change for '{image}', "
                f"but the saved desktop setting now reads "
                f"'{current or '(empty)'}' instead — check the desktop "
                "to see what actually applied.")
        # R1a: keep the H10 REG_SZ read-back; wrap the success in
        # verified() so the seam records it.
        from .. import tool_outcome as _outcome  # R1a: seam wiring

        return _outcome.verified(
            f"Wallpaper set to {image} (saved setting read back to confirm).",
            "desktop Wallpaper setting reads %s as expected" % image)
    # dark_on / dark_off
    if not _is_windows():
        return "Dark mode toggling edits the Windows registry theme keys — this system is not Windows."
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        value = 0 if action == "dark_on" else 1
        # Write BOTH theme values and read them back before claiming
        # anything (v5.9, audit H6 — the skills_desktop_prefs
        # toggle_dark_mode pattern): the shell follows
        # SystemUsesLightTheme, apps follow AppsUseLightTheme, and
        # an unwritten or unconfirmed half is a half-applied theme.
        # R1a: the read-back goes through the shared
        # core.tool_outcome.registry_dword_equals helper.
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, value)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, value)
        from .. import tool_outcome as _outcome  # R1a: seam wiring

        ok_apps, ev_apps = _outcome.registry_dword_equals(
            key_path, "AppsUseLightTheme", value)
        ok_sys, ev_sys = _outcome.registry_dword_equals(
            key_path, "SystemUsesLightTheme", value)
        mode_word = "Dark" if action == "dark_on" else "Light"
        if ok_apps and ok_sys:
            return _outcome.verified(
                f"{mode_word} mode is now on — both theme values "
                "(AppsUseLightTheme and SystemUsesLightTheme) were set "
                f"to {value} and read back to confirm. Apps pick it up "
                "immediately or on next launch.",
                "%s; %s" % (ev_apps, ev_sys))
        # A read-back that returns a different DWORD is the world
        # contradicting the claim (failed); a value that cannot be
        # read at all is unconfirmed (unverified), not a failure.
        _mismatch = [ev for ev in (ev_apps, ev_sys)
                     if "reads " in ev and ", not " in ev]
        if _mismatch:
            return _outcome.failed(
                f"The theme was asked to switch to {mode_word.lower()} "
                f"mode, but the settings did not fully take effect: "
                f"{'; '.join(_mismatch)}",
                "; ".join(_mismatch))
        return _outcome.unverified(
            "The theme values were written, but VYUK could not "
            "read the setting back to confirm the result.",
            "%s; %s" % (ev_apps, ev_sys))
    except Exception as e:  # noqa: BLE001
        return f"Could not change the theme mode: {e}"


# ---------------------------------------------------------------------------
# HANDS++ — virtual desktops & snapping (via proven hotkeys)
# ---------------------------------------------------------------------------

_DESKTOP_HOTKEYS = {
    "show_desktop": "win+d",
    "minimize_all": "win+m",
    "snap_left": "win+left",
    "snap_right": "win+right",
    "next_desktop": "win+ctrl+right",
    "prev_desktop": "win+ctrl+left",
    "task_view": "win+tab",
}


def desktop_control(action: str) -> str:
    """Desktop/window-layout actions sent as Windows hotkeys: show_desktop,
    minimize_all, snap_left, snap_right, next_desktop, prev_desktop, task_view."""
    action = (action or "").strip().lower()
    if action not in _DESKTOP_HOTKEYS:
        return f"Unknown desktop action: '{action}'. Use one of: {', '.join(_DESKTOP_HOTKEYS)}."
    if not _is_windows():
        return ("Desktop actions are Windows hotkey combos (Win+D, Win+Tab, "
                "virtual desktops) — this system is not Windows.")
    try:
        from .control import press_hotkey  # Phase 3 D4a: control hub now in core/desktop/; D2b interim closed
    except Exception as e:  # noqa: BLE001
        return f"Desktop control unavailable: the core hotkey sender could not be loaded ({e})."
    result = press_hotkey(_DESKTOP_HOTKEYS[action])
    # R1b: press_hotkey may now return an Outcome (unverified when
    # the key event was sent) — read it through str(...) for the
    # prefix check, exactly as the R1a display_control fix did.
    if str(result).startswith("Pressed hotkey"):
        # Wording discipline (v5.9, audit H14): a hotkey dispatch
        # ack confirms the keys were SENT, not that the desktop
        # obeyed — so the receipt says "sent" (the media_control
        # pattern), never "done". Snap actions name the window
        # they acted on: the foreground window at dispatch time.
        combo = _DESKTOP_HOTKEYS[action]
        if action in ("snap_left", "snap_right"):
            title = None
            try:
                from .uia import foreground_title  # Phase 3 D4b: uia hub now in core/desktop/; D2b interim closed

                title = foreground_title()
            except Exception:  # noqa: BLE001 — naming is best-effort
                title = None
            window = f"'{title}'" if title else "the active window"
            # R1b: same honest mark as the generic leg below — the
            # combo was sent, whether the desktop obeyed is
            # unconfirmed.
            from .. import tool_outcome as _outcome  # R1b: seam wiring

            return _outcome.unverified(
                "Sent %s (%s) to %s. (Sent — whether the desktop "
                "obeyed was not confirmed.)" % (action, combo, window),
                "no OS read-back exists for a sent hotkey combo")
        note = ""
        if action == "show_desktop":
            note = (" (Win+D is a toggle: if the desktop was already "
                    "showing, this restored the windows instead.)")
        # R1b: the hotkey combo was sent; no read-back exists for
        # whether the desktop obeyed — mark it honestly unverified.
        from .. import tool_outcome as _outcome  # R1b: seam wiring

        return _outcome.unverified(
            "Desktop action sent: %s (%s).%s (Sent — whether the "
            "desktop obeyed was not confirmed.)" % (action, combo, note),
            "no OS read-back exists for a sent hotkey combo")
    return result


# ---------------------------------------------------------------------------
# SYSTEM — processes & battery
# ---------------------------------------------------------------------------

def process_control(action: str, name: str = "", pid: int = 0, confirm: bool = False) -> str:
    """Inspect processes (list/top_memory) or kill one by pid (kill_pid, confirm required)."""
    action = (action or "").strip().lower()
    valid = ("list", "top_memory", "kill_pid")
    if action not in valid:
        return f"Unknown process action: '{action}'. Use one of: {', '.join(valid)}."
    try:
        import psutil
    except ImportError:
        return "Process control isn't available in this build of VYUK — the process part didn't make it into the package."
    if action == "kill_pid":
        if not confirm:
            return ("Blocked by VYUK's trust layer: killing a process by pid "
                    "needs confirm=true. Ask again with confirmation.")
        try:
            target_pid = int(pid)
        except (TypeError, ValueError):
            return "kill_pid needs a numeric pid."
        if target_pid <= 0:
            return "kill_pid needs a numeric pid."
        try:
            proc = psutil.Process(target_pid)
            pname = proc.name()
            proc.terminate()
        except psutil.NoSuchProcess:
            return f"No process with pid {target_pid} is running."
        except psutil.AccessDenied:
            return f"Access denied: VYUK cannot terminate pid {target_pid} (it may be a system process)."
        except Exception as e:  # noqa: BLE001
            return f"Could not kill pid {target_pid}: {e}"
        # Verify the exit (v5.9, audit H15): a process that ignores
        # terminate is indistinguishable from a dead one unless we
        # wait — the receipt must say which of the two happened.
        try:
            proc.wait(timeout=2)
            return (f"Terminated {pname} (pid {target_pid}) — "
                    "confirmed it has exited.")
        except psutil.TimeoutExpired:
            return (f"Sent terminate to {pname} (pid {target_pid}), "
                    "but it is still running after 2 seconds — it may "
                    "be ignoring the request or need more time to close.")
        except psutil.NoSuchProcess:
            return (f"Terminated {pname} (pid {target_pid}) — "
                    "confirmed it has exited.")
        except Exception:  # noqa: BLE001 — exit state unreadable
            return (f"Sent terminate to {pname} (pid {target_pid}); "
                    "its exit could not be confirmed.")
    try:
        procs = []
        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                proc.cpu_percent(None)  # prime the CPU counters
                procs.append(proc)
            except Exception:  # noqa: BLE001 — process died mid-scan
                continue
        time.sleep(0.2)
        rows = []
        for proc in procs:
            try:
                info = proc.info
                cpu = proc.cpu_percent(None)
                rss = info["memory_info"].rss if info.get("memory_info") else 0
                rows.append((info.get("name") or "?", info.get("pid") or 0, cpu, rss))
            except Exception:  # noqa: BLE001 — process died mid-scan
                continue
        if action == "list":
            rows.sort(key=lambda r: r[2], reverse=True)
            top, heading = rows[:15], "Top 15 processes by CPU"
        else:  # top_memory
            rows.sort(key=lambda r: r[3], reverse=True)
            top, heading = rows[:10], "Top 10 processes by memory"
        if not top:
            return "No processes could be read."
        lines = [f"• {n} (pid {p}) — CPU {c:.1f}% · {r / (1024 ** 2):.0f} MB"
                 for n, p, c, r in top]
        if len(rows) > len(top):
            lines.append(
                f"… and {len(rows) - len(top)} more processes "
                f"({len(rows)} running in total)."
            )
        return f"{heading}:\n" + "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"Could not read the process list: {e}"


def battery_info() -> str:
    """Battery percentage, charging state and estimated time left."""
    try:
        import psutil
    except ImportError:
        return "Battery info isn't available in this build of VYUK — the battery part didn't make it into the package."
    try:
        battery = psutil.sensors_battery()
    except Exception as e:  # noqa: BLE001
        return f"Could not read battery info: {e}"
    if battery is None:
        return "No battery detected — this looks like a desktop / mains-powered machine."
    state = "plugged in (charging)" if battery.power_plugged else "on battery"
    result = f"Battery: {battery.percent:.0f}% — {state}."
    if not battery.power_plugged and battery.secsleft not in (
            getattr(psutil, "POWER_TIME_UNLIMITED", -2),
            getattr(psutil, "POWER_TIME_UNKNOWN", -1), -1, -2, None):
        hours, mins = divmod(int(battery.secsleft) // 60, 60)
        result += f" Estimated time left: {hours}h {mins:02d}m."
    return result
