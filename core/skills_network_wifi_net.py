"""Wi-Fi, internet diagnostics and Bluetooth — skills_network impl (A23).

Moved verbatim from core/skills_network.py (WI-FI, INTERNET and
BLUETOOTH sections), plus the section's module-level names.
"""

import re
import socket
import tempfile
import time
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

# INTENTIONAL: shared names owned by skills_network_helpers (§2 P3(c)).
from .skills_network_helpers import _NOT_WINDOWS, _is_windows, _run


def wifi_info() -> str:
    """Describe the current Wi-Fi connection and this PC's local IP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        except OSError:
            local_ip = socket.gethostbyname(socket.gethostname())
        finally:
            s.close()
    except Exception:  # noqa: BLE001
        local_ip = "unknown"
    if not _is_windows():
        return (f"This PC's local IP is {local_ip}. "
                f"(Detailed Wi-Fi connection info uses netsh — {_NOT_WINDOWS})")
    try:
        rc, out = _run(["netsh", "wlan", "show", "interfaces"], timeout=15)
    except Exception as e:  # noqa: BLE001
        return f"Could not read Wi-Fi info: {e}"
    if rc != 0:
        return f"Could not read Wi-Fi info: {out or 'netsh failed'}"
    fields: dict[str, str] = {}
    for line in out.splitlines():
        m = re.match(r"\s*([^:]+?)\s*:\s*(.+)$", line)
        if m:
            fields.setdefault(m.group(1).strip().lower(), m.group(2).strip())
    if not fields.get("ssid") and "state" not in fields:
        return (f"No Wi-Fi connection info was reported (the adapter may be off). "
                f"This PC's local IP is {local_ip}.")
    lines = ["Wi-Fi connection:"]
    if fields.get("ssid"):
        lines.append(f"• Network: {fields['ssid']}")
    if fields.get("state"):
        lines.append(f"• State: {fields['state']}")
    if fields.get("signal"):
        lines.append(f"• Signal: {fields['signal']}")
    if fields.get("radio type"):
        lines.append(f"• Radio: {fields['radio type']}")
    if fields.get("channel"):
        lines.append(f"• Channel: {fields['channel']}")
    if fields.get("authentication"):
        lines.append(f"• Security: {fields['authentication']}")
    if fields.get("bssid"):
        lines.append(f"• Access point: {fields['bssid']}")
    if fields.get("receive rate (mbps)") or fields.get("transmit rate (mbps)"):
        lines.append(f"• Link speed: {fields.get('receive rate (mbps)', '?')} Mbps down / "
                     f"{fields.get('transmit rate (mbps)', '?')} Mbps up")
    lines.append(f"• Local IP: {local_ip}")
    return "\n".join(lines)


def _build_wifi_profile_xml(ssid: str, password: str = "") -> str:
    """Build a Windows WLAN profile XML for `ssid` (open when no password).

    Both values are XML-escaped — an SSID or password containing & < >
    or quotes can never break the profile or inject extra elements.
    """
    safe_ssid = _xml_escape(ssid)
    if password:
        safe_pw = _xml_escape(password)
        security = (
            "<MSM><security><authEncryption>"
            "<authentication>WPA2PSK</authentication><encryption>AES</encryption>"
            "<useOneX>false</useOneX></authEncryption>"
            "<sharedKey><keyType>passPhrase</keyType><protected>false</protected>"
            f"<keyMaterial>{safe_pw}</keyMaterial></sharedKey>"
            "</security></MSM>"
        )
    else:
        security = (
            "<MSM><security><authEncryption>"
            "<authentication>open</authentication><encryption>none</encryption>"
            "<useOneX>false</useOneX></authEncryption>"
            "</security></MSM>"
        )
    return (
        '<?xml version="1.0"?>\n'
        '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
        f"<name>{safe_ssid}</name>"
        f"<SSIDConfig><SSID><name>{safe_ssid}</name></SSID></SSIDConfig>"
        "<connectionType>ESS</connectionType><connectionMode>auto</connectionMode>"
        f"{security}</WLANProfile>"
    )


def _current_wifi_ssid() -> str | None:
    """The SSID this PC is currently connected to, parsed from
    wifi_info()'s own report; None when it shows no network."""
    try:
        info = wifi_info()
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"Network:\s*(.+)", info or "")
    if not m:
        return None
    return m.group(1).strip() or None


def _verify_wifi_connection(name: str, attempts: int = 4,
                            interval_s: float = 1.0) -> str:
    """Close the verification loop netsh leaves open (audit m9):
    poll the module's own wifi_info path for a few seconds and say
    whether the PC actually associated with the target network."""
    current: str | None = None
    for attempt in range(max(1, attempts)):
        current = _current_wifi_ssid()
        if current is not None and current.lower() == name.lower():
            return f"Connected to '{name}' (verified)."
        if attempt + 1 < attempts:
            time.sleep(interval_s)
    if current:
        return (f"Connection requested to '{name}', but this PC is "
                f"still on '{current}' — it may still be associating, "
                "or the password was wrong. Give it a few seconds and "
                "ask me for the Wi-Fi info to re-check.")
    return (f"Connection requested to '{name}', but Wi-Fi info shows "
            "no network yet — it may still be associating. Give it a "
            "few seconds and ask me for the Wi-Fi info to confirm.")


def connect_wifi(ssid: str, password: str = "", confirm: bool = False) -> str:
    """Join a Wi-Fi network, creating a profile from the password if given."""
    if not confirm:
        return (f"Connecting to '{ssid}' changes this PC's network and needs "
                "confirm=true. Ask again with confirmation.")
    name = (ssid or "").strip()
    if not name:
        return "Connecting needs the network name (ssid)."
    if not _is_windows():
        # F-F501: no profile was created and nothing was changed,
        # so the verdict is failed, not a plain string the receipts
        # would read "succeeded".
        from . import tool_outcome as _outcome  # R1b: seam wiring
        return _outcome.failed(
            f"Joining a Wi-Fi network with a new profile uses netsh — {_NOT_WINDOWS}",
            "netsh unavailable off Windows")
    password = password or ""
    xml = _build_wifi_profile_xml(name, password)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".xml", prefix="nova_wifi_", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(xml)
            tmp_path = Path(fh.name)
        try:
            rc, out = _run(["netsh", "wlan", "add", "profile",
                            f"filename={tmp_path}", "user=current"], timeout=20)
        except Exception as e:  # noqa: BLE001
            return f"Could not save the Wi-Fi profile for '{name}': {e}"
        if rc != 0:
            detail = out.replace(password, "•••") if password else out
            return f"Windows refused the profile for '{name}': {detail or 'netsh failed'}"
        try:
            rc, out = _run(["netsh", "wlan", "connect", f"name={name}"], timeout=20)
        except Exception as e:  # noqa: BLE001
            return (f"The profile for '{name}' was saved, but starting the "
                    f"connection failed: {e}")
        if rc != 0:
            detail = out.replace(password, "•••") if password else out
            return (f"The profile for '{name}' was saved, but connecting failed: "
                    f"{detail or 'netsh failed'}. The password may be wrong.")
        kind = "open network" if not password else "secured network"
        verdict = _verify_wifi_connection(name)
        return f"{verdict} ('{name}' is a {kind}.)"
    finally:
        # The temp profile contains the password — it never survives the call.
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def forget_wifi(ssid: str, confirm: bool = False) -> str:
    """Delete a saved Wi-Fi profile so the PC stops auto-joining it."""
    if not confirm:
        return (f"Forgetting '{ssid}' removes its saved profile from this PC and "
                "needs confirm=true. Ask again with confirmation.")
    name = (ssid or "").strip()
    if not name:
        return "Forgetting needs the network name (ssid)."
    if not _is_windows():
        return f"Deleting a Wi-Fi profile uses netsh — {_NOT_WINDOWS}"
    try:
        rc, out = _run(["netsh", "wlan", "delete", "profile", f"name={name}"], timeout=15)
    except Exception as e:  # noqa: BLE001
        return f"Could not forget '{name}': {e}"
    if rc != 0:
        return (f"Could not forget '{name}': {out or 'netsh failed'}. "
                "The profile name must match exactly — check the Wi-Fi radar / saved profiles.")
    # R1b: read the profile list back — netsh accepting the delete
    # is not proof the profile is gone.
    from . import tool_outcome as _outcome  # R1b: seam wiring

    listed, evidence = _outcome.wifi_profile_exists(name)
    if listed is False:
        return _outcome.verified(
            "Forgot '%s' — this PC will no longer auto-join it." % name,
            evidence)
    if listed is True:
        return _outcome.failed(
            "netsh accepted the delete for '%s', but the profile is "
            "still listed — the PC may still auto-join it." % name,
            evidence)
    return _outcome.unverified(
        "Delete requested for '%s' (netsh accepted it), but the "
        "profile list could not be read back to confirm it is "
        "gone." % name,
        evidence)


def public_ip() -> str:
    """Report this connection's public (internet-facing) IP address."""
    try:
        req = urllib.request.Request("https://api.ipify.org",
                                     headers={"User-Agent": "Nova"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            ip = resp.read(64).decode("ascii", "replace").strip()
    except Exception as e:  # noqa: BLE001
        return (f"Could not look up the public IP address: {e}. "
                "The PC may be offline, or the lookup service is unreachable.")
    if not re.fullmatch(r"[0-9a-fA-F.:]{3,45}", ip):
        return "The IP lookup service replied with something unexpected, so no address to report."
    result = f"Your public IP address is {ip}."
    # #37: an address alone is half an answer — compose the
    # geolocation summary (city / provider) when it works;
    # its failure never sinks the address itself.
    try:
        from .skills_web_research import ip_geolocate

        geo = ip_geolocate(ip)
        if " is in " in geo:
            result += "\n" + geo
    except Exception:  # noqa: BLE001
        pass
    return result


def _upload_speed() -> str:
    """POST ~1 MB to Cloudflare's upload endpoint, timed.
    Returns a sentence — or an honest failure sentence;
    never raises."""
    payload = b"\x00" * 1_000_000
    started = time.monotonic()
    try:
        req = urllib.request.Request(
            "https://speed.cloudflare.com/__up", data=payload,
            headers={"User-Agent": "Nova"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except Exception as e:  # noqa: BLE001
        return f" The upload part couldn't be measured ({e})."
    elapsed = max(time.monotonic() - started, 0.001)
    bps = len(payload) / elapsed
    return (f" Upload speed: {bps / 1_000_000:.1f} MB/s "
            f"({bps * 8 / 1_000_000:.0f} Mbps), measured over "
            f"{len(payload) / 1_000_000:.1f} MB in {elapsed:.1f} s.")


def _speed_finish(total: int, elapsed: float, partial: bool,
                  ttfb, extra: str = "") -> str:
    """The download report plus latency and upload (#17)."""
    report = _speed_report(total, elapsed, partial) + extra
    if ttfb is not None:
        report += (f" Latency: about {ttfb * 1000:.0f} ms "
                   "(time to first byte).")
    return report + _upload_speed()


def speed_test() -> str:
    """Measure download AND upload speed plus latency (time
    to first byte) against Cloudflare's public speed
    endpoints (#17)."""
    url = "https://speed.cloudflare.com/__down?bytes=10000000"
    total = 0
    ttfb = None
    start = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Nova"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                if ttfb is None:
                    ttfb = time.monotonic() - start
                total += len(chunk)
                if time.monotonic() - start > 20:
                    elapsed = time.monotonic() - start
                    if total >= 1_000_000:
                        return _speed_finish(total, elapsed, True, ttfb)
                    return ("The speed test was too slow to measure (under 1 MB "
                            "in 20 seconds) — the connection may be very slow or stalled.")
    except Exception as e:  # noqa: BLE001
        elapsed = time.monotonic() - start
        if total >= 1_000_000 and elapsed > 0:
            return _speed_finish(total, elapsed, True, ttfb,
                                 f" (download stopped early: {e})")
        return (f"The speed test could not run: {e}. "
                "The PC may be offline, or the test service is unreachable.")
    return _speed_finish(total, time.monotonic() - start, False, ttfb)


def _speed_report(total: int, elapsed: float, partial: bool) -> str:
    if elapsed <= 0:
        elapsed = 0.001
    bps = total / elapsed
    mbps = bps * 8 / 1_000_000
    note = " (partial — stopped at the 20-second cap)" if partial else ""
    return (f"Download speed: {bps / 1_000_000:.1f} MB/s ({mbps:.0f} Mbps), "
            f"measured over {total / 1_000_000:.1f} MB in {elapsed:.1f} s{note}.")


_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


def _valid_host(host: str) -> bool:
    h = (host or "").strip()
    return bool(h) and len(h) <= 253 and bool(_HOST_RE.match(h)) and ".." not in h


def ping_host(host: str, count: int = 4) -> str:
    """Ping a host and report average latency and packet loss."""
    target = (host or "").strip()
    if not _valid_host(target):
        return (f"'{host}' doesn't look like a valid host name or IP address "
                "(letters, digits, dots and dashes only).")
    try:
        n = max(1, min(int(count), 10))
    except (TypeError, ValueError):
        n = 4
    if _is_windows():
        argv = ["ping", "-n", str(n), "-w", "2000", target]
    else:
        argv = ["ping", "-c", str(n), "-W", "2", target]
    try:
        rc, out = _run(argv, timeout=5 * n + 10)
    except Exception as e:  # noqa: BLE001
        return f"Ping could not run: {e}"
    avg = None
    low = high = None
    m = re.search(r"Average\s*=\s*(\d+(?:\.\d+)?)\s*ms", out)
    if m:
        avg = float(m.group(1))
        lo = re.search(r"Minimum\s*=\s*(\d+(?:\.\d+)?)\s*ms", out)
        hi = re.search(r"Maximum\s*=\s*(\d+(?:\.\d+)?)\s*ms", out)
        if lo:
            low = float(lo.group(1))
        if hi:
            high = float(hi.group(1))
    else:
        # Linux: 'rtt min/avg/max/mdev = 1.0/2.0/3.0/0.4 ms' — the
        # summary the command already printed carries all three.
        m = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)", out)
        if m:
            low, avg, high = (float(m.group(1)), float(m.group(2)),
                              float(m.group(3)))
    loss = None
    m = re.search(r"\((\d+(?:\.\d+)?)%\s*loss\)", out)
    if m:
        loss = float(m.group(1))
    else:
        m = re.search(r"(\d+(?:\.\d+)?)%\s*packet loss", out)
        if m:
            loss = float(m.group(1))
    if avg is None and loss is None:
        if rc != 0 or "unreachable" in out.lower() or "could not find" in out.lower():
            tail = out.splitlines()[-1][:160] if out else "no reply"
            return f"No reply from {target} — the host may be down or blocking ping. ({tail})"
        tail = "\n".join(out.splitlines()[-3:])[:300]
        return f"Pinged {target}, but the summary couldn't be parsed. Raw tail:\n{tail}"
    bits = []
    if avg is not None:
        spread = ""
        if low is not None and high is not None:
            spread = f" (fastest {low:.0f} ms, slowest {high:.0f} ms)"
        bits.append(f"average {avg:.0f} ms{spread} over {n} ping(s)")
    if loss is not None:
        bits.append(f"{loss:.0f}% packet loss")
    return f"{target}: " + ", ".join(bits) + "."


def dns_lookup(host: str, reverse: bool = False) -> str:
    """Resolve a host name to its IP addresses; with
    reverse=True, resolve an IP address back to a host name."""
    target = (host or "").strip()
    if reverse:
        import ipaddress
        try:
            ipaddress.ip_address(target)
        except ValueError:
            return (f"'{host}' isn't an IP address, so there's nothing to "
                    "reverse-lookup. Give me an IPv4 or IPv6 address, or "
                    "drop reverse=true to resolve a host name instead.")
        try:
            name, aliases, _addrs = socket.gethostbyaddr(target)
        except Exception as e:  # noqa: BLE001
            return (f"No host name is on record for {target}: {e}")
        lines = [f"{target} belongs to {name}."]
        if aliases:
            lines.append("Also known as: " + ", ".join(aliases[:6]) + ".")
        return "\n".join(lines)
    if not _valid_host(target):
        return (f"'{host}' doesn't look like a valid host name "
                "(letters, digits, dots and dashes only).")
    try:
        infos = socket.getaddrinfo(target, None)
    except Exception as e:  # noqa: BLE001
        return f"Could not resolve '{target}': {e}"
    v4, v6 = [], []
    for info in infos:
        ip = info[4][0]
        (v6 if ":" in ip else v4).append(ip)
    v4, v6 = sorted(set(v4)), sorted(set(v6))
    if not v4 and not v6:
        return f"'{target}' resolved to no addresses."
    lines = [f"{target} resolves to:"]
    lines += [f"• {ip} (IPv4)" for ip in v4[:8]]
    if len(v4) > 8:
        lines.append(f"… and {len(v4) - 8} more IPv4 address(es) not shown "
                     f"(showing 8 of {len(v4)}).")
    lines += [f"• {ip} (IPv6)" for ip in v6[:8]]
    if len(v6) > 8:
        lines.append(f"… and {len(v6) - 8} more IPv6 address(es) not shown "
                     f"(showing 8 of {len(v6)}).")
    return "\n".join(lines)


def dns_flush() -> str:
    """Flush the DNS resolver cache. (Deliberately NOT confirm-gated:
    flushing a cache changes no settings and breaks nothing.)"""
    if not _is_windows():
        return (f"Flushing the DNS cache this way uses ipconfig — {_NOT_WINDOWS} "
                "(On Linux it depends on the resolver service.)")
    try:
        rc, out = _run(["ipconfig", "/flushdns"], timeout=15)
    except Exception as e:  # noqa: BLE001
        return f"Could not flush the DNS cache: {e}"
    if rc != 0:
        return f"Could not flush the DNS cache: {out or 'ipconfig failed'}"
    # Verify-after-act (#38): the flush counts as done only
    # when ipconfig's own success line is in its output.
    if "successfully flushed" in (out or "").lower():
        return ("DNS cache flushed — verified by ipconfig's "
                "confirmation ('Successfully flushed the DNS "
                "Resolver Cache'), so name lookups will be "
                "re-fetched fresh.")
    return ("ipconfig exited cleanly but didn't show its usual "
            "'Successfully flushed' confirmation, so the flush "
            "is unconfirmed — lookups may still use the cache.")


def ip_renew(confirm: bool = False) -> str:
    """Release and renew this PC's IP address (connection drops briefly)."""
    if not confirm:
        return ("Renewing the IP address drops the connection for a few seconds "
                "and needs confirm=true. Ask again with confirmation.")
    if not _is_windows():
        return f"Releasing/renewing an IP this way uses ipconfig — {_NOT_WINDOWS}"
    try:
        rel_rc, rel_out = _run(["ipconfig", "/release"], timeout=30)
        rc, out = _run(["ipconfig", "/renew"], timeout=60)
    except Exception as e:  # noqa: BLE001
        return f"Could not renew the IP address: {e}"
    if rc != 0:
        release_note = ""
        if rel_rc != 0:
            release_note = (f" The release step also reported trouble: "
                            f"{rel_out or 'ipconfig failed'}.")
        return (f"Renewing the IP address ran into trouble: {out or 'ipconfig failed'}. "
                f"If the connection didn't come back, restart the Wi-Fi adapter.{release_note}")
    m = re.search(r"IPv4 Address[. ]*:\s*([0-9.]+)", out)
    if not m:
        # Renew exited cleanly but reported no address (audit m12):
        # never claim a renewal VYUK can't point at.
        return ("The renew completed, but Windows reported no new IPv4 "
                "address — the adapter may still be reconnecting. Check "
                "the connection in a few seconds; if it doesn't come "
                "back, restart the Wi-Fi adapter.")
    release_note = ""
    if rel_rc != 0:
        release_note = (" (The release step reported an error, but the "
                        "renew itself succeeded.)")
    return f"IP address released and renewed. New address: {m.group(1)}.{release_note}"


def bluetooth_devices() -> str:
    """List Bluetooth devices Windows knows about, with their status."""
    if not _is_windows():
        return f"Listing Bluetooth devices uses Windows device APIs — {_NOT_WINDOWS}"
    command = ("Get-PnpDevice -Class Bluetooth | "
               "Select-Object Status, FriendlyName | Format-Table -AutoSize | Out-String -Width 200")
    try:
        rc, out = _run(["powershell", "-NoProfile", "-Command", command], timeout=30)
    except Exception as e:  # noqa: BLE001
        return f"Could not list Bluetooth devices: {e} (PowerShell may be unavailable.)"
    if rc != 0 or not out:
        return (f"Could not list Bluetooth devices: {out or 'no results'}. "
                "Bluetooth may be off, absent, or blocked by policy.")
    devices = []
    for line in out.splitlines():
        m = re.match(r"\s*(OK|Error|Unknown|Degraded)\s+(.+)$", line)
        if m and "Status" not in line:
            devices.append((m.group(2).strip(), m.group(1)))
    if not devices:
        return "No Bluetooth devices were found (Bluetooth may be off or no devices are paired)."
    lines = [f"Bluetooth devices ({len(devices)}):"]
    for name, status in devices[:25]:
        lines.append(f"• {name} — {status}")
    if len(devices) > 25:
        lines.append(f"… and {len(devices) - 25} more device(s) not shown "
                     f"(showing 25 of {len(devices)}).")
    return "\n".join(lines)
