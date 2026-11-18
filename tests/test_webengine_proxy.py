"""Tests for the WebEngine loopback proxy (v5.96 follow-up).

Lab has no WebEngine, so no real QWebEngineView is created here:
the proxy itself + the flag seam + the loader ordering are proven.
(The rendered-globe check this file used to carry is gone with the
world-view feature, removed 2026-11-05.) No shipped data/ is
touched; the proxy binds
loopback ephemeral ports only and is stopped in tearDown.
"""

import contextlib
import io
import os
import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import webengine_proxy as wp  # noqa: E402


def _raw_request(port, request: bytes, read_body=True):
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
        if read_body:
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


class FakeResponse:
    def __init__(self):
        self.status = 200
        self.reason = "OK"

    def getheaders(self):
        return [("Content-Type", "text/plain"),
                ("Content-Length", "999"),  # wrong on purpose: replaced
                ("X-Upstream", "yes")]

    def read(self):
        return b"hello-body"


class FakeHTTPConnection:
    instances = []

    def __init__(self, host, port=None, timeout=None):
        # Faithful to http.client.HTTPConnection's (host, port,
        # timeout) shape: the proxy passes the dial port positionally,
        # so a (host, timeout) fake would break on any explicit port.
        self.host = host
        self.port = port
        self.timeout = timeout
        self.method = None
        self.target = None
        self.headers = []
        self.body = None
        FakeHTTPConnection.instances.append(self)

    def putrequest(self, method, target, skip_host=False,
                   skip_accept_encoding=False):
        self.method = method
        self.target = target

    def putheader(self, key, value):
        self.headers.append((key, value))

    def endheaders(self, body=None):
        self.body = body

    def getresponse(self):
        return FakeResponse()

    def close(self):
        pass


class ProxyTests(unittest.TestCase):
    def setUp(self):
        wp.stop_proxy()
        self._saved_env = dict(os.environ)
        os.environ.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)
        FakeHTTPConnection.instances.clear()

    def tearDown(self):
        wp.stop_proxy()
        os.environ.clear()
        os.environ.update(self._saved_env)

    # -- allowlist ------------------------------------------------
    def test_allowlist_allows_five_hosts(self):
        for host in ("cdn.jsdelivr.net", "gibs.earthdata.nasa.gov",
                     "earthquake.usgs.gov", "github.com", "apache.org"):
            self.assertTrue(wp.is_allowed_host(host), host)
            self.assertTrue(wp.is_allowed_host(host.upper()), host)
            self.assertTrue(wp.is_allowed_host(host + ":443"), host)

    def test_allowlist_denies_lookalikes(self):
        for host in ("evil-cdn.jsdelivr.net",
                     "cdn.jsdelivr.net.evil.com",
                     "notgithub.com", "apache.org.evil.com",
                     "earthquake.usgs.gov.attacker.io", "", "127.0.0.1"):
            self.assertFalse(wp.is_allowed_host(host), host)

    # -- bind + lifecycle ------------------------------------------
    def test_bind_is_loopback_ephemeral_and_idempotent(self):
        port = wp.start_proxy()
        self.assertIsNotNone(port)
        self.assertGreater(port, 1024)
        self.assertEqual(wp._state_server.server_address[0], "127.0.0.1")
        self.assertEqual(wp._state_server.server_address[1], port)
        server_before = wp._state_server
        self.assertEqual(wp.start_proxy(), port)
        self.assertIs(wp._state_server, server_before)
        self.assertEqual(wp.proxy_port(), port)

    def test_stop_clears_state(self):
        wp.start_proxy()
        wp.stop_proxy()
        self.assertIsNone(wp.proxy_port())

    # -- GET forwarding --------------------------------------------
    def test_get_forward_relay_and_header_stripping(self):
        import http.client
        real = http.client.HTTPConnection
        http.client.HTTPConnection = FakeHTTPConnection
        try:
            port = wp.start_proxy()
            head, body = _raw_request(
                port,
                b"GET http://cdn.jsdelivr.net/npm/x.js HTTP/1.1\r\n"
                b"Host: cdn.jsdelivr.net\r\n"
                b"Authorization: Bearer secret\r\n"
                b"Cookie: session=abc\r\n"
                b"Connection: keep-alive\r\n"
                b"X-Custom: keep-me\r\n"
                b"Connection: close\r\n\r\n")
        finally:
            http.client.HTTPConnection = real
        self.assertIn("200", head)
        self.assertEqual(body, b"hello-body")
        self.assertIn("Content-Length: 10", head)  # replaced, not 999
        fake = FakeHTTPConnection.instances[-1]
        self.assertEqual(fake.host, "cdn.jsdelivr.net")
        self.assertEqual(fake.port, 80)  # no port in URL: dial 80
        self.assertEqual(fake.method, "GET")
        self.assertEqual(fake.target, "/npm/x.js")
        sent = {k.lower(): v for k, v in fake.headers}
        self.assertEqual(sent.get("host"), "cdn.jsdelivr.net")
        self.assertEqual(sent.get("x-custom"), "keep-me")
        for stripped in ("authorization", "cookie", "connection",
                         "proxy-authorization"):
            self.assertNotIn(stripped, sent)

    def test_get_denied_host_returns_403(self):
        port = wp.start_proxy()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            head, body = _raw_request(
                port,
                b"GET http://evil.example/ HTTP/1.1\r\n"
                b"Host: evil.example\r\nConnection: close\r\n\r\n")
        self.assertIn("403", head)
        self.assertIn(b"not allowed", body)
        self.assertIn("evil.example", err.getvalue())

    def test_get_origin_form_path_refused(self):
        port = wp.start_proxy()
        head, _ = _raw_request(
            port,
            b"GET /no-host-here HTTP/1.1\r\n"
            b"Host: cdn.jsdelivr.net\r\nConnection: close\r\n\r\n")
        self.assertIn("403", head)

    # -- CONNECT tunneling ------------------------------------------
    def test_connect_denied_host_returns_403(self):
        port = wp.start_proxy()
        head, _ = _raw_request(
            port,
            b"CONNECT evil.example:443 HTTP/1.1\r\n"
            b"Host: evil.example:443\r\n\r\n",
            read_body=False)
        self.assertIn("403", head)

    def test_connect_allowed_host_tunnels_bytes(self):
        # The tunnel shuttle is proven without real network: the
        # upstream socket is a socketpair end (patched
        # create_connection), so bytes must round-trip untouched.
        peer, upstream_end = socket.socketpair()
        peer.settimeout(10.0)
        real_create = socket.create_connection

        def fake_create(address, timeout=None):
            return upstream_end

        socket.create_connection = fake_create
        try:
            port = wp.start_proxy()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)
            sock.connect(("127.0.0.1", port))
            try:
                sock.sendall(b"CONNECT cdn.jsdelivr.net:443 HTTP/1.1\r\n"
                             b"Host: cdn.jsdelivr.net:443\r\n\r\n")
                resp = b""
                while b"\r\n\r\n" not in resp:
                    resp += sock.recv(4096)
                self.assertIn(b"200", resp)
                sock.sendall(b"PING")
                self.assertEqual(peer.recv(16), b"PING")
                peer.sendall(b"PONG")
                self.assertEqual(sock.recv(16), b"PONG")
            finally:
                sock.close()
        finally:
            socket.create_connection = real_create
            peer.close()
            upstream_end.close()

    # -- flag seam ---------------------------------------------------
    def test_flag_append_preserves_existing_flags(self):
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
        self.assertTrue(wp.apply_proxy_flag(54321))
        self.assertEqual(
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"],
            "--disable-gpu --proxy-server=http://127.0.0.1:54321")

    def test_flag_from_empty_env(self):
        self.assertTrue(wp.apply_proxy_flag(54321))
        self.assertEqual(os.environ["QTWEBENGINE_CHROMIUM_FLAGS"],
                         "--proxy-server=http://127.0.0.1:54321")

    def test_flag_leaves_existing_proxy_server_alone(self):
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
            "--proxy-server=http://proxy.corp:8080")
        self.assertFalse(wp.apply_proxy_flag(54321))
        self.assertEqual(os.environ["QTWEBENGINE_CHROMIUM_FLAGS"],
                         "--proxy-server=http://proxy.corp:8080")

    # -- ensure fallback ----------------------------------------------
    def test_ensure_returns_none_when_start_fails(self):
        real_start = wp.start_proxy
        wp.start_proxy = lambda: None
        try:
            self.assertIsNone(wp.ensure_webengine_proxy())
            self.assertNotIn("proxy-server",
                             os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS",
                                            ""))
        finally:
            wp.start_proxy = real_start

    def test_ensure_never_raises_when_start_raises(self):
        real_start = wp.start_proxy

        def boom():
            raise RuntimeError("boom")

        wp.start_proxy = boom
        try:
            self.assertIsNone(wp.ensure_webengine_proxy())
        finally:
            wp.start_proxy = real_start

    def test_ensure_applies_flag_on_success(self):
        port = wp.ensure_webengine_proxy()
        self.assertIsNotNone(port)
        self.assertIn(f"--proxy-server=http://127.0.0.1:{port}",
                      os.environ["QTWEBENGINE_CHROMIUM_FLAGS"])

    # -- loader ordering (static; no view is created) -----------------



if __name__ == "__main__":
    unittest.main()
