"""Regression test for the proxy port-drop bug (globe field check
2026-11-02).

_forward_http used to dial http.client.HTTPConnection with the
allowlist-normalised host, whose port _normalise_host strips by
design — so an absolute-http URL with an explicit non-default
port always dialed port 80 and failed with 502 "Upstream fetch
failed". The CONNECT path was unaffected (it parses the
authority's port). This file proves the fix end to end: a real
local upstream on an ephemeral port, relayed through the real
proxy handler — not a fake connection — plus the allowlist still
refusing a non-allowlisted host even when it carries a port.
"""

import contextlib
import http.server
import io
import os
import socket
import socketserver
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import webengine_proxy as wp  # noqa: E402


def _raw_request(port, request: bytes):
    """Send one raw HTTP request to the proxy; return (head, body)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10.0)
    sock.connect(("127.0.0.1", port))
    try:
        sock.sendall(request)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        body = rest
        length = None
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        if length is not None:
            while len(body) < length:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                body += chunk
            body = body[:length]
        return head.decode("latin-1"), body
    finally:
        sock.close()


class _UpstreamHandler(http.server.BaseHTTPRequestHandler):
    """Records what arrived; answers with a marker body."""
    protocol_version = "HTTP/1.0"
    seen = []

    def log_message(self, *args):  # noqa: D102 - silence logging
        pass

    def do_GET(self):  # noqa: N802 - handler naming
        _UpstreamHandler.seen.append(
            {"method": "GET", "path": self.path,
             "headers": {k.lower(): v for k, v in self.headers.items()}})
        body = b"upstream-marker"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _UpstreamServer(socketserver.ThreadingMixIn,
                      http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ProxyPortTests(unittest.TestCase):
    def setUp(self):
        wp.stop_proxy()
        self._saved_env = dict(os.environ)
        os.environ.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)
        self._saved_allowlist = wp.ALLOWED_HOSTS
        # The real relay target is loopback; admit it for this test
        # only (the shipped allowlist has no loopback host, which is
        # why the pre-fix bug hid from the suite: no real relay was
        # ever possible without this patch).
        wp.ALLOWED_HOSTS = frozenset(
            set(wp.ALLOWED_HOSTS) | {"127.0.0.1"})
        _UpstreamHandler.seen.clear()
        self.upstream = _UpstreamServer(("127.0.0.1", 0),
                                        _UpstreamHandler)
        self.upstream_port = self.upstream.server_address[1]
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()

    def tearDown(self):
        wp.stop_proxy()
        self.upstream.shutdown()
        self.upstream.server_close()
        wp.ALLOWED_HOSTS = self._saved_allowlist
        os.environ.clear()
        os.environ.update(self._saved_env)

    def test_explicit_port_relays_to_ephemeral_upstream(self):
        """Absolute http URL with a non-default port must reach
        the upstream on THAT port (pre-fix: dialed 80, got 502)."""
        proxy_port = wp.start_proxy()
        self.assertIsNotNone(proxy_port)
        head, body = _raw_request(
            proxy_port,
            (f"GET http://127.0.0.1:{self.upstream_port}/hello?x=1 "
             f"HTTP/1.1\r\n"
             f"Host: 127.0.0.1:{self.upstream_port}\r\n"
             f"Authorization: Bearer secret\r\n"
             f"Cookie: session=abc\r\n"
             f"Connection: close\r\n\r\n").encode("latin-1"))
        self.assertIn("200", head)
        self.assertEqual(body, b"upstream-marker")
        self.assertEqual(len(_UpstreamHandler.seen), 1)
        seen = _UpstreamHandler.seen[0]
        self.assertEqual(seen["path"], "/hello?x=1")
        # Host header keeps the full netloc, port included.
        self.assertEqual(seen["headers"].get("host"),
                         f"127.0.0.1:{self.upstream_port}")
        # Credential stripping holds on the port path too.
        self.assertNotIn("authorization", seen["headers"])
        self.assertNotIn("cookie", seen["headers"])

    def test_non_allowlisted_host_with_port_still_403(self):
        """The allowlist judges before any dial, port or not."""
        proxy_port = wp.start_proxy()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            head, body = _raw_request(
                proxy_port,
                b"GET http://evil.example:8080/ HTTP/1.1\r\n"
                b"Host: evil.example:8080\r\n"
                b"Connection: close\r\n\r\n")
        self.assertIn("403", head)
        self.assertIn(b"not allowed", body)
        self.assertEqual(len(_UpstreamHandler.seen), 0)


if __name__ == "__main__":
    unittest.main()
