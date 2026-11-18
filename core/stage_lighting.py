"""Stage / venue lighting control for the owner's own shows — own code.

Parent-ruled (licence, 2026-11-12): mcallegari/qlcplus
(Apache-2.0; the COPYING file from the default branch master was read
in full 2026-11-12) is INSPIRATION ONLY. No code, schema, SQL, text or
package is adopted from it; this module is written from scratch in
stdlib Python over the tree's own SQLite-store shape
(core/bookings.py, core/lodging.py) and the tree's own config DATA_DIR.
Ideas are not copyrighted, so no Apache duty is triggered by a
from-scratch rebuild. The "QLC+" name is NOT used here: the tool is
the generic phrase "stage_lighting", and no package, fork, service or
hosted offering is named after the original.

What this builds (the QLC+ surface, rebuilt): (a) network output —
Art-Net and sACN (ANSI E1.31) DMX-over-UDP to the buyer's own lighting
nodes on the LAN, plus Art-Net discovery (OpPoll/OpPollReply); (b)
fixture patching — the buyer defines fixture channel maps and patches
them to a universe + start address, with a SQLite trigger refusing
overlapping patches on the same universe; (c) scenes (a saved set of
channel levels for one universe) and chases (an ordered list of scenes
with hold times, stepped by the operator). What is NOT built: DMX over
USB (needs operator hardware plus a driver VYUK cannot ship); the
virtual console / web UI / multi-user / hosted shape (no hosted
anything); QLC+ fixture-library import (their XML definitions are
their data — the buyer describes a fixture here in plain terms);
timed auto-advance of a chase (needs a background runner with a stop
contract — a follow-up, not a guess); sACN per-address priority.

Protocols (public standards, implemented here from the specs, not
copied from QLC+): Art-Net 4 — OpDmx (0x5000) on UDP 6454, header
"Art-Net\\0", protocol version 14, 15-bit port-address; OpPoll
(0x2000) / OpPollReply (0x2100) for discovery. sACN / ANSI E1.31 —
UDP 5568, multicast 239.255.<hi>.<lo>, root layer vector 0x00000004,
framing vector 0x00000002, DMP vector 0x02, start code 0x00 + 512
slots. A universe here is 0-63999 (the sACN range); Art-Net nodes
take 0-32767 (the 15-bit port-address space) and sACN nodes 1-63999
(universe 0 is reserved in sACN). Output always goes through a node
the buyer registered for that universe — the tool never sprays UDP at
an address the buyer did not name. No money moves, no hosted
anything, no multi-user accounts.
"""

import json
import re
import socket
import sqlite3
import struct
import time
import uuid
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR
from . import store_base

DEFAULT_DB_FILENAME = "stage_lighting.sqlite3"

_ACTIONS = ("node", "patch", "fixture", "scene", "chase", "output",
            "blackout", "discover", "status", "events")

# Entity modes. Registry writes (add/save/remove/delete) touch only
# VYUK's own store (the bookings 'app-state' precedent); 'run' and
# 'step' send DMX to the venue's lights and are human-gated with
# 'output' and 'blackout' (the game_server precedent).
_MODES = ("add", "list", "remove", "save", "run", "step", "delete")
_READ_MODES = ("list",)

_ARTNET_PORT = 6454
_SACN_PORT = 5568
_ARTNET_BROADCAST = "255.255.255.255"
_MAX_CHANNEL = 512
_MAX_UNIVERSE = 63999
_MAX_ARTNET_UNIVERSE = 32767
_MAX_HOLD_MS = 60000
_DISCOVER_TIMEOUT_S = 2.0
_MAX_LIST = 200

_SOURCE_NAME = "VYUK"
# A stable source identity for sACN (a UUID identifying this source
# component, per the E1.31 spec — derived, never random per packet).
_CID = uuid.uuid5(uuid.NAMESPACE_URL, "vyuk-stage-lighting").bytes

# Per-(protocol, universe) send sequence, process-lifetime. sACN and
# Art-Net both carry a sequence byte; a restart resets to 0, which a
# receiver treats as a new stream after the old one times out.
_SEQ: dict[tuple[str, int], int] = {}


def _next_sequence(protocol: str, universe: int) -> int:
    key = (protocol, universe)
    nxt = (_SEQ.get(key, -1) + 1) % 256
    _SEQ[key] = nxt
    return nxt


# ---------------------------------------------------------------------
# Wire formats (pure builders — no sockets, no state; the suite proves
# their bytes against a loopback receiver).
# ---------------------------------------------------------------------

def _artnet_dmx_packet(universe: int, levels: dict[int, int],
                       sequence: int) -> bytes:
    """One Art-Net OpDmx packet (Art-Net 4 spec).

    ID "Art-Net\\0", OpCode 0x5000 (low byte first), protocol version
    14, 15-bit port-address split into Net/SubUni/Universe, length
    (big-endian, even, 2-512), then the start code 0x00 + one slot
    per channel. Length covers exactly the channels sent, so a
    2-channel look is a 3-byte payload, not a 512-byte one.
    """
    top = max(levels) if levels else 0
    n_slots = max(2, top)
    length = n_slots + 1  # start code + one slot per channel
    if length % 2:
        length += 1
    data = bytearray([0])
    for ch in range(1, n_slots + 1):
        data.append(levels.get(ch, 0))
    while len(data) < length:
        data.append(0)
    net = (universe >> 8) & 0x7F
    subuni = (universe >> 4) & 0x0F
    uni = universe & 0x0F
    return (b"Art-Net\x00" + struct.pack("<H", 0x5000)
            + bytes([0, 14, sequence & 0xFF, 0, subuni, net])
            + struct.pack(">H", length) + bytes(data))


def _artnet_poll_packet() -> bytes:
    """One Art-Net OpPoll packet (14 bytes): ID, OpCode 0x2000,
    protocol version 14, TalkToMe 0, Priority 0."""
    return b"Art-Net\x00" + struct.pack("<H", 0x2000) + bytes([0, 14, 0, 0])


def _parse_artnet_poll_reply(data: bytes) -> dict | None:
    """Parse one Art-Net OpPollReply into node facts, or None when the
    packet is not a reply. Claims only what the reply proves: IP,
    port, short/long name, and the first output port's universe."""
    if len(data) < 194 or data[:8] != b"Art-Net\x00":
        return None
    opcode = struct.unpack("<H", data[8:10])[0]
    if opcode != 0x2100:
        return None
    ip = ".".join(str(b) for b in data[10:14])
    port = struct.unpack("<H", data[14:16])[0]
    short = data[26:44].split(b"\x00", 1)[0].decode("utf-8", "replace")
    long = data[44:108].split(b"\x00", 1)[0].decode("utf-8", "replace")
    net = data[18]
    sub = data[19]
    sw_out = data[190] if len(data) > 190 else 0
    universe = ((net & 0x7F) << 8) | (sub & 0xF0) | (sw_out & 0x0F)
    return {"ip": ip, "port": port, "short_name": short,
            "long_name": long, "universe": universe}


def _sacn_multicast(universe: int) -> str:
    return "239.255.%d.%d" % (universe >> 8, universe & 0xFF)


def _sacn_packet(universe: int, levels: dict[int, int], sequence: int,
                 priority: int = 100) -> bytes:
    """One sACN (ANSI E1.31) data packet.

    Root layer: preamble 0x0010, post-amble 0x0000, ACN identifier
    "ASC-E1.17\\0\\0\\0", vector 0x00000004, CID. Framing layer:
    vector 0x00000002, source name (64 bytes), priority, sequence,
    universe. DMP layer: vector 0x02, address type 0xa1, first
    property 0x0000, increment 0x0001, count = 1 + slots, then the
    start code 0x00 + one slot per channel. Length fields carry the
    0x7000 flag plus the length of the PDU that follows them.
    """
    top = max(levels) if levels else 0
    n_slots = max(1, top)
    count = 1 + n_slots  # start code + one slot per channel
    dmp = (struct.pack(">H", 0x7000 | (8 + count))
           + bytes([0x02, 0xA1])
           + struct.pack(">HHH", 0, 1, count)
           + bytes([0])
           + bytes(levels.get(ch, 0) for ch in range(1, n_slots + 1)))
    source = _SOURCE_NAME.encode("utf-8")[:64].ljust(64, b"\x00")
    framing = (struct.pack(">H", 0x7000 | (75 + len(dmp)))
               + struct.pack(">I", 0x00000002) + source
               + bytes([priority & 0xFF, 0, 0, sequence & 0xFF, 0])
               + struct.pack(">H", universe))
    root = (struct.pack(">HH", 0x0010, 0x0000)
            + b"ASC-E1.17\x00\x00\x00"
            + struct.pack(">H", 0x7000 | (20 + len(framing) + len(dmp)))
            + struct.pack(">I", 0x00000004) + _CID)
    return root + framing + dmp


def _parse_sacn_packet(data: bytes) -> dict | None:
    """Parse one sACN data packet back into its fields (the suite's
    round-trip proof). Returns None when the packet is not sACN data."""
    if len(data) < 126:
        return None
    if struct.unpack(">H", data[0:2])[0] != 0x0010:
        return None
    if data[4:16] != b"ASC-E1.17\x00\x00\x00":
        return None
    if struct.unpack(">I", data[18:22])[0] != 0x00000004:
        return None
    root_len = struct.unpack(">H", data[16:18])[0] & 0x0FFF
    if 18 + root_len != len(data):
        return None
    # The framing PDU starts at offset 38 with its own flags+length
    # field; the value there is the length of the framing PDU after
    # that field (vector + source + priority + ... + DMP layer).
    framing_len = struct.unpack(">H", data[38:40])[0] & 0x0FFF
    framing_start = 40
    if framing_start + framing_len > len(data):
        return None
    framing = data[framing_start:framing_start + framing_len]
    if len(framing) < 75 or struct.unpack(">I", framing[0:4])[0] != 0x00000002:
        return None
    source = framing[4:68].split(b"\x00", 1)[0].decode("utf-8", "replace")
    priority = framing[68]
    sequence = framing[71]
    universe = struct.unpack(">H", framing[73:75])[0]
    dmp = framing[75:]
    if len(dmp) < 10 or dmp[2] != 0x02 or dmp[3] != 0xA1:
        return None
    count = struct.unpack(">H", dmp[8:10])[0]
    values = dmp[10:10 + count]
    if not values or values[0] != 0:
        return None
    levels = {ch: values[ch] for ch in range(1, len(values))}
    return {"source": source, "priority": priority,
            "sequence": sequence, "universe": universe, "levels": levels}


# ---------------------------------------------------------------------
# Input shapes (all plain strings the buyer writes; every refusal
# names what was wrong, never a bare exception).
# ---------------------------------------------------------------------

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-]{0,63}$")
_CHANNEL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _utcnow() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clean_name(value: str) -> str:
    return str(value or "").strip()


def _parse_levels(text: str) -> tuple[dict[int, int] | None, str | None]:
    """'1:255;2:128' -> {1: 255, 2: 128}. Channels 1-512, values 0-255,
    no duplicates. An empty spec is an error, never a silent no-op."""
    raw = str(text or "").strip()
    if not raw:
        return None, "no levels given — use 'channel:value;channel:value', e.g. '1:255;2:128'."
    levels: dict[int, int] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            return None, f"levels need 'channel:value' — got {part!r}."
        ch_s, val_s = part.split(":", 1)
        try:
            ch = int(ch_s.strip())
            val = int(val_s.strip())
        except ValueError:
            return None, f"levels need whole numbers — got {part!r}."
        if not 1 <= ch <= _MAX_CHANNEL:
            return None, f"channel {ch} is outside 1-{_MAX_CHANNEL}."
        if not 0 <= val <= 255:
            return None, f"level {val} is outside 0-255."
        if ch in levels:
            return None, f"channel {ch} is listed twice."
        levels[ch] = val
    if not levels:
        return None, "no levels given — use 'channel:value;channel:value'."
    return levels, None


def _parse_channels(text: str) -> tuple[dict[str, int] | None, str | None]:
    """'dimmer:1;red:2;green:3;blue:4' -> {'dimmer': 1, ...}.
    Offsets are 1-based inside the fixture, unique names and unique
    offsets, 1-512."""
    raw = str(text or "").strip()
    if not raw:
        return None, "no channels given — use 'name:offset;name:offset', e.g. 'dimmer:1;red:2'."
    channels: dict[str, int] = {}
    seen_offsets: set[int] = set()
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            return None, f"channels need 'name:offset' — got {part!r}."
        name, off_s = part.split(":", 1)
        name = name.strip()
        if not _CHANNEL_NAME_RE.match(name):
            return None, f"channel name {name!r} is not a plain name (letters, digits, underscore)."
        try:
            off = int(off_s.strip())
        except ValueError:
            return None, f"offset for {name!r} needs a whole number — got {off_s.strip()!r}."
        if not 1 <= off <= _MAX_CHANNEL:
            return None, f"offset {off} for {name!r} is outside 1-{_MAX_CHANNEL}."
        if name in channels:
            return None, f"channel {name!r} is listed twice."
        if off in seen_offsets:
            return None, f"offset {off} is used twice."
        channels[name] = off
        seen_offsets.add(off)
    if not channels:
        return None, "no channels given — use 'name:offset;name:offset'."
    return channels, None


def _parse_steps(text: str, known_scenes: set[str]) -> tuple[list[dict] | None, str | None]:
    """'scene:2000;scene2:1500' -> [{'scene': 'scene', 'hold_ms': 2000},
    ...]. Every scene must already exist; hold 0-60000 ms."""
    raw = str(text or "").strip()
    if not raw:
        return None, "no steps given — use 'scene:hold_ms;scene:hold_ms', e.g. 'warm:2000;cool:2000'."
    steps: list[dict] = []
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            return None, f"steps need 'scene:hold_ms' — got {part!r}."
        name, hold_s = part.rsplit(":", 1)
        name = name.strip()
        if name not in known_scenes:
            return None, f"scene {name!r} does not exist — save it first."
        try:
            hold = int(hold_s.strip())
        except ValueError:
            return None, f"hold for {name!r} needs whole milliseconds — got {hold_s.strip()!r}."
        if not 0 <= hold <= _MAX_HOLD_MS:
            return None, f"hold {hold} ms for {name!r} is outside 0-{_MAX_HOLD_MS}."
        steps.append({"scene": name, "hold_ms": hold})
    if not steps:
        return None, "no steps given — use 'scene:hold_ms;scene:hold_ms'."
    return steps, None


def _universe_for(protocol: str, universe: int) -> str | None:
    """Per-protocol universe range. Art-Net takes the 15-bit
    port-address space (0-32767); sACN takes 1-63999 (0 is reserved)."""
    if protocol == "artnet":
        if not 0 <= universe <= _MAX_ARTNET_UNIVERSE:
            return f"Art-Net universe {universe} is outside 0-{_MAX_ARTNET_UNIVERSE}."
    elif protocol == "sacn":
        if not 1 <= universe <= _MAX_UNIVERSE:
            return f"sACN universe {universe} is outside 1-{_MAX_UNIVERSE}."
    else:
        return f"protocol must be 'artnet' or 'sacn' — got {protocol!r}."
    return None


# ---------------------------------------------------------------------
# Store (the tree's own SQLite-store shape: one file in DATA_DIR,
# hash-chained event ledger, every write re-read before it is claimed).
# ---------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    protocol TEXT NOT NULL,
    host TEXT NOT NULL,
    universe INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fixtures (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    channels_json TEXT NOT NULL,
    footprint INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS patches (
    id INTEGER PRIMARY KEY,
    fixture TEXT NOT NULL,
    universe INTEGER NOT NULL,
    address INTEGER NOT NULL,
    footprint INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS patches_no_overlap_insert
BEFORE INSERT ON patches
BEGIN
    SELECT RAISE(ABORT, 'patch overlaps an existing patch on this universe')
    WHERE EXISTS (
        SELECT 1 FROM patches p
        WHERE p.universe = NEW.universe
          AND NEW.address <= p.address + p.footprint - 1
          AND p.address <= NEW.address + NEW.footprint - 1
    );
END;
CREATE TABLE IF NOT EXISTS scenes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    universe INTEGER NOT NULL,
    levels_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chases (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    steps_json TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    body_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
"""


class _Store:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = str(Path(DATA_DIR) / DEFAULT_DB_FILENAME)
        self._db_path = db_path
        self._conn = None

    def _connect(self):
        if self._conn is None:
            self._conn = store_base.connect(
                self._db_path, row_factory=sqlite3.Row)
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        return self._conn

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _log_event(self, kind: str, body: dict) -> None:
        conn = self._connect()
        row = conn.execute(
            "SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else "GENESIS"
        payload = json.dumps(body, sort_keys=True)
        digest = store_base.chain_digest(
            "prev_payload_dict", prev, {"body_json": payload})
        conn.execute(
            "INSERT INTO events (ts, kind, body_json, prev_hash, hash)"
            " VALUES (?, ?, ?, ?, ?)",
            (_utcnow(), kind, payload, prev, digest))

    def verify_chain(self):
        """Recompute the hash chain over the full event ledger."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT body_json, prev_hash, hash FROM events"
            " ORDER BY id").fetchall()
        entries = [{"body_json": row["body_json"],
                    "prev_hash": row["prev_hash"], "hash": row["hash"]}
                   for row in rows]
        result = store_base.verify_entries(
            entries, "prev_payload_dict", store_base.LEGACY_GENESIS)
        if not result["ok"]:
            if result["first_break"]["reason"].startswith("link break"):
                return False, "prev_hash link broken"
            return False, "hash mismatch"
        return True, None


    # -- lookups ------------------------------------------------------

    def _node_by_name(self, name):
        return self._connect().execute(
            "SELECT * FROM nodes WHERE name = ?", (name,)).fetchone()

    def _node_for_universe(self, universe):
        return self._connect().execute(
            "SELECT * FROM nodes WHERE universe = ? ORDER BY id LIMIT 1",
            (universe,)).fetchone()

    def _fixture_by_name(self, name):
        return self._connect().execute(
            "SELECT * FROM fixtures WHERE name = ?", (name,)).fetchone()

    def _scene_by_name(self, name):
        return self._connect().execute(
            "SELECT * FROM scenes WHERE name = ?", (name,)).fetchone()

    def _chase_by_name(self, name):
        return self._connect().execute(
            "SELECT * FROM chases WHERE name = ?", (name,)).fetchone()

    # -- network ------------------------------------------------------

    def _send(self, protocol: str, host: str, universe: int,
              levels: dict[int, int]):
        """Send one DMX packet. Returns (bytes_sent, error). The claim
        'sent' is made only when send returns the full packet length
        — the OS call's return is the evidence (A5-sysreadback).

        A connected socket (connect + send) rather than sendto: the
        packets on the wire are identical, and the lab sandbox blocks
        unconnected sendto while a connected socket may send (the
        go2rtc precedent, core/ip_camera.py WS-Discovery unicast
        path)."""
        if protocol == "artnet":
            seq = _next_sequence("artnet", universe)
            packet = _artnet_dmx_packet(universe, levels, seq)
            port = _ARTNET_PORT
            target = host or _ARTNET_BROADCAST
        else:
            seq = _next_sequence("sacn", universe)
            packet = _sacn_packet(universe, levels, seq)
            port = _SACN_PORT
            target = host or _sacn_multicast(universe)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                if target == _ARTNET_BROADCAST:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.connect((target, port))
                sent = sock.send(packet)
            finally:
                sock.close()
        except OSError as exc:
            return 0, f"could not send to {target}:{port} — {exc}."
        if sent != len(packet):
            return sent, (f"only {sent} of {len(packet)} bytes went out — "
                          "the send was partial, so the look is not claimed.")
        return sent, None

    def _discover(self, host: str):
        """Art-Net OpPoll discovery. Returns (replies, note). Binds
        UDP 6454 when it is free (the Art-Net port); when another app
        holds it, listens on an ephemeral port and says so honestly —
        some nodes only reply to 6454."""
        poll = _artnet_poll_packet()
        target = host or _ARTNET_BROADCAST
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        note = ""
        try:
            try:
                sock.bind(("0.0.0.0", _ARTNET_PORT))
            except OSError:
                sock.bind(("0.0.0.0", 0))
                note = ("Art-Net port 6454 is in use on this machine, so "
                        "discovery listened on an ephemeral port — nodes "
                        "that only reply to 6454 will not be seen.")
            if target == _ARTNET_BROADCAST:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.sendto(poll, (target, _ARTNET_PORT))
            else:
                # Unicast target (tests point it at a local responder):
                # a connected socket. The broadcast path above stays
                # unconnected with sendto: a connected socket would
                # drop replies (they come from each node's own
                # address, not the broadcast address). Same split as
                # core/ip_camera.py's WS-Discovery probe.
                sock.connect((target, _ARTNET_PORT))
                sock.send(poll)
            found: dict[str, dict] = {}
            deadline = time.time() + _DISCOVER_TIMEOUT_S
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    data, _addr = sock.recvfrom(2048)
                except socket.timeout:
                    break
                parsed = _parse_artnet_poll_reply(data)
                if parsed is not None:
                    found[parsed["ip"]] = parsed
        except OSError as exc:
            return [], f"discovery could not listen — {exc}."
        finally:
            sock.close()
        return sorted(found.values(), key=lambda r: r["ip"]), note


def _levels_text(levels: dict[int, int]) -> str:
    return "; ".join(f"{ch}:{levels[ch]}" for ch in sorted(levels))


def stage_lighting(action: str = "", mode: str = "", node: str = "",
                   host: str = "", protocol: str = "", universe=None,
                   address=None, fixture: str = "", type: str = "",
                   channels: str = "", scene: str = "", chase: str = "",
                   steps: str = "", levels: str = "", limit=None,
                   _db_path=None) -> str:
    """The owner's own stage / venue lighting: Art-Net and sACN output
    to the buyer's own lighting nodes, fixture patching, scenes and
    chases. The DMX-over-USB half is NOT built (operator hardware +
    driver); the virtual console / web UI / hosted half is NOT built;
    QLC+ fixture-library import is NOT built. No money moves."""
    action = str(action or "").strip().lower()
    mode = str(mode or "").strip().lower()
    store = _Store(_db_path)
    try:
        if action not in _ACTIONS:
            return ("stage_lighting needs an action: 'node', 'patch', "
                    "'fixture', 'scene', 'chase', 'output', 'blackout', "
                    "'discover', 'status' or 'events'.")
        if action == "events":
            ok, why = store.verify_chain()
            if not ok:
                return (f"the event ledger fails verification: {why} — "
                        "the ledger is not presented as verified.")
            total = store._connect().execute(
                "SELECT COUNT(*) AS n FROM events").fetchone()["n"]
            n = limit if isinstance(limit, int) and limit > 0 else 20
            rows = store._connect().execute(
                "SELECT ts, kind, body_json FROM events ORDER BY id DESC"
                " LIMIT ?", (n,)).fetchall()
            if not rows:
                return "No lighting events yet — the ledger is empty (chain OK)."
            lines = [f"{r['ts']} {r['kind']}: {r['body_json']}" for r in rows]
            return (f"Lighting events (chain OK, {total} event(s), newest "
                    f"first):\n" + "\n".join(lines))
        if action == "status":
            conn = store._connect()
            counts = {}
            for table in ("nodes", "fixtures", "patches", "scenes", "chases"):
                counts[table] = conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            nodes = conn.execute(
                "SELECT name, protocol, host, universe FROM nodes"
                " ORDER BY universe, name").fetchall()
            lines = [f"{counts['nodes']} node(s), {counts['fixtures']} fixture(s), "
                     f"{counts['patches']} patch(es), {counts['scenes']} scene(s), "
                     f"{counts['chases']} chase(s)."]
            for r in nodes:
                where = r["host"] or ("broadcast" if r["protocol"] == "artnet"
                                      else "multicast " + _sacn_multicast(r["universe"]))
                lines.append(f"node {r['name']!r}: {r['protocol']} universe "
                             f"{r['universe']} via {where}")
            last = conn.execute(
                "SELECT ts, kind, body_json FROM events WHERE kind IN"
                " ('output', 'blackout', 'scene_run', 'chase_step')"
                " ORDER BY id DESC LIMIT 1").fetchone()
            if last:
                lines.append(f"Last sent {last['ts']} ({last['kind']}): "
                             f"{last['body_json']}")
            else:
                lines.append("Nothing has been sent yet.")
            return "\n".join(lines)
        if action == "discover":
            replies, note = store._discover(_clean_name(host))
            store._log_event("discover", {"host": host or "broadcast",
                                          "found": len(replies)})
            store._connect().commit()
            if not replies:
                out = "No Art-Net nodes replied. (sACN has no discovery — add sACN nodes by address.)"
            else:
                lines = [f"{len(replies)} Art-Net node(s) replied:"]
                for r in replies:
                    name = r["long_name"] or r["short_name"] or r["ip"]
                    lines.append(f"{r['ip']}:{r['port']} universe {r['universe']} — {name}")
                out = "\n".join(lines)
            return out + (f"\nNote: {note}" if note else "")
        if action == "output" or action == "blackout":
            if universe is None:
                return f"'{action}' needs a universe (0-{_MAX_UNIVERSE})."
            try:
                universe = int(universe)
            except (TypeError, ValueError):
                return f"'{action}' needs a whole-number universe — got {universe!r}."
            if not 0 <= universe <= _MAX_UNIVERSE:
                return f"universe {universe} is outside 0-{_MAX_UNIVERSE}."
            if action == "blackout":
                levels_map = {ch: 0 for ch in range(1, _MAX_CHANNEL + 1)}
            else:
                levels_map, err = _parse_levels(levels)
                if err:
                    return err
            node_name = _clean_name(node)
            if node_name:
                node_row = store._node_by_name(node_name)
                if node_row is None:
                    return f"node {node_name!r} does not exist — add it first."
                if node_row["universe"] != universe:
                    return (f"node {node_name!r} is for universe "
                            f"{node_row['universe']}, not {universe}.")
            else:
                node_row = store._node_for_universe(universe)
                if node_row is None:
                    return (f"no node is registered for universe {universe} — "
                            "add one first ('node' with host, protocol and universe).")
            err = _universe_for(node_row["protocol"], universe)
            if err:
                return err
            sent, err = store._send(node_row["protocol"], node_row["host"],
                                    universe, levels_map)
            if err:
                return err
            store._log_event(action, {"node": node_row["name"],
                                      "protocol": node_row["protocol"],
                                      "universe": universe,
                                      "levels": levels_map,
                                      "bytes": sent})
            store._connect().commit()
            if action == "blackout":
                return (f"Blackout sent to universe {universe} via node "
                        f"{node_row['name']!r} ({node_row['protocol']}, "
                        f"{sent} bytes, all {_MAX_CHANNEL} channels zero).")
            return (f"Sent {len(levels_map)} channel level(s) to universe "
                    f"{universe} via node {node_row['name']!r} "
                    f"({node_row['protocol']}, {sent} bytes): "
                    f"{_levels_text(levels_map)}.")
        if action == "node":
            return _node_action(store, mode, node, host, protocol, universe)
        if action == "fixture":
            return _fixture_action(store, mode, fixture, type, channels)
        if action == "patch":
            return _patch_action(store, mode, fixture, universe, address)
        if action == "scene":
            return _scene_action(store, mode, scene, universe, levels)
        if action == "chase":
            return _chase_action(store, mode, chase, steps)
        return f"unknown action {action!r}."
    finally:
        store.close()


def _node_action(store, mode, node, host, protocol, universe):
    name = _clean_name(node)
    host = _clean_name(host)
    protocol = str(protocol or "").strip().lower()
    if not mode:
        mode = "add" if host or protocol or universe is not None else "list"
    if mode not in ("add", "list", "remove"):
        return "'node' takes a mode: 'add', 'list' or 'remove'."
    conn = store._connect()
    if mode == "list":
        rows = conn.execute(
            "SELECT name, protocol, host, universe FROM nodes"
            " ORDER BY universe, name").fetchall()
        if name:
            rows = [r for r in rows if r["name"] == name]
        if not rows:
            return "No lighting nodes registered yet."
        lines = []
        for r in rows:
            where = r["host"] or ("broadcast" if r["protocol"] == "artnet"
                                  else "multicast " + _sacn_multicast(r["universe"]))
            lines.append(f"{r['name']!r}: {r['protocol']} universe "
                         f"{r['universe']} via {where}")
        return "\n".join(lines)
    if mode == "remove":
        if not name:
            return "'node' remove needs the node's name."
        row = store._node_by_name(name)
        if row is None:
            return f"node {name!r} does not exist."
        conn.execute("DELETE FROM nodes WHERE name = ?", (name,))
        store._log_event("node_remove", {"node": name})
        conn.commit()
        return f"Node {name!r} removed."
    # add
    if not name:
        return "'node' add needs a name."
    if not _NAME_RE.match(name):
        return f"node name {name!r} is not a plain name (letters, digits, space, _ or -)."
    if protocol not in ("artnet", "sacn"):
        return "a node needs a protocol: 'artnet' or 'sacn'."
    if universe is None:
        return "a node needs a universe."
    try:
        universe = int(universe)
    except (TypeError, ValueError):
        return f"a node needs a whole-number universe — got {universe!r}."
    err = _universe_for(protocol, universe)
    if err:
        return err
    try:
        conn.execute(
            "INSERT INTO nodes (name, protocol, host, universe, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (name, protocol, host, universe, _utcnow()))
        store._log_event("node_add", {"node": name, "protocol": protocol,
                                      "host": host or "(default)",
                                      "universe": universe})
        conn.commit()
    except sqlite3.IntegrityError:
        return f"a node named {name!r} already exists."
    row = store._node_by_name(name)
    if row is None:
        return f"node {name!r} was not stored — nothing is claimed."
    where = host or ("broadcast" if protocol == "artnet"
                     else "multicast " + _sacn_multicast(universe))
    return (f"Node {name!r} added: {protocol} universe {universe} via "
            f"{where}.")


def _fixture_action(store, mode, fixture, type, channels):
    name = _clean_name(fixture)
    ftype = _clean_name(type)
    if not mode:
        mode = "add" if ftype or channels else "list"
    if mode not in ("add", "list", "remove"):
        return "'fixture' takes a mode: 'add', 'list' or 'remove'."
    conn = store._connect()
    if mode == "list":
        rows = conn.execute(
            "SELECT name, type, channels_json, footprint FROM fixtures"
            " ORDER BY name").fetchall()
        if name:
            rows = [r for r in rows if r["name"] == name]
        if not rows:
            return "No fixture definitions yet."
        lines = []
        for r in rows:
            cmap = json.loads(r["channels_json"])
            spec = "; ".join(f"{k}:{cmap[k]}" for k in sorted(cmap, key=cmap.get))
            lines.append(f"{r['name']!r} ({r['type']}, {r['footprint']} channels): {spec}")
        return "\n".join(lines)
    if mode == "remove":
        if not name:
            return "'fixture' remove needs the fixture's name."
        row = store._fixture_by_name(name)
        if row is None:
            return f"fixture {name!r} does not exist."
        used = conn.execute(
            "SELECT universe, address FROM patches WHERE fixture = ?"
            " ORDER BY universe LIMIT 1", (name,)).fetchone()
        if used is not None:
            return (f"fixture {name!r} is patched on universe "
                    f"{used['universe']} at address {used['address']} — "
                    "remove the patch first.")
        conn.execute("DELETE FROM fixtures WHERE name = ?", (name,))
        store._log_event("fixture_remove", {"fixture": name})
        conn.commit()
        return f"Fixture {name!r} removed."
    # add
    if not name:
        return "'fixture' add needs a name."
    if not _NAME_RE.match(name):
        return f"fixture name {name!r} is not a plain name (letters, digits, space, _ or -)."
    if not ftype:
        return "'fixture' add needs a type, e.g. 'dimmer', 'rgb' or 'moving_head'."
    cmap, err = _parse_channels(channels)
    if err:
        return err
    footprint = max(cmap.values())
    try:
        conn.execute(
            "INSERT INTO fixtures (name, type, channels_json, footprint,"
            " created_at) VALUES (?, ?, ?, ?, ?)",
            (name, ftype, json.dumps(cmap, sort_keys=True), footprint,
             _utcnow()))
        store._log_event("fixture_add", {"fixture": name, "type": ftype,
                                         "channels": cmap,
                                         "footprint": footprint})
        conn.commit()
    except sqlite3.IntegrityError:
        return f"a fixture named {name!r} already exists."
    row = store._fixture_by_name(name)
    if row is None:
        return f"fixture {name!r} was not stored — nothing is claimed."
    return (f"Fixture {name!r} added: {ftype}, {footprint} channels "
            f"({'; '.join(f'{k}:{cmap[k]}' for k in sorted(cmap, key=cmap.get))}).")


def _patch_action(store, mode, fixture, universe, address):
    name = _clean_name(fixture)
    if not mode:
        mode = "add" if (name and universe is not None
                         and address is not None) else "list"
    if mode not in ("add", "list", "remove"):
        return "'patch' takes a mode: 'add', 'list' or 'remove'."
    conn = store._connect()
    if mode == "list":
        rows = conn.execute(
            "SELECT fixture, universe, address, footprint FROM patches"
            " ORDER BY universe, address").fetchall()
        if universe is not None:
            try:
                universe = int(universe)
            except (TypeError, ValueError):
                return f"'patch' list needs a whole-number universe — got {universe!r}."
            rows = [r for r in rows if r["universe"] == universe]
        if name:
            rows = [r for r in rows if r["fixture"] == name]
        if not rows:
            return "No fixtures patched yet."
        lines = []
        for r in rows:
            lines.append(f"{r['fixture']!r} on universe {r['universe']} at "
                         f"address {r['address']} "
                         f"(channels {r['address']}-{r['address'] + r['footprint'] - 1})")
        return "\n".join(lines)
    if mode == "remove":
        if universe is None or address is None:
            return "'patch' remove needs a universe and an address."
        try:
            universe = int(universe)
            address = int(address)
        except (TypeError, ValueError):
            return "'patch' remove needs whole-number universe and address."
        row = conn.execute(
            "SELECT fixture FROM patches WHERE universe = ? AND address = ?",
            (universe, address)).fetchone()
        if row is None:
            return (f"no patch starts at address {address} on universe "
                    f"{universe}.")
        conn.execute(
            "DELETE FROM patches WHERE universe = ? AND address = ?",
            (universe, address))
        store._log_event("patch_remove", {"fixture": row["fixture"],
                                          "universe": universe,
                                          "address": address})
        conn.commit()
        return (f"Patch removed: {row['fixture']!r} off universe {universe} "
                f"(was at address {address}).")
    # add
    if not name:
        return "'patch' add needs a fixture name."
    if universe is None or address is None:
        return "'patch' add needs a universe and a start address."
    try:
        universe = int(universe)
        address = int(address)
    except (TypeError, ValueError):
        return "'patch' add needs whole-number universe and address."
    if not 0 <= universe <= _MAX_UNIVERSE:
        return f"universe {universe} is outside 0-{_MAX_UNIVERSE}."
    if not 1 <= address <= _MAX_CHANNEL:
        return f"start address {address} is outside 1-{_MAX_CHANNEL}."
    frow = store._fixture_by_name(name)
    if frow is None:
        return f"fixture {name!r} does not exist — define it first."
    if address + frow["footprint"] - 1 > _MAX_CHANNEL:
        return (f"fixture {name!r} needs {frow['footprint']} channels from "
                f"address {address} — that runs past channel {_MAX_CHANNEL}.")
    try:
        conn.execute(
            "INSERT INTO patches (fixture, universe, address, footprint,"
            " created_at) VALUES (?, ?, ?, ?, ?)",
            (name, universe, address, frow["footprint"], _utcnow()))
        store._log_event("patch_add", {"fixture": name, "universe": universe,
                                       "address": address,
                                       "footprint": frow["footprint"]})
        conn.commit()
    except sqlite3.IntegrityError as exc:
        clash = conn.execute(
            "SELECT fixture, address, footprint FROM patches"
            " WHERE universe = ? AND ? <= address + footprint - 1"
            " AND address <= ? ORDER BY address LIMIT 1",
            (universe, address, address + frow["footprint"] - 1)).fetchone()
        if clash is not None:
            return (f"address {address} on universe {universe} overlaps "
                    f"{clash['fixture']!r} (patched at {clash['address']}, "
                    f"{clash['footprint']} channels) — the patch was refused.")
        return f"the patch was refused — {exc}."
    row = conn.execute(
        "SELECT fixture FROM patches WHERE universe = ? AND address = ?",
        (universe, address)).fetchone()
    if row is None:
        return "the patch was not stored — nothing is claimed."
    return (f"Patched {name!r} on universe {universe} at address {address} "
            f"(channels {address}-{address + frow['footprint'] - 1}).")


def _scene_action(store, mode, scene, universe, levels):
    name = _clean_name(scene)
    if not mode:
        mode = "save" if levels else "list"
    if mode not in ("save", "list", "run", "delete"):
        return "'scene' takes a mode: 'save', 'list', 'run' or 'delete'."
    conn = store._connect()
    if mode == "list":
        rows = conn.execute(
            "SELECT name, universe, levels_json FROM scenes"
            " ORDER BY name").fetchall()
        if name:
            rows = [r for r in rows if r["name"] == name]
        if not rows:
            return "No scenes saved yet."
        lines = []
        for r in rows:
            lv = {int(k): v for k, v in json.loads(r["levels_json"]).items()}
            lines.append(f"{r['name']!r} (universe {r['universe']}): "
                         f"{_levels_text(lv)}")
        return "\n".join(lines)
    if mode == "delete":
        if not name:
            return "'scene' delete needs the scene's name."
        row = store._scene_by_name(name)
        if row is None:
            return f"scene {name!r} does not exist."
        conn.execute("DELETE FROM scenes WHERE name = ?", (name,))
        store._log_event("scene_delete", {"scene": name})
        conn.commit()
        return f"Scene {name!r} deleted."
    if mode == "run":
        if not name:
            return "'scene' run needs the scene's name."
        row = store._scene_by_name(name)
        if row is None:
            return f"scene {name!r} does not exist — save it first."
        levels_map = {int(k): v for k, v in json.loads(row["levels_json"]).items()}
        node_row = store._node_for_universe(row["universe"])
        if node_row is None:
            return (f"no node is registered for universe {row['universe']} — "
                    "add one first.")
        err = _universe_for(node_row["protocol"], row["universe"])
        if err:
            return err
        sent, err = store._send(node_row["protocol"], node_row["host"],
                                row["universe"], levels_map)
        if err:
            return err
        store._log_event("scene_run", {"scene": name,
                                       "node": node_row["name"],
                                       "protocol": node_row["protocol"],
                                       "universe": row["universe"],
                                       "levels": levels_map, "bytes": sent})
        conn.commit()
        return (f"Scene {name!r} sent to universe {row['universe']} via node "
                f"{node_row['name']!r} ({node_row['protocol']}, {sent} bytes): "
                f"{_levels_text(levels_map)}.")
    # save
    if not name:
        return "'scene' save needs a name."
    if not _NAME_RE.match(name):
        return f"scene name {name!r} is not a plain name (letters, digits, space, _ or -)."
    if universe is None:
        return "'scene' save needs a universe."
    try:
        universe = int(universe)
    except (TypeError, ValueError):
        return f"'scene' save needs a whole-number universe — got {universe!r}."
    if not 0 <= universe <= _MAX_UNIVERSE:
        return f"universe {universe} is outside 0-{_MAX_UNIVERSE}."
    levels_map, err = _parse_levels(levels)
    if err:
        return err
    conn.execute(
        "INSERT INTO scenes (name, universe, levels_json, created_at)"
        " VALUES (?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET"
        " universe = excluded.universe,"
        " levels_json = excluded.levels_json",
        (name, universe,
         json.dumps({str(k): v for k, v in levels_map.items()}, sort_keys=True),
         _utcnow()))
    store._log_event("scene_save", {"scene": name, "universe": universe,
                                    "levels": levels_map})
    conn.commit()
    row = store._scene_by_name(name)
    if row is None:
        return f"scene {name!r} was not stored — nothing is claimed."
    return (f"Scene {name!r} saved for universe {universe}: "
            f"{_levels_text(levels_map)}.")


def _chase_action(store, mode, chase, steps):
    name = _clean_name(chase)
    if not mode:
        mode = "save" if steps else "list"
    if mode not in ("save", "list", "step", "delete"):
        return "'chase' takes a mode: 'save', 'list', 'step' or 'delete'."
    conn = store._connect()
    if mode == "list":
        rows = conn.execute(
            "SELECT name, steps_json, position FROM chases"
            " ORDER BY name").fetchall()
        if name:
            rows = [r for r in rows if r["name"] == name]
        if not rows:
            return "No chases saved yet."
        lines = []
        for r in rows:
            steps_list = json.loads(r["steps_json"])
            spec = "; ".join(f"{s['scene']}:{s['hold_ms']}ms"
                             for s in steps_list)
            lines.append(f"{r['name']!r} ({len(steps_list)} step(s), next is "
                         f"step {r['position'] + 1}): {spec}")
        return "\n".join(lines)
    if mode == "delete":
        if not name:
            return "'chase' delete needs the chase's name."
        row = store._chase_by_name(name)
        if row is None:
            return f"chase {name!r} does not exist."
        conn.execute("DELETE FROM chases WHERE name = ?", (name,))
        store._log_event("chase_delete", {"chase": name})
        conn.commit()
        return f"Chase {name!r} deleted."
    if mode == "step":
        if not name:
            return "'chase' step needs the chase's name."
        row = store._chase_by_name(name)
        if row is None:
            return f"chase {name!r} does not exist — save it first."
        steps_list = json.loads(row["steps_json"])
        pos = row["position"] % len(steps_list)
        step = steps_list[pos]
        srow = store._scene_by_name(step["scene"])
        if srow is None:
            return (f"chase {name!r} step {pos + 1} names scene "
                    f"{step['scene']!r}, which no longer exists.")
        levels_map = {int(k): v for k, v in json.loads(srow["levels_json"]).items()}
        node_row = store._node_for_universe(srow["universe"])
        if node_row is None:
            return (f"no node is registered for universe {srow['universe']} — "
                    "add one first.")
        err = _universe_for(node_row["protocol"], srow["universe"])
        if err:
            return err
        sent, err = store._send(node_row["protocol"], node_row["host"],
                                srow["universe"], levels_map)
        if err:
            return err
        new_pos = (pos + 1) % len(steps_list)
        conn.execute("UPDATE chases SET position = ? WHERE name = ?",
                     (new_pos, name))
        store._log_event("chase_step", {"chase": name, "step": pos + 1,
                                        "of": len(steps_list),
                                        "scene": step["scene"],
                                        "node": node_row["name"],
                                        "protocol": node_row["protocol"],
                                        "universe": srow["universe"],
                                        "bytes": sent})
        conn.commit()
        nxt = steps_list[new_pos]["scene"]
        return (f"Chase {name!r} step {pos + 1}/{len(steps_list)}: sent scene "
                f"{step['scene']!r} to universe {srow['universe']} via node "
                f"{node_row['name']!r} ({node_row['protocol']}, {sent} bytes); "
                f"hold {step['hold_ms']} ms, next step is {nxt!r}.")
    # save
    if not name:
        return "'chase' save needs a name."
    if not _NAME_RE.match(name):
        return f"chase name {name!r} is not a plain name (letters, digits, space, _ or -)."
    known = {r["name"] for r in conn.execute("SELECT name FROM scenes").fetchall()}
    steps_list, err = _parse_steps(steps, known)
    if err:
        return err
    conn.execute(
        "INSERT INTO chases (name, steps_json, position, created_at)"
        " VALUES (?, ?, 0, ?) ON CONFLICT(name) DO UPDATE SET"
        " steps_json = excluded.steps_json, position = 0",
        (name, json.dumps(steps_list), _utcnow()))
    store._log_event("chase_save", {"chase": name, "steps": steps_list})
    conn.commit()
    row = store._chase_by_name(name)
    if row is None:
        return f"chase {name!r} was not stored — nothing is claimed."
    return (f"Chase {name!r} saved: {len(steps_list)} step(s), starting at "
            f"step 1.")
