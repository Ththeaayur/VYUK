"""VYUK <-> native-host localhost channel (click-anywhere Batch 1,
Plan 9 §1.2, N-3/P-9/Q-2/Q-9/Q-10/R-23/R-24).

Own code — nothing adopted (Parent-ruled (licence, 2026-11-14)).

A dedicated ``ThreadingHTTPServer`` in VYUK's main process, bound to
127.0.0.1 ONLY, following the companion-server pattern
(``core/companion.py``: ``ThreadingHTTPServer`` at :125/:441, the
``_pick_port`` scan shape at :315-321, random 32-byte tokens per start
at :45, the named daemon thread at :470-474).

Rendezvous: ``<app data>/browser_host.json`` — ``{port, token, pid,
started}`` — written at EVERY start with a fresh per-start random
32-byte token. The path is resolved by the tree's app-data pattern
(``os.environ.get("APPDATA") or str(Path.home() / "AppData" /
"Roaming")``) — never from the HKCU key.

Trust boundary (R-24, stated honestly): the rendezvous file's
plaintext token IS a new on-disk secret. It is per-start (a stolen
token dies at the next start), localhost-only, and covered by the
same-user local-machine trust boundary as the ledger, the token
stores and the config in the same app-data dir. A restrictive file
ACL (current user only) is a NAMED hardening follow-up, not v1.

State lives in the main process; the host is stateless. The goal
store is in-memory only — approval dies with the process, so a
restart re-gates (Q-2).

Endpoints (every request carries the token in the
``X-VYUK-Browser-Token`` header; mismatch -> 403):

- POST ``/handshake`` — request ``{v, host_id, extension_id,
  dpi_awareness}``; response ``{v: 1, goal, scope, state_version,
  dpi_awareness}`` echoing the level. Refuses (403) a handshake with
  no level or a level below per-monitor-aware. ``"unknown"`` is
  accepted: on Linux there is no per-monitor DPI-awareness rung to
  report, and the host records the level honestly as ``"unknown"``
  rather than faking one.
- GET ``/events`` — 30 s long-poll: responds immediately when a
  command is queued, else ``{"v": 1, "command": null}`` on timeout.
- POST ``/observation`` — body ``{v, observation_pass_id, ...}``;
  updates the goal's in-memory ``latest_pass_id``.
- GET ``/goal`` — ``{goal, state_version, latest_pass_id}``; the live
  goal record (state ``"running" | "paused"``) or ``null`` when no
  goal is running. A PAUSED goal is live.
"""

import json
import os
import secrets
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_TOKEN_HEADER = "X-VYUK-Browser-Token"
_DEFAULT_PORT = 49152
_PORT_SCAN_END = 49252
_LONG_POLL_S = 30.0
_PAUSED_ALARM_EXPIRY_S = 30 * 60  # R-7: paused > 30 min -> alarm cleared

# DPI-awareness levels, weakest first. "unknown" is not a Windows
# level at all (Linux host) — it is accepted, never faked.
_DPI_LEVELS_BELOW_PER_MONITOR = {"unaware", "system"}
_DPI_LEVELS_ACCEPTED = {"per-monitor-v1", "per-monitor-v2", "unknown"}


def _app_data_dir() -> Path:
    return Path(os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming"))


def rendezvous_path() -> Path:
    """Full path of the rendezvous file (app data)."""
    return _app_data_dir() / "browser_host.json"


def _parse_updated_at(value):
    """Pause time as an epoch float, from an epoch number or an ISO
    string (``task_ledger._now()`` writes ISO); None when unparsable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return None
    return None


def alarm_clear_decision(goal_record):
    """Orphaned-alarm clearer (N-19, R-7).

    ``goal_record`` is the record GET /goal would return (or None).
    Returns ``"clear"`` or ``"keep"``:

    - no goal record -> ``"clear"`` (the goal ended);
    - live running goal -> ``"keep"``;
    - paused goal paused <= 30 min -> ``"keep"`` (the goal may resume
      and re-gate; the alarm is its wake);
    - paused goal paused > 30 min -> ``"clear"`` (pause time is the
      record's ``updated_at`` — the pause write is the last upsert).

    Expiry clears the ALARM, never the goal: the record stays paused,
    and the resume path re-creates the alarm. A paused record with no
    usable ``updated_at`` is kept — the expiry must be proven, never
    guessed.
    """
    if goal_record is None:
        return "clear"
    state = (goal_record or {}).get("state")
    if state == "running":
        return "keep"
    if state == "paused":
        paused_at = _parse_updated_at(goal_record.get("updated_at"))
        if paused_at is None:
            return "keep"
        if time.time() - paused_at > _PAUSED_ALARM_EXPIRY_S:
            return "clear"
        return "keep"
    # Unknown state: keep — never clear on a record we cannot read.
    return "keep"


class BrowserHostChannel:
    """The localhost channel server. Owns the port, the per-start
    token, the rendezvous file, the command queue and the in-memory
    goal record."""

    def __init__(self):
        self.server = None
        self.thread = None
        self.port = None
        self.token = None
        self.started_at = None
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._commands = []
        self._goal = None
        self._state_version = 0
        self._latest_pass_id = None

    # -- port pick (companion _pick_port shape) -----------------------
    @staticmethod
    def _pick_port(start: int):
        """First free port in [start, _PORT_SCAN_END], else None."""
        for port in range(max(1024, int(start)), _PORT_SCAN_END + 1):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
            finally:
                sock.close()
        return None

    # -- lifecycle ----------------------------------------------------
    def start(self):
        """Bind 127.0.0.1, write the rendezvous file with a fresh
        token, serve on the named daemon thread. Returns
        ``(True, "listening on 127.0.0.1:<port>")`` or ``(False, reason
        naming the port range)``."""
        with self._lock:
            if self.server is not None:
                return True, f"already listening on 127.0.0.1:{self.port}"
            chosen = self._pick_port(_DEFAULT_PORT)
            if chosen is None:
                return False, (
                    f"Could not listen on 127.0.0.1 (ports {_DEFAULT_PORT}-"
                    f"{_PORT_SCAN_END} are all taken). Free one and try again."
                )
            token = secrets.token_hex(32)  # 32 random bytes, hex

            class _Server(ThreadingHTTPServer):
                daemon_threads = True
                allow_reuse_address = True

            server = None
            bound_port = chosen
            for candidate in range(chosen, _PORT_SCAN_END + 1):
                try:
                    server = _Server(("127.0.0.1", candidate), _ChannelHandler)
                    bound_port = candidate
                    break
                except OSError:
                    server = None
                    continue
            if server is None:
                return False, (
                    f"Could not listen on 127.0.0.1 (ports {chosen}-"
                    f"{_PORT_SCAN_END} are all taken). Free one and try again."
                )
            server.channel = self  # type: ignore[attr-defined]
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.2},
                name="vyuk-browser-host", daemon=True)
            self.server = server
            self.thread = thread
            self.port = bound_port
            self.token = token
            self.started_at = time.time()
            self._commands = []
            thread.start()
            self._write_rendezvous()
            return True, f"listening on 127.0.0.1:{bound_port}"

    def stop(self):
        """Stop serving. The rendezvous file is left in place — a
        restart rewrites it with a fresh token; a stale file is what
        the host's rotation path re-reads."""
        with self._lock:
            server, thread = self.server, self.thread
            self.server = None
            self.thread = None
            self.port = None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:  # noqa: BLE001 — stop is best-effort
                pass
        if thread is not None:
            thread.join(timeout=2.0)

    def _write_rendezvous(self):
        path = rendezvous_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "port": self.port,
            "token": self.token,
            "pid": os.getpid(),
            "started": self.started_at,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    # -- in-process API (main process / tests) ------------------------
    def queue_command(self, cmd):
        """Queue a command for the host's next GET /events poll."""
        with self._cond:
            self._commands.append(cmd)
            self._cond.notify_all()

    def set_goal(self, goal_dict):
        """Store the live goal record (a copy). Bumps state_version.
        ``None`` clears the goal (goal ended)."""
        with self._lock:
            self._goal = dict(goal_dict) if goal_dict is not None else None
            if goal_dict is None:
                self._latest_pass_id = None
            self._state_version += 1

    def get_goal(self):
        """A copy of the live goal record, or None when no goal is running."""
        with self._lock:
            return dict(self._goal) if self._goal is not None else None

    @property
    def latest_pass_id(self):
        with self._lock:
            return self._latest_pass_id

    @property
    def state_version(self):
        with self._lock:
            return self._state_version

    def alarm_decision(self):
        """The orphaned-alarm decision for the current goal record."""
        return alarm_clear_decision(self.get_goal())

    # -- internals used by the handler --------------------------------
    def _take_command(self, timeout=_LONG_POLL_S):
        """Wait up to ``timeout`` for a queued command; the command or
        None on timeout. Responds immediately when one is queued."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                if self._commands:
                    return self._commands.pop(0)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=min(remaining, 0.5))

    def _note_observation(self, observation_pass_id):
        with self._lock:
            if observation_pass_id is not None:
                self._latest_pass_id = observation_pass_id


class _ChannelHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence default stderr logging
        pass

    # -- helpers ------------------------------------------------------
    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _check_token(self):
        channel = self.server.channel  # type: ignore[attr-defined]
        presented = self.headers.get(_TOKEN_HEADER)
        if not presented or presented != channel.token:
            self._send_json(403, {"v": 1, "error": "host token mismatch — restart VYUK"})
            return False
        return True

    # -- endpoints ----------------------------------------------------
    def do_POST(self):
        channel = self.server.channel  # type: ignore[attr-defined]
        if self.path == "/handshake":
            if not self._check_token():
                return
            body = self._read_json()
            if body is None:
                self._send_json(400, {"v": 1, "error": "handshake body must be a JSON object"})
                return
            if body.get("v") != 1:
                self._send_json(400, {"v": 1, "error": "handshake v must be 1"})
                return
            level = body.get("dpi_awareness")
            if not level or level not in _DPI_LEVELS_ACCEPTED:
                self._send_json(403, {
                    "v": 1,
                    "error": (
                        "dpi_awareness below per-monitor-aware — browser goals "
                        "need a per-monitor-aware host "
                        f"(got {level!r})"
                    ),
                })
                return
            goal = channel.get_goal()
            self._send_json(200, {
                "v": 1,
                "goal": goal,
                "scope": (goal or {}).get("scope") if goal else None,
                "state_version": channel.state_version,
                "dpi_awareness": level,
            })
            return
        if self.path == "/observation":
            if not self._check_token():
                return
            body = self._read_json()
            if body is None:
                self._send_json(400, {"v": 1, "error": "observation body must be a JSON object"})
                return
            if body.get("v") != 1:
                self._send_json(400, {"v": 1, "error": "observation v must be 1"})
                return
            channel._note_observation(body.get("observation_pass_id"))
            self._send_json(200, {"v": 1, "latest_pass_id": channel.latest_pass_id})
            return
        self._send_json(404, {"v": 1, "error": f"unknown endpoint {self.path}"})

    def do_GET(self):
        channel = self.server.channel  # type: ignore[attr-defined]
        if self.path == "/events":
            if not self._check_token():
                return
            cmd = channel._take_command(timeout=_LONG_POLL_S)
            self._send_json(200, {"v": 1, "command": cmd})
            return
        if self.path == "/goal":
            if not self._check_token():
                return
            self._send_json(200, {
                "v": 1,
                "goal": channel.get_goal(),
                "state_version": channel.state_version,
                "latest_pass_id": channel.latest_pass_id,
            })
            return
        self._send_json(404, {"v": 1, "error": f"unknown endpoint {self.path}"})
