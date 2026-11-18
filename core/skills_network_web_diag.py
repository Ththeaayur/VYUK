"""
Network skills IV for VYUK (v5.12, Pack Q) — the "is it really up, and
who is who" pack: website reachability with honest failure classes,
TLS certificate inspection, the local-network devices this PC has
actually talked to, and the owner of a listening port.

Same rules as the other packs: plain functions returning
human-readable strings; every dependency is stdlib (urllib, ssl,
socket, subprocess) or the guarded psutil the other network packs
use, reached lazily so this module imports cleanly anywhere. Every
tool here is a LOOK, not a touch — nothing changes the machine, the
network or the website. Caps (page bytes read, SANs shown, devices
listed) are disclosed in the result text or the tool descriptions.

Deliberately NOT here: single-port reachability is check_port,
host liveness is ping_host, name resolution is dns_lookup and
bandwidth is speed_test (all in the earlier network packs) — this
pack never duplicates them.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 VYUK/5.12")
_BODY_CAP = 64 * 1024          # never download whole pages
_FETCH_TIMEOUT = 10            # seconds, per the tool descriptions
_MAX_DEVICES = 64              # ARP rows shown before the cap is stated
_MAX_SANS = 10                 # certificate names shown before the cap

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _run(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """Run a command, capturing output. Returns (returncode, stdout, stderr);
    returncode is -1 when the command could not be started at all and -2 on
    timeout, with the reason in stderr. Never raises, never uses a shell."""
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


def _import_psutil():
    """The guarded psutil import the network packs share; None when absent."""
    try:
        import psutil

        return psutil
    except Exception:  # noqa: BLE001
        return None


_NO_PSUTIL = ("Couldn't do that — the psutil package isn't available "
              "in this VYUK install.")

# ---------------------------------------------------------------------------
# WEBSITE STATUS — is a site up, really (read-only)
# ---------------------------------------------------------------------------


def _normalise_url(raw: str) -> tuple[str, bool, str]:
    """(full_url, was_bare_domain, host). A bare domain gets https://;
    anything without a usable host comes back with an empty host."""
    text = (raw or "").strip()
    if not text:
        return "", False, ""
    bare = "://" not in text
    full = f"https://{text}" if bare else text
    host = urllib.parse.urlsplit(full).hostname or ""
    return full, bare, host


def _status_meaning(code: int) -> str:
    """The plain-English class of an HTTP status code."""
    if 200 <= code < 300:
        return "the site is up and answered fine"
    if 300 <= code < 400:
        return "a redirect (already followed to where it leads)"
    if 400 <= code < 500:
        return "the site answered but refused the page"
    if 500 <= code < 600:
        return "the site itself is erroring"
    return "an unusual response"


def _error_kind(exc: BaseException) -> str:
    """Classify a fetch failure: dns / tls / refused / timeout / connect /
    other. URLError wraps the real cause in .reason — unwrap it first."""
    seen = exc
    if isinstance(seen, urllib.error.URLError) and seen.reason is not None:
        seen = seen.reason
    if isinstance(seen, socket.gaierror):
        return "dns"
    if isinstance(seen, (ssl.CertificateError, ssl.SSLError)):
        return "tls"
    if isinstance(seen, ConnectionRefusedError):
        return "refused"
    if isinstance(seen, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(seen, OSError):
        return "connect"
    return "other"


def _fetch_once(url: str) -> dict:
    """One HTTP(S) fetch with redirects followed and the body read
    capped at _BODY_CAP. Returns {'outcome': 'response', ...} for ANY
    HTTP answer (including 4xx/5xx — the site did answer) or
    {'outcome': 'error', 'kind': ...} when no answer arrived."""
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "*/*"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            resp.read(_BODY_CAP)
            ms = int((time.perf_counter() - started) * 1000)
            return {"outcome": "response",
                    "code": resp.getcode() or 0,
                    "reason": getattr(resp, "reason", "") or "",
                    "final_url": resp.geturl() or url,
                    "server": resp.headers.get("Server", "") if resp.headers else "",
                    "ms": ms}
    except urllib.error.HTTPError as e:
        # An HTTP error status is still an answer from the site.
        ms = int((time.perf_counter() - started) * 1000)
        try:
            e.read(_BODY_CAP)
        except Exception:  # noqa: BLE001 — the status is the news, not the body
            pass
        headers = getattr(e, "headers", None) or getattr(e, "hdrs", None)
        return {"outcome": "response",
                "code": e.code or 0,
                "reason": getattr(e, "reason", "") or "",
                "final_url": e.geturl() or url,
                "server": headers.get("Server", "") if headers else "",
                "ms": ms}
    except Exception as e:  # noqa: BLE001 — classified, never a traceback
        return {"outcome": "error", "kind": _error_kind(e),
                "detail": str(e)[:160]}


def _describe_response(url: str, result: dict) -> str:
    code = result["code"]
    reason = f" {result['reason']}" if result["reason"] else ""
    lines = [f"{url} answered {code}{reason} in {result['ms']} ms — "
             f"{_status_meaning(code)}."]
    if result["final_url"] != url:
        lines.append(f"It redirected to {result['final_url']}.")
    if result["server"]:
        lines.append(f"Server: {result['server']}.")
    return "\n".join(lines)


def _describe_error(url: str, host: str, result: dict) -> str:
    kind = result["kind"]
    if kind == "dns":
        return (f"Couldn't check {url} — the name '{host}' couldn't be "
                "resolved (the DNS lookup failed). Check the spelling, "
                "or this PC's internet connection.")
    if kind == "refused":
        return (f"{host} refused the connection — the computer answered, "
                "but nothing is accepting web connections there right "
                "now (the site may be down).")
    if kind == "tls":
        return (f"The secure connection to {host} failed (a TLS/SSL "
                "error) — the site's certificate or encryption "
                f"handshake was rejected. Details: {result['detail']}")
    if kind == "timeout":
        return (f"{host} didn't answer within {_FETCH_TIMEOUT} seconds "
                "— it may be down, or something is silently dropping "
                "the connection.")
    return f"Couldn't check {url} — {result['detail']}"


_MAX_STATUS_SITES = 10  # sites per multi-check before the cap is stated


def _fetch_verdict(full: str, bare: bool, host: str) -> tuple[str, bool]:
    """The verdict text for one normalised URL, and whether the site
    counts as up: it answered with any status under 500 (a 4xx means
    the site is up but refused the page; a 5xx means the site itself
    is erroring, and a classified fetch failure means no answer)."""
    result = _fetch_once(full)
    if result["outcome"] == "response":
        return _describe_response(full, result), result["code"] < 500
    # The secure attempt failed. For a bare domain, plain HTTP is
    # worth one clearly-labelled retry — some sites never enabled
    # HTTPS, or their certificate setup is broken.
    if bare and result["kind"] in ("tls", "refused", "timeout", "connect"):
        plain = "http://" + full[len("https://"):]
        retry = _fetch_once(plain)
        if retry["outcome"] == "response":
            return (f"The secure (HTTPS) connection to {host} failed, "
                    "so I retried over plain HTTP — it answered:\n"
                    + _describe_response(plain, retry)), retry["code"] < 500
        return ((_describe_error(full, host, result)
                 + " Plain HTTP didn't answer either."), False)
    return _describe_error(full, host, result), False


def website_status(url: str) -> str:
    """Fetch a page and report status code, timing, server — or the
    classified reason no answer arrived. Several sites separated by
    spaces or commas get one verdict each plus an up-count summary;
    one site's failure never sinks the others."""
    parts = [p for p in re.split(r"[\s,]+", (url or "").strip()) if p]
    if len(parts) > 1:
        lines: list[str] = []
        up = 0
        for part in parts[:_MAX_STATUS_SITES]:
            full, bare, host = _normalise_url(part)
            if not host:
                lines.append(f"'{part}' doesn't look like a website "
                             "address — skipped.")
                continue
            try:
                verdict, is_up = _fetch_verdict(full, bare, host)
            except Exception as e:  # noqa: BLE001 — one site must not sink the rest
                verdict, is_up = f"Couldn't check {part} — {e}", False
            lines.append(verdict)
            if is_up:
                up += 1
        checked = min(len(parts), _MAX_STATUS_SITES)
        lines.append(f"{up} of {checked} are up.")
        if len(parts) > _MAX_STATUS_SITES:
            lines.append(f"Only the first {_MAX_STATUS_SITES} sites "
                         "were checked.")
        return "\n".join(lines)
    full, bare, host = _normalise_url(url)
    if not host:
        return ("Give me a website to check — e.g. "
                "website_status('example.com').")
    verdict, _is_up = _fetch_verdict(full, bare, host)
    return verdict

# ---------------------------------------------------------------------------
# SSL CERTIFICATE — who issued it, when does it expire (read-only)
# ---------------------------------------------------------------------------


def _parse_cert_date(text: str):
    """A decoded-cert date ('Oct 21 00:00:00 2026 GMT') as a date, or None."""
    cleaned = " ".join((text or "").split())
    for fmt in ("%b %d %H:%M:%S %Y %Z", "%b %d %H:%M:%S %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _rdn_value(rdns, key: str) -> str:
    """One attribute out of a decoded cert's RDN sequence
    ((('commonName', 'x'),), ...) — '' when absent."""
    for rdn in rdns or ():
        for k, v in rdn:
            if k == key:
                return v
    return ""


def _cert_summary(decoded: dict, today) -> dict:
    """Pure summary of a decoded certificate dict (as produced by
    ssl._ssl._test_decode_cert) against a given date: subject CN,
    issuer organisation, validity window, days left (negative when
    expired) and the SAN list. Date maths lives here so the suite
    can pin it with known answers."""
    sans = [str(v) for (_k, v) in decoded.get("subjectAltName", ())]
    cn = _rdn_value(decoded.get("subject"), "commonName")
    if not cn and sans:
        cn = sans[0]
    issuer = (_rdn_value(decoded.get("issuer"), "organizationName")
              or _rdn_value(decoded.get("issuer"), "commonName"))
    not_before = _parse_cert_date(decoded.get("notBefore", ""))
    not_after = _parse_cert_date(decoded.get("notAfter", ""))
    days_left = (not_after - today).days if not_after else None
    return {"cn": cn, "issuer": issuer, "sans": sans,
            "not_before": not_before, "not_after": not_after,
            "days_left": days_left,
            "expired": days_left is not None and days_left < 0}


def _cert_covers(summary: dict, host: str) -> str | None:
    """The name pattern in the cert that covers `host`, or None.

    Browsers accept an exact SAN/CN match, or a wildcard like
    '*.example.com' covering exactly ONE extra label (so it covers
    www.example.com but not example.com itself, and not
    a.b.example.com). Mirrors that rule over the names the cert
    already declared — pure function over the summary dict."""
    names = list(summary.get("sans") or [])
    cn = summary.get("cn") or ""
    if cn and cn not in names:
        names.append(cn)
    wanted = (host or "").strip().lower().rstrip(".")
    if not wanted or not names:
        return None
    for name in names:
        pattern = str(name).strip().lower().rstrip(".")
        if pattern == wanted:
            return name
        if pattern.startswith("*."):
            suffix = pattern[1:]  # '.example.com'
            if wanted.endswith(suffix):
                head = wanted[: -len(suffix)]
                if head and "." not in head:
                    return name
    return None


def _fetch_cert_der(host: str, port: int) -> bytes:
    """The peer's certificate in DER form. The context is
    deliberately UNVERIFIED: this tool inspects certificates,
    including broken and expired ones — a verifying handshake would
    refuse exactly the certificates the user asks about. Nothing is
    trusted on the strength of this connection; it only reads."""
    ctx = ssl._create_unverified_context()
    with socket.create_connection((host, port), timeout=_FETCH_TIMEOUT) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            return ssock.getpeercert(binary_form=True) or b""


def _decode_cert(der: bytes) -> dict:
    """DER bytes → the decoded dict, via a temp PEM file that is
    always removed afterwards."""
    pem = ssl.DER_cert_to_PEM_cert(der)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
                "w", suffix=".pem", delete=False) as fh:
            fh.write(pem)
            tmp = fh.name
        return ssl._ssl._test_decode_cert(tmp)
    finally:
        if tmp:
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass


def ssl_cert_info(domain: str, port: int = 443) -> str:
    """Inspect a host's TLS certificate: issuer, validity, expiry, names."""
    host = (domain or "").strip()
    if "://" in host:  # a URL was pasted — take just its host
        host = urllib.parse.urlsplit(host).hostname or ""
    host = host.split("/")[0].strip()
    if not host:
        return ("Give me a domain to inspect — e.g. "
                "ssl_cert_info('example.com').")
    try:
        port_num = int(port)
    except (TypeError, ValueError):
        return f"'{port}' isn't a port number — ports run from 1 to 65535."
    if not 1 <= port_num <= 65535:
        return (f"Port {port_num} is out of range — ports run from "
                "1 to 65535.")
    try:
        der = _fetch_cert_der(host, port_num)
    except socket.gaierror:
        return (f"Couldn't resolve the name '{host}' — check the "
                "spelling, or use an IP address.")
    except ConnectionRefusedError:
        return (f"{host}:{port_num} refused the connection — nothing "
                "is listening there, so there is no certificate to read.")
    except (ssl.SSLError, ConnectionResetError):
        return (f"Couldn't complete a TLS handshake with "
                f"{host}:{port_num} — that port may not serve TLS "
                "(it might be plain HTTP, or something that isn't a "
                "web server at all).")
    except (socket.timeout, TimeoutError):
        return (f"{host}:{port_num} didn't answer within "
                f"{_FETCH_TIMEOUT} seconds — no certificate to read.")
    except OSError as e:
        return f"Couldn't reach {host}:{port_num} — {e}."
    if not der:
        return (f"{host}:{port_num} completed a TLS handshake but "
                "presented no certificate.")
    try:
        decoded = _decode_cert(der)
    except Exception as e:  # noqa: BLE001
        return f"Got a certificate from {host}:{port_num} but couldn't decode it: {e}"
    s = _cert_summary(decoded, datetime.now(timezone.utc).date())
    lines = [f"TLS certificate for {host}:{port_num}:"]
    lines.append(f"• Issued to: {s['cn'] or '(no common name)'}")
    if s["issuer"]:
        lines.append(f"• Issued by: {s['issuer']}")
    if s["not_before"] and s["not_after"]:
        lines.append(f"• Valid: {s['not_before'].isoformat()} → "
                     f"{s['not_after'].isoformat()}")
    if s["expired"]:
        lines.append(f"• EXPIRED — this certificate stopped being "
                     f"valid on {s['not_after'].isoformat()}, "
                     f"{-s['days_left']} day(s) ago. Browsers will "
                     "refuse or warn loudly about this site.")
    elif s["days_left"] is not None:
        lines.append(f"• Expires in {s['days_left']} day(s) "
                     f"(on {s['not_after'].isoformat()}).")
    if s["sans"]:
        shown = s["sans"][:_MAX_SANS]
        extra = (f" …and {len(s['sans']) - len(shown)} more"
                 if len(s["sans"]) > len(shown) else "")
        lines.append(f"• Covers {len(s['sans'])} name(s): "
                     f"{', '.join(shown)}{extra}")
    match = _cert_covers(s, host)
    if match is not None:
        lines.append(f"• This host IS covered — '{host}' matches "
                     f"'{match}' in the certificate.")
    elif s["sans"] or s["cn"]:
        lines.append(f"• This host is NOT covered — '{host}' matches "
                     "none of the certificate's names, so a browser "
                     "would refuse this certificate for this address.")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# LOCAL DEVICES — the ARP cache, honestly framed (read-only)
# ---------------------------------------------------------------------------

_WIN_IFACE = re.compile(r"^\s*Interface:\s*(\d{1,3}(?:\.\d{1,3}){3})")
_WIN_ENTRY = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+"
    r"([0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})\s+\S+")
_LINUX_ENTRY = re.compile(
    r"\((\d{1,3}(?:\.\d{1,3}){3})\)\s+at\s+"
    r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\b.*?\bon\s+(\S+)")


def _arp_noise(ip: str, mac: str) -> bool:
    """Multicast groups, subnet broadcasts and placeholder MACs are
    cache furniture, not devices."""
    octets = ip.split(".")
    try:
        first, last = int(octets[0]), int(octets[-1])
    except (ValueError, IndexError):
        return True
    if 224 <= first <= 239 or last == 255:
        return True
    return mac in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff")


def _parse_arp(text: str) -> list[dict]:
    """Parse `arp -a` output into device rows [{ip, mac, iface}].

    Pure function over canned text so the suite can pin both shapes:
    Windows prints 'Interface: <ip>' blocks with dash-separated MACs;
    Linux prints 'host (ip) at aa:bb:.. [ether] on iface' lines
    (entries with '<incomplete>' instead of a MAC are skipped — the
    regex simply can't match them). Duplicated IPs keep their first
    sighting; multicast/broadcast/all-zero rows are dropped here so
    every caller sees devices only."""
    devices: dict[str, dict] = {}
    iface = ""
    for line in (text or "").splitlines():
        m = _WIN_IFACE.match(line)
        if m:
            iface = m.group(1)
            continue
        m = _WIN_ENTRY.match(line)
        if m:
            ip, mac = m.group(1), m.group(2).replace("-", ":").lower()
            row_iface = iface
        else:
            m = _LINUX_ENTRY.search(line)  # host/'?' precedes the (ip)
            if not m:
                continue
            ip, mac, row_iface = (m.group(1), m.group(2).lower(),
                                  m.group(3))
        if ip in devices or _arp_noise(ip, mac):
            continue
        devices[ip] = {"ip": ip, "mac": mac, "iface": row_iface}
    return sorted(devices.values(),
                  key=lambda d: tuple(int(o) for o in d["ip"].split(".")))


def list_local_devices() -> str:
    """Devices in the ARP cache — recently talked to, NOT a scan."""
    rc, out, err = _run(["arp", "-a"], timeout=20)
    if rc == -1:
        return f"Couldn't read the local device list — {err}"
    if rc == -2:
        return "Reading the local device list timed out and was stopped."
    devices = _parse_arp(out)
    if not devices:
        return ("No local devices in the ARP cache right now — this PC "
                "hasn't exchanged packets with anything on the local "
                "network recently (or the cache was just cleared).")
    shown = devices[:_MAX_DEVICES]
    lines = [f"{len(devices)} device(s) seen on your network recently. "
             "This is this PC's ARP cache — devices it has actually "
             "exchanged packets with, not a scan; a quiet device that "
             "hasn't talked to this PC won't appear here:"]
    for d in shown:
        via = f" (via {d['iface']})" if d["iface"] else ""
        lines.append(f"• {d['ip']} — {d['mac']}{via}")
    if len(devices) > len(shown):
        lines.append(f"…and {len(devices) - len(shown)} more "
                     f"(showing the first {_MAX_DEVICES}).")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# PORT OWNER — which process holds a listening port (read-only)
# ---------------------------------------------------------------------------


def find_process_on_port(port: int, protocol: str = "tcp") -> str:
    """Find the process that owns a listening/bound port."""
    try:
        port_num = int(port)
    except (TypeError, ValueError):
        return f"'{port}' isn't a port number — ports run from 1 to 65535."
    if not 1 <= port_num <= 65535:
        return (f"Port {port_num} is out of range — ports run from "
                "1 to 65535.")
    proto = (protocol or "tcp").strip().lower()
    if proto not in ("tcp", "udp"):
        return (f"Unknown protocol: '{protocol}'. Use 'tcp' or 'udp'.")
    psutil = _import_psutil()
    if psutil is None:
        return _NO_PSUTIL
    try:
        conns = psutil.net_connections(kind="inet")
    except Exception as e:  # noqa: BLE001 — AccessDenied on locked-down systems
        return (f"Couldn't read the connection table: {e}. (On some "
                "systems this needs VYUK to run with more privileges.)")
    want_type = socket.SOCK_STREAM if proto == "tcp" else socket.SOCK_DGRAM
    owners = [c for c in conns
              if c.type == want_type and c.laddr and c.laddr.port == port_num
              and (proto == "udp" or c.status == psutil.CONN_LISTEN)]
    label = "TCP" if proto == "tcp" else "UDP"
    if not owners:
        if proto == "tcp":
            return f"Nothing is listening on port {port_num} (TCP)."
        return f"Nothing is bound to port {port_num} (UDP)."
    lines = []
    for c in owners:
        where = f" on {c.laddr.ip}" if getattr(c.laddr, "ip", "") else ""
        verb = "listening" if proto == "tcp" else "bound"
        if c.pid is None:
            lines.append(f"Port {port_num} ({label}) is in use{where} — "
                         "owned by the system (no process is reported).")
            continue
        try:
            name = psutil.Process(c.pid).name()
            owner = f"{name} (PID {c.pid})"
        except Exception:  # noqa: BLE001 — gone, or not ours to see
            owner = f"PID {c.pid} (name hidden — system process)"
        lines.append(f"Port {port_num} ({label}) is in use by {owner}, "
                     f"{verb}{where}.")
    lines.append("You can change its priority with "
                 "set_process_priority if it's in the way.")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (core/funnel/tools_registry.py wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Network IV pack tool on a ToolRegistry-like object."""
    registry.register(
        name="website_status",
        description="Check whether a website is really up: fetches the page (browser-like, redirects followed, never more than 64 KB read) and reports the HTTP status code in plain words (up / refused the page / site erroring), the response time, the final URL after redirects and the server software when it says. Failures are classified plainly: DNS failure, connection refused, TLS error or timeout — and for a bare domain whose secure connection fails it retries plain HTTP once and labels that clearly. Several sites separated by spaces or commas are checked one by one (up to 10), with a verdict per site and a closing 'N of M are up' summary. Use when the user asks 'is X down?' or wants a site checked. For one specific port use check_port instead. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "Website to check — a full URL ('https://example.com/page') or a bare domain ('example.com', checked over HTTPS first); several sites can be given separated by spaces or commas"},
            },
            "required": ["url"],
        },
        handler=website_status,
    )
    registry.register(
        name="ssl_cert_info",
        description="Inspect a host's TLS (SSL) certificate: who it was issued to, the issuing organisation, the validity window, how many days until it expires (an expired certificate is called out loudly), the names it covers (up to 10 shown), and a verdict on whether the certificate actually covers the host asked about. Connects without verifying so even broken or expired certificates can be inspected — this reads the certificate, it does not trust it. Use when the user asks about a site's certificate, an HTTPS warning, or when a certificate expires. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "domain": {"type": "string",
                           "description": "Host name (or a URL — only its host is used), e.g. 'example.com'"},
                "port": {"type": "integer",
                         "description": "TLS port, 1-65535 (default 443)"},
            },
            "required": ["domain"],
        },
        handler=ssl_cert_info,
    )
    registry.register(
        name="list_local_devices",
        description="List the devices on the local network this PC has recently talked to — IP and MAC address per device, from the PC's ARP cache. This is NOT a network scan: it only shows devices the PC has actually exchanged packets with, so a quiet device won't appear. Use when the user asks who's on their network or wants a device's MAC/IP. Read-only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=list_local_devices,
    )
    registry.register(
        name="find_process_on_port",
        description="Find which process owns a port on this PC: for TCP, who is LISTENING on it; for UDP, who has it bound. Names the process and its PID (or says the name is hidden for system processes) and points at set_process_priority as a follow-up. Use when the user asks 'what's using port 8080?' or a program complains a port is taken. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "port": {"type": "integer",
                         "description": "Port number, 1-65535"},
                "protocol": {"type": "string",
                             "enum": ["tcp", "udp"],
                             "description": "tcp (default) or udp"},
            },
            "required": ["port"],
        },
        handler=find_process_on_port,
    )
