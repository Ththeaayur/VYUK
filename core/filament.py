"""3D-printer filament spool inventory (R19-7, Donkie/Spoolman inspiration).

Spoolman is a self-hosted web service that tracks 3D-printer filament
spools — vendors, filament types (material, density, diameter,
colour, weight), individual spools (initial weight, used weight,
remaining weight/length, location, lot, price) — and, when connected
to OctoPrint / Klipper / Moonraker, updates spool weights live as
printing progresses. This module builds the honest buildable
remainder in own stdlib code: a LOCAL FILAMENT SPOOL INVENTORY —
vendors, filament types and spools in VYUK's own store, with usage
recorded by the operator, remaining weight and remaining length
derived from the filament's density and diameter, and low-stock
warnings. No server, no printer connection, no network call.

Licence: MIT — "Parent-ruled (licence, 2026-11-12)" (the Spoolman LICENSE,
read in full from master: 1,072 bytes, 21 lines, copyright
"Copyright (c) 2023 Daniel Hultgren"). Nothing is adopted — no Python, no
FastAPI, no SQLAlchemy, no web app, no wrapper, no port. The build
takes only the inventory shapes (vendor, filament, spool, weights,
usage) and rebuilds them as own stdlib code.

What is built (one effectful tool `filament`, free by design):
  * vendor — add / list / get / remove. A vendor has a name, an
    optional comment and an optional empty-spool weight (the weight
    of one of its empty spools, in grams — a filament's empty-spool
    weight falls back to it). Removing a vendor is refused while
    it still has filament types; the refusal names the count.
  * filament — add / list / get / remove. A filament type belongs
    to a vendor and has a name, a material (PLA, PETG, ABS, TPU,
    ...), a density in g/cm3, a diameter in mm, a full-spool
    filament weight in grams, an empty-spool weight in grams, a
    colour, an article number and a comment. Removing a filament
    type is refused while it still has spools; the refusal names
    the count.
  * spool — add / list / get / remove / use. A spool belongs to a
    filament type and has an initial weight, a used weight, a
    remaining weight (initial minus used, never a stored counter),
    a remaining length in metres (derived from the remaining
    weight, the filament density and the diameter), a location, a
    lot number, a price in integer minor units, a low-stock
    threshold in grams and a comment. `use` records grams printed
    from a spool and refuses honestly when the spool is short,
    naming the remaining and the asked grams.
  * status — counts of vendors, filament types and spools, the
    total remaining weight and length, and the spools at or below
    their low-stock threshold.
  * events — the hash-chained ledger (list / verify).

What is NOT built (STOP, stated on the tin):
  * NO WEB APP, no server, no hosted anything. The tool is local
    only, single-user by design.
  * NO PRINTER CONTROL — no OctoPrint / Klipper / Moonraker
    integration, no print queue, no live spool updates from a
    printer. Usage is recorded by the operator (the `use`
    action), never pulled from a printer.
  * NO SpoolmanDB — no central filament database, no network call
    anywhere; every filament type is operator-supplied.
  * NO QR LABELS — no label designer, no label printing.
  * NO multi-user roles, no staff accounts, no channel sync, no
    network call of any kind.

Classification: `filament` writes only to VYUK's own store, so it
is free by design ("app-state", the erp / wardrobe precedent).
Unknown or missing action gates fail-closed (the handler refuses
them anyway).

Bounds: at most 100 vendors; at most 1,000 filament types; at
most 10,000 spools; weights 0-100,000 g; density 0.1-10 g/cm3;
diameter 0.1-10 mm; price at most 1,000,000,000 minor units;
names at most 120 chars; material at most 64 chars; comments at
most 500 chars; low-stock threshold 0-100,000 g (default 100 g).
"""

import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path

from .config import DATA_DIR
from . import store_base

_ACTIONS = ("vendor", "filament", "spool", "status", "events")

_MAX_VENDORS = 100
_MAX_FILAMENTS = 1000
_MAX_SPOOLS = 10000
_MAX_WEIGHT = 100000
_MAX_DENSITY = 10.0
_MIN_DENSITY = 0.1
_MAX_DIAMETER = 10.0
_MIN_DIAMETER = 0.1
_MAX_PRICE = 1000000000
_MAX_NAME = 120
_MAX_MATERIAL = 64
_MAX_COMMENT = 500
_MAX_LOCATION = 120
_MAX_LOT = 64
_MAX_ARTICLE = 64
_DEFAULT_LOW_THRESHOLD = 100

PROPOSED_MARKER = "Parent-ruled (licence, 2026-11-12)"

# An RLock: _do_status / _do_events call verify_chain(), which
# takes the lock again.
_LOCK = threading.RLock()

_COLOR_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


class _Refusal(Exception):
    pass


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def _clean_str(v, what, limit=_MAX_COMMENT, required=True):
    if v is None:
        if required:
            raise _Refusal("%s is required" % what)
        return None
    if not isinstance(v, str):
        raise _Refusal("%s must be a string" % what)
    v = v.strip()
    if required and not v:
        raise _Refusal("%s must not be empty" % what)
    if len(v) > limit:
        raise _Refusal("%s is at most %d characters" % (what, limit))
    return v if v else None


def _clean_num(v, what, lo, hi, required=False, default=None):
    if v is None:
        if required:
            raise _Refusal("%s is required" % what)
        return default
    if not _is_num(v):
        raise _Refusal("%s must be a number" % what)
    v = float(v)
    if v < lo or v > hi:
        raise _Refusal("%s must be between %s and %s" % (what, lo, hi))
    return v


def _clean_int(v, what, lo, hi, required=False, default=None):
    if v is None:
        if required:
            raise _Refusal("%s is required" % what)
        return default
    if not _is_int(v):
        raise _Refusal("%s must be an integer" % what)
    if v < lo or v > hi:
        raise _Refusal("%s must be between %d and %d" % (what, lo, hi))
    return v


def _clean_color(v, what="color"):
    if v is None:
        return None
    if not isinstance(v, str):
        raise _Refusal("%s must be a string" % what)
    v = v.strip()
    if not _COLOR_RE.match(v):
        raise _Refusal("%s must be a hex colour like #FF0000" % what)
    return "#" + v.lstrip("#").upper()


def _clean_limit(v):
    if v is None:
        return 20
    if not _is_int(v) or v < 1 or v > 100:
        raise _Refusal("limit must be an integer between 1 and 100")
    return v


def _db_path() -> Path:
    return Path(DATA_DIR) / "filament.sqlite3"


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS vendors ("
    " id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,"
    " comment TEXT, empty_spool_weight REAL,"
    " created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS filaments ("
    " id TEXT PRIMARY KEY, vendor_id TEXT NOT NULL,"
    " name TEXT NOT NULL, material TEXT NOT NULL,"
    " density REAL NOT NULL, diameter REAL NOT NULL,"
    " weight REAL, spool_weight REAL, color TEXT,"
    " article_number TEXT, comment TEXT, created REAL NOT NULL,"
    " UNIQUE(vendor_id, name))",
    "CREATE TABLE IF NOT EXISTS spools ("
    " id TEXT PRIMARY KEY, filament_id TEXT NOT NULL,"
    " initial_weight REAL NOT NULL, used_weight REAL NOT NULL,"
    " location TEXT, lot_number TEXT, price INTEGER,"
    " low_threshold REAL NOT NULL, comment TEXT,"
    " created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS events ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
    " kind TEXT NOT NULL, payload TEXT NOT NULL,"
    " prev_hash TEXT NOT NULL, hash TEXT NOT NULL)",
)


class _FilamentStore(store_base.StoreBase):
    """Vendors + filament types + spools + a hash-chained ledger.
    Open-per-operation; every write is followed by a re-read before
    the caller claims it (A1-store). Reads answer empty WITHOUT
    creating the file."""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else _db_path()


    def _connect(self):
        conn = store_base.connect(
            str(self.path), pragmas=("PRAGMA journal_mode=WAL",))
        for stmt in _SCHEMA:
            conn.execute(stmt)
        conn.commit()
        return conn

    def _append_event_locked(self, conn, kind, payload):
        ts = time.time()
        prev = conn.execute(
            "SELECT hash FROM events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        prev_hash = prev[0] if prev else "0" * 64
        digest = store_base.chain_digest(
            "json_body_ts", prev_hash,
            {"ts": ts, "kind": kind, "payload": payload})
        conn.execute(
            "INSERT INTO events (ts, kind, payload, prev_hash, hash)"
            " VALUES (?, ?, ?, ?, ?)",
            (ts, kind, json.dumps(payload, sort_keys=True),
             prev_hash, digest))
        return digest

    def verify_chain(self):
        """Recompute every event hash and check the chain links.
        Returns (ok, event count)."""
        if self._missing():
            return True, 0
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id, ts, kind, payload, prev_hash, hash"
                    " FROM events ORDER BY id").fetchall()
            finally:
                conn.close()
        try:
            entries = [{"ts": ts, "kind": kind,
                        "payload": json.loads(payload),
                        "prev_hash": stored_prev,
                        "hash": stored_hash}
                       for _id, ts, kind, payload, stored_prev,
                       stored_hash in rows]
        except ValueError:
            return False, len(rows)
        result = store_base.verify_entries(
            entries, "json_body_ts", store_base.GENESIS)
        return result["ok"], len(rows)


def _next_id(conn, table, prefix):
    row = conn.execute(
        "SELECT id FROM %s ORDER BY id DESC LIMIT 1" % table).fetchone()
    n = 0
    if row:
        try:
            n = int(row[0].split("-")[1])
        except (IndexError, ValueError):
            n = 0
    return "%s-%04d" % (prefix, n + 1)


def _remaining_length_m(remaining_g, density, diameter_mm):
    """Metres of filament left: mass / (density * cross-section).

    Cross-section is pi * (d/2)^2 mm2; one metre of that section is
    that many cm3, so grams per metre is density * section (mm2).
    PLA 1.24 g/cm3 at 1.75 mm is ~2.98 g/m, so 1,000 g is ~335 m.
    """
    section = math.pi * (diameter_mm / 2.0) ** 2
    per_metre = density * section
    if per_metre <= 0:
        return 0.0
    return remaining_g / per_metre


def _resolve_vendor(store, conn, vendor):
    """A vendor id (VND-0001) or a unique vendor name."""
    if vendor is None:
        raise _Refusal("vendor is required")
    vendor = str(vendor).strip()
    if not vendor:
        raise _Refusal("vendor is required")
    row = conn.execute(
        "SELECT id, name, comment, empty_spool_weight, created"
        " FROM vendors WHERE id = ?", (vendor,)).fetchone()
    if row is None:
        rows = conn.execute(
            "SELECT id, name, comment, empty_spool_weight, created"
            " FROM vendors WHERE name = ?", (vendor,)).fetchall()
        if not rows:
            raise _Refusal("no vendor %r" % vendor)
        row = rows[0]
    return row


def _resolve_filament(store, conn, filament):
    """A filament id (FIL-0001) or a name that resolves to exactly one."""
    if filament is None:
        raise _Refusal("filament is required")
    filament = str(filament).strip()
    if not filament:
        raise _Refusal("filament is required")
    row = conn.execute(
        "SELECT id, vendor_id, name, material, density, diameter,"
        " weight, spool_weight, color, article_number, comment,"
        " created FROM filaments WHERE id = ?", (filament,)).fetchone()
    if row is None:
        rows = conn.execute(
            "SELECT id, vendor_id, name, material, density, diameter,"
            " weight, spool_weight, color, article_number, comment,"
            " created FROM filaments WHERE name = ?",
            (filament,)).fetchall()
        if not rows:
            raise _Refusal("no filament %r" % filament)
        if len(rows) > 1:
            raise _Refusal(
                "filament %r is ambiguous (%d filament types share the"
                " name — use its id)" % (filament, len(rows)))
        row = rows[0]
    return row


def _resolve_spool(store, conn, spool):
    """A spool id (SPL-0001). Spools have no natural name."""
    if spool is None:
        raise _Refusal("spool is required")
    spool = str(spool).strip()
    if not spool:
        raise _Refusal("spool is required")
    row = conn.execute(
        "SELECT id, filament_id, initial_weight, used_weight, location,"
        " lot_number, price, low_threshold, comment, created"
        " FROM spools WHERE id = ?", (spool,)).fetchone()
    if row is None:
        raise _Refusal("no spool %r" % spool)
    return row


def _vendor_dict(row):
    return {"id": row[0], "name": row[1], "comment": row[2],
            "empty_spool_weight": row[3], "created": row[4]}


def _filament_dict(row, store, conn):
    vendor = conn.execute(
        "SELECT name FROM vendors WHERE id = ?", (row[1],)).fetchone()
    return {"id": row[0], "vendor_id": row[1],
            "vendor": vendor[0] if vendor else None,
            "name": row[2], "material": row[3],
            "density": row[4], "diameter": row[5],
            "weight": row[6], "spool_weight": row[7],
            "color": row[8], "article_number": row[9],
            "comment": row[10], "created": row[11]}


def _spool_dict(row, store, conn):
    filament = conn.execute(
        "SELECT name, material, density, diameter FROM filaments"
        " WHERE id = ?", (row[1],)).fetchone()
    remaining = row[2] - row[3]
    if remaining < 0:
        remaining = 0.0
    length = 0.0
    if filament is not None:
        length = _remaining_length_m(remaining, filament[2],
                                     filament[3])
    return {"id": row[0], "filament_id": row[1],
            "filament": filament[0] if filament else None,
            "material": filament[1] if filament else None,
            "initial_weight": row[2], "used_weight": row[3],
            "remaining_weight": remaining,
            "remaining_length_m": round(length, 2),
            "location": row[4], "lot_number": row[5],
            "price": row[6], "low_threshold": row[7],
            "comment": row[8], "created": row[9],
            "low_stock": remaining <= row[7]}


def _do_vendor(store, kind, args, _db_path=None):
    kind = (kind or "").strip().lower()
    if kind not in ("add", "list", "get", "remove"):
        raise _Refusal("unknown vendor kind %r — add, list, get, remove"
                       % kind)
    if kind in ("list", "get"):
        if store._missing():
            if kind == "list":
                return "ok", "no vendors yet — the store is empty.", {
                    "vendors": [], "count": 0}
            raise _Refusal("no vendor %r" % (args.get("vendor"),))
        with _LOCK:
            conn = store._connect()
            try:
                if kind == "list":
                    limit = _clean_limit(args.get("limit"))
                    rows = conn.execute(
                        "SELECT id, name, comment, empty_spool_weight,"
                        " created FROM vendors ORDER BY name LIMIT ?",
                        (limit,)).fetchall()
                    return "ok", "%d vendor(s)." % len(rows), {
                        "vendors": [_vendor_dict(r) for r in rows],
                        "count": len(rows)}
                row = _resolve_vendor(store, conn, args.get("vendor"))
                return "ok", "vendor %s." % row[1], {
                    "vendor": _vendor_dict(row)}
            finally:
                conn.close()
    name = _clean_str(args.get("name") or args.get("vendor"), "name",
                      limit=_MAX_NAME)
    if kind == "add":
        comment = _clean_str(args.get("comment"), "comment",
                             limit=_MAX_COMMENT, required=False)
        empty = _clean_num(args.get("spool_weight")
                           or args.get("empty_spool_weight"),
                           "empty_spool_weight", 0, _MAX_WEIGHT)
        with _LOCK:
            conn = store._connect()
            try:
                if conn.execute(
                        "SELECT COUNT(*) FROM vendors").fetchone()[0] \
                        >= _MAX_VENDORS:
                    raise _Refusal("at most %d vendors" % _MAX_VENDORS)
                if conn.execute(
                        "SELECT id FROM vendors WHERE name = ?",
                        (name,)).fetchone() is not None:
                    raise _Refusal("a vendor named %r already exists"
                                   % name)
                vid = _next_id(conn, "vendors", "VND")
                conn.execute(
                    "INSERT INTO vendors (id, name, comment,"
                    " empty_spool_weight, created) VALUES (?, ?, ?, ?, ?)",
                    (vid, name, comment, empty, time.time()))
                store._append_event_locked(
                    conn, "vendor_add",
                    {"id": vid, "name": name})
                conn.commit()
                row = conn.execute(
                    "SELECT id, name, comment, empty_spool_weight,"
                    " created FROM vendors WHERE id = ?",
                    (vid,)).fetchone()
                if row is None:
                    raise _Refusal("the vendor was not stored")
                return "ok", "vendor %s added (%s)." % (name, vid), {
                    "vendor": _vendor_dict(row)}
            finally:
                conn.close()
    # kind == "remove"
    with _LOCK:
        conn = store._connect()
        try:
            row = _resolve_vendor(store, conn, args.get("vendor") or name)
            n = conn.execute(
                "SELECT COUNT(*) FROM filaments WHERE vendor_id = ?",
                (row[0],)).fetchone()[0]
            if n:
                raise _Refusal(
                    "vendor %s still has %d filament type(s) — remove"
                    " them first" % (row[1], n))
            conn.execute("DELETE FROM vendors WHERE id = ?", (row[0],))
            store._append_event_locked(
                conn, "vendor_remove", {"id": row[0], "name": row[1]})
            conn.commit()
            gone = conn.execute(
                "SELECT id FROM vendors WHERE id = ?",
                (row[0],)).fetchone()
            if gone is not None:
                raise _Refusal("the vendor was not removed")
            return "ok", "vendor %s removed." % row[1], {
                "vendor": _vendor_dict(row)}
        finally:
            conn.close()


def _do_filament(store, kind, args, _db_path=None):
    kind = (kind or "").strip().lower()
    if kind not in ("add", "list", "get", "remove"):
        raise _Refusal("unknown filament kind %r — add, list, get, remove"
                       % kind)
    if kind in ("list", "get"):
        if store._missing():
            if kind == "list":
                return "ok", "no filament types yet — the store is empty.", {
                    "filaments": [], "count": 0}
            raise _Refusal("no filament %r" % (args.get("filament"),))
        with _LOCK:
            conn = store._connect()
            try:
                if kind == "list":
                    limit = _clean_limit(args.get("limit"))
                    rows = conn.execute(
                        "SELECT id, vendor_id, name, material, density,"
                        " diameter, weight, spool_weight, color,"
                        " article_number, comment, created"
                        " FROM filaments ORDER BY name LIMIT ?",
                        (limit,)).fetchall()
                    return "ok", "%d filament type(s)." % len(rows), {
                        "filaments": [_filament_dict(r, store, conn)
                                      for r in rows],
                        "count": len(rows)}
                row = _resolve_filament(store, conn,
                                        args.get("filament"))
                return "ok", "filament %s." % row[2], {
                    "filament": _filament_dict(row, store, conn)}
            finally:
                conn.close()
    name = _clean_str(args.get("name") or args.get("filament"), "name",
                      limit=_MAX_NAME)
    if kind == "add":
        material = _clean_str(args.get("material"), "material",
                              limit=_MAX_MATERIAL)
        density = _clean_num(args.get("density"), "density",
                             _MIN_DENSITY, _MAX_DENSITY, required=True)
        diameter = _clean_num(args.get("diameter"), "diameter",
                              _MIN_DIAMETER, _MAX_DIAMETER, required=True)
        weight = _clean_num(args.get("weight"), "weight",
                            0, _MAX_WEIGHT)
        spool_weight = _clean_num(args.get("spool_weight"),
                                  "spool_weight", 0, _MAX_WEIGHT)
        color = _clean_color(args.get("color"))
        article = _clean_str(args.get("article_number"),
                             "article_number", limit=_MAX_ARTICLE,
                             required=False)
        comment = _clean_str(args.get("comment"), "comment",
                             limit=_MAX_COMMENT, required=False)
        with _LOCK:
            conn = store._connect()
            try:
                vendor = _resolve_vendor(store, conn,
                                         args.get("vendor"))
                if conn.execute(
                        "SELECT COUNT(*) FROM filaments").fetchone()[0] \
                        >= _MAX_FILAMENTS:
                    raise _Refusal("at most %d filament types"
                                   % _MAX_FILAMENTS)
                if conn.execute(
                        "SELECT id FROM filaments WHERE vendor_id = ?"
                        " AND name = ?", (vendor[0], name)).fetchone() \
                        is not None:
                    raise _Refusal(
                        "vendor %s already has a filament type named %r"
                        % (vendor[1], name))
                if spool_weight is None:
                    spool_weight = vendor[3]
                fid = _next_id(conn, "filaments", "FIL")
                conn.execute(
                    "INSERT INTO filaments (id, vendor_id, name,"
                    " material, density, diameter, weight, spool_weight,"
                    " color, article_number, comment, created)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (fid, vendor[0], name, material, density, diameter,
                     weight, spool_weight, color, article, comment,
                     time.time()))
                store._append_event_locked(
                    conn, "filament_add",
                    {"id": fid, "vendor_id": vendor[0], "name": name,
                     "material": material})
                conn.commit()
                row = conn.execute(
                    "SELECT id, vendor_id, name, material, density,"
                    " diameter, weight, spool_weight, color,"
                    " article_number, comment, created FROM filaments"
                    " WHERE id = ?", (fid,)).fetchone()
                if row is None:
                    raise _Refusal("the filament type was not stored")
                return "ok", "filament %s added (%s)." % (name, fid), {
                    "filament": _filament_dict(row, store, conn)}
            finally:
                conn.close()
    # kind == "remove"
    with _LOCK:
        conn = store._connect()
        try:
            row = _resolve_filament(store, conn,
                                    args.get("filament") or name)
            n = conn.execute(
                "SELECT COUNT(*) FROM spools WHERE filament_id = ?",
                (row[0],)).fetchone()[0]
            if n:
                raise _Refusal(
                    "filament %s still has %d spool(s) — remove them"
                    " first" % (row[2], n))
            conn.execute("DELETE FROM filaments WHERE id = ?", (row[0],))
            store._append_event_locked(
                conn, "filament_remove",
                {"id": row[0], "name": row[2]})
            conn.commit()
            gone = conn.execute(
                "SELECT id FROM filaments WHERE id = ?",
                (row[0],)).fetchone()
            if gone is not None:
                raise _Refusal("the filament type was not removed")
            return "ok", "filament %s removed." % row[2], {
                "filament": _filament_dict(row, store, conn)}
        finally:
            conn.close()


def _do_spool(store, kind, args, _db_path=None):
    kind = (kind or "").strip().lower()
    if kind not in ("add", "list", "get", "remove", "use"):
        raise _Refusal(
            "unknown spool kind %r — add, list, get, remove, use" % kind)
    if kind in ("list", "get"):
        if store._missing():
            if kind == "list":
                return "ok", "no spools yet — the store is empty.", {
                    "spools": [], "count": 0}
            raise _Refusal("no spool %r" % (args.get("spool"),))
        with _LOCK:
            conn = store._connect()
            try:
                if kind == "list":
                    limit = _clean_limit(args.get("limit"))
                    rows = conn.execute(
                        "SELECT id, filament_id, initial_weight,"
                        " used_weight, location, lot_number, price,"
                        " low_threshold, comment, created FROM spools"
                        " ORDER BY created LIMIT ?",
                        (limit,)).fetchall()
                    return "ok", "%d spool(s)." % len(rows), {
                        "spools": [_spool_dict(r, store, conn)
                                   for r in rows],
                        "count": len(rows)}
                row = _resolve_spool(store, conn, args.get("spool"))
                return "ok", "spool %s." % row[0], {
                    "spool": _spool_dict(row, store, conn)}
            finally:
                conn.close()
    if kind == "add":
        initial = _clean_num(args.get("initial_weight"),
                             "initial_weight", 0, _MAX_WEIGHT)
        used = _clean_num(args.get("used_weight"), "used_weight",
                          0, _MAX_WEIGHT, default=0.0)
        remaining = _clean_num(args.get("remaining_weight"),
                               "remaining_weight", 0, _MAX_WEIGHT)
        location = _clean_str(args.get("location"), "location",
                              limit=_MAX_LOCATION, required=False)
        lot = _clean_str(args.get("lot_number"), "lot_number",
                         limit=_MAX_LOT, required=False)
        price = _clean_int(args.get("price"), "price",
                           0, _MAX_PRICE)
        low = _clean_num(args.get("low_threshold"), "low_threshold",
                         0, _MAX_WEIGHT, default=_DEFAULT_LOW_THRESHOLD)
        comment = _clean_str(args.get("comment"), "comment",
                             limit=_MAX_COMMENT, required=False)
        with _LOCK:
            conn = store._connect()
            try:
                filament = _resolve_filament(store, conn,
                                             args.get("filament"))
                if conn.execute(
                        "SELECT COUNT(*) FROM spools").fetchone()[0] \
                        >= _MAX_SPOOLS:
                    raise _Refusal("at most %d spools" % _MAX_SPOOLS)
                if initial is None:
                    initial = filament[6]
                if initial is None:
                    raise _Refusal(
                        "initial_weight is required — the filament type"
                        " %s has no full-spool weight to fall back on"
                        % filament[2])
                if remaining is not None:
                    if used not in (None, 0.0):
                        raise _Refusal(
                            "give used_weight or remaining_weight, not"
                            " both")
                    used = initial - remaining
                    if used < 0:
                        raise _Refusal(
                            "remaining_weight %s g is more than the"
                            " initial weight %s g"
                            % (remaining, initial))
                if used > initial:
                    raise _Refusal(
                        "used_weight %s g is more than the initial"
                        " weight %s g" % (used, initial))
                sid = _next_id(conn, "spools", "SPL")
                conn.execute(
                    "INSERT INTO spools (id, filament_id,"
                    " initial_weight, used_weight, location, lot_number,"
                    " price, low_threshold, comment, created)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (sid, filament[0], initial, used, location, lot,
                     price, low, comment, time.time()))
                store._append_event_locked(
                    conn, "spool_add",
                    {"id": sid, "filament_id": filament[0],
                     "initial_weight": initial, "used_weight": used})
                conn.commit()
                row = conn.execute(
                    "SELECT id, filament_id, initial_weight,"
                    " used_weight, location, lot_number, price,"
                    " low_threshold, comment, created FROM spools"
                    " WHERE id = ?", (sid,)).fetchone()
                if row is None:
                    raise _Refusal("the spool was not stored")
                return "ok", "spool %s added (%s g of %s)." % (
                    sid, initial - used, filament[2]), {
                    "spool": _spool_dict(row, store, conn)}
            finally:
                conn.close()
    if kind == "use":
        grams = _clean_num(args.get("used_weight"), "used_weight",
                           0, _MAX_WEIGHT, required=True)
        if grams <= 0:
            raise _Refusal("used_weight must be more than 0 g")
        with _LOCK:
            conn = store._connect()
            try:
                row = _resolve_spool(store, conn, args.get("spool"))
                remaining = row[2] - row[3]
                if grams > remaining:
                    raise _Refusal(
                        "spool %s has only %s g left — %s g asked"
                        % (row[0], remaining, grams))
                cur = conn.execute(
                    "UPDATE spools SET used_weight = used_weight + ?"
                    " WHERE id = ? AND used_weight + ?"
                    " <= initial_weight",
                    (grams, row[0], grams))
                if cur.rowcount != 1:
                    raise _Refusal(
                        "spool %s has only %s g left — %s g asked"
                        % (row[0], remaining, grams))
                store._append_event_locked(
                    conn, "spool_use",
                    {"id": row[0], "grams": grams})
                conn.commit()
                after = conn.execute(
                    "SELECT id, filament_id, initial_weight,"
                    " used_weight, location, lot_number, price,"
                    " low_threshold, comment, created FROM spools"
                    " WHERE id = ?", (row[0],)).fetchone()
                if after is None or abs(after[3] - (row[3] + grams)) > 1e-9:
                    raise _Refusal("the use was not stored")
                left = after[2] - after[3]
                return "ok", "%s g used from spool %s — %s g left." % (
                    grams, row[0], left), {
                    "spool": _spool_dict(after, store, conn)}
            finally:
                conn.close()
    # kind == "remove"
    with _LOCK:
        conn = store._connect()
        try:
            row = _resolve_spool(store, conn, args.get("spool"))
            conn.execute("DELETE FROM spools WHERE id = ?", (row[0],))
            store._append_event_locked(
                conn, "spool_remove", {"id": row[0]})
            conn.commit()
            gone = conn.execute(
                "SELECT id FROM spools WHERE id = ?",
                (row[0],)).fetchone()
            if gone is not None:
                raise _Refusal("the spool was not removed")
            return "ok", "spool %s removed." % row[0], {
                "spool": _spool_dict(row, store, conn)}
        finally:
            conn.close()


def _do_status(store, args, _db_path=None):
    if store._missing():
        return "ok", "filament: no spools yet, ledger empty.", {
            "vendors": 0, "filaments": 0, "spools": 0,
            "total_remaining_weight": 0.0, "total_remaining_length_m": 0.0,
            "low_spools": [], "ledger_events": 0}
    ok, n_events = store.verify_chain()
    with _LOCK:
        conn = store._connect()
        try:
            vendors = conn.execute(
                "SELECT COUNT(*) FROM vendors").fetchone()[0]
            filaments = conn.execute(
                "SELECT COUNT(*) FROM filaments").fetchone()[0]
            rows = conn.execute(
                "SELECT s.id, s.filament_id, s.initial_weight,"
                " s.used_weight, s.location, s.lot_number, s.price,"
                " s.low_threshold, s.comment, s.created,"
                " f.density, f.diameter FROM spools s"
                " JOIN filaments f ON f.id = s.filament_id").fetchall()
            total_weight = 0.0
            total_length = 0.0
            low = []
            for r in rows:
                remaining = r[2] - r[3]
                if remaining < 0:
                    remaining = 0.0
                total_weight += remaining
                total_length += _remaining_length_m(remaining, r[10],
                                                    r[11])
                if remaining <= r[7]:
                    low.append(_spool_dict(r[:10], store, conn))
            return "ok", (
                "filament: %d vendor(s), %d filament type(s), %d"
                " spool(s), %s g (%s m) remaining, %d low spool(s),"
                " ledger %s." % (vendors, filaments, len(rows),
                                 round(total_weight, 2),
                                 round(total_length, 2), len(low),
                                 "intact" if ok else "BROKEN")), {
                "vendors": vendors, "filaments": filaments,
                "spools": len(rows),
                "total_remaining_weight": round(total_weight, 2),
                "total_remaining_length_m": round(total_length, 2),
                "low_spools": low, "ledger_events": n_events,
                "ledger_ok": ok,
                "ledger_message": ("ledger verifies over %d event(s)"
                                   % n_events) if ok
                else "ledger is BROKEN"}
        finally:
            conn.close()


def _do_events(store, args, _db_path=None):
    kind = (args.get("kind") or "list").strip().lower()
    if kind not in ("list", "verify"):
        raise _Refusal("unknown events kind %r — list, verify" % kind)
    if store._missing():
        if kind == "verify":
            return "ok", "ledger verifies over 0 event(s).", {
                "ok": True, "events": 0}
        return "ok", "no events yet — the ledger is empty.", {
            "events": [], "count": 0}
    if kind == "verify":
        ok, n = store.verify_chain()
        if not ok:
            return ("error",
                    "the ledger chain is BROKEN (%d event(s) — re-check"
                    " before trusting any of it)" % n,
                    {"ok": False, "events": n})
        return "ok", "ledger verifies over %d event(s)." % n, {
            "ok": True, "events": n}
    with _LOCK:
        conn = store._connect()
        try:
            limit = _clean_limit(args.get("limit"))
            rows = conn.execute(
                "SELECT id, ts, kind, payload, prev_hash, hash"
                " FROM events ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
            events = [{"id": r[0], "ts": r[1], "kind": r[2],
                       "payload": json.loads(r[3]),
                       "prev_hash": r[4], "hash": r[5]} for r in rows]
            return "ok", "%d event(s)." % len(events), {
                "events": events, "count": len(events)}
        finally:
            conn.close()


def _handler(action=None, kind=None, vendor=None, filament=None,
             spool=None, name=None, material=None, density=None,
             diameter=None, weight=None, spool_weight=None, color=None,
             article_number=None, comment=None, location=None,
             lot_number=None, price=None, initial_weight=None,
             used_weight=None, remaining_weight=None,
             low_threshold=None, limit=None,
             _store=None, _db_path=None):
    """The funnel entry point for `filament`.

    action: vendor | filament | spool | status | events. kind: the
    sub-action (add/list/get/remove/use/verify). The rest are the
    named arguments.
    """
    store = _store or _FilamentStore(_db_path)
    args = {"kind": kind, "vendor": vendor, "filament": filament,
            "spool": spool, "name": name, "material": material,
            "density": density, "diameter": diameter,
            "weight": weight, "spool_weight": spool_weight,
            "color": color, "article_number": article_number,
            "comment": comment, "location": location,
            "lot_number": lot_number, "price": price,
            "initial_weight": initial_weight,
            "used_weight": used_weight,
            "remaining_weight": remaining_weight,
            "low_threshold": low_threshold, "limit": limit}
    try:
        action = _clean_str(action, "action", limit=20)
        if action not in _ACTIONS:
            raise _Refusal("unknown action %r — %s"
                           % (action, ", ".join(_ACTIONS)))
        if action == "vendor":
            status, msg, data = _do_vendor(store, kind, args, _db_path)
        elif action == "filament":
            status, msg, data = _do_filament(store, kind, args, _db_path)
        elif action == "spool":
            status, msg, data = _do_spool(store, kind, args, _db_path)
        elif action == "status":
            status, msg, data = _do_status(store, args, _db_path)
        elif action == "events":
            status, msg, data = _do_events(store, args, _db_path)
        else:  # pragma: no cover - guarded by the check above
            raise _Refusal("unknown action %r" % action)
    except _Refusal as exc:
        status, msg, data = "error", str(exc), None
    except sqlite3.Error as exc:
        status, msg, data = "error", "the store failed: %s" % exc, None
    if status == "error":
        return {"status": "error", "message": msg}
    out = {"status": "ok", "message": msg}
    if data is not None:
        out.update(data)
    return out


def register_all(registry):
    registry.register(
        name="filament",
        description=("Track 3D-printer filament spools: vendors,"
                     " filament types (material, density, diameter,"
                     " colour, weight) and spools with remaining"
                     " weight and length, usage recording and"
                     " low-stock warnings, over a hash-chained"
                     " ledger."),
        handler=_handler,
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "vendor, filament, spool,"
                                          " status or events",
                           "enum": list(_ACTIONS)},
                "kind": {"type": "string",
                         "description": "the sub-action: add, list,"
                                        " get, remove, use or verify"},
                "vendor": {"type": "string",
                           "description": "a vendor id (VND-0001) or"
                                          " a vendor name"},
                "filament": {"type": "string",
                             "description": "a filament id (FIL-0001)"
                                            " or a filament name"},
                "spool": {"type": "string",
                          "description": "a spool id (SPL-0001)"},
                "name": {"type": "string",
                         "description": "a vendor or filament name"},
                "material": {"type": "string",
                             "description": "the filament material"
                                            " (PLA, PETG, ABS, TPU,"
                                            " ...)"},
                "density": {"type": "number",
                            "description": "the filament density in"
                                           " g/cm3 (0.1-10)"},
                "diameter": {"type": "number",
                             "description": "the filament diameter in"
                                            " mm (0.1-10)"},
                "weight": {"type": "number",
                           "description": "the full-spool filament"
                                          " weight in grams"},
                "spool_weight": {"type": "number",
                                 "description": "the empty-spool"
                                                " weight in grams"},
                "color": {"type": "string",
                          "description": "the filament colour as a"
                                         " hex code like #FF0000"},
                "article_number": {"type": "string",
                                   "description": "the filament"
                                                  " article number"},
                "comment": {"type": "string",
                            "description": "a comment"},
                "location": {"type": "string",
                             "description": "where the spool is"
                                            " stored"},
                "lot_number": {"type": "string",
                               "description": "the spool lot number"},
                "price": {"type": "integer",
                          "description": "the spool price in integer"
                                         " minor units"},
                "initial_weight": {"type": "number",
                                   "description": "the spool initial"
                                                  " weight in grams"},
                "used_weight": {"type": "number",
                                "description": "grams used (spool"
                                               " add) or grams to"
                                               " record as used"
                                               " (spool use)"},
                "remaining_weight": {"type": "number",
                                     "description": "the spool"
                                                    " remaining weight"
                                                    " in grams (spool"
                                                    " add, instead of"
                                                    " used_weight)"},
                "low_threshold": {"type": "number",
                                  "description": "the low-stock"
                                                 " threshold in grams"
                                                 " (default 100)"},
                "limit": {"type": "integer",
                          "description": "the maximum rows to return"
                                         " (1-100)"},
            },
            "required": ["action"],
        },
    )
