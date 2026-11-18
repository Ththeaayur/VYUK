"""
Network skills III for VYUK (v5.11, Pack M) — two tools the earlier
network packs didn't cover: Wake-on-LAN (send a magic packet that can
power a sleeping PC on) and an inventory of the folders THIS PC
shares with the network.

Same rules as the other packs: plain functions returning
human-readable strings; platform dependencies reached lazily and
guarded, so this module imports cleanly on any OS and a missing
piece degrades to an honest line instead of a crash. Structured
PowerShell output (fields joined by the unit separator) — localised
labels are never matched. Caps are disclosed in the result text.

Honesty note for wake_on_lan: sending a magic packet is all any
tool can do — whether the target machine wakes depends on ITS BIOS
and adapter settings, and there is no acknowledgement to listen for.
The result says exactly that instead of implying the PC is awake.

Wiring: the parent (core/tools_handlers_pack_hands.py, _register_hands4_tools) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import re
import socket
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _run(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """Run a command, capturing output. Returns (returncode, stdout, stderr);
    returncode is -1 when the command could not be started at all and -2 on
    timeout, with the reason in stderr. Never raises, never uses a shell.
    stdin is /dev/null so a tool that wants console input fails visibly
    instead of hanging."""
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "stdin": subprocess.DEVNULL,
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
    return "\n".join(lines[-4:])[:limit]


_SEP = "\x1f"  # unit separator: fields in PowerShell output ride on this,
               # never on localised labels or column spacing (v4.7 rule).


def _ps_structured(command: str, timeout: int = 60) -> tuple[int, list[list[str]], str]:
    """Run a PowerShell command whose output lines are fields joined by
    _SEP, and return (returncode, parsed rows, stderr). Parsing is purely
    structural — no localised header or label is ever matched."""
    rc, out, err = _run(["powershell", "-NoProfile", "-Command", command],
                        timeout=timeout)
    rows: list[list[str]] = []
    for line in (out or "").splitlines():
        if line.strip():
            rows.append(line.split(_SEP))
    return rc, rows, err


# ---------------------------------------------------------------------------
# WAKE-ON-LAN — a magic packet that can power on a sleeping PC
# ---------------------------------------------------------------------------

_MAC_FORMATS = (
    "AA:BB:CC:DD:EE:FF (colons), AA-BB-CC-DD-EE-FF (dashes), "
    "or AABBCCDDEEFF (12 hex digits)"
)


def _parse_mac(mac) -> bytes | None:
    """Parse a MAC address into 6 bytes, or None when the text isn't one.

    Accepts colon-separated, dash-separated, or bare 12-hex-digit
    forms, in any case. Anything else returns None so the handler can
    answer with format guidance instead of sending a junk packet."""
    text = (mac or "").strip()
    if re.fullmatch(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}", text):
        return bytes(int(part, 16) for part in text.split(":"))
    if re.fullmatch(r"[0-9A-Fa-f]{2}(-[0-9A-Fa-f]{2}){5}", text):
        return bytes(int(part, 16) for part in text.split("-"))
    if re.fullmatch(r"[0-9A-Fa-f]{12}", text):
        return bytes(int(text[i:i + 2], 16) for i in range(0, 12, 2))
    return None


def _format_mac(mac_bytes: bytes) -> str:
    """The canonical display form: uppercase, colon-separated."""
    return ":".join(f"{b:02X}" for b in mac_bytes)


def _build_magic_packet(mac_bytes: bytes) -> bytes:
    """The Wake-on-LAN magic packet: 6 bytes of 0xFF followed by the
    target's MAC address repeated 16 times — 102 bytes in total."""
    return b"\xff" * 6 + bytes(mac_bytes) * 16


def _send_packet(packet: bytes, address: str, port: int) -> None:
    """Broadcast one UDP packet. Raises on socket failure (the handler
    words it); factored out so tests can aim it at a loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (address, port))


def wake_on_lan(mac: str, broadcast: str = "255.255.255.255",
                port: int = 9, repeat: int = 1) -> str:
    """Send a Wake-on-LAN magic packet to power on a sleeping PC.

    `repeat` sends the packet that many times (1–5), a tenth of a
    second apart — a single UDP broadcast can be dropped on a busy
    or sleepy network, and the packet costs nothing to resend."""
    mac_bytes = _parse_mac(mac)
    if mac_bytes is None:
        return (f"'{mac}' doesn't look like a MAC address. Use one of: "
                f"{_MAC_FORMATS}.")
    try:
        port_num = int(port)
    except (TypeError, ValueError):
        return f"The port must be a number between 1 and 65535 (got '{port}')."
    if not 1 <= port_num <= 65535:
        return f"The port must be between 1 and 65535 (got {port_num})."
    try:
        times = int(repeat)
    except (TypeError, ValueError):
        return f"repeat must be a whole number between 1 and 5 (got '{repeat}')."
    if not 1 <= times <= 5:
        return f"repeat must be between 1 and 5 (got {times})."
    address = (broadcast or "255.255.255.255").strip() or "255.255.255.255"
    packet = _build_magic_packet(mac_bytes)
    try:
        for attempt in range(times):
            _send_packet(packet, address, port_num)
            if attempt + 1 < times:
                time.sleep(0.1)
    except Exception as e:  # noqa: BLE001
        return (f"Couldn't send the Wake-on-LAN packet to "
                f"{_format_mac(mac_bytes)}: {e}")
    sent = (f"Magic packet sent to {_format_mac(mac_bytes)}"
            if times == 1 else
            f"Magic packet sent {times} times to {_format_mac(mac_bytes)}")
    return (
        f"{sent} "
        f"(broadcast {address}, port {port_num}).\n"
        "That only sends the wake-up signal: the other PC must have "
        "Wake-on-LAN enabled in its BIOS and network adapter settings "
        "for it to work, and there's no acknowledgement to listen "
        "for — so I can't confirm from here whether it actually woke "
        "up. Give it a few seconds and try pinging or connecting to "
        "it."
    )


# ---------------------------------------------------------------------------
# NETWORK SHARES — the folders THIS PC offers to the network (read-only)
# ---------------------------------------------------------------------------

_SHARES_PS = (
    "Get-SmbShare | ForEach-Object { $_.Name + [char]0x1f + $_.Path + "
    "[char]0x1f + $(if ($_.Description) { $_.Description } else { '' }) }"
)

_MAX_SHARES_SHOWN = 40


def _share_line(name: str, path: str, description: str) -> str:
    """One share as a display line; $-suffixed names are the shares
    Windows creates itself, marked as such."""
    default = " (Windows default share)" if name.endswith("$") else ""
    desc = f" — {description}" if description else ""
    where = f" → {path}" if path else ""
    return f"• {name}{default}{where}{desc}"


def _parse_net_share(text: str) -> list[list[str]]:
    """Parse `net share` output into [name, path, description] rows.

    Only used when the structured cmdlet is unavailable. net share
    pads its columns with runs of spaces, so rows are split on 2+
    spaces; anything that doesn't fit that shape is skipped rather
    than guessed at."""
    rows: list[list[str]] = []
    lines = (text or "").splitlines()
    in_body = False
    for line in lines:
        stripped = line.strip()
        if not in_body:
            if stripped and set(stripped) <= {"-", " "}:
                in_body = True
            continue
        if not stripped:
            continue
        parts = re.split(r"\s{2,}", stripped)
        if len(parts) >= 2:
            rows.append([parts[0], parts[1],
                         parts[2] if len(parts) > 2 else ""])
    return rows


def _format_shares(rows: list[list[str]]) -> str:
    lines = [_share_line(*(row + ["", "", ""])[:3]) for row in rows]
    shown = lines[:_MAX_SHARES_SHOWN]
    text = f"Folders this PC shares ({len(rows)}):\n" + "\n".join(shown)
    if len(rows) > _MAX_SHARES_SHOWN:
        text += (f"\n… and {len(rows) - _MAX_SHARES_SHOWN} more "
                 f"(showing the first {_MAX_SHARES_SHOWN}).")
    return text


def list_network_shares() -> str:
    """List the folders this PC shares with the local network."""
    if not _is_windows():
        return ("Listing shared folders uses Windows sharing tools "
                "(Get-SmbShare) — this system is not Windows.")
    rc, rows, _err = _ps_structured(_SHARES_PS, timeout=60)
    if rc != 0 or not rows:
        # The SMB cmdlets may be missing (older/leaner Windows):
        # fall back to net share, whose output is parsed structurally.
        nrc, nout, nerr = _run(["net", "share"], timeout=30)
        fallback_rows = _parse_net_share(nout) if nrc == 0 else []
        if fallback_rows:
            return _format_shares(fallback_rows)
        if rc == 0 and not rows:
            return ("This PC isn't sharing any folders — "
                    "Get-SmbShare reported no shares.")
        detail = _tail(nerr) or _tail(_err)
        return ("Couldn't list the shared folders."
                + (f"\n{detail}" if detail else ""))
    return _format_shares(rows)


# ---------------------------------------------------------------------------
# Registration — called by core/tools_handlers_pack_hands.py (_register_hands4_tools; Phase 5 docs-only rename, wiring unchanged)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Network III pack tool on a ToolRegistry-like object."""
    registry.register(
        name="wake_on_lan",
        description="Send a Wake-on-LAN magic packet to power on a sleeping or shut-down PC over the local network, given its network adapter's MAC address; repeat (1-5) resends the packet for reliability. The target must have Wake-on-LAN enabled in its BIOS and adapter settings — the packet is fire-and-forget, so the result can't confirm the machine actually woke. Use when the user wants to wake or start another PC remotely.",
        parameters={
            "type": "object",
            "properties": {
                "mac": {"type": "string", "description": "The target PC's MAC address, e.g. 'AA:BB:CC:DD:EE:FF' (dashes or bare hex also accepted)"},
                "broadcast": {"type": "string", "description": "Broadcast address to send to (default '255.255.255.255'; a subnet broadcast like '192.168.1.255' can work better on some networks)"},
                "port": {"type": "integer", "description": "UDP port to send to (default 9; 7 is the other common Wake-on-LAN port)"},
                "repeat": {"type": "integer", "description": "Send the packet this many times, 1-5 (default 1); repeats help on busy networks, since one broadcast can be dropped"},
            },
            "required": ["mac"],
        },
        handler=wake_on_lan,
    )
    registry.register(
        name="list_network_shares",
        description="List the folders THIS PC shares with the local network (share name, folder path, description), including the default Windows shares (marked). Use when the user asks what they're sharing, or checks whether a folder is exposed on the network. Read-only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=list_network_shares,
    )
