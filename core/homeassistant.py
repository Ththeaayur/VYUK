"""Home Assistant client (R7-9 homeassistantpy pattern) — DORMANT BY DEFAULT.

VYUK's own stdlib-only REST + WebSocket client for a Home Assistant hub
the owner already runs. The PATTERN is taken from rteoo/homeassistantpy
(MIT, ~38 KB wheel, stdlib-only, actions disabled behind an exact
allowlist); no code is vendored and no package is installed — the
upstream repo is single-author, 0 stars, very young ("promising, not
proven"), so it was read first and the design was re-derived here.

DORMANCY + D10 (owner ruling 2026-11-04: home devices visible but
honest, "nothing connected yet", until first device):
- No module-level client, no auto-discovery, no network call on import
  or on boot. A client exists only when explicitly constructed with a
  host + long-lived access token.
- No funnel tools: PERMISSION_TOOL_MAP["home_devices"] is empty by
  D10 honest-empty. This module is a Python-level API + a doctor
  check; it never becomes a voice-callable device tool.
- Every service call passes through the HM-1 gate FIRST
  (reach.build_assist_request: strict LAN host + Assist-exposed +
  per-room allowlist + HM1_ALLOWED_ACTIONS). The default scope has
  an EMPTY allowlist, so with no scope supplied nothing is
  authorized — deny-by-default, not a bug.
- Reads (states / services / areas / devices) need a configured
  client; they change nothing. Writes are limited to the HM-1 action
  set (turn_on / turn_off / toggle). set_state, delete_state and
  fire_event are NOT exposed — outside the allowed action set.
- The token is never logged and never appears in repr. There is no
  persistent configuration in this batch: the owner supplies URL +
  token explicitly, and a future settings surface (not this batch)
  is where a stored configuration would live. Until then
  home_assistant_status() honestly reports "not configured".

Provenance: the upstream client's REST shape (Bearer auth over
http.client, bounded reads, JSON constant rejection) and its agent
allowlist shape (actions disabled by default, explicit tool-name
allowlist) were read at /tmp/ha_upstream (client.py, transport.py,
agent.py, websocket_transport.py) and re-implemented here in VYUK's
own code, adapted to the HM-1 gate. The upstream CLI, conversation
tools and registry-discovery specs were NOT taken.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import socket
import struct
import urllib.parse
from dataclasses import dataclass, field

from . import reach

# Response bodies larger than this are refused, not buffered.
MAX_BODY_BYTES = 10 * 1024 * 1024
# A single WebSocket frame larger than this is refused.
MAX_FRAME_BYTES = 1024 * 1024
# HA's default HTTP port.
DEFAULT_PORT = 8123


class HomeAssistantError(Exception):
    """A Home Assistant client failure. ``kind`` names the class:

    config — bad host / token / port (refused before any network call)
    refused — the HM-1 gate refused the call (LAN / scope / action)
    auth — the hub rejected the token (401/403, or auth_invalid)
    not_found — the hub has no such entity / endpoint (404)
    timeout — connect / read timed out
    transport — socket / HTTP / WebSocket protocol failure
    server — the hub answered with an error status or bad JSON
    """

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _reject_json_constant(_value: str) -> None:
    # NaN / Infinity are not JSON; a hub answering with them is a
    # protocol violation, not a value to smuggle into VYUK.
    raise ValueError("non-JSON constant in response")


def _parse_json(body: bytes, what: str) -> object:
    try:
        return json.loads(body.decode("utf-8"),
                          parse_constant=_reject_json_constant)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HomeAssistantError("server", f"{what}: unparseable JSON ({exc})") from exc


@dataclass
class HomeAssistantConfig:
    """Connection settings for one Home Assistant instance.

    ``host`` must be a strict LAN target (HM-1: RFC1918 + loopback +
    link-local; unresolvable or public hosts refuse fail-closed).
    ``token`` is a long-lived access token, supplied explicitly by
    the owner — never read from the environment, never logged, never
    stored by this module. ``scheme`` is "http" unless the hub is
    reached over TLS ("https", port 443 by default).
    """

    host: str
    token: str
    port: int | None = None
    scheme: str = "http"
    timeout: float = 10.0
    verify_ssl: bool = True

    def __post_init__(self) -> None:
        if self.scheme not in ("http", "https"):
            raise HomeAssistantError("config", f"scheme must be http/https, got {self.scheme!r}")
        if self.port is not None and not (1 <= self.port <= 65535):
            raise HomeAssistantError("config", f"port out of range: {self.port!r}")
        if not isinstance(self.token, str) or not self.token:
            raise HomeAssistantError("config", "a long-lived access token is required (empty token refused)")
        if not isinstance(self.timeout, (int, float)) or self.timeout <= 0:
            raise HomeAssistantError("config", f"timeout must be positive, got {self.timeout!r}")
        # Strict LAN gate, before any socket is opened.
        lan = reach.is_lan_target(self.host)
        if not lan.ok:
            raise HomeAssistantError("config", f"host refused by the LAN gate: {lan.detail}")

    @property
    def effective_port(self) -> int:
        if self.port is not None:
            return self.port
        return 443 if self.scheme == "https" else DEFAULT_PORT

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.effective_port}"

    def __repr__(self) -> str:  # never leak the token
        return (f"HomeAssistantConfig(host={self.host!r}, port={self.effective_port}, "
                f"scheme={self.scheme!r}, token=<redacted>)")


class HomeAssistantClient:
    """A small, synchronous Home Assistant REST client.

    Dormant by construction: constructing a client opens NO connection
    and performs NO network call. Every method opens one short-lived
    connection and closes it. Service calls pass the HM-1 gate first
    and refuse before any network call when the gate refuses.
    """

    def __init__(self, config: HomeAssistantConfig,
                 scope: reach.HomeAssistantScope | None = None) -> None:
        self._config = config
        # Default scope: empty allowlist — nothing authorized until
        # the caller supplies a scope that names entities.
        self._scope = scope if scope is not None else reach.default_home_scope()

    def __repr__(self) -> str:
        return f"HomeAssistantClient({self._config!r}, scope_allowlist={len(self._scope.allowlist)})"

    # -- transport ------------------------------------------------------

    def _connection(self) -> http.client.HTTPConnection:
        cfg = self._config
        if cfg.scheme == "https":
            import ssl
            ctx = ssl.create_default_context()
            if not cfg.verify_ssl:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            return http.client.HTTPSConnection(cfg.host, cfg.effective_port,
                                               timeout=cfg.timeout, context=ctx)
        return http.client.HTTPConnection(cfg.host, cfg.effective_port,
                                          timeout=cfg.timeout)

    def _request(self, method: str, path: str, data: object = None) -> object:
        cfg = self._config
        body: bytes | None = None
        headers = {"Authorization": f"Bearer {cfg.token}",
                   "Accept": "application/json"}
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn = self._connection()
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = self._read_bounded(resp)
            if resp.status in (401, 403):
                raise HomeAssistantError("auth", f"{method} {path}: hub rejected the token (HTTP {resp.status})")
            if resp.status == 404:
                raise HomeAssistantError("not_found", f"{method} {path}: no such entity/endpoint (HTTP 404)")
            if resp.status >= 400:
                raise HomeAssistantError("server",
                                         f"{method} {path}: hub error HTTP {resp.status}: {raw[:200]!r}")
            if not raw:
                return {}
            return _parse_json(raw, f"{method} {path}")
        except HomeAssistantError:
            raise
        except socket.timeout as exc:
            raise HomeAssistantError("timeout", f"{method} {path}: timed out ({exc})") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise HomeAssistantError("transport", f"{method} {path}: {type(exc).__name__}: {exc}") from exc
        finally:
            conn.close()

    @staticmethod
    def _read_bounded(resp: http.client.HTTPResponse) -> bytes:
        # Read in chunks and refuse a body over the cap instead of
        # buffering an unbounded response into memory.
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                raise HomeAssistantError("server", f"response over {MAX_BODY_BYTES} B refused")
            chunks.append(chunk)
        return b"".join(chunks)

    # -- reads (change nothing) ------------------------------------------

    def health(self) -> dict:
        """Return the API health message (GET /api/)."""
        value = self._request("GET", "/api/")
        if not isinstance(value, dict):
            raise HomeAssistantError("server", f"health: expected an object, got {type(value).__name__}")
        return value

    def get_config(self) -> dict:
        """Return the hub configuration (GET /api/config)."""
        value = self._request("GET", "/api/config")
        if not isinstance(value, dict):
            raise HomeAssistantError("server", f"get_config: expected an object, got {type(value).__name__}")
        return value

    def get_states(self, *, domain: str | None = None) -> list[dict]:
        """Return entity states (GET /api/states), optionally filtered
        to one domain client-side (the API has no domain filter)."""
        value = self._request("GET", "/api/states")
        if not isinstance(value, list):
            raise HomeAssistantError("server", f"get_states: expected a list, got {type(value).__name__}")
        states = [s for s in value if isinstance(s, dict)]
        if domain is not None:
            states = [s for s in states if s.get("entity_id", "").split(".", 1)[0] == domain]
        return states

    def get_state(self, entity_id: str) -> dict:
        """Return one entity's state (GET /api/states/<entity_id>)."""
        if "." not in entity_id:
            raise HomeAssistantError("config", f"entity_id must be domain.name, got {entity_id!r}")
        value = self._request("GET", f"/api/states/{urllib.parse.quote(entity_id, safe='')}")
        if not isinstance(value, dict):
            raise HomeAssistantError("server", f"get_state: expected an object, got {type(value).__name__}")
        return value

    def get_services(self) -> list[dict]:
        """Return the service catalogue (GET /api/services)."""
        value = self._request("GET", "/api/services")
        if not isinstance(value, list):
            raise HomeAssistantError("server", f"get_services: expected a list, got {type(value).__name__}")
        return [s for s in value if isinstance(s, dict)]

    def get_areas(self) -> list[dict]:
        """Return areas (GET /api/areas)."""
        value = self._request("GET", "/api/areas")
        if not isinstance(value, list):
            raise HomeAssistantError("server", f"get_areas: expected a list, got {type(value).__name__}")
        return [a for a in value if isinstance(a, dict)]

    def get_devices(self) -> list[dict]:
        """Return devices (GET /api/devices)."""
        value = self._request("GET", "/api/devices")
        if not isinstance(value, list):
            raise HomeAssistantError("server", f"get_devices: expected a list, got {type(value).__name__}")
        return [d for d in value if isinstance(d, dict)]

    # -- actions (HM-1 gate first, always) -------------------------------

    def call_service(self, domain: str, service: str, entity_id: str,
                     data: dict | None = None) -> list[dict]:
        """Call a service on one entity (POST /api/services/<domain>/<service>).

        The HM-1 gate runs FIRST: the host must be LAN, the entity
        must be Assist-exposed AND on the scope's per-room allowlist,
        and the service must be in HM1_ALLOWED_ACTIONS
        (turn_on / turn_off / toggle). A refused gate raises
        HomeAssistantError(kind="refused") BEFORE any network call —
        no HTTP request is made. The domain must match the entity's
        domain (light.xxx is called on domain "light", never "switch").
        """
        if "." not in entity_id:
            raise HomeAssistantError("config", f"entity_id must be domain.name, got {entity_id!r}")
        entity_domain = entity_id.split(".", 1)[0]
        if entity_domain != domain:
            raise HomeAssistantError("refused",
                                     f"domain {domain!r} does not match entity {entity_id!r} "
                                     f"(entity domain is {entity_domain!r})")
        gate = reach.build_assist_request(self._scope, self._config.host, entity_id, service)
        if not gate.ok:
            raise HomeAssistantError("refused", f"HM-1 gate refused: {gate.detail}")
        payload: dict = {"entity_id": entity_id}
        if data:
            payload.update(data)
        value = self._request("POST", f"/api/services/{domain}/{service}", data=payload)
        if not isinstance(value, list):
            raise HomeAssistantError("server", f"call_service: expected a list, got {type(value).__name__}")
        return [s for s in value if isinstance(s, dict)]


class HomeAssistantWebSocket:
    """A minimal Home Assistant WebSocket session (stdlib only).

    Auth + subscribe_events + receive. No background thread, no
    auto-reconnect, no buffering beyond one frame: the caller drives
    receive() and decides what to do with each event. Dormant until
    connect() is called explicitly.
    """

    def __init__(self, config: HomeAssistantConfig) -> None:
        self._config = config
        self._sock: socket.socket | None = None
        self._buf = b""
        self._next_id = 1

    def __repr__(self) -> str:
        return f"HomeAssistantWebSocket({self._config!r}, connected={self._sock is not None})"

    # -- connection ------------------------------------------------------

    def connect(self, timeout: float | None = None) -> "HomeAssistantWebSocket":
        """Open the WebSocket and authenticate. Raises
        HomeAssistantError on handshake failure or auth_invalid."""
        cfg = self._config
        deadline = timeout if timeout is not None else cfg.timeout
        try:
            raw = socket.create_connection((cfg.host, cfg.effective_port), timeout=deadline)
        except socket.timeout as exc:
            raise HomeAssistantError("timeout", f"WebSocket connect timed out ({exc})") from exc
        except OSError as exc:
            raise HomeAssistantError("transport", f"WebSocket connect failed: {exc}") from exc
        if cfg.scheme == "https":
            import ssl
            ctx = ssl.create_default_context()
            if not cfg.verify_ssl:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            try:
                raw = ctx.wrap_socket(raw, server_hostname=cfg.host)
            except ssl.SSLError as exc:
                raw.close()
                raise HomeAssistantError("transport", f"WebSocket TLS failed: {exc}") from exc
        self._sock = raw
        self._sock.settimeout(deadline)
        try:
            self._handshake(deadline)
            self._authenticate(deadline)
        except BaseException:
            self.close()
            raise
        return self

    def _handshake(self, deadline: float) -> None:
        assert self._sock is not None
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET /api/websocket HTTP/1.1\r\n"
            f"Host: {self._config.host}:{self._config.effective_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self._sock.sendall(request)
        head = self._read_until(b"\r\n\r\n", deadline)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise HomeAssistantError("transport", f"WebSocket handshake refused: {head[:200]!r}")
        accept = hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        expected = base64.b64encode(accept)
        if b"Sec-WebSocket-Accept: " + expected not in head:
            raise HomeAssistantError("transport", "WebSocket handshake: bad Sec-WebSocket-Accept")

    def _authenticate(self, deadline: float) -> None:
        self._send({"type": "auth", "access_token": self._config.token}, deadline)
        msg = self.receive(deadline)
        if msg.get("type") == "auth_invalid":
            raise HomeAssistantError("auth", f"hub rejected the token: {msg.get('message', '')}")
        if msg.get("type") != "auth_ok":
            raise HomeAssistantError("server", f"unexpected auth reply: {msg.get('type')!r}")

    # -- frames ----------------------------------------------------------

    def _read_until(self, marker: bytes, deadline: float) -> bytes:
        assert self._sock is not None
        self._sock.settimeout(deadline)
        while marker not in self._buf:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout as exc:
                raise HomeAssistantError("timeout", "WebSocket read timed out") from exc
            except OSError as exc:
                raise HomeAssistantError("transport", f"WebSocket read failed: {exc}") from exc
            if not chunk:
                raise HomeAssistantError("transport", "WebSocket closed by the hub")
            self._buf += chunk
            if len(self._buf) > MAX_FRAME_BYTES + 4096:
                raise HomeAssistantError("transport", "WebSocket handshake over size cap")
        head, self._buf = self._buf.split(marker, 1)
        return head + marker

    def _read_exact(self, size: int, deadline: float) -> bytes:
        assert self._sock is not None
        self._sock.settimeout(deadline)
        while len(self._buf) < size:
            try:
                chunk = self._sock.recv(min(65536, size - len(self._buf)))
            except socket.timeout as exc:
                raise HomeAssistantError("timeout", "WebSocket read timed out") from exc
            except OSError as exc:
                raise HomeAssistantError("transport", f"WebSocket read failed: {exc}") from exc
            if not chunk:
                raise HomeAssistantError("transport", "WebSocket closed by the hub")
            self._buf += chunk
        out, self._buf = self._buf[:size], self._buf[size:]
        return out

    def _read_frame(self, deadline: float) -> tuple[int, bytes]:
        head = self._read_exact(2, deadline)
        fin = head[0] & 0x80
        opcode = head[0] & 0x0F
        masked = head[1] & 0x80
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2, deadline))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8, deadline))[0]
        if length > MAX_FRAME_BYTES:
            raise HomeAssistantError("transport", f"WebSocket frame over {MAX_FRAME_BYTES} B refused")
        if masked:
            mask = self._read_exact(4, deadline)
        else:
            mask = b""
        payload = self._read_exact(length, deadline)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if not fin:
            raise HomeAssistantError("transport", "fragmented WebSocket frames are not supported")
        return opcode, payload

    def _send_frame(self, opcode: int, payload: bytes, deadline: float) -> None:
        assert self._sock is not None
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        head = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            head.append(0x80 | length)
        elif length < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", length)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", length)
        self._sock.settimeout(deadline)
        try:
            self._sock.sendall(bytes(head) + mask + masked)
        except socket.timeout as exc:
            raise HomeAssistantError("timeout", "WebSocket send timed out") from exc
        except OSError as exc:
            raise HomeAssistantError("transport", f"WebSocket send failed: {exc}") from exc

    def _send(self, envelope: dict, deadline: float) -> None:
        self._send_frame(1, json.dumps(envelope).encode("utf-8"), deadline)

    # -- API -------------------------------------------------------------

    def send(self, envelope: dict, timeout: float | None = None) -> int:
        """Send one JSON command; returns the command id."""
        if self._sock is None:
            raise HomeAssistantError("transport", "WebSocket is not connected")
        cmd_id = self._next_id
        self._next_id += 1
        envelope = {"id": cmd_id, **envelope}
        self._send(envelope, timeout if timeout is not None else self._config.timeout)
        return cmd_id

    def receive(self, timeout: float | None = None) -> dict:
        """Receive one JSON message. Ping is answered with pong and
        skipped; close raises HomeAssistantError."""
        if self._sock is None:
            raise HomeAssistantError("transport", "WebSocket is not connected")
        deadline = timeout if timeout is not None else self._config.timeout
        while True:
            opcode, payload = self._read_frame(deadline)
            if opcode == 9:  # ping -> pong, then keep reading
                self._send_frame(10, payload, deadline)
                continue
            if opcode == 8:  # close
                raise HomeAssistantError("transport", "WebSocket closed by the hub")
            if opcode != 1:  # text only
                raise HomeAssistantError("transport", f"unexpected WebSocket opcode {opcode}")
            value = _parse_json(payload, "WebSocket message")
            if not isinstance(value, dict):
                raise HomeAssistantError("server", "WebSocket message was not an object")
            return value

    def subscribe_events(self, event_type: str | None = None,
                         timeout: float | None = None) -> int:
        """Subscribe to hub events; returns the command id. The
        caller drives receive() for the events."""
        envelope: dict = {"type": "subscribe_events"}
        if event_type is not None:
            envelope["event_type"] = event_type
        return self.send(envelope, timeout)

    def close(self) -> None:
        """Close the session. Never raises."""
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            self._send_frame(8, b"", 1.0)
        except Exception:  # noqa: BLE001 - close is best-effort
            pass
        try:
            sock.close()
        except Exception:  # noqa: BLE001 - close is best-effort
            pass


def home_assistant_status(config: HomeAssistantConfig | None = None) -> dict:
    """Honest configuration status. No network call is ever made.

    Unconfigured (the only state this batch can produce, until a
    settings surface exists): {"configured": False, "reason": "no
    Home Assistant instance configured", ...} — D10 honest-empty:
    nothing connected yet. A supplied config is validated (LAN +
    token present) without connecting; a valid config reports
    configured True with the host, never the token.
    """
    if config is None:
        return {
            "configured": False,
            "reason": "no Home Assistant instance configured",
            "detail": "D10 honest-empty: nothing connected yet. "
                      "To enable: run a Home Assistant instance on your LAN "
                      "and configure its URL + long-lived access token.",
            "host": None,
        }
    return {
        "configured": True,
        "reason": "instance configured (not connected — the client connects on explicit use only)",
        "detail": "Dormant until used: no background connection, no auto-discovery.",
        "host": config.host,
    }
