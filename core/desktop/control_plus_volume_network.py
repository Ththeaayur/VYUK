"""Control Plus split (B26): control_plus_volume_network.py -- moved verbatim from core/desktop/control_plus.py; see the facade for the split map."""

from .control_plus_helpers import _is_windows  # INTENTIONAL (B26: cross-part bare name; defined in control_plus_helpers)
import re
import shutil
import subprocess

def _pycaw_volume():
    """The pycaw IAudioEndpointVolume for the default speakers.

    Raises ImportError when pycaw/comtypes are absent (the caller
    falls back to nircmd / an honest message); any other failure
    propagates to the caller's error branch. Kept as its own seam
    so the volume handler can be tested without the COM stack.
    """
    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def set_volume_level(percent: int) -> str:
    """Set the exact master volume (0-100) via pycaw; honest fallbacks otherwise.

    Receipt discipline (v5.9, audit H8): after setting, the level
    is read back from the device and the ACTUAL percentage is
    reported; a request outside 0-100 is clamped and the clamp is
    disclosed instead of silently applied.
    """
    try:
        requested = int(percent)
    except (TypeError, ValueError):
        return "Volume level must be a number between 0 and 100."
    percent = max(0, min(100, requested))
    clamp_note = (
        f" (The request for {requested}% was clamped to the 0–100 range.)"
        if requested != percent else ""
    )
    if not _is_windows():
        return "Exact volume level needs Windows audio endpoints (pycaw) — this system is not Windows, so the volume was not changed."
    # Preferred path: pycaw endpoint volume
    try:
        volume = _pycaw_volume()
    except ImportError:
        volume = None  # fall through to nircmd / honest fallback
    except Exception as e:  # noqa: BLE001
        return f"Could not set the exact volume via pycaw: {e}"
    if volume is not None:
        try:
            volume.SetMasterVolumeLevelScalar(percent / 100.0, None)
        except Exception as e:  # noqa: BLE001
            return f"Could not set the exact volume via pycaw: {e}"
        try:
            actual = round(float(volume.GetMasterVolumeLevelScalar()) * 100)
        except Exception:  # noqa: BLE001 — set landed; read-back didn't
            actual = None
        if actual is None:
            return (f"Volume set to {percent}% — but the level could "
                    f"not be read back to confirm it.{clamp_note}")
        if actual == percent:
            return (f"Volume set to exactly {actual}% "
                    f"(read back from the device to confirm).{clamp_note}")
        return (f"Volume is now {actual}% — asked for {percent}%, "
                f"and the device reads back {actual}%.{clamp_note}")
    # Fallback: nircmd, only if the user actually has it installed
    nircmd = shutil.which("nircmd")
    if nircmd:
        try:
            units = int(65535 * percent / 100)
            proc = subprocess.run(
                [nircmd, "setsysvolume", str(units)],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                return (f"Volume set to {percent}% (via nircmd; that "
                        f"path offers no read-back to confirm).{clamp_note}")
            return f"nircmd could not set the volume (exit code {proc.returncode})."
        except Exception as e:  # noqa: BLE001
            return f"Could not set the volume via nircmd: {e}"
    return (
        "Exact volume level isn't available in this build of VYUK — the exact-volume part didn't make it into the package. "
        "(The regular volume up/down steps in adjust_volume still work.)"
    )


# ---------------------------------------------------------------------------
# HANDS++ — network / Wi-Fi / Bluetooth
# ---------------------------------------------------------------------------

def _net_status() -> str:
    import socket

    lines = []
    try:
        hostname = socket.gethostname()
        lines.append(f"Hostname: {hostname}")
        try:
            lines.append(f"Primary IP: {socket.gethostbyname(hostname)}")
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        lines.append(f"Hostname lookup failed: {e}")
    try:
        import psutil

        addrs = psutil.net_if_addrs()
        shown = 0
        for iface, entries in addrs.items():
            ipv4 = [e.address for e in entries
                    if getattr(e.family, "name", "") == "AF_INET" and not e.address.startswith("127.")]
            if ipv4:
                lines.append(f"• {iface}: {', '.join(ipv4)}")
                shown += 1
            if shown >= 10:
                break
        if shown == 0:
            lines.append("No non-loopback IPv4 adapters found.")
    except ImportError:
        lines.append("Adapter list isn't available in this build of VYUK — the network part didn't make it into the package.")
    except Exception as e:  # noqa: BLE001
        lines.append(f"Adapter list failed: {e}")
    return "\n".join(lines)


def _parse_wifi_ssids(text: str) -> list[str]:
    """SSID names from `netsh wlan show networks` output, in scan
    order, de-duplicated. Pure string work — no netsh runs here."""
    ssids = []
    for line in (text or "").splitlines():
        m = re.match(r"\s*SSID\s+\d+\s*:\s*(.+)$", line)
        if m and m.group(1).strip() and m.group(1).strip() not in ssids:
            ssids.append(m.group(1).strip())
    return ssids


def _format_wifi_list(ssids: list[str]) -> str:
    """The wifi_list result text. At most 15 networks are shown; when
    more are in range the heading states the true total found."""
    if not ssids:
        return "No Wi-Fi networks found (Wi-Fi may be off)."
    shown = ssids[:15]
    if len(ssids) > 15:
        heading = f"Wi-Fi networks nearby ({len(shown)} shown of {len(ssids)} found):"
    else:
        heading = f"Wi-Fi networks nearby ({len(shown)} shown):"
    return heading + "\n" + "\n".join(f"• {s}" for s in shown)


def network_control(action: str, ssid: str = "", password: str = "") -> str:
    """Network info + Wi-Fi control. Wi-Fi actions are Windows (netsh) only."""
    action = (action or "").strip().lower()
    valid = ("status", "wifi_on", "wifi_off", "wifi_list", "wifi_connect", "bluetooth_toggle")
    if action not in valid:
        return f"Unknown network action: '{action}'. Use one of: {', '.join(valid)}."
    if action == "status":
        return _net_status()
    if action == "bluetooth_toggle":
        return (
            "VYUK can't toggle Bluetooth reliably: Windows exposes no dependable "
            "command-line switch for the Bluetooth radio (the PowerShell radio APIs "
            "are fragile and often silently fail). Please toggle it in Settings > "
            "Bluetooth & devices — VYUK can open Settings with launch_application."
        )
    # Everything below is Wi-Fi via netsh — Windows only.
    if not _is_windows():
        return f"Wi-Fi action '{action}' uses netsh and is only available on Windows — this system is not Windows."
    try:
        if action in ("wifi_on", "wifi_off"):
            state = "enabled" if action == "wifi_on" else "disabled"
            verb = "enable" if action == "wifi_on" else "disable"
            proc = subprocess.run(
                ["netsh", "interface", "set", "interface", "Wi-Fi", verb],
                capture_output=True, text=True, timeout=15,
            )
            out = ((proc.stdout or "") + (proc.stderr or "")).strip()
            if proc.returncode != 0:
                return (
                    f"Could not turn Wi-Fi {state}: {out or 'unknown error'}. "
                    "Toggling the adapter usually needs an Administrator terminal."
                )
            # R1b: read the adapter state back — netsh accepting the
            # command is not proof the adapter changed state.
            from .. import tool_outcome as _outcome  # R1b: seam wiring

            want_on = action == "wifi_on"
            actual, evidence = _outcome.wifi_adapter_state("Wi-Fi")
            if actual is True and want_on:
                return _outcome.verified(
                    "Wi-Fi adapter enabled.", evidence)
            if actual is False and not want_on:
                return _outcome.verified(
                    "Wi-Fi adapter disabled.", evidence)
            if actual is not None:
                return _outcome.failed(
                    "Wi-Fi adapter is still %s after the %s request — "
                    "netsh accepted the command but the adapter did "
                    "not change state."
                    % ("enabled" if actual else "disabled", verb),
                    evidence)
            return _outcome.unverified(
                "Wi-Fi adapter %s was requested (netsh accepted it), "
                "but the adapter state could not be read back to "
                "confirm it." % verb,
                evidence)
        if action == "wifi_list":
            proc = subprocess.run(
                ["netsh", "wlan", "show", "networks"],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode != 0:
                return f"Could not list Wi-Fi networks: {((proc.stdout or '') + (proc.stderr or '')).strip()}"
            return _format_wifi_list(_parse_wifi_ssids(proc.stdout or ""))
        # wifi_connect
        name = (ssid or "").strip()
        if not name:
            return "Wi-Fi connect needs the network name (ssid)."
        proc = subprocess.run(
            ["netsh", "wlan", "connect", f"name={name}"],
            capture_output=True, text=True, timeout=20,
        )
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if proc.returncode != 0 or "fail" in out.lower() and "successfully" not in out.lower():
            return (
                f"Could not connect to '{name}': {out or 'unknown error'}. "
                "Note: Windows connects using a SAVED profile for that network — "
                "if this PC never joined it before, connect once manually in "
                "Settings so Windows saves the password; VYUK cannot create a "
                "profile from a raw password this way."
            )
        note = ""
        if password:
            note = " (The password given was not used — Windows used the saved profile for this network.)"
        # R1b: read the associated SSID back — netsh accepting the
        # connect command is not proof this PC joined the network.
        from .. import tool_outcome as _outcome  # R1b: seam wiring

        current, evidence = _outcome.wifi_connected_ssid()
        if current is not None and current.lower() == name.lower():
            return _outcome.verified(
                "Connected to Wi-Fi network '%s' (verified).%s"
                % (name, note),
                evidence)
        if current:
            return _outcome.unverified(
                "Connection requested for '%s', but this PC is on "
                "'%s' instead — it may still be associating, or the "
                "password was wrong. Give it a few seconds and ask "
                "for the Wi-Fi info to re-check.%s" % (name, current, note),
                evidence)
        return _outcome.unverified(
            "Connection requested for Wi-Fi network '%s', but the "
            "connection could not be confirmed — no network is "
            "associated yet. It may still be associating.%s"
            % (name, note),
            evidence)
    except Exception as e:  # noqa: BLE001
        return f"Network action '{action}' failed: {e}"
