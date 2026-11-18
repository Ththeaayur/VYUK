"""Split from core/senses.py (Phase B batch B4): family impl module. Move only — see senses.py facade."""

import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request


# ---------------------------------------------------------------------------
# Proxy-aware opener (mirrors core.voice.local_assets._build_opener's policy)
# ---------------------------------------------------------------------------


def _usable_proxy(value) -> str | None:
    """A proxy value urllib can actually dial — an http/https URL
    with a host. PAC files and bare host:port junk return None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.lower().endswith(".pac"):
        return None
    try:
        parts = urllib.parse.urlsplit(value)
        host = parts.hostname
        _ = parts.port  # raises on a malformed port
    except Exception:  # noqa: BLE001
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    return value


def _build_sniff_opener(extra_handlers=()):
    """An opener that honours the system proxy (getproxies() +
    HTTP(S)_PROXY env, env winning) with invalid values dropped,
    falling back to an explicitly proxy-free opener — the same
    policy as the download path in core.voice.local_assets. Never raises."""
    try:
        found: dict = {}
        try:
            found = urllib.request.getproxies() or {}
        except Exception:  # noqa: BLE001
            found = {}
        merged: dict[str, str] = {}
        for scheme in ("http", "https"):
            value = found.get(scheme)
            if isinstance(value, str) and value.strip():
                merged[scheme] = value.strip()
        for scheme in ("http", "https"):
            for var in (f"{scheme.upper()}_PROXY", f"{scheme}_proxy"):
                value = (os.environ.get(var) or "").strip()
                if value:
                    merged[scheme] = value
                    break
        clean = {s: u for s, v in merged.items()
                 if (u := _usable_proxy(v))}
        if clean:
            return urllib.request.build_opener(
                urllib.request.ProxyHandler(clean), *extra_handlers)
    except Exception:  # noqa: BLE001 — fall through to direct
        pass
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), *extra_handlers)


# ---------------------------------------------------------------------------
# Wi-Fi surroundings
# ---------------------------------------------------------------------------


def _wifi_command_output(args: list[str]) -> tuple[int, str]:
    """Run a Wi-Fi listing command; (returncode, decoded stdout).
    -1 when the command could not run at all. Never raises."""
    kwargs: dict = {"capture_output": True, "timeout": 10,
                    "stdin": subprocess.DEVNULL}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        completed = subprocess.run(args, **kwargs)
        text = (completed.stdout or b"").decode("utf-8", "replace")
        return completed.returncode, text
    except Exception:  # noqa: BLE001
        return -1, ""


def _parse_netsh_networks(text: str) -> list[dict]:
    """Parse `netsh wlan show networks mode=bssid` output.

    Pure function over canned text. Each SSID block may list
    several BSSIDs; the entry keeps the strongest signal, that
    BSSID's channel, and the AP count. Entries are deduplicated
    by SSID (netsh can repeat a block), sorted strongest-first
    and capped at 25. Hidden SSIDs (empty name) are kept as "".
    """
    found: dict[str, dict] = {}
    order: list[str] = []
    current: dict | None = None
    bssid_signal: int | None = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        m = re.match(r"SSID\s+\d+\s*:\s*(.*)$", line)
        if m:
            ssid = m.group(1).strip()
            if ssid not in found:
                found[ssid] = {"ssid": ssid, "signal": None,
                               "secured": None, "channel": None,
                               "aps": 0}
                order.append(ssid)
            current = found[ssid]
            bssid_signal = None
            continue
        if current is None:
            continue
        m = re.match(r"Authentication\s*:\s*(.+)$", line)
        if m and current["secured"] is None:
            current["secured"] = "open" not in m.group(1).lower()
            continue
        if re.match(r"BSSID\s+\d+\s*:", line):
            current["aps"] += 1
            bssid_signal = None
            continue
        m = re.match(r"Signal\s*:\s*(\d+)\s*%", line)
        if m:
            bssid_signal = int(m.group(1))
            if current["signal"] is None \
                    or bssid_signal > current["signal"]:
                current["signal"] = bssid_signal
            continue
        m = re.match(r"Channel\s*:\s*(\d+)", line)
        if m and bssid_signal is not None \
                and bssid_signal == current["signal"]:
            current["channel"] = int(m.group(1))
    nets = [found[s] for s in order]
    nets.sort(key=lambda n: (n["signal"] is not None,
                             n["signal"] or -1), reverse=True)
    return nets[:25]


def _parse_nmcli_networks(text: str) -> list[dict]:
    """Parse `nmcli -t -f SSID,SIGNAL,SECURITY,CHAN dev wifi list`.

    nmcli's terse mode separates fields with ':' and escapes a
    literal colon inside a value as '\\:'. A non-empty SECURITY
    field means the network is secured."""
    found: dict[str, dict] = {}
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        parts = re.split(r"(?<!\\):", raw)
        if len(parts) < 2:
            continue
        ssid = parts[0].replace("\\:", ":").strip()
        try:
            signal = int(parts[1])
        except ValueError:
            signal = None
        secured = bool(len(parts) > 2 and parts[2].strip())
        channel = None
        if len(parts) > 3:
            try:
                channel = int(parts[3])
            except ValueError:
                channel = None
        prev = found.get(ssid)
        if prev is None:
            found[ssid] = {"ssid": ssid, "signal": signal,
                           "secured": secured, "channel": channel,
                           "aps": 1}
        else:
            prev["aps"] += 1
            if signal is not None and (prev["signal"] is None
                                       or signal > prev["signal"]):
                prev["signal"] = signal
                prev["channel"] = channel
    nets = list(found.values())
    nets.sort(key=lambda n: (n["signal"] is not None,
                             n["signal"] or -1), reverse=True)
    return nets[:25]


def wifi_scan() -> dict:
    """The Wi-Fi networks around this PC right now.

    {"available": bool, "networks": [{ssid, signal, secured,
    channel, aps}...], "note": str} — Windows reads netsh, Linux
    falls back to nmcli when present; anywhere else (or on any
    failure) available is False and the note says why.
    """
    try:
        if sys.platform.startswith("win"):
            rc, text = _wifi_command_output(
                ["netsh", "wlan", "show", "networks", "mode=bssid"])
            if rc == 0 and text.strip():
                nets = _parse_netsh_networks(text)
                note = (f"{len(nets)} network(s) in range."
                        if nets else
                        "netsh answered but listed no networks — "
                        "Wi-Fi may be off or out of range.")
                return {"available": True, "networks": nets,
                        "note": note}
            return {"available": False, "networks": [],
                    "note": "netsh couldn't list Wi-Fi networks "
                            "(Wi-Fi may be off, or there is no "
                            "wireless adapter)."}
        if shutil.which("nmcli"):
            rc, text = _wifi_command_output(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,CHAN",
                 "dev", "wifi", "list"])
            if rc == 0:
                nets = _parse_nmcli_networks(text)
                return {"available": True, "networks": nets,
                        "note": f"{len(nets)} network(s) in range."}
            return {"available": False, "networks": [],
                    "note": "nmcli couldn't list Wi-Fi networks."}
        return {"available": False, "networks": [],
                "note": "No Wi-Fi listing tool (netsh or nmcli) is "
                        "available on this system."}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "networks": [],
                "note": f"Wi-Fi scan failed: {e}"}

