"""R10-1 chrome-devtools-mcp PORT — CDP console / network /
performance inspection over the tree's own CDP path, as own
stdlib code.

What chrome-devtools-mcp is: Chrome's official DevTools MCP
server (ChromeDevTools/chrome-devtools-mcp, Apache-2.0, 55,043
stars, pushed 2026-11-10) — it drives the user's real Chrome
over CDP as MCP tools, including performance traces, console /
network inspection and screenshots.

The finding has two parts, decided separately:

(a) MCP server — STOPPED, gated, NOT built here. VYUK has an
MCP client (core/mcp_client.py) but no server, and exposing
VYUK's tools as an MCP server — letting other agents drive
VYUK — is a PRODUCT decision needing an owner ruling, not a
worker's build. The tree already records chrome-devtools-mcp
itself as DEV-ONLY (core/desktop/browser_pilot_licences.py,
PC-B11: "no server code, dependency, or wiring ships in the
tree"), and the server collects Google usage statistics by
default — the dev harness pins --no-usage-statistics for
exactly that reason, and telemetry stays OFF. Adopting the
Node/TypeScript server as a sidecar would also add a Node
runtime dependency: Node exists in the lab but is NOT a tree
runtime. Licence (Apache-2.0, read from the repo) and weight
(npm 1.12.0 unpacked 14,282,149 B; repo ~22 MB) do NOT decide
it — the ruling and the telemetry default do. The ruling owed:
may VYUK expose its tools to other MCP clients, and on what
terms.

(b) Console / network / performance introspection — BUILT here,
as a PORT, not an adoption. The gap was real and verified on
the backup: core/desktop/browser_cdp.py is an HTTP-JSON
handshake only (/json/version + /json/list, 132 lines) with
ZERO console / network / performance hits anywhere in the
tree, and ZERO WebSocket hits at all. Screenshots already
exist (take_screenshot / screenshot_region / screen_look, OS
capture with the capture guard), so no CDP screenshot is
built — only the three missing lanes. The browser engine
cannot carry this either: stock Playwright is importable in
NO test interpreter (it lives in .venv-pw only), and the
worker's attach path carries no console/network/performance
lane. So this module adds the missing piece in the
smallest honest shape: a minimal stdlib-only RFC 6455
WebSocket client (socket + base64 + hashlib + struct, no new
dependency) speaking CDP over the debug port the tree already
knows, with three read-only funnel tools.

Trust boundaries (non-negotiable, from the tree's own
pattern):
- The real-Chrome attach opt-in gates EVERY call
  (_real_chrome_attach_allowed, OFF by default). With it off
  the tools refuse with the standard enable instructions and
  never touch the network. Nothing auto-attaches, nothing
  launches Chrome, nothing restarts it.
- Loopback only: the HTTP handshake goes to 127.0.0.1 and the
  WebSocket to the URL Chrome itself advertises for its own
  debug port. Nothing leaves the machine.
- Page targets only: the target picker takes type == "page"
  from /json/list, never a service worker, background page or
  extension target.
- Read-only: Runtime / Log / Network / Performance enable +
  event collection + one Performance.getMetrics snapshot. No
  Runtime.evaluate (no JavaScript runs in the page), no
  Page.reload, no navigation, no input, no writes of any kind.
- No credentials, ever: no request or response bodies are
  collected, no headers are collected (so no Cookie /
  Authorization / Set-Cookie can arrive), and network URLs are
  reported with the query string and fragment stripped —
  queries can carry tokens. Console text is the page's own
  console output, truncated to 500 characters per entry and
  capped at 100 entries; it is returned to the session that
  asked, never stored, never sent anywhere.
- Live window only: the tools watch for a few seconds (default
  5, max 20) and report what they saw. They cannot see events
  from before the call, and they say so when nothing appears.
- The console and network results quote page-chosen text, so
  both tools are untrusted producers ("web page") and egress
  reads (they do loopback network I/O, so they are NOT in
  _LOCAL_ONLY_READS — under taint they confirm-gate exactly
  like tab_census). The performance result is locally templated
  numbers, so it is neither.

Verification: the lab has no Chrome, so the suite drives a
fake CDP server in-process (raw sockets, same wire format:
HTTP /json/version + /json/list and a WebSocket endpoint that
answers the four enable/getMetrics calls and emits scripted
console / network events). The real-Chrome leg — a real
console, a real network capture, a real metrics snapshot on
the owner's Windows machine — is a field leg, owed, not
claimed here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import time
import urllib.parse

from .browser_cdp import (
    _REAL_ATTACH_REFUSAL,
    _cdp_fetch_json,
    _cdp_handshake,
    _cdp_port,
    _real_chrome_attach_allowed,
)

__all__ = [
    "browser_console",
    "browser_network",
    "browser_performance",
    "register_all",
]

# Capture window: default 5 s, hard cap 20 s. The funnel's
# standard deadline is 120 s, so even the longest window plus
# handshake overhead is comfortably inside it — no deadline
# class entry is needed.
_DEFAULT_SECONDS = 5
_MAX_SECONDS = 20

# Entry caps: console text is page-chosen prose, so each entry
# is truncated and the list is capped; network rows are capped
# by the caller's max_entries (default 50, hard cap 200).
_MAX_CONSOLE_ENTRIES = 100
_CONSOLE_TEXT_CHARS = 500
_DEFAULT_MAX_ENTRIES = 50
_MAX_ENTRIES = 200

_CONNECT_TIMEOUT_S = 3.0
_CALL_TIMEOUT_S = 5.0

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class _CdpError(Exception):
    """A plain-words CDP failure: no Chrome, no target, a closed
    socket, or an error field in a CDP response."""


def _clamp_seconds(value) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_SECONDS
    if seconds < 1:
        return 1
    if seconds > _MAX_SECONDS:
        return _MAX_SECONDS
    return seconds


def _clamp_entries(value) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_ENTRIES
    if count < 1:
        return 1
    if count > _MAX_ENTRIES:
        return _MAX_ENTRIES
    return count


def _strip_query(url: str) -> str:
    """The URL without its query string or fragment: queries can
    carry tokens, and the path + host identify the request."""
    try:
        parts = urllib.parse.urlsplit(url or "")
    except ValueError:
        return url or ""
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, "", ""))


# ---------------------------------------------------------------------------
# Minimal stdlib WebSocket client (RFC 6455) — client side only.
# No third-party library: the tree is deliberately torch-free and
# stdlib-first, and a CDP client needs exactly this much wire.
# ---------------------------------------------------------------------------

class _CdpWebSocket:
    """One WebSocket connection to a CDP debug endpoint.

    Client frames are always masked (the spec requires it);
    server frames arrive unmasked. Fragmented messages are
    re-assembled; ping is answered with pong; a close frame ends
    the connection. Binary frames are ignored — CDP speaks text.
    """

    def __init__(self, ws_url: str, timeout: float = _CONNECT_TIMEOUT_S):
        parsed = urllib.parse.urlsplit(ws_url)
        if parsed.scheme not in ("ws", "wss"):
            raise _CdpError(f"not a WebSocket URL: {ws_url!r}")
        if parsed.scheme == "wss":
            raise _CdpError(
                "CDP over TLS (wss) is not supported — the debug "
                "port is loopback ws only.")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        self._sock = socket.create_connection(
            (host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buf = bytearray()
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        try:
            self._sock.sendall(request.encode("ascii"))
            head = self._read_until(b"\r\n\r\n", timeout)
        except (OSError, _CdpError) as exc:
            self._close_socket()
            raise _CdpError(
                f"could not open the WebSocket to Chrome: {exc}") from exc
        lines = head.decode("latin-1").split("\r\n")
        if not lines or "101" not in lines[0]:
            self._close_socket()
            raise _CdpError(
                "Chrome did not accept the WebSocket upgrade "
                f"({lines[0] if lines else 'no response'}).")
        accept = base64.b64encode(
            hashlib.sha1(
                (key + _WS_GUID).encode("ascii")).digest()).decode("ascii")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        if headers.get("sec-websocket-accept") != accept:
            self._close_socket()
            raise _CdpError(
                "Chrome's WebSocket accept key does not match — "
                "refusing to speak to it.")

    def _close_socket(self) -> None:
        try:
            self._sock.close()
        except Exception:  # noqa: BLE001 — closing must never raise
            pass

    def _read_until(self, marker: bytes, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            idx = self._buf.find(marker)
            if idx >= 0:
                out = bytes(self._buf[:idx + len(marker)])
                del self._buf[:idx + len(marker)]
                return out
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _CdpError("timed out waiting for Chrome to answer.")
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout as exc:
                raise _CdpError(
                    "timed out waiting for Chrome to answer.") from exc
            except OSError as exc:
                raise _CdpError(f"lost the connection to Chrome: {exc}") from exc
            if not chunk:
                raise _CdpError("Chrome closed the connection.")
            self._buf.extend(chunk)

    def _read_exact(self, count: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while len(self._buf) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _CdpError("timed out waiting for Chrome to answer.")
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout as exc:
                raise _CdpError(
                    "timed out waiting for Chrome to answer.") from exc
            except OSError as exc:
                raise _CdpError(f"lost the connection to Chrome: {exc}") from exc
            if not chunk:
                raise _CdpError("Chrome closed the connection.")
            self._buf.extend(chunk)
        out = bytes(self._buf[:count])
        del self._buf[:count]
        return out

    def send_text(self, payload: str) -> None:
        data = payload.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        try:
            self._sock.sendall(bytes(header) + masked)
        except OSError as exc:
            raise _CdpError(f"could not send to Chrome: {exc}") from exc

    def _send_pong(self, payload: bytes) -> None:
        header = bytearray([0x8A])
        length = len(payload)
        mask = os.urandom(4)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        try:
            self._sock.sendall(bytes(header) + masked)
        except OSError:
            pass  # a failed pong never breaks the read loop

    def recv_message(self, timeout: float) -> str | None:
        """The next complete text message, or None when Chrome
        closes the connection or the timeout runs out (the
        collector treats a timeout as the end of its window)."""
        deadline = time.monotonic() + timeout
        fragments: list[bytes] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                first = self._read_exact(1, remaining)[0]
                second = self._read_exact(1, remaining)[0]
            except _CdpError:
                return None
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                try:
                    length = struct.unpack(
                        "!H", self._read_exact(2, remaining))[0]
                except _CdpError:
                    return None
            elif length == 127:
                try:
                    length = struct.unpack(
                        "!Q", self._read_exact(8, remaining))[0]
                except _CdpError:
                    return None
            mask_key = b""
            if masked:
                try:
                    mask_key = self._read_exact(4, remaining)
                except _CdpError:
                    return None
            try:
                payload = self._read_exact(length, remaining)
            except _CdpError:
                return None
            if mask_key:
                payload = bytes(
                    b ^ mask_key[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:  # close
                return None
            if opcode == 0x9:  # ping -> pong, keep reading
                self._send_pong(payload)
                continue
            if opcode == 0xA:  # pong — ignore, keep reading
                continue
            if opcode == 0x2:  # binary — CDP speaks text; ignore
                continue
            if opcode in (0x1, 0x0):  # text / continuation
                fragments.append(payload)
                if fin:
                    try:
                        return b"".join(fragments).decode("utf-8")
                    except UnicodeDecodeError:
                        return b"".join(fragments).decode(
                            "utf-8", "replace")
            # Any other opcode is ignored; the loop keeps reading.

    def close(self) -> None:
        try:
            header = bytes([0x88, 0x80]) + os.urandom(4)
            self._sock.sendall(header)
        except OSError:
            pass
        self._close_socket()


# ---------------------------------------------------------------------------
# CDP session: one page target, commands by id, events collected.
# ---------------------------------------------------------------------------

class _CdpSession:
    """A command/response session over one page target's socket.
    Events that arrive while a command is in flight are kept in
    _events and drained first by collect()."""

    def __init__(self, ws_url: str):
        self._ws = _CdpWebSocket(ws_url)
        self._next_id = 0
        self._events: list[dict] = []

    def call(self, method: str, params: dict | None = None,
             timeout: float = _CALL_TIMEOUT_S) -> dict:
        self._next_id += 1
        call_id = self._next_id
        self._ws.send_text(json.dumps(
            {"id": call_id, "method": method,
             "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _CdpError(
                    f"Chrome did not answer {method} in time.")
            raw = self._ws.recv_message(remaining)
            if raw is None:
                raise _CdpError(
                    f"Chrome stopped answering during {method}.")
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if message.get("id") == call_id:
                if "error" in message:
                    err = message["error"]
                    raise _CdpError(
                        f"{method} failed: "
                        f"{err.get('message', err)}")
                return message.get("result", {})
            if "method" in message:
                self._events.append(message)

    def collect(self, seconds: float) -> list[dict]:
        """Events for ``seconds`` more, draining anything that
        arrived during earlier calls first."""
        events = list(self._events)
        self._events = []
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return events
            raw = self._ws.recv_message(remaining)
            if raw is None:
                return events
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if "method" in message:
                events.append(message)

    def close(self) -> None:
        self._ws.close()


# ---------------------------------------------------------------------------
# Attach gate + target picker (the browser_cdp.py handshake, reused).
# ---------------------------------------------------------------------------

def _require_attach() -> int:
    """The debug port when the attach opt-in is on and Chrome
    answers it, else a _CdpError carrying the plain-words
    refusal (the module functions turn it into the answer)."""
    if not _real_chrome_attach_allowed():
        raise _CdpError(_REAL_ATTACH_REFUSAL)
    port = _cdp_port()
    if _cdp_handshake(port) is None:
        raise _CdpError(
            "Chrome isn't answering on the debug port "
            f"{port} — start Chrome with "
            f"--remote-debugging-port={port} (and turn on browser "
            "control first: Settings → Browser control → Enable "
            "browser control), then ask again. I didn't attach to "
            "anything, and no other browser was tried.")
    return port


def _pick_page_target(port: int, hint: str | None) -> dict:
    """The first type == "page" target from /json/list, or the
    first whose title or URL contains ``hint``. Page targets
    only — never a service worker, background page or extension
    target."""
    try:
        targets = _cdp_fetch_json(port, "/json/list")
    except Exception as exc:  # noqa: BLE001 — no answer, plain words
        raise _CdpError(
            f"Chrome stopped answering on the debug port: {exc}") from exc
    pages = [t for t in (targets or [])
             if isinstance(t, dict) and t.get("type") == "page"
             and t.get("webSocketDebuggerUrl")]
    if not pages:
        raise _CdpError(
            "Chrome is answering, but it has no open page to "
            "inspect — open a page in Chrome first, then ask again.")
    if hint:
        needle = str(hint).strip().lower()
        for target in pages:
            haystack = f"{target.get('title', '')} {target.get('url', '')}"
            if needle in haystack.lower():
                return target
        raise _CdpError(
            f"No open Chrome page matches {hint!r} — the open "
            "pages are: " + "; ".join(
                t.get("title") or t.get("url") or "untitled"
                for t in pages[:10]) + ".")
    return pages[0]


def _open_session(target_hint: str | None) -> tuple[_CdpSession, dict]:
    port = _require_attach()
    target = _pick_page_target(port, target_hint)
    session = _CdpSession(str(target["webSocketDebuggerUrl"]))
    return session, target


def _target_label(target: dict) -> str:
    title = (target.get("title") or "").strip()
    url = _strip_query(str(target.get("url") or ""))
    if title and url:
        return f"{title} ({url})"
    return title or url or "the open page"


# ---------------------------------------------------------------------------
# The three collectors.
# ---------------------------------------------------------------------------

def _console_text(args: list) -> str:
    """One console call's text: the value / description of each
    argument, joined; truncated to the per-entry cap."""
    parts = []
    for arg in args or []:
        if not isinstance(arg, dict):
            continue
        value = arg.get("value")
        if value is None:
            value = arg.get("description", "")
        parts.append(str(value))
    text = " ".join(p for p in parts if p != "")
    if len(text) > _CONSOLE_TEXT_CHARS:
        text = text[:_CONSOLE_TEXT_CHARS] + "…"
    return text


def browser_console(seconds=5, target=None) -> str:
    """Read the console of the attached real Chrome page.

    Watches live for ``seconds`` (default 5, max 20) and reports
    console messages, JavaScript exceptions and log entries —
    what chrome-devtools-mcp's console surface shows, over the
    tree's own CDP path. Live window only: nothing from before
    the call is visible, and the answer says so when nothing
    appears. Read-only; gated on the real-Chrome attach opt-in.
    """
    seconds = _clamp_seconds(seconds)
    try:
        session, page = _open_session(target)
    except _CdpError as exc:
        return str(exc)
    entries: list[str] = []
    seen = 0
    try:
        session.call("Runtime.enable")
        session.call("Log.enable")
        events = session.collect(seconds)
    except _CdpError as exc:
        return str(exc)
    finally:
        session.close()
    for event in events:
        method = event.get("method", "")
        params = event.get("params", {}) or {}
        line = None
        if method == "Runtime.consoleAPICalled":
            kind = str(params.get("type", "log"))
            text = _console_text(params.get("args", []))
            if text:
                line = f"[{kind}] console: {text}"
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails", {}) or {}
            text = str(details.get("text", ""))
            exc = details.get("exception", {}) or {}
            desc = str(exc.get("description", ""))
            line = text or desc or "JavaScript exception"
            if desc and desc != text:
                line = f"{text}: {desc}" if text else desc
            line = f"[error] exception: {line}"
        elif method == "Log.entryAdded":
            entry = params.get("entry", {}) or {}
            level = str(entry.get("level", "info"))
            text = str(entry.get("text", ""))
            if len(text) > _CONSOLE_TEXT_CHARS:
                text = text[:_CONSOLE_TEXT_CHARS] + "…"
            if text:
                line = f"[{level}] log: {text}"
        if line is not None:
            seen += 1
            if len(entries) < _MAX_CONSOLE_ENTRIES:
                entries.append(line)
    label = _target_label(page)
    if not entries:
        return (f"Nothing appeared in the console on {label} in "
                f"those {seconds} seconds — the console was quiet "
                "for the whole window. (This watches live only; "
                "it cannot see messages from before you asked.)")
    note = ""
    if seen > _MAX_CONSOLE_ENTRIES:
        note = (f" — showing the first {_MAX_CONSOLE_ENTRIES}, "
                f"{seen - _MAX_CONSOLE_ENTRIES} more arrived")
    return (f"Console on {label} — {seen} entries in "
            f"{seconds} seconds{note}:\n" + "\n".join(entries))


def browser_network(seconds=5, target=None, max_entries=50) -> str:
    """Read the network requests of the attached real Chrome page.

    Watches live for ``seconds`` (default 5, max 20) and reports
    each request's method, URL, status, size and time — what
    chrome-devtools-mcp's network surface shows, over the tree's
    own CDP path. No bodies, no headers: nothing that could
    carry a cookie or a token is ever collected, and URLs are
    reported with the query string stripped for the same reason.
    Live window only; read-only; gated on the real-Chrome
    attach opt-in.
    """
    seconds = _clamp_seconds(seconds)
    limit = _clamp_entries(max_entries)
    try:
        session, page = _open_session(target)
    except _CdpError as exc:
        return str(exc)
    try:
        session.call("Network.enable")
        events = session.collect(seconds)
    except _CdpError as exc:
        return str(exc)
    finally:
        session.close()
    # Join by requestId: willBeSent (method + URL) -> response
    # (status) -> finished (bytes + time) / failed (reason).
    requests: dict[str, dict] = {}
    order: list[str] = []
    for event in events:
        method = event.get("method", "")
        params = event.get("params", {}) or {}
        request_id = str(params.get("requestId", ""))
        if not request_id:
            continue
        if method == "Network.requestWillBeSent":
            if request_id not in requests:
                requests[request_id] = {}
                order.append(request_id)
            req = params.get("request", {}) or {}
            requests[request_id].update({
                "url": _strip_query(str(req.get("url", ""))),
                "http_method": str(req.get("method", "")),
                "start": params.get("timestamp"),
            })
        elif method == "Network.responseReceived":
            if request_id not in requests:
                requests[request_id] = {}
                order.append(request_id)
            resp = params.get("response", {}) or {}
            requests[request_id].update({
                "status": resp.get("status"),
                "mime": str(resp.get("mimeType", "")),
            })
        elif method == "Network.loadingFinished":
            if request_id in requests:
                requests[request_id]["bytes"] = params.get(
                    "encodedDataLength")
                end = params.get("timestamp")
                start = requests[request_id].get("start")
                if isinstance(end, (int, float)) and isinstance(
                        start, (int, float)):
                    requests[request_id]["ms"] = (end - start) * 1000.0
        elif method == "Network.loadingFailed":
            if request_id in requests:
                requests[request_id]["failed"] = str(
                    params.get("errorText", "failed"))
    label = _target_label(page)
    rows: list[str] = []
    failed = 0
    total_bytes = 0
    for request_id in order[:limit]:
        entry = requests[request_id]
        url = entry.get("url") or "(unknown URL)"
        http_method = entry.get("http_method") or "?"
        if "failed" in entry:
            failed += 1
            rows.append(
                f"{http_method} FAILED ({entry['failed']}) {url}")
            continue
        status = entry.get("status")
        size = entry.get("bytes")
        if isinstance(size, (int, float)):
            total_bytes += int(size)
            size_text = (f"{size / 1024:.1f} KB" if size >= 1024
                         else f"{int(size)} B")
        else:
            size_text = "size unknown"
        ms = entry.get("ms")
        ms_text = f"{ms:.0f} ms" if isinstance(ms, (int, float)) else "—"
        status_text = str(status) if status is not None else "no response"
        rows.append(
            f"{http_method} {status_text} {size_text} {ms_text} {url}")
    if not rows:
        return (f"No network requests on {label} in those "
                f"{seconds} seconds. (This watches live only; it "
                "cannot see requests from before you asked.)")
    note = ""
    if len(order) > limit:
        note = (f" — showing the first {limit} of {len(order)}")
    total = len(order)
    return (f"Network on {label} — {total} requests in "
            f"{seconds} seconds ({failed} failed, "
            f"{total_bytes / 1024:.1f} KB total){note}:\n"
            + "\n".join(rows))


def _human_metric(name: str, value) -> str:
    """One Performance.getMetrics entry in plain words: byte
    sizes as MB, durations as ms, the rest raw."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"{name}: {value}"
    if name in ("JSHeapUsedSize", "JSHeapTotalSize"):
        return f"{name}: {number / (1024 * 1024):.1f} MB"
    if name in ("TaskDuration", "TaskDurationPerFrame",
                "FirstMeaningfulPaint", "DomContentLoaded",
                "NavigationStart"):
        return f"{name}: {number * 1000:.0f} ms"
    if number == int(number):
        return f"{name}: {int(number)}"
    return f"{name}: {number:.2f}"


def browser_performance(target=None) -> str:
    """Read the performance metrics of the attached real Chrome page.

    One Performance.getMetrics snapshot — heap sizes, DOM nodes,
    layout counts, task time and the rest — what
    chrome-devtools-mcp's performance surface reports, over the
    tree's own CDP path. No JavaScript runs in the page (no
    Runtime.evaluate) and nothing is recorded over time; this is
    a single read. Read-only; gated on the real-Chrome attach
    opt-in.
    """
    try:
        session, page = _open_session(target)
    except _CdpError as exc:
        return str(exc)
    try:
        session.call("Performance.enable")
        result = session.call("Performance.getMetrics")
    except _CdpError as exc:
        return str(exc)
    finally:
        session.close()
    metrics = result.get("metrics", []) or []
    label = _target_label(page)
    if not metrics:
        return (f"Chrome returned no performance metrics for "
                f"{label}.")
    lines = [_human_metric(str(m.get("name", "?")), m.get("value"))
             for m in metrics if isinstance(m, dict)]
    return (f"Performance on {label}:\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# Funnel registration.
# ---------------------------------------------------------------------------

def register_all(registry) -> None:
    """Register the CDP inspection pack (R10-1, chrome-devtools-mcp
    port): browser_console / browser_network / browser_performance
    — three read-only tools over the tree's own CDP path."""
    registry.register(
        name="browser_console",
        description="Read the console of the attached real Chrome page: console messages, JavaScript errors and log entries as they appear over a few seconds. Needs browser control enabled and Chrome running with the debug port open; watches live only (default 5 seconds, max 20) and cannot see messages from before you asked. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "seconds": {"type": "integer",
                            "description": "How many seconds to watch "
                            "the console for (1-20, default 5)"},
                "target": {"type": "string",
                           "description": "Optional: part of a page "
                           "title or URL to inspect — without it, "
                           "the first open page is used"},
            },
            "required": [],
        },
        handler=browser_console,
    )
    registry.register(
        name="browser_network",
        description="Read the network requests of the attached real Chrome page: each request's method, URL, status, size and time as they happen over a few seconds. Needs browser control enabled and Chrome running with the debug port open; watches live only (default 5 seconds, max 20) and cannot see requests from before you asked. No request or response bodies and no headers are ever collected, and URLs are shown without their query string. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "seconds": {"type": "integer",
                            "description": "How many seconds to watch "
                            "requests for (1-20, default 5)"},
                "target": {"type": "string",
                           "description": "Optional: part of a page "
                           "title or URL to inspect — without it, "
                           "the first open page is used"},
                "max_entries": {"type": "integer",
                                "description": "How many requests to "
                                "report at most (1-200, default 50)"},
            },
            "required": [],
        },
        handler=browser_network,
    )
    registry.register(
        name="browser_performance",
        description="Read the performance metrics of the attached real Chrome page: heap sizes, DOM nodes, layout counts and task time in one snapshot. Needs browser control enabled and Chrome running with the debug port open. No JavaScript runs in the page and nothing is recorded over time. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string",
                           "description": "Optional: part of a page "
                           "title or URL to inspect — without it, "
                           "the first open page is used"},
            },
            "required": [],
        },
        handler=browser_performance,
    )
