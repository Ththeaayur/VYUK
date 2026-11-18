"""Game servers: run, watch and talk to the owner's own game servers.

Parent-ruled (licence, 2026-11-11): MCSManager/MCSManager
(Apache-2.0 per the GitHub API license endpoint — the LICENSE file,
11,340 bytes, standard Apache License 2.0 text, read in full
2026-11-11 from the API endpoint at
https://api.github.com/repos/MCSManager/MCSManager/license, which
serves the file content; the raw download URL 404'd for the parent,
so the API endpoint is where it was found) is INSPIRATION ONLY. No
code, binary, schema, text or package is adopted from it; this module
is written from scratch in stdlib Python over the tree's own shapes
(core/desktop/control_shell.py's live-process registry + process-tree
kill, core/skills_admin_helpers.py's memory-facts registry). Ideas are
not copyrighted, so no Apache duty is triggered by a from-scratch
rebuild. The "MCSManager"/"MCSM" name is not used here: the tool is
the generic phrase "game_server", and no package, fork, service or
hosted offering is named after the original. No Node runtime is added
and there is no second app — only this module inside VYUK.

What this is, and what it is not — one verdict per MCSManager
capability:
- Minecraft server status query: BUILT. The public Server List Ping
  protocol, from scratch: handshake (protocol 767) + status request
  over TCP, JSON reply parsed for version, players online/max/sample
  and description; a ping round-trip measures latency. No Minecraft
  code or jar is involved — the protocol is a socket exchange.
- RCON console: BUILT. The public RCON packet protocol (shared by
  Minecraft and Valve Source servers): auth packet, exec packet,
  multi-packet response reassembly. The password is supplied per call,
  never stored (the registry payload has no password field), never
  echoed in any returned string, never logged.
- Steam server query: BUILT. The public A2S_INFO query (Valve Source
  engine query): UDP request, challenge round-trip, response parsed
  for name, map, game, players online/max and version.
- Game-server process start/stop/monitor: BUILT, operator-supplied
  only. VYUK starts, watches and stops a server the owner already has
  (their own Minecraft jar, their own Steam server installed with
  SteamCMD, or any other long-running server command). VYUK does NOT
  ship, download or install a server binary — the llama-cpp-python
  GGUF / rustic-binary precedent: operator-supplied, not shipped, not
  fetched by VYUK. The process runs in its own process group/session
  (the control_shell shape) and is registered in control_shell's
  live-process registry, so a failsafe trip kills it too.
- Server console / log reading: BUILT. The server's stdout is read by
  a daemon thread into a bounded ring (500 lines); 'console' reads the
  tail, 'send' writes one line to the server's stdin (how a Minecraft
  or Source console takes commands, including "stop"/"quit").
- One-click deployment / app marketplace: NOT BUILT. That half
  downloads server binaries (Mojang's Minecraft server is
  proprietary/EULA; Steam servers come via Valve's SteamCMD). If
  management cannot work without VYUK fetching a binary, that half
  is STOP, not a build — the owner supplies the server.
- Multi-user / distributed / hosting panel: NOT BUILT. That is the
  public/hosted shape (other people's accounts, other machines, a
  sales panel) — held back for owner rulings, the R16-7/R16-8/R17-1
  public-half precedent. This tool manages the owner's own servers on
  their own machine, single operator.
- Docker containers: NOT BUILT. No Docker in the product; the
  process half above is the desktop shape.

Nearest surfaces that are NOT this capability (gap proved first):
- core/gaming_detect_drive.py DETECTS installed games/launchers
  (Steam as a detected app) — detection, not management.
- core/desktop/control_shell.py run_command / start_background_command
  run one-shot or generic background jobs — a job has no server
  registry, no stdin (its stdin is DEVNULL, so a server console
  cannot be talked to), no query protocol and no console ring.
  That is why this module spawns through control_shell._spawn_shell
  itself (stdin_pipe=True — the one shell spawn site, auditfix3
  pins exactly one), which mirrors the run_command spawn except
  stdin is left a PIPE so the server console can be talked to.
- core/desktop/control_plus_desktop.py process_control lists
  processes and kills one by pid (confirm-gated) — inspection and
  killing, not starting, watching or talking to a server.
- core/skills_files_ops.py tail_file reads the tail of a file —
  a file tail, not a live server console.

Classification: the tool is effectful (read=False), confirm=True —
the task_scheduler_control / service_control / process_control
precedent: starting/stopping a server changes the machine, so the
executor-level human gate applies to the acting actions
(start/stop/restart/send/rcon) via an args-aware branch in
core/funnel/tools_gate.py; the reads (query/status/console/list)
and the registry writes (add/remove, VYUK's own store, the
bookings 'app-state' precedent) stay free. Unknown or missing
action gates fail-closed. The RCON password is per call only.
"""

import collections
import json
import re
import socket
import struct
import subprocess
import sys
import threading
import time

from .skills_admin_helpers import _load, _nonempty, _save, _store

_GAME_CATEGORY = "game_server"
_ACTIONS = ("add", "remove", "list", "start", "stop", "restart",
            "status", "query", "console", "send", "rcon")
_READ_ACTIONS = ("query", "status", "console", "list")
_GATE_ACTIONS = ("start", "stop", "restart", "send", "rcon")
_KINDS = ("minecraft", "steam", "generic")
_MAX_NAME_CHARS = 80
_MAX_LINES = 200
_DEFAULT_LINES = 50
_CONSOLE_RING = 500
_MINECRAFT_QUERY_PORT = 25565
_MINECRAFT_RCON_PORT = 25575
_STEAM_QUERY_PORT = 27015
_PROTOCOL_VERSION = 767  # Minecraft 1.21.1; status only needs any recent one


# --------------------------------------------------------------- registry ---

def _server_key(name: str) -> str:
    return (name or "").strip().lower()


def _load_servers():
    entries, broken = _load(_GAME_CATEGORY, {
        "name": _nonempty, "kind": lambda v: v in _KINDS,
        "command": _nonempty})
    return entries, broken


def _find_server(entries, name):
    want = _server_key(name)
    for entry in entries:
        if _server_key(entry.get("name")) == want:
            return entry
    return None


def _reread_server(name):
    entries, _broken = _load_servers()
    return _find_server(entries, name)


# ------------------------------------------------------------ live process ---

class _Running:
    """One server VYUK started: its process, console ring, thread."""

    def __init__(self, proc, command, cwd):
        self.proc = proc
        self.command = command
        self.cwd = cwd
        self.started_at = time.monotonic()
        self.lines = collections.deque(maxlen=_CONSOLE_RING)
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._reader, daemon=True)

    def _reader(self):
        try:
            for raw in self.proc.stdout:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                with self.lock:
                    self.lines.append(line)
        except Exception:  # noqa: BLE001 — reader death must not kill the tool
            pass

    def tail(self, count):
        with self.lock:
            return list(self.lines)[-max(0, count):]

    @property
    def alive(self):
        return self.proc.poll() is None


_LIVE = {}
_LIVE_LOCK = threading.Lock()


def _spawn_server(command, cwd):
    """Spawn a game server through control_shell._spawn_shell — the
    tree's ONE shell spawn site (auditfix3 pins exactly one) — with
    stdin left a PIPE instead of DEVNULL: a game server takes
    console input on stdin, including "stop", and a DEVNULL stdin
    could never be told anything. The process-group/session setup
    and live-process registration are _spawn_shell's own; this
    module adds nothing to the spawn shape. Imported lazily: this
    module must not pull the desktop control tree at import time.
    """
    from .desktop import control_shell as _cs
    return _cs._spawn_shell(command, cwd=cwd, merge_stderr=True,
                            stdin_pipe=True)


def _kill_tree(proc):
    """control_shell's process-tree kill, imported lazily (see above)."""
    from .desktop import control_shell as _cs
    try:
        _cs._kill_process_tree(proc)
    finally:
        try:
            _cs._unregister_live_proc(proc)
        except Exception:  # noqa: BLE001 — best effort; the kill matters
            pass


def _drop_live(name):
    with _LIVE_LOCK:
        return _LIVE.pop(_server_key(name), None)


def _get_live(name):
    with _LIVE_LOCK:
        return _LIVE.get(_server_key(name))


# ------------------------------------------------------------ small helpers ---

def _coerce_int(value, default, lo, hi):
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _coerce_port(value, default):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if 1 <= n <= 65535 else default


def _clean(text):
    return " ".join(str(text or "").split())


def _default_ports(kind):
    if kind == "minecraft":
        return _MINECRAFT_QUERY_PORT, _MINECRAFT_RCON_PORT
    if kind == "steam":
        return _STEAM_QUERY_PORT, _STEAM_QUERY_PORT
    return 0, 0


def _validate_name(name):
    name = _clean(name)
    if not name:
        return None, "Give the server a name."
    if len(name) > _MAX_NAME_CHARS:
        return None, f"Server name is too long ({len(name)} chars, max {_MAX_NAME_CHARS})."
    if not re.fullmatch(r"[A-Za-z0-9 _\-.]+", name):
        return None, ("Server name may only use letters, digits, spaces, "
                      "underscore, dot and dash.")
    return name, ""


# --------------------------------------------------- Minecraft Server List Ping ---

def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _read_varint(f):
    n = 0
    shift = 0
    while True:
        raw = f.read(1)
        if not raw:
            raise EOFError("connection closed while reading a varint")
        b = raw[0]
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n
        shift += 7
        if shift > 35:
            raise ValueError("varint too long")


def _mc_packet(payload):
    return _varint(len(payload)) + payload


def _mc_read_packet(f):
    length = _read_varint(f)
    if length < 1 or length > 1_000_000:
        raise ValueError(f"bad packet length {length}")
    data = f.read(length)
    if len(data) != length:
        raise EOFError("connection closed mid-packet")
    return data


def _mc_description(desc):
    """Plain text from a description that may be a string or a chat dict."""
    if isinstance(desc, str):
        return desc
    if isinstance(desc, dict):
        text = str(desc.get("text") or "")
        for part in desc.get("extra") or []:
            if isinstance(part, dict):
                text += str(part.get("text") or "")
            elif isinstance(part, str):
                text += part
        return text
    return ""


def minecraft_status(host, port, timeout=5.0):
    """(ok, result-or-error): Server List Ping against a Minecraft server.

    Handshake + status request, JSON reply; then a ping round-trip
    for latency. Claims only what the reply proves; the favicon is
    reported as present/absent, never returned (it is base64 bulk).
    """
    host = str(host or "").strip()
    port = _coerce_port(port, _MINECRAFT_QUERY_PORT)
    if not host:
        return False, "No host given for the Minecraft query."
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except (OSError, ValueError) as e:
        return False, f"Could not reach {host}:{port} ({e})."
    try:
        sock.settimeout(timeout)
        f = sock.makefile("rb")
        host_b = host.encode("utf-8", "replace")
        handshake = (b"\x00" + _varint(_PROTOCOL_VERSION)
                     + _varint(len(host_b)) + host_b
                     + struct.pack(">H", port) + _varint(1))
        sock.sendall(_mc_packet(handshake))
        sock.sendall(_mc_packet(b"\x00"))
        data = _mc_read_packet(f)
        f2 = _PacketReader(data)
        packet_id = f2.varint()
        if packet_id != 0:
            return False, f"Unexpected status packet id {packet_id}."
        payload = json.loads(f2.rest().decode("utf-8", "replace"))
        started = time.monotonic()
        ping_payload = struct.pack(">q", int(time.time() * 1000))
        sock.sendall(_mc_packet(b"\x01" + ping_payload))
        echo = _mc_read_packet(f)
        latency_ms = (time.monotonic() - started) * 1000.0
        if len(echo) < 9 or echo[0:1] != b"\x01":
            latency_ms = None
        version = payload.get("version") or {}
        players = payload.get("players") or {}
        sample = [str(p.get("name") or "") for p in
                  (players.get("sample") or []) if isinstance(p, dict)]
        return True, {
            "online": True,
            "version": str(version.get("name") or ""),
            "protocol": version.get("protocol"),
            "players_online": players.get("online"),
            "players_max": players.get("max"),
            "players_sample": [s for s in sample if s][:12],
            "description": _mc_description(payload.get("description")),
            "has_favicon": bool(payload.get("favicon")),
            "enforces_secure_chat": bool(payload.get("enforcesSecureChat")),
            "latency_ms": (round(latency_ms, 1)
                           if latency_ms is not None else None),
        }
    except (OSError, ValueError, EOFError, UnicodeDecodeError) as e:
        return False, f"Could not read a status reply from {host}:{port} ({e})."
    finally:
        try:
            sock.close()
        except Exception:  # noqa: BLE001 — close is best effort
            pass


class _PacketReader:
    """Varint-prefixed reads over one already-read packet's bytes."""

    def __init__(self, data):
        self._data = data
        self._pos = 0

    def varint(self):
        n = 0
        shift = 0
        while True:
            if self._pos >= len(self._data):
                raise EOFError("packet ended mid-varint")
            b = self._data[self._pos]
            self._pos += 1
            n |= (b & 0x7F) << shift
            if not b & 0x80:
                return n
            shift += 7
            if shift > 35:
                raise ValueError("varint too long")

    def rest(self):
        out = self._data[self._pos:]
        self._pos = len(self._data)
        return out


# ------------------------------------------------------------------- RCON ---

def _rcon_packet(req_id, ptype, payload):
    body = struct.pack("<ii", req_id, ptype) + payload + b"\x00\x00"
    return struct.pack("<i", len(body)) + body


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("connection closed mid-packet")
        buf += chunk
    return bytes(buf)


def _rcon_read_packet(sock):
    raw_len = _recv_exact(sock, 4)
    (length,) = struct.unpack("<i", raw_len)
    if length < 10 or length > 1_000_000:
        raise ValueError(f"bad RCON packet length {length}")
    body = _recv_exact(sock, length)
    req_id, ptype = struct.unpack("<ii", body[:8])
    return req_id, ptype, body[8:-2]


def rcon_command(host, port, password, command, timeout=5.0):
    """(ok, response-or-error): run one command over RCON.

    Auth first (a -1 id means the password was refused); then the
    exec packet. Long replies arrive split at 4096 bytes, so packets
    are read until a short one arrives or the socket idles — the
    reply is only the bytes the server sent. The password is never
    returned, in any path.
    """
    host = str(host or "").strip()
    port = _coerce_port(port, _MINECRAFT_RCON_PORT)
    if not host:
        return False, "No host given for RCON."
    if not password:
        return False, "RCON needs a password — give it per call; it is never stored."
    if not str(command or "").strip():
        return False, "No RCON command given."
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except (OSError, ValueError) as e:
        return False, f"Could not reach the RCON port at {host}:{port} ({e})."
    try:
        sock.settimeout(timeout)
        sock.sendall(_rcon_packet(1, 3, str(password).encode("utf-8")))
        req_id, _ptype, _payload = _rcon_read_packet(sock)
        if req_id == -1:
            return False, "RCON refused the password."
        sock.sendall(_rcon_packet(2, 2, str(command).encode("utf-8")))
        parts = []
        deadline = time.monotonic() + max(2.0, float(timeout))
        sock.settimeout(0.5)
        while True:
            try:
                req_id, _ptype, payload = _rcon_read_packet(sock)
            except socket.timeout:
                break
            if req_id == -1:
                return False, "RCON refused the command."
            parts.append(payload)
            if len(payload) < 4096:
                break
            if time.monotonic() >= deadline:
                break
        text = b"".join(parts).decode("utf-8", "replace")
        return True, text
    except (OSError, ValueError, EOFError) as e:
        return False, f"RCON failed at {host}:{port} ({e})."
    finally:
        try:
            sock.close()
        except Exception:  # noqa: BLE001 — close is best effort
            pass


# -------------------------------------------------------------- Steam A2S ---

def _a2s_cstr(data, pos):
    end = data.index(b"\x00", pos)
    return data[pos:end].decode("utf-8", "replace"), end + 1


def steam_query(host, port, timeout=5.0):
    """(ok, result-or-error): A2S_INFO against a Steam/Source server.

    UDP request + challenge round-trip, response parsed for name,
    map, game, players online/max and version. Claims only what the
    response proves.
    """
    host = str(host or "").strip()
    port = _coerce_port(port, _STEAM_QUERY_PORT)
    if not host:
        return False, "No host given for the Steam query."
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        # A connected UDP socket (not sendto/recvfrom): the lab
        # sandbox blocks unconnected UDP sendto, and the Source
        # query is a single-peer exchange anyway.
        sock.connect((host, port))
        request = b"\xff\xff\xff\xffTSource Engine Query\x00"
        sock.send(request)
        try:
            resp = sock.recv(4096)
        except socket.timeout:
            return False, f"No answer from {host}:{port} (timed out)."
        if len(resp) >= 9 and resp[:4] == b"\xff\xff\xff\xff" and resp[4:5] == b"A":
            sock.send(request + resp[5:9])
            try:
                resp = sock.recv(4096)
            except socket.timeout:
                return False, f"No answer from {host}:{port} after the challenge."
        if len(resp) < 5 or resp[:4] != b"\xff\xff\xff\xff" or resp[4:5] != b"I":
            return False, (f"{host}:{port} did not answer as a Source "
                           "server (unexpected reply type).")
        data = resp[5:]
        pos = 1  # protocol byte
        name, pos = _a2s_cstr(data, pos)
        game_map, pos = _a2s_cstr(data, pos)
        folder, pos = _a2s_cstr(data, pos)
        game, pos = _a2s_cstr(data, pos)
        if pos + 2 > len(data):
            raise ValueError("short A2S reply")
        (app_id,) = struct.unpack_from("<H", data, pos)
        pos += 2
        players, max_players, bots = data[pos], data[pos + 1], data[pos + 2]
        pos += 3
        server_type = chr(data[pos])
        environment = chr(data[pos + 1])
        visibility = data[pos + 2]
        vac = data[pos + 3]
        pos += 4
        version, pos = _a2s_cstr(data, pos)
        result = {
            "online": True,
            "name": name,
            "map": game_map,
            "folder": folder,
            "game": game,
            "app_id": app_id,
            "players_online": players,
            "players_max": max_players,
            "bots": bots,
            "server_type": server_type,
            "environment": environment,
            "password_protected": bool(visibility),
            "vac_secured": bool(vac),
            "version": version,
        }
        if pos < len(data):
            edf = data[pos]
            pos += 1
            if edf & 0x80 and pos + 2 <= len(data):
                (game_port,) = struct.unpack_from("<H", data, pos)
                result["game_port"] = game_port
                pos += 2
            if edf & 0x10 and pos + 8 <= len(data):
                pos += 8
            if edf & 0x40 and pos + 2 <= len(data):
                pos += 2
                _tv_name, pos = _a2s_cstr(data, pos)
            if edf & 0x20 and pos < len(data):
                keywords, _pos = _a2s_cstr(data, pos)
                result["keywords"] = keywords
        return True, result
    except (OSError, ValueError, IndexError) as e:
        return False, f"Steam query failed at {host}:{port} ({e})."
    finally:
        try:
            sock.close()
        except Exception:  # noqa: BLE001 — close is best effort
            pass


# ------------------------------------------------------------------ handler ---

def _query_for(entry, timeout):
    kind = entry.get("kind")
    host = str(entry.get("host") or "").strip()
    port = _coerce_port(entry.get("port"), 0)
    if kind == "minecraft":
        return minecraft_status(host, port or _MINECRAFT_QUERY_PORT, timeout)
    if kind == "steam":
        return steam_query(host, port or _STEAM_QUERY_PORT, timeout)
    return False, ("This server's kind ('generic') has no query "
                   "protocol — 'status' reports whether its process "
                   "is running instead.")


def _stop_running(name, entry, timeout):
    """Stop a live server; returns (stopped_bool, message)."""
    running = _get_live(name)
    if running is None:
        return False, (f"'{name}' is not running under VYUK — it may "
                       "still be running outside VYUK; check with "
                       "'query' or 'status'. Nothing was stopped.")
    kind = entry.get("kind")
    proc = running.proc
    graceful = None
    if kind == "minecraft":
        graceful = "stop"
    elif kind == "steam":
        graceful = "quit"
    if graceful:
        try:
            proc.stdin.write((graceful + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (OSError, ValueError, BrokenPipeError):
            pass
        deadline = time.monotonic() + (10.0 if kind == "minecraft" else 5.0)
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
    if proc.poll() is None:
        _kill_tree(proc)
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 — the kill is what matters
            pass
    _drop_live(name)
    if proc.poll() is None:
        return False, (f"'{name}' was sent the stop signal but is still "
                       "running (pid "
                       f"{proc.pid}) — it may be ignoring the request.")
    return True, f"'{name}' stopped (pid {proc.pid})."


def game_server(action: str = "", server: str = "", kind: str = "",
                command: str = "", working_dir: str = "", host: str = "",
                port: int = 0, rcon_host: str = "", rcon_port: int = 0,
                rcon_password: str = "", lines: int = 0,
                timeout_seconds: float = 0) -> str:
    """Manage the owner's own game servers (Minecraft / Steam / generic).

    Actions: 'add' saves a server the owner already has (name + kind
    + the command that starts it + where it lives + its query/RCON
    ports); 'remove' forgets one; 'list' shows saved servers;
    'start' launches one (human-confirmed, executor gate);
    'stop'/'restart' stop one (human-confirmed); 'status' reports
    whether its process is running under VYUK plus a best-effort
    query; 'query' asks the server itself (Minecraft Server List
    Ping, or Steam A2S); 'console' reads the tail of its captured
    output; 'send' writes one line to its console stdin
    (human-confirmed); 'rcon' runs one command over RCON
    (human-confirmed; password per call, never stored or echoed).
    The server binary is operator-supplied: VYUK never downloads,
    installs or ships one.
    """
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return (f"Unknown game-server action: '{action}'. Use one of: "
                + ", ".join(_ACTIONS) + ".")
    timeout = _coerce_int(timeout_seconds, 5, 1, 30)

    if action == "list":
        entries, broken = _load_servers()
        if not entries and not broken:
            return "No game servers saved yet. Use 'add' with a name, kind and the command that starts the server you already have."
        out = []
        for entry in entries:
            running = _get_live(entry.get("name"))
            state = ("running, pid "
                     f"{running.proc.pid}" if running is not None
                     else "not running under VYUK")
            out.append(f"- {entry.get('name')} ({entry.get('kind')}): "
                       f"{state}; query {entry.get('host') or '—'}:"
                       f"{entry.get('port') or '—'}")
        if broken:
            out.append(f"({broken} saved server record(s) could not be read.)")
        return "\n".join(out)

    name, name_err = _validate_name(server)
    if action == "add":
        if name_err:
            return name_err
        kind = (kind or "").strip().lower()
        if kind not in _KINDS:
            return ("'kind' must be 'minecraft', 'steam' or 'generic' — "
                    f"got '{kind}'.")
        if not _nonempty(command):
            return ("'command' is the command that starts the server "
                    "you already have — VYUK never downloads or "
                    "installs a server binary.")
        cwd = _clean(working_dir)
        if cwd:
            import os as _os
            if not _os.path.isdir(cwd):
                return f"Working directory does not exist: {cwd}"
        host = _clean(host) or "127.0.0.1"
        qport, rport = _default_ports(kind)
        port_n = _coerce_port(port, qport)
        rcon_host_n = _clean(rcon_host) or host
        rcon_port_n = _coerce_port(rcon_port, rport)
        entries, _broken = _load_servers()
        if _find_server(entries, name) is not None:
            return (f"A server named '{name}' is already saved — "
                    "'remove' it first to replace it.")
        payload = {"name": name, "kind": kind,
                   # The launch command is stored verbatim (stripped
                   # only): _clean would collapse its newlines and
                   # break multi-line commands.
                   "command": str(command).strip(),
                   "working_dir": cwd, "host": host, "port": port_n,
                   "rcon_host": rcon_host_n, "rcon_port": rcon_port_n}
        _save(_GAME_CATEGORY, _server_key(name), payload)
        again = _reread_server(name)
        if again is None:
            return (f"Server '{name}' was written but the re-read did "
                    "not find it — nothing was saved.")
        return (f"Saved server '{name}' ({kind}). VYUK will start it "
                f"with the command you gave, in {cwd or 'the current folder'}; "
                f"query {host}:{port_n or '—'}, RCON {rcon_host_n}:"
                f"{rcon_port_n or '—'}. The server itself is yours — "
                "VYUK never downloads one.")

    if name_err:
        return name_err
    entries, _broken = _load_servers()
    entry = _find_server(entries, name)
    if entry is None:
        return (f"No saved server named '{name}'. Use 'list' to see "
                "the saved servers, or 'add' to save one.")

    if action == "remove":
        running = _get_live(name)
        if running is not None:
            return (f"'{name}' is still running (pid "
                    f"{running.proc.pid}) — 'stop' it first, then remove it.")
        fact = _store().get_fact(_GAME_CATEGORY, _server_key(name))
        if fact is None or not _store().delete_fact(fact["id"]):
            return f"Could not remove '{name}' — it was not in the store."
        if _reread_server(name) is not None:
            return (f"'{name}' was deleted but the re-read still finds "
                    "it — it was not removed.")
        return f"Removed server '{name}'."

    if action == "start":
        if _get_live(name) is not None:
            running = _get_live(name)
            return (f"'{name}' is already running (pid "
                    f"{running.proc.pid}).")
        cwd = entry.get("working_dir") or None
        if cwd:
            import os as _os
            if not _os.path.isdir(cwd):
                return f"Working directory no longer exists: {cwd}"
        try:
            proc = _spawn_server(entry.get("command"), cwd)
        except Exception as e:  # noqa: BLE001 — spawn failure is a refusal
            return f"Could not start '{name}': {e}"
        running = _Running(proc, entry.get("command"), cwd or "")
        with _LIVE_LOCK:
            _LIVE[_server_key(name)] = running
        running.thread.start()
        # A process that dies at once must not be claimed started:
        # watch for an instant exit before reporting.
        deadline = time.monotonic() + 0.5
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if proc.poll() is not None:
            _drop_live(name)
            tail = running.tail(5)
            detail = ("\n" + "\n".join(tail)) if tail else ""
            return (f"'{name}' exited immediately (code "
                    f"{proc.returncode}) — it did not stay running."
                    f"{detail}")
        return (f"Started '{name}' (pid {proc.pid}). It is still "
                "booting — use 'status' or 'query' to check it, and "
                "'console' to read what it prints.")

    if action == "stop":
        stopped, message = _stop_running(name, entry, timeout)
        return message

    if action == "restart":
        stopped, message = _stop_running(name, entry, timeout)
        if not stopped and "not running under VYUK" not in message:
            return message
        if "not running under VYUK" in message:
            pass  # nothing to stop; start it anyway
        cwd = entry.get("working_dir") or None
        try:
            proc = _spawn_server(entry.get("command"), cwd)
        except Exception as e:  # noqa: BLE001 — spawn failure is a refusal
            return f"Could not restart '{name}': {e}"
        running = _Running(proc, entry.get("command"), cwd or "")
        with _LIVE_LOCK:
            _LIVE[_server_key(name)] = running
        running.thread.start()
        deadline = time.monotonic() + 0.5
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if proc.poll() is not None:
            _drop_live(name)
            return (f"'{name}' was stopped, but the restart exited "
                    f"immediately (code {proc.returncode}).")
        return f"Restarted '{name}' (pid {proc.pid})."

    if action == "status":
        running = _get_live(name)
        if running is not None and not running.alive:
            _drop_live(name)
            running = None
        if running is None:
            base = f"'{name}' is not running under VYUK."
            if entry.get("kind") == "generic":
                return base + " (Kind 'generic' has no query protocol.)"
            ok, result = _query_for(entry, timeout)
            if ok:
                return (base + " But its query port answers: "
                        f"{result.get('players_online')}/"
                        f"{result.get('players_max')} players online "
                        f"({result.get('name') or result.get('version') or ''}). "
                        "It may be running outside VYUK.")
            return base + " Its query port does not answer either."
        uptime = int(time.monotonic() - running.started_at)
        out = (f"'{name}' is running (pid {running.proc.pid}, up "
               f"{uptime}s, {len(running.tail(_CONSOLE_RING))} console "
               "lines captured).")
        ok, result = _query_for(entry, timeout)
        if ok:
            out += (f" Query answers: "
                    f"{result.get('players_online')}/"
                    f"{result.get('players_max')} players online.")
        else:
            out += f" Query does not answer yet: {result}"
        return out

    if action == "query":
        ok, result = _query_for(entry, timeout)
        if not ok:
            return result
        lines = [f"'{name}' answers ({entry.get('kind')}):"]
        for key in ("version", "name", "map", "game",
                    "players_online", "players_max", "bots",
                    "description", "latency_ms"):
            if result.get(key) not in (None, ""):
                lines.append(f"  {key}: {result[key]}")
        sample = result.get("players_sample") or []
        if sample:
            lines.append("  players: " + ", ".join(sample))
        return "\n".join(lines)

    if action == "console":
        running = _get_live(name)
        if running is None:
            return (f"'{name}' is not running under VYUK, so there is "
                    "no console to read — start it first.")
        count = _coerce_int(lines, _DEFAULT_LINES, 1, _MAX_LINES)
        tail = running.tail(count)
        if not tail:
            return f"'{name}' has printed nothing yet."
        return (f"Last {len(tail)} line(s) from '{name}':\n"
                + "\n".join(tail))

    if action == "send":
        running = _get_live(name)
        if running is None:
            return (f"'{name}' is not running under VYUK, so there is "
                    "no console to write to — start it first.")
        line = _clean(command)
        if not line:
            return "Nothing to send — give the console line in 'command'."
        try:
            running.proc.stdin.write((line + "\n").encode("utf-8"))
            running.proc.stdin.flush()
        except (OSError, ValueError, BrokenPipeError) as e:
            return f"Could not write to '{name}' console ({e})."
        return f"Sent to '{name}' console: {line}"

    if action == "rcon":
        rhost = _clean(rcon_host) or str(entry.get("rcon_host") or "")
        rport = _coerce_port(rcon_port, entry.get("rcon_port") or 0)
        if not rhost or not rport:
            return (f"'{name}' has no RCON port saved — add it with an "
                    "RCON host/port, or give rcon_host/rcon_port here.")
        cmd = _clean(command)
        if not cmd:
            return "No RCON command given — put it in 'command'."
        ok, result = rcon_command(rhost, rport, rcon_password, cmd, timeout)
        if not ok:
            return result
        if not result:
            return f"RCON on '{name}' ran '{cmd}' and returned nothing."
        if len(result) > 4000:
            result = result[:4000] + "\n… (truncated)"
        return f"RCON on '{name}' ('{cmd}'):\n{result}"

    return f"Unknown game-server action: '{action}'."  # pragma: no cover
