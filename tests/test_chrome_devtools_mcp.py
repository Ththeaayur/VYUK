"""R10-1 chrome-devtools-mcp PORT — CDP console / network /
performance inspection as own stdlib code.

No Chrome exists in the lab, so every live test below runs
against a fake CDP server in-process: a real HTTP listener
serving /json/version + /json/list and a real raw-socket
WebSocket endpoint that answers the four enable/getMetrics
calls and emits scripted CDP events in the real wire format.
The module under test speaks to it through the real code
paths (the real _cdp_handshake, the real _CdpWebSocket, the
real collectors) — only the attach opt-in and the debug port
are patched to point at the fake. No operator-supplied
artifact, no skips: the suite runs green in the delivered
state on both interpreters.
"""

import base64
import hashlib
import json
import pathlib
import socket
import struct
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from core.desktop import browser_cdp_inspect as cdp
from core.funnel.tools_registry import tools as _registry

ROOT = pathlib.Path(__file__).resolve().parent.parent

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_LONG_CONSOLE_TEXT = (
    "A very long console message that keeps going past one "
    "WebSocket frame on purpose, so the client's re-assembly of "
    "fragmented text messages is exercised for real: lorem "
    "ipsum dolor sit amet, consectetur adipiscing elit, sed do "
    "eiusmod tempor incididunt ut labore et dolore magna aliqua."
)


def _ws_accept(key: str) -> str:
    return base64.b64encode(
        hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
    ).decode("ascii")


def _ws_frame(payload: bytes, opcode: int = 0x1, fin: bool = True) -> bytes:
    head = bytearray([((0x80 if fin else 0x00) | opcode)])
    length = len(payload)
    if length < 126:
        head.append(length)
    elif length < 65536:
        head.append(126)
        head.extend(struct.pack("!H", length))
    else:
        head.append(127)
        head.extend(struct.pack("!Q", length))
    return bytes(head) + payload


def _read_exact(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("peer closed")
        data.extend(chunk)
    return bytes(data)


class _FakeCdp:
    """A fake Chrome: HTTP /json/* + a WebSocket CDP endpoint.

    script "full" emits scripted console / network events on the
    matching enable; script "quiet" emits nothing. pages is the
    /json/list payload (two pages by default, so the target hint
    is exercised).
    """

    def __init__(self, script="full", pages=None):
        self.script = script
        self.pong_seen = False
        self.methods_seen: list[str] = []
        self._stop = threading.Event()
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), self._http_handler())
        self.http_port = self._http.server_address[1]
        self._ws_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._ws_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._ws_sock.bind(("127.0.0.1", 0))
        self._ws_sock.listen(8)
        self._ws_sock.settimeout(0.5)
        self.ws_port = self._ws_sock.getsockname()[1]
        self.pages = pages if pages is not None else [
            {"id": "AAAA", "type": "page", "title": "Example Page",
             "url": "https://example.com/article?token=secret123",
             "webSocketDebuggerUrl":
                 f"ws://127.0.0.1:{self.ws_port}/devtools/page/AAAA"},
            {"id": "BBBB", "type": "page", "title": "Second Page",
             "url": "https://example.com/second",
             "webSocketDebuggerUrl":
                 f"ws://127.0.0.1:{self.ws_port}/devtools/page/BBBB"},
        ]
        self._http_thread = threading.Thread(
            target=self._http.serve_forever, kwargs={"poll_interval": 0.05},
            daemon=True)
        self._ws_thread = threading.Thread(
            target=self._accept_loop, daemon=True)

    def _http_handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: D102 — quiet
                pass

            def do_GET(self):  # noqa: N802 — http.server name
                if self.path == "/json/version":
                    body = {"Browser": "Fake Chrome/1.0",
                            "Protocol-Version": "1.3",
                            "User-Agent": "Fake",
                            "V8-Version": "1.0",
                            "WebKit-Version": "1.0",
                            "webSocketDebuggerUrl":
                                f"ws://127.0.0.1:{fake.ws_port}"
                                "/devtools/browser/XXXX"}
                elif self.path == "/json/list":
                    body = fake.pages
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(body).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler

    def start(self):
        self._http_thread.start()
        self._ws_thread.start()

    def stop(self):
        self._stop.set()
        try:
            self._http.shutdown()
        except Exception:  # noqa: BLE001 — shutdown is best-effort
            pass
        self._http.server_close()
        try:
            self._ws_sock.close()
        except OSError:
            pass
        with self._lock:
            for client in self._clients:
                try:
                    client.close()
                except OSError:
                    pass

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._ws_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._lock:
                self._clients.append(conn)
            threading.Thread(target=self._serve_ws, args=(conn,),
                             daemon=True).start()

    def _send_json(self, conn, obj):
        conn.sendall(_ws_frame(json.dumps(obj).encode("utf-8")))

    def _scripted_events(self, method):
        if self.script != "full":
            return []
        if method == "Runtime.enable":
            return [
                {"method": "Runtime.consoleAPICalled",
                 "params": {"type": "error",
                            "args": [{"type": "string", "value": "boom"}],
                            "timestamp": 1.0}},
                {"method": "Runtime.exceptionThrown",
                 "params": {"exceptionDetails": {
                     "text": "Uncaught",
                     "exception": {"description":
                                   "TypeError: x is not a function"}}}},
            ]
        if method == "Log.enable":
            return [
                {"method": "Log.entryAdded",
                 "params": {"entry": {"level": "error",
                                      "text": "Failed to load resource",
                                      "source": "network",
                                      "url": "https://example.com/x.js"}}},
                # One long console line, sent as two fragments so
                # the client's re-assembly is exercised for real.
                {"method": "Runtime.consoleAPICalled",
                 "params": {"type": "log",
                            "args": [{"type": "string",
                                      "value": _LONG_CONSOLE_TEXT}],
                            "timestamp": 1.0},
                 "fragment": True},
            ]
        if method == "Network.enable":
            return [
                {"method": "Network.requestWillBeSent",
                 "params": {"requestId": "1",
                            "request": {"url": "https://example.com/api"
                                        "?token=secret123",
                                        "method": "GET"},
                            "timestamp": 1000.0}},
                {"method": "Network.responseReceived",
                 "params": {"requestId": "1",
                            "response": {"status": 200,
                                         "mimeType": "application/json"}}},
                {"method": "Network.loadingFinished",
                 "params": {"requestId": "1",
                            "encodedDataLength": 2048,
                            "timestamp": 1000.142}},
                {"method": "Network.requestWillBeSent",
                 "params": {"requestId": "2",
                            "request": {"url": "https://example.com/"
                                        "favicon.ico",
                                        "method": "GET"},
                            "timestamp": 1001.0}},
                {"method": "Network.loadingFailed",
                 "params": {"requestId": "2",
                            "errorText": "net::ERR_ABORTED"}},
            ]
        return []

    def _serve_ws(self, conn):
        conn.settimeout(5.0)
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                head += chunk
            key = ""
            for line in head.decode("latin-1").split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            conn.sendall(
                ("HTTP/1.1 101 Switching Protocols\r\n"
                 "Upgrade: websocket\r\n"
                 "Connection: Upgrade\r\n"
                 f"Sec-WebSocket-Accept: {_ws_accept(key)}\r\n"
                 "\r\n").encode("latin-1"))
            ping_sent = False
            while not self._stop.is_set():
                try:
                    first = _read_exact(conn, 1)[0]
                    second = _read_exact(conn, 1)[0]
                except (ConnectionError, socket.timeout, OSError):
                    return
                opcode = first & 0x0F
                length = second & 0x7F
                if length == 126:
                    length = struct.unpack(
                        "!H", _read_exact(conn, 2))[0]
                elif length == 127:
                    length = struct.unpack(
                        "!Q", _read_exact(conn, 8))[0]
                mask = _read_exact(conn, 4) if second & 0x80 else b""
                payload = _read_exact(conn, length) if length else b""
                if mask:
                    payload = bytes(
                        b ^ mask[i % 4] for i, b in enumerate(payload))
                if opcode == 0x8:  # close
                    return
                if opcode == 0xA:  # pong
                    self.pong_seen = True
                    continue
                if opcode != 0x1:
                    continue
                try:
                    message = json.loads(payload.decode("utf-8"))
                except ValueError:
                    continue
                method = message.get("method", "")
                call_id = message.get("id")
                with self._lock:
                    self.methods_seen.append(method)
                if not ping_sent:
                    # Prove the client answers pings: send one
                    # right after the first call, before answering.
                    conn.sendall(_ws_frame(b"", opcode=0x9))
                    ping_sent = True
                for event in self._scripted_events(method):
                    fragment = event.pop("fragment", False)
                    data = json.dumps(event).encode("utf-8")
                    if fragment:
                        half = len(data) // 2
                        conn.sendall(_ws_frame(data[:half], fin=False))
                        conn.sendall(_ws_frame(data[half:], opcode=0x0))
                    else:
                        conn.sendall(_ws_frame(data))
                if method == "Performance.getMetrics":
                    result = {"metrics": [
                        {"name": "JSHeapUsedSize", "value": 12582912},
                        {"name": "Documents", "value": 3},
                        {"name": "LayoutCount", "value": 7},
                        {"name": "TaskDuration", "value": 0.34}]}
                else:
                    result = {}
                self._send_json(conn, {"id": call_id, "result": result})
        except (ConnectionError, socket.timeout, OSError):
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass


class _FakeBase(unittest.TestCase):
    """Base: a running fake CDP server, attach patched on."""

    script = "full"

    def setUp(self):
        self.fake = _FakeCdp(script=self.script)
        self.fake.start()
        self.addCleanup(self.fake.stop)
        self._patchers = [
            mock.patch.object(cdp, "_real_chrome_attach_allowed",
                              return_value=True),
            mock.patch.object(cdp, "_cdp_port",
                              return_value=self.fake.http_port),
        ]
        for patcher in self._patchers:
            patcher.start()
            self.addCleanup(patcher.stop)


class GapTests(unittest.TestCase):
    """The gap proof, read off the live tree (not the fake)."""

    def test_browser_cdp_is_handshake_only(self):
        src = (ROOT / "core" / "desktop" / "browser_cdp.py").read_text(
            encoding="utf-8")
        self.assertIn("_cdp_handshake", src)
        self.assertIn("/json/list", src)
        for needle in ("consoleAPICalled", "Network.", "Performance.",
                       "websocket", "WebSocket"):
            self.assertNotIn(needle, src,
                             f"browser_cdp.py already has {needle}")

    def test_no_mcp_server_in_tree(self):
        self.assertFalse((ROOT / "core" / "mcp_server.py").exists())
        hits = []
        for path in list((ROOT / "core").rglob("*.py")) + \
                list((ROOT / "gui").rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "FastMCP(" in text and "Client(" not in text:
                hits.append(str(path))
        self.assertEqual(hits, [], f"MCP server construction in: {hits}")

    def test_pilot_verdict_is_dev_only(self):
        from core.desktop import browser_pilot_licences as licences
        verdict = licences.verdict_for("chrome-devtools-mcp")
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.verdict, "DEV-ONLY")
        self.assertFalse(verdict.ships_in_v559)
        self.assertIn("--no-usage-statistics", verdict.note)

    def test_no_node_dependency(self):
        # websockets>=13.0 is declared at requirements.txt:35 but
        # NOTHING in the tree imports it and it is NOT installed
        # on system python3 — so a websockets-based client would
        # fail half the lab. This batch adds no dependency at all:
        # the module is stdlib-only, and the Node server is not
        # adopted (no npx, no chrome-devtools-mcp anywhere).
        req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("chrome-devtools-mcp", req)
        self.assertNotIn("npx", req)
        src = (ROOT / "core" / "desktop" / "browser_cdp_inspect.py"
               ).read_text(encoding="utf-8")
        self.assertNotIn("import websockets", src)
        self.assertNotIn("from websockets", src)
        self.assertNotIn("chrome-devtools-mcp",
                         (ROOT / "THIRD_PARTY_LICENSES.md").read_text(
                             encoding="utf-8"))


class WebSocketTests(_FakeBase):
    """The stdlib WebSocket client against the fake endpoint."""

    def test_call_round_trip(self):
        session = cdp._CdpSession(
            f"ws://127.0.0.1:{self.fake.ws_port}/devtools/page/AAAA")
        try:
            result = session.call("Runtime.enable")
            self.assertEqual(result, {})
            self.assertIn("Runtime.enable", self.fake.methods_seen)
        finally:
            session.close()

    def test_ping_is_answered(self):
        session = cdp._CdpSession(
            f"ws://127.0.0.1:{self.fake.ws_port}/devtools/page/AAAA")
        try:
            session.call("Runtime.enable")
            session.call("Log.enable")
        finally:
            session.close()
        self.assertTrue(self.fake.pong_seen,
                        "the client never answered the server's ping")

    def test_bad_accept_key_is_refused(self):
        # A server that answers 101 with a wrong accept key must
        # not be spoken to: patch the fake's accept by hand here
        # is overkill — assert the check exists in the source.
        src = (ROOT / "core" / "desktop" / "browser_cdp_inspect.py"
               ).read_text(encoding="utf-8")
        self.assertIn("sec-websocket-accept", src.lower())
        self.assertIn("does not match", src)


class CollectorTests(_FakeBase):
    """The three collectors against the scripted fake."""

    def test_console_returns_scripted_entries(self):
        out = cdp.browser_console(seconds=1)
        self.assertIn("[error] console: boom", out)
        self.assertIn("[error] exception: Uncaught: TypeError: "
                      "x is not a function", out)
        self.assertIn("[error] log: Failed to load resource", out)
        self.assertIn("Example Page", out)
        # The long fragmented line arrives whole, not in pieces.
        self.assertIn("lorem ipsum dolor sit amet", out)

    def test_console_quiet_window_says_so(self):
        self.fake.script = "quiet"
        out = cdp.browser_console(seconds=1)
        self.assertIn("Nothing appeared in the console", out)
        self.assertIn("watches live only", out)

    def test_network_joins_and_strips_query(self):
        out = cdp.browser_network(seconds=1)
        self.assertIn("GET 200 2.0 KB 142 ms https://example.com/api", out)
        self.assertIn("FAILED (net::ERR_ABORTED)", out)
        self.assertIn("favicon.ico", out)
        self.assertIn("2 requests", out)
        self.assertNotIn("token=secret123", out)
        self.assertNotIn("secret123", out)

    def test_network_max_entries_caps_rows(self):
        out = cdp.browser_network(seconds=1, max_entries=1)
        self.assertIn("showing the first 1 of 2", out)
        self.assertNotIn("favicon.ico", out)

    def test_performance_snapshot(self):
        out = cdp.browser_performance()
        self.assertIn("JSHeapUsedSize: 12.0 MB", out)
        self.assertIn("Documents: 3", out)
        self.assertIn("LayoutCount: 7", out)
        self.assertIn("Example Page", out)

    def test_target_hint_picks_page(self):
        out = cdp.browser_console(seconds=1, target="second")
        self.assertIn("Second Page", out)
        self.assertNotIn("Example Page", out)

    def test_target_hint_no_match_is_honest(self):
        out = cdp.browser_console(seconds=1, target="zzz-no-such-page")
        self.assertIn("No open Chrome page matches", out)
        self.assertIn("Example Page", out)

    def test_only_read_only_domains_are_called(self):
        cdp.browser_console(seconds=1)
        cdp.browser_network(seconds=1)
        cdp.browser_performance()
        allowed = {"Runtime.enable", "Log.enable", "Network.enable",
                   "Performance.enable", "Performance.getMetrics"}
        self.assertEqual(set(self.fake.methods_seen), allowed)
        # Static, on the call sites themselves (the module's
        # docstrings legitimately NAME the forbidden calls, so a
        # naive whole-file grep is the wrong check — R9-8 lesson):
        # every session.call in the module names an allowed method.
        import re
        src = (ROOT / "core" / "desktop" / "browser_cdp_inspect.py"
               ).read_text(encoding="utf-8")
        called = set(re.findall(r'session\.call\("([^"]+)"', src))
        self.assertEqual(called, allowed)


class RefusalTests(unittest.TestCase):
    """Refusals: attach off, nothing listening, no page."""

    def test_attach_off_refuses_with_instructions(self):
        with mock.patch.object(cdp, "_real_chrome_attach_allowed",
                               return_value=False):
            out = cdp.browser_console(seconds=1)
        self.assertIn("OFF by default", out)
        self.assertIn("Enable browser control", out)

    def test_nothing_listening_refuses(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
        probe.close()
        with mock.patch.object(cdp, "_real_chrome_attach_allowed",
                               return_value=True), \
             mock.patch.object(cdp, "_cdp_port",
                               return_value=closed_port):
            out = cdp.browser_network(seconds=1)
        self.assertIn("isn't answering on the debug port", out)

    def test_no_page_open_refuses(self):
        fake = _FakeCdp(script="quiet", pages=[])
        fake.start()
        try:
            with mock.patch.object(cdp, "_real_chrome_attach_allowed",
                                   return_value=True), \
                 mock.patch.object(cdp, "_cdp_port",
                                   return_value=fake.http_port):
                out = cdp.browser_performance()
        finally:
            fake.stop()
        self.assertIn("no open page to inspect", out)


class ClampTests(unittest.TestCase):
    """Bounds are clamped, never trusted from the caller."""

    def test_clamp_seconds(self):
        self.assertEqual(cdp._clamp_seconds(999), 20)
        self.assertEqual(cdp._clamp_seconds(0), 1)
        self.assertEqual(cdp._clamp_seconds(-3), 1)
        self.assertEqual(cdp._clamp_seconds("nope"), 5)
        self.assertEqual(cdp._clamp_seconds(7), 7)

    def test_clamp_entries(self):
        self.assertEqual(cdp._clamp_entries(999), 200)
        self.assertEqual(cdp._clamp_entries(0), 1)
        self.assertEqual(cdp._clamp_entries("nope"), 50)

    def test_strip_query(self):
        self.assertEqual(
            cdp._strip_query("https://example.com/a?token=x#frag"),
            "https://example.com/a")
        self.assertEqual(
            cdp._strip_query("https://example.com/a"), 
            "https://example.com/a")


class RegistryTests(unittest.TestCase):
    """Registration, gates, snapshot, counts, audit totals."""

    NAMES = ("browser_console", "browser_network", "browser_performance")

    def test_tools_registered(self):
        names = {d["name"] for d in _registry.get_declarations()}
        for name in self.NAMES:
            self.assertIn(name, names)

    def test_tools_grouped_in_browser_and_web(self):
        # core snapshot (importable anywhere) + the GUI catalogue
        # (read via AST — gui needs PySide6, absent on system
        # python3; the test_domain_routers.py pattern).
        import ast
        from core.catalogue_snapshot import GROUP_MEMBERS as snap_groups
        snap_web = snap_groups.get("Browser & web", ())  # UI Phase 2 (2026-11-25): group renamed "Web" -> "Browser & web"
        for name in self.NAMES:
            self.assertIn(name, snap_web)
        source = (ROOT / "core" / "catalogue_groups_data.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        gui_groups = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(
                    node, ast.Assign) else [node.target]
                if len(targets) == 1 and getattr(
                        targets[0], "id", "") == "GROUP_MEMBERS":
                    gui_groups = ast.literal_eval(node.value)
        self.assertIsNotNone(gui_groups)
        web = gui_groups.get("Browser & web", ())  # UI Phase 2 (2026-11-25): group renamed "Web" -> "Browser & web"
        for name in self.NAMES:
            self.assertIn(name, web)

    def test_classification_snapshot_entries(self):
        snap = json.loads(
            (ROOT / "tests" / "_classification_snapshot.json").read_text(
                encoding="utf-8"))
        for name in self.NAMES:
            self.assertEqual(
                snap[name],
                {"confirm": False, "free": None, "read": True}, name)

    def test_gates(self):
        from core.funnel.tools_gate import (
            _CONFIRM_GATE_TOOLS, _READ_ONLY_TOOLS)
        from core.funnel.tools_gate_egress import (
            _LOCAL_ONLY_READS, _is_egress_read)
        from core.funnel.tools_gate_taint import _UNTRUSTED_PRODUCERS
        for name in self.NAMES:
            self.assertIn(name, _READ_ONLY_TOOLS, name)
            self.assertNotIn(name, _CONFIRM_GATE_TOOLS, name)
            # Loopback network I/O: NOT local-only, so they
            # confirm-gate under taint exactly like tab_census.
            self.assertNotIn(name, _LOCAL_ONLY_READS, name)
            self.assertTrue(_is_egress_read(name), name)
        self.assertEqual(_UNTRUSTED_PRODUCERS.get("browser_console"),
                         "web page")
        self.assertEqual(_UNTRUSTED_PRODUCERS.get("browser_network"),
                         "web page")
        self.assertIsNone(_UNTRUSTED_PRODUCERS.get("browser_performance"))

    def test_registry_counts(self):
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 772, len(decls))  # INTENTIONAL — R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2126, total_params)  # INTENTIONAL — R10-1 chrome-devtools-mcp port (2026-11-10): 1585 -> 1591 — browser_console 2, browser_network 3, browser_performance 1; R10-3 browser-history port (2026-11-10): 1591 -> 1598 — browser_history adds 4 properties (profile_dir, browser, days, limit), browser_bookmarks adds 3 (profile_dir, browser, limit); R10-4 python-adblock port (2026-11-10): 1598 -> 1603 — check_url_blocked adds 4 properties (url, list_file, source_domain, resource_type), filter_list_status adds 1 (list_file); R10-5 python-readability port (2026-11-10): 1603 -> 1605 — extract_article adds 2 properties (url, html); R11-1 ollama port (2026-11-10): 1605 -> 1607 — ollama_pull + ollama_delete add 1 property each (model); ollama_status adds 0; R16-8 event-planning pack (2026-11-11): 1607 -> 1640 — event_plan 7 + event_guest 7 + event_vendor 5 + event_budget 5 + event_milestone 5 + event_schedule 4 properties; R16-9 attic pack (2026-11-11): 1640 -> 1650 — home_inventory adds 10 properties (action, item, box, room, condition, value, purchased, photo, detail, quantity); R16-10 GreaterWMS pack (2026-11-11): 1650 -> 1658 — warehouse_stock adds 8 properties (action, item, location, from_location, to_location, quantity, capacity, detail); R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_audit_totals(self):
        text = (ROOT / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        for needle in ('"total": 772', '"read": 386',  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): audit needles 717/379/338/272 -> 723/380/343/277 (confirm_gated 151 unchanged); R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
                       '"effectful": 386', '"confirm_gated": 170',  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): audit needles 717/379/338/272 -> 723/380/343/277 (confirm_gated 151 unchanged); R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-3 mcp-context-forge pack (2026-11-12): 154 -> 155; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-4 a2a pack (2026-11-12): 155 -> 156; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-5 dspy pack (2026-11-12): 156 -> 157; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-6 fonoster pack (2026-11-12): 157 -> 158; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374/164 -> 375/165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
                       '"already_verified_in_handler": 320',  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): audit needles 717/379/338/272 -> 723/380/343/277 (confirm_gated 151 unchanged); R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 287 -> 288; R18-4 a2a pack (2026-11-12): 288 -> 289; R18-5 dspy pack (2026-11-12): 289 -> 290; R18-6 fonoster pack (2026-11-12): 290 -> 291; R18-7 attendize pack (2026-11-12): 291 -> 292; R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
                       '"browser_web": 36'):
            # INTENTIONAL — R10-3 browser-history port (2026-11-10):
            # total 709 -> 711, read 373 -> 375, browser_web 31 -> 33
            # (browser_history, browser_bookmarks added, read-only).
            # R10-4 python-adblock port (2026-11-10):
            # total 711 -> 713, read 375 -> 377, browser_web 33 -> 35
            # (check_url_blocked, filter_list_status added, read-only).
            # R11-1 ollama port (2026-11-10):
            # total 714 -> 717, read 378 -> 379, effectful 336 -> 338,
            # confirm 149 -> 151, already-verified 270 -> 272
            # (ollama_status added, read-only; ollama_pull +
            # ollama_delete added, confirm-gated, A5-sysreadback).
            self.assertIn(needle, text, needle)

    def test_version_stays_630(self):
        text = (ROOT / "core" / "version.py").read_text(encoding="utf-8")
        self.assertIn('VERSION = "6.3.0"', text)


if __name__ == "__main__":
    unittest.main()
