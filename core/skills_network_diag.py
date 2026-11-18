"""
Network skills II for VYUK (v5.5, Pack E) — the read-only diagnostics
pack: live connection inventory, route tracing, saved Wi-Fi profiles,
adapter inventory and single-port reachability checks.

Same rules as the other packs: plain functions returning
human-readable strings; every platform dependency (psutil, tracert /
traceroute, netsh) is reached lazily and guarded, so this module
imports cleanly on any OS and a missing tool degrades to an honest
line instead of a crash. Nothing here changes the machine — every
tool is a look, not a touch — and every cap (rows shown, hops traced)
is disclosed in the result text.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import re
import shutil
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


def _clamp_int(value, lo: int, hi: int, default: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, num))


def _import_psutil():
    """The guarded psutil import both psutil tools share; None when absent."""
    try:
        import psutil

        return psutil
    except Exception:  # noqa: BLE001
        return None


_NO_PSUTIL = ("Couldn't do that — the psutil package isn't available "
              "in this VYUK install.")

# ---------------------------------------------------------------------------
# CONNECTIONS — who is this PC talking to right now (read-only)
# ---------------------------------------------------------------------------


def _endpoint(addr) -> str:
    """An (ip, port) endpoint as text; IPv6 addresses get brackets."""
    if not addr:
        return ""
    ip = getattr(addr, "ip", "")
    port = getattr(addr, "port", "")
    if ":" in str(ip):
        return f"[{ip}]:{port}"
    return f"{ip}:{port}"


def list_connections(state: str = "established", count: int = 15,
                     process: str = "") -> str:
    """Live TCP/UDP connections with the owning process, heaviest truth first."""
    mode = (state or "established").strip().lower()
    if mode not in ("established", "listening", "all"):
        return (f"Unknown state filter: '{state}'. Use 'established', "
                "'listening' or 'all'.")
    n = _clamp_int(count, 1, 40, 15)
    needle = (process or "").strip().lower()
    psutil = _import_psutil()
    if psutil is None:
        return _NO_PSUTIL
    try:
        conns = psutil.net_connections(kind="inet")
    except Exception as e:  # noqa: BLE001 — AccessDenied on locked-down systems
        return (f"Couldn't read the connection table: {e}. (On some "
                "systems this needs VYUK to run with more privileges.)")

    wanted = {
        "established": {psutil.CONN_ESTABLISHED},
        "listening": {psutil.CONN_LISTEN},
        "all": None,
    }[mode]
    rows_all = [c for c in conns if wanted is None or c.status in wanted]
    # Deterministic order: process, then local port — the table itself
    # comes back in kernel order, which jumps around between calls.
    names: dict[int | None, str] = {}

    def proc_label(pid) -> str:
        if pid is None:
            return "unknown process"
        if pid not in names:
            try:
                names[pid] = psutil.Process(pid).name()
            except Exception:  # noqa: BLE001 — gone, or not ours to see
                names[pid] = f"pid {pid}"
        return names[pid]

    def row(c) -> str:
        label = proc_label(c.pid)
        local = _endpoint(c.laddr)
        remote = _endpoint(c.raddr)
        if c.status == psutil.CONN_LISTEN:
            return f"{local} — {label}"
        if remote:
            return f"{label} → {remote} ({c.status})"
        return f"{local} — {label} ({c.status})"

    rows_all.sort(key=lambda c: (proc_label(c.pid).lower(),
                                 getattr(c.laddr, "port", 0) or 0))
    unfiltered_count = len(rows_all)
    if needle:
        rows_all = [c for c in rows_all
                    if needle in proc_label(c.pid).lower()]
    if not rows_all:
        if needle:
            return (f"No {mode} connections from a process matching "
                    f"'{process}' right now "
                    f"({unfiltered_count} without the filter).")
        return {
            "established": "No established connections right now.",
            "listening": "Nothing is listening for connections right now.",
            "all": "The connection table is empty right now.",
        }[mode]
    shown = rows_all[:n]
    heading = {
        "established": "Established connections",
        "listening": "Listening sockets",
        "all": "All connections",
    }[mode]
    if needle:
        heading += f" from processes matching '{process}'"
    lines = [f"{heading} (showing {len(shown)} of {len(rows_all)}):"]
    lines += [f"• {row(c)}" for c in shown]
    if len(rows_all) > len(shown):
        lines.append(f"…and {len(rows_all) - len(shown)} more "
                     "(count goes up to 40).")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# TRACE ROUTE — the path packets take to a host (read-only)
# ---------------------------------------------------------------------------

_MS_TOKEN = re.compile(r"<\s*\d+\s*ms|\d+(?:\.\d+)?\s*ms")
_HOP_LINE = re.compile(r"^\s*(\d+)\s+(.+)$")


def _parse_tracert_output(text: str) -> list[str]:
    """Parse tracert (Windows) or traceroute (Linux) output into hop rows.

    Pure function over canned text so the suite can pin the format.
    Both tools print one line per hop: a hop number, up to three
    probe times (or *), and the address that answered. Headers
    ('Tracing route to…', 'traceroute to…') and footers ('Trace
    complete.') carry no leading hop number followed by timings, so
    they fall out naturally. Returns rows like '1. 192.168.1.1
    (1 ms, 1 ms, 1 ms)' or '3. * * * (no reply)'."""
    rows: list[str] = []
    for line in (text or "").splitlines():
        m = _HOP_LINE.match(line)
        if not m:
            continue
        hop, rest = m.group(1), m.group(2)
        if "timed out" in rest.lower():
            rows.append(f"{hop}. * * * (no reply — request timed out)")
            continue
        times = _MS_TOKEN.findall(rest)
        if not times and "*" not in rest:
            continue  # a numbered line with no probes isn't a hop row
        # The address is whatever token isn't a timing, an asterisk or
        # part of 'Request timed out.' (handled above). tracert puts
        # it last, traceroute first — take the last candidate, which
        # is the only one traceroute ever prints before its timings.
        scrubbed = _MS_TOKEN.sub(" ", rest)
        candidates = [tok for tok in scrubbed.split()
                      if tok not in ("*", "ms")]
        if not candidates:
            rows.append(f"{hop}. * * * (no reply)")
            continue
        pretty = [" ".join(t.split()) for t in times]
        suffix = f" ({', '.join(pretty)})" if pretty else ""
        rows.append(f"{hop}. {candidates[-1]}{suffix}")
    return rows


def trace_route(host: str, max_hops: int = 15) -> str:
    """Trace the route to a host, hop by hop."""
    target = (host or "").strip()
    if not target:
        return "Give me a host to trace to — e.g. trace_route('8.8.8.8')."
    if target.startswith("-") or any(ch.isspace() for ch in target):
        return (f"'{host}' doesn't look like a host name or IP address "
                "(no spaces, and it can't start with '-').")
    hops = _clamp_int(max_hops, 1, 30, 15)
    timeout = min(60, max(10, hops * 3))
    if _is_windows():
        argv = ["tracert", "-d", "-h", str(hops), "-w", "800", target]
    elif shutil.which("traceroute"):
        argv = ["traceroute", "-n", "-m", str(hops), "-w", "2", target]
    else:
        return (f"Couldn't trace the route to {target} — neither "
                "tracert (Windows) nor traceroute is available on "
                "this system.")
    rc, out, err = _run(argv, timeout=timeout)
    if rc == -2:
        return (f"The trace to {target} took longer than {timeout} "
                "seconds and was stopped. Routes often stall when a "
                "hop doesn't answer — try fewer hops.")
    if rc == -1:
        return f"Couldn't trace the route to {target} — {err}"
    rows = _parse_tracert_output(out)
    if not rows:
        detail = _tail(out) or _tail(err)
        return (f"Couldn't trace the route to {target}."
                + (f" The tool said:\n{detail}" if detail else ""))
    lines = [f"Route to {target} ({len(rows)} hops answered, "
             f"max {hops}):"]
    lines += [f"• {r}" for r in rows]
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# SAVED WI-FI — networks this PC remembers (read-only)
# ---------------------------------------------------------------------------


def _parse_netsh_profiles(text: str) -> list[str]:
    """Extract saved Wi-Fi profile names from `netsh wlan show profiles`.

    Shape-based, not label-based: a profile line is an indented
    'Some Label : NetworkName' pair whose label ENDS in the word
    'Profile' (All User Profile / Current User Profile / Group
    Policy Profile). The name is everything after the FIRST colon,
    so names containing ':' survive. Section headers ('User
    profiles') and the 'Profiles on interface X:' line (empty value)
    fall out on their own. Duplicates are dropped, order preserved."""
    names: list[str] = []
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        label = label.strip()
        value = value.strip()
        if not value or not label.lower().endswith("profile"):
            continue
        if value not in names:
            names.append(value)
    return names


def saved_wifi_networks() -> str:
    """Wi-Fi networks this PC has saved profiles for."""
    if not _is_windows():
        return ("Listing saved Wi-Fi networks uses netsh — this "
                "system is not Windows, so there is nothing to read.")
    rc, out, err = _run(["netsh", "wlan", "show", "profiles"], timeout=20)
    if rc == -1:
        return ("Couldn't list saved Wi-Fi networks — netsh isn't "
                f"available ({err}).")
    if rc != 0:
        detail = _tail(out) or _tail(err)
        return ("netsh couldn't list the saved Wi-Fi networks — the "
                "Wi-Fi service may be off."
                + (f" It said:\n{detail}" if detail else ""))
    names = _parse_netsh_profiles(out)
    if not names:
        return ("No saved Wi-Fi networks found — this PC has no "
                "stored Wi-Fi profiles (or the Wi-Fi service is off).")
    lines = [f"Saved Wi-Fi networks ({len(names)}) — networks this "
             "PC has connected to before:"]
    lines += [f"• {n}" for n in names]
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# ADAPTERS — the network hardware and its addresses (read-only)
# ---------------------------------------------------------------------------


def network_adapters(show_ipv6: bool = False) -> str:
    """Every network adapter: state, link speed, MAC and IPv4 address
    (plus the IPv6 address when show_ipv6 asks for it)."""
    if isinstance(show_ipv6, str):
        want_v6 = show_ipv6.strip().lower() in ("true", "1", "yes")
    else:
        want_v6 = bool(show_ipv6)
    psutil = _import_psutil()
    if psutil is None:
        return _NO_PSUTIL
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the network adapters: {e}"
    rows: list[str] = []
    for name in sorted(addrs):
        entries = addrs[name]
        # Family constants come from the socket module, not psutil
        # (psutil 7 exposes no AF_* of its own); the link-layer entry
        # is simply the one that is neither IPv4 nor IPv6.
        ipv4s = [a.address for a in entries
                 if a.family == socket.AF_INET and a.address]
        ipv6s = [a.address for a in entries
                 if a.family == socket.AF_INET6 and a.address]
        macs = [a.address for a in entries
                if a.family not in (socket.AF_INET, socket.AF_INET6)
                and a.address and ":" in a.address]
        is_loopback = name.lower() in ("lo", "loopback",
                                       "loopback pseudo-interface 1")
        rows.append((name, ipv4s, macs, is_loopback, ipv6s))
    visible = [r for r in rows if not r[3]] or rows
    if not visible:
        return "No network adapters were found on this PC."
    lines = [f"Network adapters ({len(visible)}):"]
    for name, ipv4s, macs, _lo, ipv6s in visible:
        st = stats.get(name)
        state = "up" if (st is not None and st.isup) else "down"
        parts = [f"{name} — {state}"]
        if st is not None and st.isup:
            parts.append(f"{st.speed} Mbps" if st.speed
                         else "speed unknown")
        if macs:
            parts.append(f"MAC {macs[0]}")
        if ipv4s:
            extra = f" (+{len(ipv4s) - 1} more)" if len(ipv4s) > 1 else ""
            parts.append(f"IPv4 {ipv4s[0]}{extra}")
        if want_v6 and ipv6s:
            extra6 = (f" (+{len(ipv6s) - 1} more)"
                      if len(ipv6s) > 1 else "")
            parts.append(f"IPv6 {ipv6s[0]}{extra6}")
        lines.append("• " + ", ".join(parts))
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# PORT CHECK — is one specific door open (read-only)
# ---------------------------------------------------------------------------


def check_port(host: str, port: int) -> str:
    """Try one TCP connection to host:port and report honestly."""
    target = (host or "").strip()
    if not target:
        return "Give me a host to check — e.g. check_port('8.8.8.8', 53)."
    try:
        port_num = int(port)
    except (TypeError, ValueError):
        return f"'{port}' isn't a port number — ports run from 1 to 65535."
    if not 1 <= port_num <= 65535:
        return (f"Port {port_num} is out of range — ports run from "
                "1 to 65535.")
    started = time.monotonic()
    try:
        with socket.create_connection((target, port_num), timeout=4):
            ms = int((time.monotonic() - started) * 1000)
            return (f"{target}:{port_num} is reachable — connected "
                    f"in {ms} ms.")
    except ConnectionRefusedError:
        return (f"{target}:{port_num} refused the connection — the "
                "host answered, but nothing is listening on that port "
                "(or a firewall rejected it).")
    except socket.gaierror:
        return (f"Couldn't resolve the host name '{target}' — check "
                "the spelling, or use an IP address.")
    except (socket.timeout, TimeoutError):
        return (f"{target}:{port_num} timed out after 4 seconds — "
                "the host didn't answer (it may be down, or a "
                "firewall is silently dropping the packets).")
    except OSError as e:
        return f"Couldn't check {target}:{port_num} — {e}."

# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (core/funnel/tools_registry.py wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Network II pack tool on a ToolRegistry-like object."""
    registry.register(
        name="list_connections",
        description="List this PC's live network connections with the process that owns each: established connections ('chrome.exe → 142.250.x.x:443'), listening sockets, or all of them. An optional process filter narrows the list to one app's connections. Use when the user asks what's connected to the internet, which app is talking to a server, or what's listening on the PC. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "state": {"type": "string",
                          "enum": ["established", "listening", "all"],
                          "description": "Which connections to show (default established)"},
                "count": {"type": "integer",
                          "description": "How many to show, 1-40 (default 15); the total is always stated"},
                "process": {"type": "string",
                            "description": "Optional: only show connections whose process name contains this text, e.g. 'chrome'"},
            },
            "required": [],
        },
        handler=list_connections,
    )
    registry.register(
        name="trace_route",
        description="Trace the network route to a host hop by hop (Windows tracert / Linux traceroute), showing each router that answers and its response times. Use when a site is slow or unreachable and the user wants to know where the path breaks. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Host name or IP address to trace to, e.g. '8.8.8.8' or 'example.com'"},
                "max_hops": {"type": "integer",
                             "description": "Maximum hops to follow, 1-30 (default 15)"},
            },
            "required": ["host"],
        },
        handler=trace_route,
    )
    registry.register(
        name="saved_wifi_networks",
        description="List the Wi-Fi networks this PC has saved profiles for — the networks it has connected to before and will rejoin automatically. Read-only. Remove one with forget_wifi; the one you're on now is wifi_info.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=saved_wifi_networks,
    )
    registry.register(
        name="network_adapters",
        description="List the PC's network adapters with their state (up/down), link speed, MAC address and IPv4 address; show_ipv6=true also names each adapter's IPv6 address. Use when the user asks about their network hardware, which adapter is in use, or needs a MAC address. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "show_ipv6": {"type": "boolean",
                              "description": "Also show each adapter's IPv6 address (default false)"},
            },
            "required": [],
        },
        handler=network_adapters,
    )
    registry.register(
        name="check_port",
        description="Check whether one TCP port on a host is reachable: tries a real connection and reports open (with the connect time), refused, timed out, or unresolvable as distinct answers. Use when the user asks if a service is up or a port is open. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Host name or IP address, e.g. '8.8.8.8'"},
                "port": {"type": "integer", "description": "TCP port, 1-65535, e.g. 443"},
            },
            "required": ["host", "port"],
        },
        handler=check_port,
    )
