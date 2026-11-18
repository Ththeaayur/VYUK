"""Loopback forward proxy for Qt WebEngine traffic (v5.96 follow-up).

INTENTIONAL (world-view removal, 2026-11-05): the globe page was
this proxy's only WebEngine consumer, and it is gone — no consumer
remains in the tree. The proxy and its tests stay as the seam for
any future WebEngine page; the WHY section below is the 2026-10-30
field record of why it was built.

WHY THIS EXISTS
  Field-proven 2026-10-30: voice/Gemini works in the app, so
  app-level network is fine, but WebEngine has no network at all
  on the owner's machine (QtWebEngineProcess.exe blocked at the OS
  level, or WebEngine's network path broken independently). Three
  independent remote globe loads fail inside WebEngine only:
  MapLibre .mjs + CSS from cdn.jsdelivr.net, tiles from
  gibs.earthdata.nasa.gov, and the feed from
  earthquake.usgs.gov — while the owner's normal browser opens
  the same hosts. The globe loader itself does nothing wrong
  (plain default QWebEngineView, no custom profile/proxy/flags).

  The fix in code, without asking the owner for a firewall
  click: the app fetches through its OWN network stack (the main
  exe, proven allowed by voice working) and serves WebEngine over
  loopback, which firewalls do not block. WebEngine is pointed at
  the proxy with --proxy-server, set BEFORE the first
  QWebEngineView is created (the flag is read at WebEngine-context
  init; the globe page was the app's only WebEngine consumer, so
  the flag timing held for it).

SECURITY SHAPE (read before touching this file)
  * Bind is 127.0.0.1 ONLY, on an ephemeral port (never 0.0.0.0,
    never a fixed port). Only local processes can reach it.
  * Host allowlist, exactly five hosts, enforced at BOTH seams:
    plain-GET forwarding (absolute http URIs + Host header) and
    CONNECT tunneling (https authority). Anything else is refused
    with 403 (GET) or 403 (CONNECT) + one loud log line naming the
    refused host. No body is ever logged.
  * CONNECT tunnels bytes WITHOUT reading them: no MITM, no TLS
    interception, no credential handling. The proxy never sees
    plaintext for https — it shuttles encrypted bytes.
  * GET forwarding strips Authorization, Cookie, and
    Proxy-Authorization headers before forwarding, and never logs
    headers or bodies. Upstream bodies are relayed with a correct
    Content-Length (read fully, then sent); redirects pass through
    untouched for the client to follow.
  * The proxy is started lazily, only when the globe is actually
    opened (never at app startup), exactly once per process. A
    restart onto a new port is FORBIDDEN: the chromium flag
    already points at the old port, so a restart would stale it.
    If the serve thread dies, it logs loudly and clears state;
    ensure_webengine_proxy() then returns None so the loader
    falls back to direct behavior instead of pointing WebEngine
    at a dead proxy. The page must never break because the proxy
    failed.

  This module is stdlib-only (http.server, socket, threading)
  so the proxy works in every install layout, frozen or not.
"""

from __future__ import annotations

import http.client
import http.server
import os
from . import config_spec
import select
import socket
import socketserver
import sys
import threading
from urllib.parse import urlparse

# Functional hosts first (MapLibre engine, GIBS tiles, USGS feed),
# then footer licence links so clicks on them inside any future
# WebEngine page don't die. Nothing else is ever forwarded.
ALLOWED_HOSTS = frozenset({
    "cdn.jsdelivr.net",
    "gibs.earthdata.nasa.gov",
    "earthquake.usgs.gov",
    "github.com",
    "apache.org",
})

_PROXY_ENV_VAR = "QTWEBENGINE_CHROMIUM_FLAGS"
_PROXY_FLAG_PREFIX = "--proxy-server="

# Hop-by-hop headers are never forwarded (RFC 9110 §7.6.1), and
# credential-bearing headers are stripped even though the five
# allowlisted hosts are public, credential-free endpoints: the
# proxy must never become a credential carrier.
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-connection", "te", "trailer",
    "transfer-encoding", "upgrade", "proxy-authenticate",
    "proxy-authorization",
})
_STRIPPED = frozenset({"authorization", "cookie", "proxy-authorization"})

_UPSTREAM_TIMEOUT_S = 30.0

_LOG_PREFIX = "[webengine_proxy]"


def _log(message: str) -> None:
    """One loud log line. Never called with a body or a header."""
    print(f"{_LOG_PREFIX} {message}", file=sys.stderr, flush=True)


def _normalise_host(host: str) -> str:
    """Lowercase host with any port and IPv6 brackets stripped."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        # [::1]:8080 -> ::1 (bracketed literal; never allowlisted,
        # but normalised so the check below is exact, not fuzzy).
        end = host.find("]")
        if end != -1:
            return host[1:end]
        return host
    if ":" in host:
        host = host.rsplit(":", 1)[0]
    return host


def is_allowed_host(host: str) -> bool:
    """Exact allowlist match (case-insensitive, port-insensitive).

    No suffix matching, no wildcards: "evil-cdn.jsdelivr.net"
    and "cdn.jsdelivr.net.evil.com" are both refused.
    """
    return _normalise_host(host) in ALLOWED_HOSTS


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    """One proxied request. GET/HEAD forward; CONNECT tunnels."""

    protocol_version = "HTTP/1.1"
    server_version = "VYUKWebEngineProxy/1"

    # -- helpers ------------------------------------------------
    def log_message(self, *args):  # noqa: D102 - silence default logging
        pass

    def _deny(self, host: str) -> None:
        _log(f"refused host: {host!r} (not on the globe allowlist)")
        body = b"Host not allowed by the VYUK globe proxy."
        try:
            self.send_response(403)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _forwardable_headers(self):
        """Upstream headers: hop-by-hop + credentials stripped."""
        out = []
        for key, value in self.headers.items():
            low = key.lower()
            if low in _HOP_BY_HOP or low in _STRIPPED or low == "host":
                continue
            out.append((key, value))
        return out

    # -- plain-HTTP forwarding (absolute URIs only) -------------
    def _forward_http(self, method: str) -> None:
        parsed = urlparse(self.path)
        if parsed.scheme != "http" or not parsed.netloc:
            # Origin-form paths (no host) and https absolute URIs
            # are never forwarded: the former has no target, the
            # latter would require MITM. Both are refused, loudly.
            self._deny(parsed.netloc or "(origin-form path)")
            return
        host = _normalise_host(parsed.netloc)
        if not is_allowed_host(host):
            self._deny(parsed.netloc)
            return
        # The dial port is parsed SEPARATELY from the allowlist
        # check: _normalise_host strips ports by design (the
        # allowlist judges hosts, port-insensitive), so reusing
        # its output for the dial always dialed port 80 and any
        # explicit non-default port failed with 502. parsed.port
        # is None when no port is given (dial 80, as before).
        try:
            port = parsed.port
        except ValueError:
            # Unparseable port (e.g. non-numeric): not a valid
            # target, so refuse it loudly like any bad target.
            self._deny(parsed.netloc)
            return
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        body = None
        length = self.headers.get("Content-Length")
        if length is not None:
            try:
                body = self.rfile.read(int(length))
            except (ValueError, OSError):
                body = None
        try:
            conn = http.client.HTTPConnection(
                host, port or 80, timeout=_UPSTREAM_TIMEOUT_S)
            conn.putrequest(method, target, skip_host=True,
                            skip_accept_encoding=True)
            conn.putheader("Host", parsed.netloc)
            for key, value in self._forwardable_headers():
                conn.putheader(key, value)
            conn.endheaders(body)
            resp = conn.getresponse()
            data = resp.read()
            self.send_response(resp.status, resp.reason)
            for key, value in resp.getheaders():
                if key.lower() in _HOP_BY_HOP:
                    continue
                if key.lower() == "content-length":
                    continue  # replaced below with the true length
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if method != "HEAD" and data:
                self.wfile.write(data)
            conn.close()
        except (OSError, http.client.HTTPException) as exc:
            _log(f"upstream fetch failed for {host}: {exc.__class__.__name__}")
            body = b"Upstream fetch failed."
            try:
                self.send_response(502)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        self._forward_http("GET")

    def do_HEAD(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        self._forward_http("HEAD")

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        self._forward_http("POST")

    def do_PUT(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        self._forward_http("PUT")

    def do_DELETE(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        self._forward_http("DELETE")

    # -- CONNECT tunneling (bytes shuttle, never read) ----------
    def do_CONNECT(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        authority = self.path or ""
        host = _normalise_host(authority)
        if not is_allowed_host(host):
            self._deny(authority)
            return
        port = 443
        if ":" in authority and not authority.startswith("["):
            try:
                port = int(authority.rsplit(":", 1)[1])
            except ValueError:
                port = 443
        elif authority.startswith("[") and "]:" in authority:
            try:
                port = int(authority.rsplit(":", 1)[1])
            except ValueError:
                port = 443
        try:
            upstream = socket.create_connection(
                (host, port), timeout=_UPSTREAM_TIMEOUT_S)
        except OSError as exc:
            _log(f"CONNECT to {host} failed: {exc.__class__.__name__}")
            body = b"Upstream connection failed."
            try:
                self.send_response(502)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        try:
            self.send_response(200, "Connection Established")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, OSError):
            upstream.close()
            return
        client = self.connection
        # Bidirectional byte shuttle. Neither side's bytes are
        # inspected, logged, or modified — the TLS stream passes
        # through untouched, so this is a tunnel, not a MITM.
        try:
            # Blocking sockets with a write timeout: select() below still
            # gates every recv(), but sendall() must be allowed to wait
            # for the peer. On NON-blocking sockets sendall() raised
            # BlockingIOError (an OSError) as soon as the kernel buffer
            # filled, which tore the tunnel down mid-download.
            client.settimeout(_UPSTREAM_TIMEOUT_S)
            upstream.settimeout(_UPSTREAM_TIMEOUT_S)
            sockets = [client, upstream]
            while sockets:
                readable, _, _ = select.select(sockets, [], [], 60.0)
                if not readable:
                    break
                for src in readable:
                    try:
                        chunk = src.recv(65536)
                    except (BlockingIOError, InterruptedError, socket.timeout):
                        continue
                    if not chunk:
                        sockets = []
                        break
                    dst = upstream if src is client else client
                    try:
                        dst.sendall(chunk)
                    except OSError:
                        sockets = []
                        break
        except OSError:
            pass
        finally:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                upstream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
            upstream.close()


class _ProxyServer(socketserver.ThreadingMixIn,
                   http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# -- lifecycle: exactly one proxy per process --------------------
_state_lock = threading.Lock()
_state_server: _ProxyServer | None = None
_state_thread: threading.Thread | None = None
_state_port: int | None = None


def _serve_forever(server: _ProxyServer) -> None:
    """Serve thread body: on death, log loudly and clear state.

    The state is cleared (not restarted) because a restart onto a
    new port would stale the --proxy-server flag already handed to
    WebEngine. ensure_webengine_proxy() then returns None and the
    loader falls back to direct behavior.
    """
    global _state_server, _state_thread, _state_port
    try:
        server.serve_forever(poll_interval=0.2)
    except Exception as exc:  # noqa: BLE001 - death must be loud, not silent
        _log(f"proxy thread died: {exc.__class__.__name__}: {exc}")
    finally:
        with _state_lock:
            if _state_server is server:
                _state_server = None
                _state_thread = None
                _state_port = None
        _log("proxy stopped (state cleared; loader falls back "
             "to direct on next globe open)")


def start_proxy() -> int | None:
    """Start the loopback proxy once; return its port, or None.

    Idempotent: a live proxy is returned as-is (same port, no
    second listener). A dead proxy is NOT restarted (see
    _serve_forever). Any start failure returns None — never raises.
    """
    global _state_server, _state_thread, _state_port
    with _state_lock:
        if _state_server is not None and _state_thread is not None:
            if _state_thread.is_alive():
                return _state_port
            # Dead proxy: clear, do not restart onto a new port.
            _state_server = None
            _state_thread = None
            _state_port = None
            _log("previous proxy thread is dead; not restarting "
                 "(flag would stale) — falling back to direct")
            return None
        try:
            server = _ProxyServer(("127.0.0.1", 0), _ProxyHandler)
        except OSError as exc:
            _log(f"proxy bind failed: {exc.__class__.__name__}: {exc}")
            return None
        port = server.server_address[1]
        thread = threading.Thread(target=_serve_forever, args=(server,),
                                  name="vyuk-webengine-proxy", daemon=True)
        _state_server = server
        _state_thread = thread
        _state_port = port
        thread.start()
        _log(f"proxy listening on 127.0.0.1:{port} (loopback only, "
             f"allowlist: {', '.join(sorted(ALLOWED_HOSTS))})")
        return port


def stop_proxy() -> None:
    """Stop the proxy (tests + shutdown only; the app never calls
    this during normal use — the proxy lives for app lifetime)."""
    global _state_server, _state_thread, _state_port
    with _state_lock:
        server, thread = _state_server, _state_thread
        _state_server = None
        _state_thread = None
        _state_port = None
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except OSError:
            pass
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=5.0)


def proxy_port() -> int | None:
    """The live proxy's port, or None if no live proxy exists."""
    with _state_lock:
        if (_state_server is not None and _state_thread is not None
                and _state_thread.is_alive()):
            return _state_port
        return None


def apply_proxy_flag(port: int) -> bool:
    """Append --proxy-server to QTWEBENGINE_CHROMIUM_FLAGS.

    Preserves any pre-existing flags (append, never clobber). If
    the flags already contain ANY proxy-server setting, they are
    left completely alone and False is returned with a loud log:
    the owner's (or another tool's) explicit proxy choice wins
    over this mitigation, and two proxy-server flags would fight.
    Returns True only when this mitigation's flag was appended.
    """
    current = os.environ.get(_PROXY_ENV_VAR, "")
    if _PROXY_FLAG_PREFIX in current:
        _log("proxy flag NOT applied: QTWEBENGINE_CHROMIUM_FLAGS "
             "already contains a proxy-server setting")
        return False
    flag = f"--proxy-server=http://127.0.0.1:{port}"
    os.environ[_PROXY_ENV_VAR] = (
        f"{current} {flag}".strip() if current else flag)
    return True


def ensure_webengine_proxy() -> int | None:
    """Start the proxy (once) + apply the chromium flag. The loader
    seam: returns the port on success, None on any failure, and
    NEVER raises — the loader falls back to direct behavior on
    None and the page must never break because the proxy failed.
    """
    if config_spec.env("VYUK_GLOBE_PROXY").strip() == "0":
        _log("disabled by VYUK_GLOBE_PROXY=0 (WebEngine goes direct)")
        return None
    try:
        port = start_proxy()
    except Exception as exc:  # noqa: BLE001 - belt and braces
        _log(f"proxy ensure failed: {exc.__class__.__name__}: {exc}")
        return None
    if port is None:
        return None
    try:
        apply_proxy_flag(port)
    except Exception as exc:  # noqa: BLE001 - flag failure is not fatal
        _log(f"proxy flag failed: {exc.__class__.__name__}: {exc}")
        return None
    return port
