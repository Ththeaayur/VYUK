"""R7-9 homeassistantpy — dormant HA client as VYUK's own stdlib code.

Proves the client against local fake Home Assistant servers (REST via
http.server, WebSocket via a minimal stdlib frame server in this file)
— no live HA instance exists in the lab, and the owner runs none.

The batch's hard boundaries, pinned here:
- D10 honest-empty: no home-device funnel tools (registry stays 677,
  PERMISSION_TOOL_MAP["home_devices"] stays empty).
- Dormant: no network call on import or on construction.
- HM-1 gate first: a service call the gate refuses makes NO HTTP
  request at all; the default (empty-allowlist) scope authorizes
  nothing.
- No set_state / delete_state / fire_event on the client — outside
  the HM-1 action set (turn_on / turn_off / toggle).
- The token never appears in repr.
"""

import base64
import hashlib
import json
import socket
import struct
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from core import homeassistant as ha
from core import reach
from core.permission_grants import PERMISSION_TOOL_MAP

TOKEN = "test-long-lived-token"
STATES = [
    {"entity_id": "light.living_room", "state": "on",
     "attributes": {"friendly_name": "Living Room", "brightness": 128}},
    {"entity_id": "switch.fan", "state": "off",
     "attributes": {"friendly_name": "Fan"}},
]
SERVICES = [
    {"domain": "light", "services": {"turn_on": {}, "turn_off": {}, "toggle": {}}},
    {"domain": "switch", "services": {"turn_on": {}, "turn_off": {}, "toggle": {}}},
]


# ---------------------------------------------------------------- fake REST

class _FakeHAHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = []  # (method, path, headers-dict, body-bytes)

    def log_message(self, *args):  # keep the test output clean
        pass

    def _auth_ok(self):
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def _send_json(self, status, value):
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, body=b""):
        type(self).requests.append(
            (self.command, self.path, dict(self.headers), body))

    def do_GET(self):
        self._record()
        if not self._auth_ok():
            self._send_json(401, {"message": "Unauthorized"})
            return
        if self.path == "/api/":
            self._send_json(200, {"message": "API running."})
        elif self.path == "/api/config":
            self._send_json(200, {"version": "2026.11.0", "location_name": "Test Home"})
        elif self.path == "/api/states":
            self._send_json(200, STATES)
        elif self.path.startswith("/api/states/"):
            entity = self.path.split("/api/states/", 1)[1]
            found = [s for s in STATES if s["entity_id"] == entity]
            if found:
                self._send_json(200, found[0])
            else:
                self._send_json(404, {"message": "Entity not found."})
        elif self.path == "/api/services":
            self._send_json(200, SERVICES)
        elif self.path == "/api/areas":
            self._send_json(200, [{"name": "Living Room", "area_id": "living_room"}])
        elif self.path == "/api/devices":
            self._send_json(200, [{"name": "Test bulb", "id": "dev1"}])
        else:
            self._send_json(404, {"message": "Not found."})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self._record(body)
        if not self._auth_ok():
            self._send_json(401, {"message": "Unauthorized"})
            return
        if self.path.startswith("/api/services/"):
            # HA returns the affected states.
            self._send_json(200, STATES)
        else:
            self._send_json(404, {"message": "Not found."})


# ---------------------------------------------------------------- fake WS

def _ws_frame(payload: bytes) -> bytes:
    # Server -> client: unmasked text frame.
    head = bytearray([0x81])
    n = len(payload)
    if n < 126:
        head.append(n)
    elif n < 65536:
        head.append(126)
        head += struct.pack(">H", n)
    else:
        head.append(127)
        head += struct.pack(">Q", n)
    return bytes(head) + payload


def _ws_read_frame(conn, buf):
    # Read one masked client -> server frame; returns (opcode, payload, buf).
    while len(buf) < 2:
        chunk = conn.recv(4096)
        if not chunk:
            raise ConnectionError("client closed")
        buf += chunk
    fin_op, b2 = buf[0], buf[1]
    buf = buf[2:]
    length = b2 & 0x7F
    if length == 126:
        while len(buf) < 2:
            buf += conn.recv(4096)
        length = struct.unpack(">H", buf[:2])[0]
        buf = buf[2:]
    elif length == 127:
        while len(buf) < 8:
            buf += conn.recv(4096)
        length = struct.unpack(">Q", buf[:8])[0]
        buf = buf[8:]
    mask = b""
    if b2 & 0x80:
        while len(buf) < 4:
            buf += conn.recv(4096)
        mask, buf = buf[:4], buf[4:]
    while len(buf) < length:
        chunk = conn.recv(4096)
        if not chunk:
            raise ConnectionError("client closed")
        buf += chunk
    payload, buf = buf[:length], buf[length:]
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return fin_op & 0x0F, payload, buf


class _FakeWSServer(threading.Thread):
    """Minimal WebSocket server: handshake, auth_ok, subscribe result,
    then one event. Records what the client sent."""

    def __init__(self, token_ok=True):
        super().__init__(daemon=True)
        self.token_ok = token_ok
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)
        self.received = []  # decoded client JSON messages
        self.ready = threading.Event()

    def run(self):
        self.ready.set()
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(10.0)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            head, buf = buf.split(b"\r\n\r\n", 1)
            key = None
            for line in head.decode("latin-1").split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            accept = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()).decode("ascii")
            conn.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: " + accept.encode("ascii") + b"\r\n\r\n")
            try:
                # auth
                _, payload, buf = _ws_read_frame(conn, buf)
                msg = json.loads(payload.decode("utf-8"))
                self.received.append(msg)
                if msg.get("access_token") == TOKEN and self.token_ok:
                    conn.sendall(_ws_frame(json.dumps({"type": "auth_ok"}).encode("utf-8")))
                else:
                    conn.sendall(_ws_frame(json.dumps(
                        {"type": "auth_invalid", "message": "Invalid access token"}).encode("utf-8")))
                    return
                # subscribe
                _, payload, buf = _ws_read_frame(conn, buf)
                msg = json.loads(payload.decode("utf-8"))
                self.received.append(msg)
                conn.sendall(_ws_frame(json.dumps(
                    {"id": msg.get("id"), "type": "result", "success": True, "result": []}).encode("utf-8")))
                # one event
                conn.sendall(_ws_frame(json.dumps(
                    {"id": msg.get("id"), "type": "event",
                     "event": {"event_type": "state_changed",
                               "data": {"entity_id": "light.living_room", "new_state": {"state": "off"}}}}).encode("utf-8")))
                # wait for the client to close (or time out)
                try:
                    while True:
                        opcode, _, buf = _ws_read_frame(conn, buf)
                        if opcode == 8:
                            return
                except (ConnectionError, socket.timeout, OSError):
                    return
            except (ConnectionError, socket.timeout, OSError, ValueError):
                return

    def stop(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------- suite

class _FakeRestMixin:
    def setUp(self):
        _FakeHAHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHAHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def client(self, scope=None):
        cfg = ha.HomeAssistantConfig(host="127.0.0.1", token=TOKEN, port=self.port)
        return ha.HomeAssistantClient(cfg, scope=scope)


class TestConfig(unittest.TestCase):
    def test_non_lan_host_refused_before_any_network(self):
        with self.assertRaises(ha.HomeAssistantError) as cm:
            ha.HomeAssistantConfig(host="8.8.8.8", token=TOKEN)
        self.assertEqual(cm.exception.kind, "config")

    def test_public_hostname_refused(self):
        with self.assertRaises(ha.HomeAssistantError) as cm:
            ha.HomeAssistantConfig(host="example.com", token=TOKEN)
        self.assertEqual(cm.exception.kind, "config")

    def test_empty_token_refused(self):
        with self.assertRaises(ha.HomeAssistantError) as cm:
            ha.HomeAssistantConfig(host="127.0.0.1", token="")
        self.assertEqual(cm.exception.kind, "config")

    def test_bad_port_refused(self):
        with self.assertRaises(ha.HomeAssistantError):
            ha.HomeAssistantConfig(host="127.0.0.1", token=TOKEN, port=70000)

    def test_bad_scheme_refused(self):
        with self.assertRaises(ha.HomeAssistantError):
            ha.HomeAssistantConfig(host="127.0.0.1", token=TOKEN, scheme="ftp")

    def test_token_never_in_repr(self):
        cfg = ha.HomeAssistantConfig(host="127.0.0.1", token=TOKEN)
        self.assertNotIn(TOKEN, repr(cfg))
        self.assertIn("<redacted>", repr(cfg))
        client = ha.HomeAssistantClient(cfg)
        self.assertNotIn(TOKEN, repr(client))

    def test_no_network_on_construction(self):
        cfg = ha.HomeAssistantConfig(host="127.0.0.1", token=TOKEN)
        with patch("socket.create_connection", side_effect=AssertionError("network on construct")), \
             patch("socket.socket", side_effect=AssertionError("network on construct")):
            ha.HomeAssistantClient(cfg)
            ha.HomeAssistantWebSocket(cfg)

    def test_status_unconfigured_is_honest(self):
        status = ha.home_assistant_status()
        self.assertFalse(status["configured"])
        self.assertIn("no Home Assistant instance configured", status["reason"])
        self.assertIsNone(status["host"])

    def test_status_configured_validates_without_connecting(self):
        cfg = ha.HomeAssistantConfig(host="127.0.0.1", token=TOKEN)
        status = ha.home_assistant_status(cfg)
        self.assertTrue(status["configured"])
        self.assertEqual(status["host"], "127.0.0.1")
        self.assertNotIn(TOKEN, json.dumps(status))


class TestRest(_FakeRestMixin, unittest.TestCase):
    def test_health(self):
        out = self.client().health()
        self.assertEqual(out["message"], "API running.")
        method, path, headers, _ = _FakeHAHandler.requests[-1]
        self.assertEqual((method, path), ("GET", "/api/"))
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")

    def test_get_states_and_domain_filter(self):
        client = self.client()
        self.assertEqual(len(client.get_states()), 2)
        lights = client.get_states(domain="light")
        self.assertEqual([s["entity_id"] for s in lights], ["light.living_room"])

    def test_get_state_and_not_found(self):
        client = self.client()
        state = client.get_state("light.living_room")
        self.assertEqual(state["state"], "on")
        with self.assertRaises(ha.HomeAssistantError) as cm:
            client.get_state("light.nope")
        self.assertEqual(cm.exception.kind, "not_found")

    def test_get_services_areas_devices(self):
        client = self.client()
        self.assertEqual(client.get_services()[0]["domain"], "light")
        self.assertEqual(client.get_areas()[0]["name"], "Living Room")
        self.assertEqual(client.get_devices()[0]["name"], "Test bulb")

    def test_wrong_token_is_auth_error(self):
        cfg = ha.HomeAssistantConfig(host="127.0.0.1", token="wrong-token", port=self.port)
        with self.assertRaises(ha.HomeAssistantError) as cm:
            ha.HomeAssistantClient(cfg).health()
        self.assertEqual(cm.exception.kind, "auth")

    def test_oversized_body_refused(self):
        client = self.client()
        with patch.object(ha, "MAX_BODY_BYTES", 10):
            with self.assertRaises(ha.HomeAssistantError) as cm:
                client.get_states()
            self.assertEqual(cm.exception.kind, "server")


class TestGate(_FakeRestMixin, unittest.TestCase):
    def _scoped(self):
        scope = reach.HomeAssistantScope(
            expose_new=False,
            allowlist={"light.living_room": "living_room"},
            assist_exposed={"light.living_room"})
        return scope

    def test_default_scope_authorizes_nothing_and_makes_no_request(self):
        client = self.client()  # default scope: empty allowlist
        with self.assertRaises(ha.HomeAssistantError) as cm:
            client.call_service("light", "turn_off", "light.living_room")
        self.assertEqual(cm.exception.kind, "refused")
        posts = [r for r in _FakeHAHandler.requests if r[0] == "POST"]
        self.assertEqual(posts, [])  # the gate refused BEFORE any network call

    def test_scoped_call_reaches_the_hub(self):
        client = self.client(scope=self._scoped())
        out = client.call_service("light", "turn_off", "light.living_room")
        self.assertEqual(len(out), 2)
        method, path, _, body = _FakeHAHandler.requests[-1]
        self.assertEqual((method, path), ("POST", "/api/services/light/turn_off"))
        self.assertEqual(json.loads(body), {"entity_id": "light.living_room"})

    def test_action_outside_hm1_set_refused(self):
        client = self.client(scope=self._scoped())
        with self.assertRaises(ha.HomeAssistantError) as cm:
            client.call_service("light", "lock", "light.living_room")
        self.assertEqual(cm.exception.kind, "refused")
        posts = [r for r in _FakeHAHandler.requests if r[0] == "POST"]
        self.assertEqual(posts, [])

    def test_entity_outside_allowlist_refused(self):
        client = self.client(scope=self._scoped())
        with self.assertRaises(ha.HomeAssistantError) as cm:
            client.call_service("switch", "turn_on", "switch.fan")
        self.assertEqual(cm.exception.kind, "refused")

    def test_entity_not_assist_exposed_refused(self):
        scope = reach.HomeAssistantScope(
            expose_new=False,
            allowlist={"light.living_room": "living_room"},
            assist_exposed=set())
        with self.assertRaises(ha.HomeAssistantError) as cm:
            self.client(scope=scope).call_service("light", "turn_on", "light.living_room")
        self.assertEqual(cm.exception.kind, "refused")

    def test_domain_entity_mismatch_refused(self):
        client = self.client(scope=self._scoped())
        with self.assertRaises(ha.HomeAssistantError) as cm:
            client.call_service("switch", "turn_off", "light.living_room")
        self.assertEqual(cm.exception.kind, "refused")

    def test_no_state_mutation_methods(self):
        client = self.client()
        for name in ("set_state", "delete_state", "fire_event"):
            self.assertFalse(hasattr(client, name), name)

    def test_home_devices_scope_still_empty(self):
        # D10 honest-empty: no home-device funnel tools exist.
        self.assertEqual(PERMISSION_TOOL_MAP["home_devices"], frozenset())


class TestWebSocket(unittest.TestCase):
    def _ws(self, token_ok=True):
        server = _FakeWSServer(token_ok=token_ok)
        server.start()
        server.ready.wait(timeout=5.0)
        return server

    def test_connect_auth_subscribe_receive_event(self):
        server = self._ws()
        try:
            cfg = ha.HomeAssistantConfig(host="127.0.0.1", token=TOKEN, port=server.port)
            ws = ha.HomeAssistantWebSocket(cfg)
            ws.connect()
            try:
                cmd_id = ws.subscribe_events(event_type="state_changed")
                self.assertIsInstance(cmd_id, int)
                result = ws.receive(timeout=5.0)
                self.assertEqual(result["type"], "result")
                self.assertTrue(result["success"])
                event = ws.receive(timeout=5.0)
                self.assertEqual(event["type"], "event")
                self.assertEqual(event["event"]["data"]["entity_id"], "light.living_room")
            finally:
                ws.close()
            sent = [m.get("type") for m in server.received]
            self.assertEqual(sent, ["auth", "subscribe_events"])
            self.assertEqual(server.received[0].get("access_token"), TOKEN)
        finally:
            server.stop()

    def test_bad_token_is_auth_error(self):
        server = self._ws(token_ok=False)
        try:
            cfg = ha.HomeAssistantConfig(host="127.0.0.1", token=TOKEN, port=server.port)
            ws = ha.HomeAssistantWebSocket(cfg)
            with self.assertRaises(ha.HomeAssistantError) as cm:
                ws.connect()
            self.assertEqual(cm.exception.kind, "auth")
        finally:
            server.stop()

    def test_receive_before_connect_refused(self):
        cfg = ha.HomeAssistantConfig(host="127.0.0.1", token=TOKEN)
        ws = ha.HomeAssistantWebSocket(cfg)
        with self.assertRaises(ha.HomeAssistantError) as cm:
            ws.receive()
        self.assertEqual(cm.exception.kind, "transport")


class TestDoctorAndSurface(unittest.TestCase):
    def test_doctor_reports_not_configured_pass(self):
        import doctor as doctor_mod
        seen = []
        real_report = doctor_mod.report
        doctor_mod.report = lambda status, label, detail="", fix="": seen.append((status, label, detail))
        try:
            doctor_mod.check_home_assistant()
        finally:
            doctor_mod.report = real_report
        ha_rows = [r for r in seen if r[1] == "Home Assistant"]
        self.assertEqual(len(ha_rows), 1)
        status, _label, detail = ha_rows[0]
        self.assertEqual(status, "PASS")
        self.assertIn("not configured", detail)
        self.assertIn("nothing connected yet", detail)

    def test_doctor_check_is_registered(self):
        import doctor as doctor_mod
        self.assertIn(doctor_mod.check_home_assistant, doctor_mod._CHECKS)

    def test_no_module_level_network(self):
        # The module performs no network call at import: no socket /
        # http call outside function bodies (AST check, R7-8 shape).
        import ast
        from pathlib import Path
        tree = ast.parse(Path(ha.__file__).read_text())
        body = tree.body
        # Skip the module docstring: it names the things the module
        # forbids (socket / HTTPConnection) in prose, not in code.
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        for node in body:
            if isinstance(node, (ast.Expr, ast.Assign, ast.AnnAssign)):
                src = ast.dump(node)
                self.assertNotIn("socket", src)
                self.assertNotIn("HTTPConnection", src)
                self.assertNotIn("create_connection", src)


if __name__ == "__main__":
    unittest.main()
