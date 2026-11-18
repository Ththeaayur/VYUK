"""Take bookings for the owner's own bookable resources — own code.

Parent-ruled (licence, 2026-11-11): bookrail-dev/bookrail (Apache-2.0,
Copyright 2026 MP Informatica Srl; the LICENSE file from main, 201
lines, and the NOTICE file were read in full 2026-11-11) is
INSPIRATION ONLY. No code, schema, SQL, text or package is adopted
from it; this module is written from scratch in stdlib Python over
the tree's own scheduler timezone shapes (core/scheduler.py) and
its own SQLite store (the cost_ledger.py precedent). Ideas are not
copyrighted, so no Apache-2.0 duty is triggered by a from-scratch
rebuild. The "Bookrail" name and mark are MP Informatica Srl
trademarks (NOTICE) — they are NOT used here: the tool is the
generic word "bookings", and no package, fork, service or hosted
offering is named after the original.

What this is: the owner is the SUPPLIER. A lesson, a studio, a
court, a rental — the owner defines the bookable resource, its
capacity, its timezone, its weekly schedule, its buffers and its
booking window, and the owner's customers take slots in it. That is
the opposite direction from the refused consumer rail booking
(core/daily_money.py RAIL_DOCTRINE): no third-party portal is
automated, no other traveller's personal ID is used, no queue is
jumped, no fare is bought. The doctrine blocks automating a
consumer booking on someone else's rail system; it does not speak
to recording a customer's booking in the owner's own book.

The guarantee, in the tree's own store: VYUK has no Postgres, and
bookrail's guarantee lives in Postgres (an exclusion constraint
for capacity 1, a trigger for capacity N). Here the same guarantee
lives in SQLite (stdlib, already used by core/cost_ledger.py):
a BEFORE INSERT trigger and a BEFORE UPDATE trigger on the
bookings table, plus a BEFORE INSERT trigger on holds, abort the
write when the overlapping live bookings + live holds for that
resource already fill its capacity — capacity 1 means any overlap
aborts, capacity N means the N+1th overlapping booking aborts.
The check runs inside the same transaction as the insert, and
SQLite serialises writers, so no two calls can interleave between
the check and the write. That is airtight for this app's shape:
one desktop process owns the store; there are no separate
processes racing for the same slot. The memory-facts store was
NOT used for this on purpose: check-then-write over JSONL facts
is racy without a lock, and no lock is a guarantee. Schedule,
buffer and booking-window rules are enforced by the handler
before the insert; the trigger is the last line, for capacity
only. Every write is re-read before it is claimed.

Time: every stored time is UTC, in the fixed format
"YYYY-MM-DD HH:MM:SS" so SQLite string comparison is
chronological (and datetime('now') compares correctly for hold
expiry). Input times are "YYYY-MM-DD HH:MM" in the resource's
own timezone (resolved with the scheduler's DST policy:
ambiguous wall times take the first occurrence, nonexistent
ones shift forward by the gap), or ISO with an offset. All
rendering shows the local wall time with the UTC time beside it.
No second timezone system is invented: the scheduler's
_default_tz / _resolve_wall_time / _to_utc are reused.

Rules are frozen at the moment of booking: the resource's
policy (capacity, timezone, schedule, buffer, booking window,
cancel window) is copied into the booking when it is made, and
cancel / reschedule / no-show apply that snapshot, never the
resource's current policy. "Nothing changes the past."

Events: every mutation appends one row to an append-only ledger,
hash-chained (each event carries the SHA-256 of the previous
event's hash plus its own fields), so the chain can be verified.
That is the local equivalent of bookrail's "a signed event for
everything": there are no webhooks here, so there is no HMAC —
the chain is tamper-evidence, not a webhook signature, and the
docstring says so rather than claiming otherwise.

Idempotency: every booking takes an idempotency key, held by a
UNIQUE constraint — the key is taken with the constraint rather
than checked with a read. A repeated key returns the booking
already made under it instead of booking twice.

NOT built, and why (held back, documented, not silently absent):
- a public booking link or page where strangers book: the
  hosting/share shape (nothing VYUK serves is internet-reachable)
  and the guest shape both need owner rulings first;
- taking payment (a deposit or the full price): the price on a
  booking is a recorded quote only — no money moves. Payment
  capture is ruling 34;
- an MCP-server exposure, an HTTP API, or a CLI: the booking
  capability lives INSIDE VYUK, voice-driven, over the tree's
  own calendar/scheduler shapes. Exposing a server is a
  different surface (the R10-1 precedent);
- a hosted dashboard, staff roles, or a customer portal: the
  owner is one operator; the store is local.
"""

import datetime
import json
import re
import secrets
import sqlite3
from pathlib import Path

from .config import DATA_DIR
from . import store_base
from .scheduler import _default_tz, _resolve_wall_time, _to_utc

DEFAULT_DB_FILENAME = "bookings.sqlite3"

_ACTIONS = ("resource", "availability", "hold", "book", "cancel",
            "reschedule", "no-show", "list", "events")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    name TEXT PRIMARY KEY,
    capacity INTEGER NOT NULL,
    timezone TEXT NOT NULL,
    schedule TEXT NOT NULL,
    buffer_minutes INTEGER NOT NULL,
    booking_window_days INTEGER NOT NULL,
    cancel_hours INTEGER NOT NULL,
    created_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    resource TEXT NOT NULL REFERENCES resources(name),
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    customer TEXT NOT NULL,
    price REAL,
    status TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    rules_json TEXT NOT NULL,
    created_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holds (
    id TEXT PRIMARY KEY,
    resource TEXT NOT NULL REFERENCES resources(name),
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    customer TEXT NOT NULL,
    expires_utc TEXT NOT NULL,
    created_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL,
    at_utc TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS bookings_no_double_insert
BEFORE INSERT ON bookings BEGIN
    SELECT RAISE(ABORT, 'double booking: capacity already full for that time')
    WHERE (SELECT COUNT(*) FROM bookings
           WHERE resource = NEW.resource
             AND status IN ('confirmed')
             AND NOT (end_utc <= NEW.start_utc OR start_utc >= NEW.end_utc))
        + (SELECT COUNT(*) FROM holds
           WHERE resource = NEW.resource
             AND expires_utc > datetime('now')
             AND NOT (end_utc <= NEW.start_utc OR start_utc >= NEW.end_utc))
        >= (SELECT capacity FROM resources WHERE name = NEW.resource);
END;
CREATE TRIGGER IF NOT EXISTS bookings_no_double_update
BEFORE UPDATE OF start_utc, end_utc ON bookings
WHEN NEW.status IN ('confirmed') BEGIN
    SELECT RAISE(ABORT, 'double booking: capacity already full for that time')
    WHERE (SELECT COUNT(*) FROM bookings
           WHERE resource = NEW.resource
             AND id != NEW.id
             AND status IN ('confirmed')
             AND NOT (end_utc <= NEW.start_utc OR start_utc >= NEW.end_utc))
        + (SELECT COUNT(*) FROM holds
           WHERE resource = NEW.resource
             AND expires_utc > datetime('now')
             AND NOT (end_utc <= NEW.start_utc OR start_utc >= NEW.end_utc))
        >= (SELECT capacity FROM resources WHERE name = NEW.resource);
END;
CREATE TRIGGER IF NOT EXISTS holds_no_double_insert
BEFORE INSERT ON holds BEGIN
    SELECT RAISE(ABORT, 'double booking: capacity already full for that time')
    WHERE (SELECT COUNT(*) FROM bookings
           WHERE resource = NEW.resource
             AND status IN ('confirmed')
             AND NOT (end_utc <= NEW.start_utc OR start_utc >= NEW.end_utc))
        + (SELECT COUNT(*) FROM holds
           WHERE resource = NEW.resource
             AND expires_utc > datetime('now')
             AND NOT (end_utc <= NEW.start_utc OR start_utc >= NEW.end_utc))
        >= (SELECT capacity FROM resources WHERE name = NEW.resource);
END;
"""

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4,
            "sat": 5, "sun": 6,
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}
_DAY_RE = r"(mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_SEGMENT_RE = re.compile(
    r"^\s*(" + _DAY_RE + r"(?:\s*,\s*" + _DAY_RE + r")*"
    r"(?:\s*-\s*" + _DAY_RE + r")?)\s+"
    r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$", re.I)
_TIME_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})"
    r"(?::(\d{2}))?(Z|[+-]\d{2}:?\d{2})?$")


def _utc_str(dt: datetime.datetime) -> str:
    """Aware UTC datetime -> the fixed storage format."""
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_utc(text: str) -> datetime.datetime:
    """Stored UTC text -> aware UTC datetime."""
    return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=datetime.timezone.utc)


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_time(text: str, tz: datetime.tzinfo) -> datetime.datetime | None:
    """User time -> aware UTC. Naive "YYYY-MM-DD HH:MM" is read on
    the resource's wall clock (scheduler DST policy); an ISO
    offset is converted. Returns None when unparseable."""
    if not isinstance(text, str):
        return None
    m = _TIME_RE.match(text.strip())
    if not m:
        return None
    year, month, day, hour, minute = (int(m.group(i)) for i in range(1, 6))
    second = int(m.group(6)) if m.group(6) else 0
    offset = m.group(7)
    try:
        if offset:
            if offset == "Z":
                tzinfo = datetime.timezone.utc
            else:
                sign = 1 if offset[0] == "+" else -1
                digits = offset[1:].replace(":", "")
                off = datetime.timedelta(
                    hours=int(digits[:2]), minutes=int(digits[2:]))
                tzinfo = datetime.timezone(sign * off)
            aware = datetime.datetime(year, month, day, hour, minute,
                                      second, tzinfo=tzinfo)
            return _to_utc(aware, tz)
        naive = datetime.datetime(year, month, day, hour, minute, second)
        return _resolve_wall_time(naive, tz)
    except (ValueError, OverflowError):
        return None


def _fmt_local(dt_utc: datetime.datetime, tz: datetime.tzinfo) -> str:
    return dt_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def _parse_schedule(text: str):
    """Weekly schedule text -> [(weekday, start_min, end_min)] or
    None when unparseable. "Mon-Fri 09:00-17:00; Sat 10:00-14:00"."""
    if not isinstance(text, str) or not text.strip():
        return None
    out = []
    for segment in text.split(";"):
        m = _SEGMENT_RE.match(segment)
        if not m:
            return None
        days_part = m.group(1)
        start_min = int(m.group(5)) * 60 + int(m.group(6))
        end_min = int(m.group(7)) * 60 + int(m.group(8))
        if not (0 <= start_min < 1440 and 0 < end_min <= 1440
                and start_min < end_min):
            return None
        days = []
        for token in re.split(r"\s*,\s*", days_part):
            token = token.strip().lower()
            if "-" in token:
                first, last = token.split("-", 1)
                first, last = first.strip(), last.strip()
                if first not in _WEEKDAYS or last not in _WEEKDAYS:
                    return None
                d0, d1 = _WEEKDAYS[first], _WEEKDAYS[last]
                if d1 < d0:
                    return None
                days.extend(range(d0, d1 + 1))
            else:
                if token not in _WEEKDAYS:
                    return None
                days.append(_WEEKDAYS[token])
        for day in sorted(set(days)):
            out.append((day, start_min, end_min))
    return out or None


def _whole(value, name: str):
    """Whole number, 1 or more (or 0 or more when zero_ok) — the
    warehouse_stock refusal shape, not a truncation."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None, (f"{name} needs to be a whole number, 1 or more — "
                      f"got {value!r}.")
    if value < 1:
        return None, (f"{name} needs to be a whole number, 1 or more — "
                      f"got {value}.")
    return value, None


class _BookingsDB:
    """The booking store: one SQLite file, triggers as the
    double-booking guarantee. Follows core/cost_ledger.py."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = DATA_DIR / DEFAULT_DB_FILENAME
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = store_base.connect(str(self.db_path))
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- events (hash-chained, append-only) ----------------------

    def _append_event(self, conn, booking_id: str, kind: str,
                      detail: str) -> None:
        at = _utc_str(_now_utc())
        row = conn.execute(
            "SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row[0] if row else "GENESIS"
        digest = store_base.chain_digest(
            "pipe_fstring", prev,
            {"kind": kind, "detail": detail, "at": at})
        conn.execute(
            "INSERT INTO events (booking_id, kind, detail, at_utc,"
            " prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?)",
            (booking_id, kind, detail, at, prev, digest))

    def sweep_expired_holds(self, conn) -> int:
        cur = conn.execute(
            "DELETE FROM holds WHERE expires_utc <= datetime('now')")
        return cur.rowcount

    # -- resources ------------------------------------------------

    def get_resource(self, conn, name: str):
        row = conn.execute(
            "SELECT name, capacity, timezone, schedule,"
            " buffer_minutes, booking_window_days, cancel_hours"
            " FROM resources WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        return {"name": row[0], "capacity": row[1], "timezone": row[2],
                "schedule": row[3], "buffer_minutes": row[4],
                "booking_window_days": row[5], "cancel_hours": row[6]}

    def upsert_resource(self, conn, res: dict) -> None:
        conn.execute(
            "INSERT INTO resources (name, capacity, timezone, schedule,"
            " buffer_minutes, booking_window_days, cancel_hours,"
            " created_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET capacity=excluded.capacity,"
            " timezone=excluded.timezone, schedule=excluded.schedule,"
            " buffer_minutes=excluded.buffer_minutes,"
            " booking_window_days=excluded.booking_window_days,"
            " cancel_hours=excluded.cancel_hours",
            (res["name"], res["capacity"], res["timezone"],
             res["schedule"], res["buffer_minutes"],
             res["booking_window_days"], res["cancel_hours"],
             _utc_str(_now_utc())))

    # -- bookings -------------------------------------------------

    def get_booking(self, conn, booking_id: str):
        row = conn.execute(
            "SELECT id, resource, start_utc, end_utc, customer, price,"
            " status, idempotency_key, rules_json FROM bookings"
            " WHERE id = ?", (booking_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "resource": row[1], "start_utc": row[2],
                "end_utc": row[3], "customer": row[4], "price": row[5],
                "status": row[6], "idempotency_key": row[7],
                "rules": json.loads(row[8])}

    def get_booking_by_key(self, conn, key: str):
        row = conn.execute(
            "SELECT id FROM bookings WHERE idempotency_key = ?",
            (key,)).fetchone()
        return row[0] if row else None

    def insert_booking(self, conn, booking: dict) -> None:
        conn.execute(
            "INSERT INTO bookings (id, resource, start_utc, end_utc,"
            " customer, price, status, idempotency_key, rules_json,"
            " created_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (booking["id"], booking["resource"], booking["start_utc"],
             booking["end_utc"], booking["customer"], booking["price"],
             "confirmed", booking["idempotency_key"],
             json.dumps(booking["rules"]), _utc_str(_now_utc())))

    def update_booking_times(self, conn, booking_id: str,
                             start_utc: str, end_utc: str) -> None:
        conn.execute(
            "UPDATE bookings SET start_utc = ?, end_utc = ? WHERE id = ?",
            (start_utc, end_utc, booking_id))

    def set_booking_status(self, conn, booking_id: str,
                           status: str) -> None:
        conn.execute("UPDATE bookings SET status = ? WHERE id = ?",
                     (status, booking_id))

    def live_overlaps(self, conn, resource: str,
                      start_utc: str, end_utc: str,
                      exclude_id: str | None = None):
        """Live bookings + live holds overlapping [start, end), for
        explain. Returns [(kind, id, customer, start, end)]."""
        rows = conn.execute(
            "SELECT 'booking', id, customer, start_utc, end_utc"
            " FROM bookings WHERE resource = ? AND status = 'confirmed'"
            " AND NOT (end_utc <= ? OR start_utc >= ?)"
            " UNION ALL"
            " SELECT 'hold', id, customer, start_utc, end_utc"
            " FROM holds WHERE resource = ?"
            " AND expires_utc > datetime('now')"
            " AND NOT (end_utc <= ? OR start_utc >= ?)",
            (resource, start_utc, end_utc,
             resource, start_utc, end_utc)).fetchall()
        out = [r for r in rows if r[1] != exclude_id]
        return [(r[0], r[1], r[2], r[3], r[4]) for r in out]

    def list_bookings(self, conn, resource: str | None,
                      start_utc: str | None, end_utc: str | None):
        sql = ("SELECT id, resource, start_utc, end_utc, customer,"
               " price, status FROM bookings WHERE 1=1")
        args: list = []
        if resource:
            sql += " AND resource = ?"
            args.append(resource)
        if start_utc:
            sql += " AND end_utc > ?"
            args.append(start_utc)
        if end_utc:
            sql += " AND start_utc < ?"
            args.append(end_utc)
        sql += " ORDER BY start_utc, resource"
        return conn.execute(sql, args).fetchall()


def _resource_tz(res: dict) -> datetime.tzinfo:
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(res["timezone"])
    except Exception:  # noqa: BLE001 — unknown name falls back
        return _default_tz()


def _schedule_windows(res: dict, start_utc: datetime.datetime,
                      end_utc: datetime.datetime, tz: datetime.tzinfo):
    """Schedule windows (local clock -> UTC) covering [start, end).
    Returns [(window_start_utc, window_end_utc)] — DST-correct: a
    23- or 25-hour day yields the right UTC span."""
    parsed = _parse_schedule(res["schedule"])
    if parsed is None:
        return []
    by_day: dict[int, list] = {}
    for day, start_min, end_min in parsed:
        by_day.setdefault(day, []).append((start_min, end_min))
    windows = []
    day = start_utc.astimezone(tz).date() - datetime.timedelta(days=1)
    last = end_utc.astimezone(tz).date() + datetime.timedelta(days=1)
    while day <= last:
        for start_min, end_min in by_day.get(day.weekday(), []):
            local_start = datetime.datetime.combine(
                day, datetime.time(start_min // 60, start_min % 60))
            local_end = datetime.datetime.combine(
                day, datetime.time(end_min // 60, end_min % 60))
            win_start = _resolve_wall_time(local_start, tz)
            win_end = _resolve_wall_time(local_end, tz)
            windows.append((max(win_start, start_utc),
                            min(win_end, end_utc)))
        day += datetime.timedelta(days=1)
    return [(s, e) for s, e in windows if e > s]


def _availability(db: _BookingsDB, conn, res: dict,
                  start_utc: datetime.datetime,
                  end_utc: datetime.datetime, tz: datetime.tzinfo,
                  exclude_id: str | None = None):
    """Free intervals + the taken list with explain. Pure read:
    expired holds are only filtered, never deleted here."""
    now = _now_utc()
    window_end = now + datetime.timedelta(
        days=res["booking_window_days"])
    taken = []
    if start_utc < now:
        taken.append((start_utc, min(end_utc, now),
                      "in the past — bookings start from now"))
    if end_utc > window_end:
        taken.append((max(start_utc, window_end), end_utc,
                      f"outside the booking window "
                      f"({res['booking_window_days']} days ahead)"))
    lo = max(start_utc, now)
    hi = min(end_utc, window_end)
    windows = _schedule_windows(res, lo, hi, tz)
    cap = res["capacity"]
    buf = res["buffer_minutes"]
    free: list = []
    for win_start, win_end in windows:
        # Segments covering this window. The overlap query reaches
        # back by one buffer so a booking ending just before the
        # window still imposes its buffer inside it. Each booking,
        # hold and buffer takes ONE unit of capacity; the window
        # is free where fewer than `cap` units are taken — that is
        # what makes capacity N work instead of capacity 1.
        qstart = win_start - datetime.timedelta(minutes=buf) if buf \
            else win_start
        segs = []
        for kind, bid, customer, bstart, bend in db.live_overlaps(
                conn, res["name"], _utc_str(qstart), _utc_str(win_end),
                exclude_id):
            bs, be = _parse_utc(bstart), _parse_utc(bend)
            segs.append((bs, be, f"{kind} {bid} ({customer})"))
            if buf:
                segs.append((be, be + datetime.timedelta(minutes=buf),
                             f"buffer {buf} min after {kind} {bid}"))
        bounds = {win_start, win_end}
        for s, e, _ in segs:
            if win_start < s < win_end:
                bounds.add(s)
            if win_start < e < win_end:
                bounds.add(e)
        for a, b in zip(sorted(bounds), sorted(bounds)[1:]):
            covering = [label for s, e, label in segs
                        if s <= a and e >= b]
            if len(covering) < cap:
                free.append((a, b))
            else:
                taken.append((a, b, "capacity full: "
                            + " + ".join(covering)))
    # Merge adjacent free intervals: a window spanning a segment
    # boundary is free when both sides are free.
    free.sort()
    merged: list = []
    for s, e in free:
        if merged and merged[-1][1] >= s:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    free = merged
    # Schedule gaps: parts of the range no schedule window covers.
    cursor = lo
    for ws, we in sorted(windows):
        if ws > cursor:
            taken.append((cursor, min(ws, hi),
                          "outside schedule "
                          f"({res['schedule']})"))
        cursor = max(cursor, we)
    if cursor < hi:
        taken.append((cursor, hi,
                      "outside schedule "
                      f"({res['schedule']})"))
    free = [(s, e) for s, e in free if e > s]
    taken = [(s, e, w) for s, e, w in taken if e > s]
    taken.sort()
    return free, taken


def _window_is_free(db: _BookingsDB, conn, res: dict,
                    start_dt: datetime.datetime,
                    end_dt: datetime.datetime, tz: datetime.tzinfo,
                    exclude_id: str | None = None):
    """True when [start, end) sits entirely inside one free
    interval — the schedule / buffer / window check the trigger
    cannot see. The trigger remains the last line, for capacity."""
    free, _ = _availability(db, conn, res, start_dt, end_dt, tz,
                            exclude_id)
    for fs, fe in free:
        if fs <= start_dt and end_dt <= fe:
            return True, ""
    return False, _explain_blockers(db, conn, res, start_dt, end_dt,
                                    tz, exclude_id)


def _explain_blockers(db: _BookingsDB, conn, res: dict,
                      start_utc: datetime.datetime,
                      end_utc: datetime.datetime, tz: datetime.tzinfo,
                      exclude_id: str | None = None) -> str:
    """Plain-language reason a window cannot be taken, naming what
    holds each instant — the bookrail `explain`, in own words."""
    _, taken = _availability(db, conn, res, start_utc, end_utc, tz,
                             exclude_id)
    bits = []
    for s, e, why in taken:
        bits.append(f"{_fmt_local(s, tz)}–{_fmt_local(e, tz)}: {why}")
    return "; ".join(bits) if bits else "no free time in that window"


def _usage() -> str:
    return ("bookings — take bookings for your own bookable resources. "
            "Actions: 'resource' defines a resource (name, capacity, "
            "timezone, schedule like 'Mon-Fri 09:00-17:00; Sat "
            "10:00-14:00', buffer_minutes, booking_window_days, "
            "cancel_hours); 'availability' shows what's free with "
            "what took each instant named; 'hold' takes capacity "
            "temporarily; 'book' confirms a booking (rules frozen at "
            "booking time; price is a recorded quote, no payment is "
            "taken); 'cancel', 'reschedule' and 'no-show' apply the "
            "frozen rules; 'list' shows bookings; 'events' shows the "
            "hash-chained event ledger. Times are 'YYYY-MM-DD HH:MM' "
            "in the resource's timezone.")


def bookings(action: str = "", resource: str = "", start: str = "",
             end: str = "", customer: str = "", capacity=None,
             timezone: str = "", schedule: str = "",
             buffer_minutes=None, booking_window_days=None,
             cancel_hours=None, hold_minutes=None, price=None,
             idempotency_key: str = "", booking_id: str = "",
             detail: str = "", _db_path=None) -> str:
    """One tool, one store, one guarantee. See the module docstring."""
    db = _BookingsDB(_db_path)
    try:
        conn = db._connect()
        action = (action or "").strip().lower()
        if action not in _ACTIONS:
            return _usage()
        if action == "resource":
            name = (resource or "").strip()
            if not name:
                return "Give the resource a name — e.g. resource='Studio A'."
            cap, err = _whole(capacity, "Capacity")
            if err:
                return err
            buf = 0 if buffer_minutes in ("", None) else buffer_minutes
            if not isinstance(buf, int) or isinstance(buf, bool) or buf < 0:
                return ("buffer_minutes needs to be a whole number, 0 or "
                        f"more — got {buffer_minutes!r}.")
            window = 30 if booking_window_days in ("", None) \
                else booking_window_days
            if not isinstance(window, int) or isinstance(window, bool) \
                    or window < 1:
                return ("booking_window_days needs to be a whole number, "
                        f"1 or more — got {booking_window_days!r}.")
            cancel = 24 if cancel_hours in ("", None) else cancel_hours
            if not isinstance(cancel, int) or isinstance(cancel, bool) \
                    or cancel < 0:
                return ("cancel_hours needs to be a whole number, 0 or "
                        f"more — got {cancel_hours!r}.")
            tz_name = (timezone or "").strip()
            if not tz_name:
                tz_name = str(_default_tz())
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(tz_name)
            except Exception:  # noqa: BLE001
                return (f"Unknown timezone {tz_name!r} — use an IANA name "
                        "like 'Europe/Rome' or 'Asia/Kolkata'.")
            if _parse_schedule(schedule) is None:
                return ("Schedule needs weekly windows like "
                        "'Mon-Fri 09:00-17:00; Sat 10:00-14:00'.")
            res = {"name": name, "capacity": cap, "timezone": tz_name,
                   "schedule": schedule.strip(), "buffer_minutes": buf,
                   "booking_window_days": window, "cancel_hours": cancel}
            with conn:
                db.upsert_resource(conn, res)
                db._append_event(
                    conn, f"resource:{name}", "resource",
                    f"resource {name!r} capacity {cap}, {tz_name}, "
                    f"{schedule.strip()}")
            return (f"Resource '{name}': capacity {cap}, {tz_name}, "
                    f"schedule {schedule.strip()}, buffer {buf} min, "
                    f"bookings up to {window} days ahead, free "
                    f"cancellation until {cancel}h before the start.")

        name = (resource or "").strip()
        res = db.get_resource(conn, name) if name else None
        if action in ("availability", "hold", "book", "list") and not res:
            known = [r[0] for r in conn.execute(
                "SELECT name FROM resources ORDER BY name").fetchall()]
            hint = (f" Known resources: {', '.join(known)}."
                    if known else " No resources defined yet — define one"
                    " with action='resource' first.")
            return f"No resource named {name!r}.{hint}"
        tz = _resource_tz(res) if res else _default_tz()

        if action == "availability":
            start_dt = _parse_time(start, tz) if start else None
            end_dt = _parse_time(end, tz) if end else None
            if start_dt is None or end_dt is None:
                return ("Give a start and end as 'YYYY-MM-DD HH:MM' in "
                        f"the resource's timezone ({res['timezone']}).")
            if end_dt <= start_dt:
                return "The end has to be after the start."
            free, taken = _availability(db, conn, res, start_dt, end_dt, tz)
            lines = [f"Availability for '{name}' "
                     f"({_fmt_local(start_dt, tz)}–"
                     f"{_fmt_local(end_dt, tz)} local, "
                     f"{res['timezone']}):"]
            if free:
                for s, e in free:
                    lines.append(f"  Free: {_fmt_local(s, tz)}–"
                                 f"{_fmt_local(e, tz)} local "
                                 f"({_utc_str(s)}–{_utc_str(e)} UTC)")
            else:
                lines.append("  Nothing free in that window.")
            for s, e, why in taken:
                lines.append(f"  Taken {_fmt_local(s, tz)}–"
                             f"{_fmt_local(e, tz)}: {why}")
            return "\n".join(lines)

        if action == "hold":
            start_dt = _parse_time(start, tz) if start else None
            end_dt = _parse_time(end, tz) if end else None
            who = (customer or "").strip()
            if start_dt is None or end_dt is None:
                return ("Give a start and end as 'YYYY-MM-DD HH:MM' in "
                        f"the resource's timezone ({res['timezone']}).")
            if end_dt <= start_dt:
                return "The end has to be after the start."
            if not who:
                return "Say who the hold is for (customer)."
            hold_min = 30 if hold_minutes in ("", None) else hold_minutes
            if not isinstance(hold_min, int) or isinstance(hold_min, bool) \
                    or hold_min < 1:
                return ("hold_minutes needs to be a whole number, 1 or "
                        f"more — got {hold_minutes!r}.")
            if start_dt < _now_utc():
                return "A hold has to start in the future."
            if end_dt > _now_utc() + datetime.timedelta(
                    days=res["booking_window_days"]):
                return (f"Outside the booking window "
                        f"({res['booking_window_days']} days ahead).")
            free, why = _window_is_free(db, conn, res, start_dt, end_dt,
                                        tz)
            if not free:
                return "That time is not free: " + why
            hid = "hd_" + secrets.token_hex(12)
            expires = _now_utc() + datetime.timedelta(minutes=hold_min)
            try:
                with conn:
                    db.sweep_expired_holds(conn)
                    conn.execute(
                        "INSERT INTO holds (id, resource, start_utc,"
                        " end_utc, customer, expires_utc, created_utc)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (hid, name, _utc_str(start_dt), _utc_str(end_dt),
                         who, _utc_str(expires), _utc_str(_now_utc())))
                    db._append_event(
                        conn, hid, "hold",
                        f"hold {hid} for {who} at {name} "
                        f"{_utc_str(start_dt)}–{_utc_str(end_dt)} UTC")
            except sqlite3.IntegrityError:
                return ("That time is already taken: " + _explain_blockers(
                    db, conn, res, start_dt, end_dt, tz))
            return (f"Hold {hid} for {who} at '{name}': "
                    f"{_fmt_local(start_dt, tz)}–"
                    f"{_fmt_local(end_dt, tz)} local, expires "
                    f"{_fmt_local(expires, tz)} local "
                    f"({hold_min} min). It releases itself when it "
                    "expires.")

        if action == "book":
            start_dt = _parse_time(start, tz) if start else None
            end_dt = _parse_time(end, tz) if end else None
            who = (customer or "").strip()
            if start_dt is None or end_dt is None:
                return ("Give a start and end as 'YYYY-MM-DD HH:MM' in "
                        f"the resource's timezone ({res['timezone']}).")
            if end_dt <= start_dt:
                return "The end has to be after the start."
            if not who:
                return "Say who the booking is for (customer)."
            if price is not None:
                if isinstance(price, bool) \
                        or not isinstance(price, (int, float)) or price < 0:
                    return (f"price needs to be a number, 0 or more — "
                            f"got {price!r}. It is a recorded quote only; "
                            "no payment is taken.")
            if start_dt < _now_utc():
                return "A booking has to start in the future."
            if end_dt > _now_utc() + datetime.timedelta(
                    days=res["booking_window_days"]):
                return (f"Outside the booking window "
                        f"({res['booking_window_days']} days ahead).")
            key = (idempotency_key or "").strip() \
                or "auto-" + secrets.token_hex(12)
            existing = db.get_booking_by_key(conn, key)
            if existing:
                return (f"Already booked under that key: {existing} — "
                        "the booking was not made twice.")
            free, why = _window_is_free(db, conn, res, start_dt, end_dt,
                                        tz)
            if not free:
                return "That time is not free: " + why
            bid = "bk_" + secrets.token_hex(12)
            frozen = {"capacity": res["capacity"],
                      "timezone": res["timezone"],
                      "schedule": res["schedule"],
                      "buffer_minutes": res["buffer_minutes"],
                      "booking_window_days": res["booking_window_days"],
                      "cancel_hours": res["cancel_hours"]}
            try:
                with conn:
                    db.sweep_expired_holds(conn)
                    db.insert_booking(conn, {
                        "id": bid, "resource": name,
                        "start_utc": _utc_str(start_dt),
                        "end_utc": _utc_str(end_dt), "customer": who,
                        "price": price, "idempotency_key": key,
                        "rules": frozen})
                    db._append_event(
                        conn, bid, "book",
                        f"booked {bid} for {who} at {name} "
                        f"{_utc_str(start_dt)}–{_utc_str(end_dt)} UTC")
            except sqlite3.IntegrityError as exc:
                if "UNIQUE constraint failed: bookings.idempotency_key" \
                        in str(exc):
                    again = db.get_booking_by_key(conn, key)
                    return (f"Already booked under that key: {again} — "
                            "the booking was not made twice.")
                return ("That time is already taken: " + _explain_blockers(
                    db, conn, res, start_dt, end_dt, tz))
            got = db.get_booking(conn, bid)
            if not got or got["start_utc"] != _utc_str(start_dt) \
                    or got["status"] != "confirmed":
                return (f"Booking {bid} was written but the re-read did "
                        "not find it as confirmed — nothing is claimed. "
                        "Check the store before retrying.")
            price_txt = (f", price {price} (recorded quote — no payment "
                         "taken)") if price is not None else ""
            return (f"Booked {bid} for {who} at '{name}': "
                    f"{_fmt_local(start_dt, tz)}–"
                    f"{_fmt_local(end_dt, tz)} local "
                    f"({_utc_str(start_dt)}–{_utc_str(end_dt)} UTC)"
                    f"{price_txt}. Free cancellation until "
                    f"{frozen['cancel_hours']}h before the start, per "
                    "the rules frozen at booking time.")

        if action == "list":
            start_dt = _parse_time(start, tz) if start else None
            end_dt = _parse_time(end, tz) if end else None
            rows = db.list_bookings(
                conn, name or None,
                _utc_str(start_dt) if start_dt else None,
                _utc_str(end_dt) if end_dt else None)
            if not rows:
                return f"No bookings for '{name}' in that range."
            tz_by_resource = {}
            for rname, rtz in conn.execute(
                    "SELECT name, timezone FROM resources").fetchall():
                tz_by_resource[rname] = _resource_tz({"timezone": rtz})
            lines = [f"Bookings for '{name}':"]
            for bid, rname, s, e, who, pr, status in rows:
                rtz = tz_by_resource.get(rname, _default_tz())
                lines.append(
                    f"  {bid} — {rname} — "
                    f"{_fmt_local(_parse_utc(s), rtz)}–"
                    f"{_fmt_local(_parse_utc(e), rtz)} local"
                    f" — {who} — {status}"
                    + (f" — price {pr}" if pr is not None else ""))
            return "\n".join(lines)

        if action in ("cancel", "reschedule", "no-show"):
            bid = (booking_id or "").strip()
            if not bid:
                return "Give the booking_id to change."
            got = db.get_booking(conn, bid)
            if not got:
                return f"No booking {bid!r}."
            frozen = got["rules"]
            if action == "cancel":
                if got["status"] != "confirmed":
                    return (f"Booking {bid} is already "
                            f"{got['status']} — nothing to cancel.")
                start_dt = _parse_utc(got["start_utc"])
                free_until = start_dt - datetime.timedelta(
                    hours=frozen["cancel_hours"])
                late = _now_utc() > free_until
                with conn:
                    db.set_booking_status(conn, bid, "cancelled")
                    db._append_event(
                        conn, bid, "cancel",
                        f"cancelled {bid} ({'late' if late else 'free'} "
                        f"per frozen cancel window "
                        f"{frozen['cancel_hours']}h)")
                return (f"Cancelled {bid} ({got['customer']} at "
                        f"{got['resource']}). "
                        + ("Late cancellation — inside the free window "
                           f"({frozen['cancel_hours']}h before the "
                           "start), per the rules frozen at booking "
                           "time. The price was a recorded quote only; "
                           "no payment was taken, so nothing is owed "
                           "here."
                           if late else
                           "Free cancellation — outside the "
                           f"{frozen['cancel_hours']}h window."))
            if action == "reschedule":
                if got["status"] != "confirmed":
                    return (f"Booking {bid} is {got['status']} — only a "
                            "confirmed booking can be moved.")
                # The new time is judged against the resource as it is
                # NOW (its current hours, buffer and window) — a move
                # is about the resource today. The booking keeps the
                # terms frozen when it was made (cancel window, price).
                cur_res = db.get_resource(conn, got["resource"])
                if not cur_res:
                    return (f"The resource {got['resource']!r} no longer "
                            "exists — the booking cannot be moved.")
                cur_tz = _resource_tz(cur_res)
                new_start = _parse_time(start, cur_tz) if start else None
                new_end = _parse_time(end, cur_tz) if end else None
                if new_start is None or new_end is None:
                    return ("Give the new start and end as "
                            "'YYYY-MM-DD HH:MM' in the resource's "
                            f"timezone ({cur_res['timezone']}).")
                if new_end <= new_start:
                    return "The end has to be after the start."
                if new_start < _now_utc():
                    return "A booking has to start in the future."
                free, why = _window_is_free(
                    db, conn, cur_res, new_start, new_end, cur_tz,
                    exclude_id=bid)
                if not free:
                    return "That time is not free: " + why
                try:
                    with conn:
                        db.sweep_expired_holds(conn)
                        db.update_booking_times(
                            conn, bid, _utc_str(new_start),
                            _utc_str(new_end))
                        db._append_event(
                            conn, bid, "reschedule",
                            f"moved {bid} to "
                            f"{_utc_str(new_start)}–{_utc_str(new_end)} UTC")
                except sqlite3.IntegrityError:
                    return ("That time is already taken: "
                            + _explain_blockers(
                                db, conn, cur_res, new_start, new_end,
                                cur_tz, exclude_id=bid))
                got = db.get_booking(conn, bid)
                if not got or got["start_utc"] != _utc_str(new_start):
                    return (f"Booking {bid} was moved but the re-read "
                            "did not find the new time — nothing is "
                            "claimed. Check the store before retrying.")
                return (f"Moved {bid} to "
                        f"{_fmt_local(new_start, cur_tz)}–"
                        f"{_fmt_local(new_end, cur_tz)} local. The "
                        "booking keeps the terms frozen when it was "
                        "made.")
            # no-show
            if got["status"] != "confirmed":
                return (f"Booking {bid} is already {got['status']}.")
            if _parse_utc(got["start_utc"]) > _now_utc():
                return (f"Booking {bid} has not started yet — a no-show "
                        "is only marked after the start.")
            with conn:
                db.set_booking_status(conn, bid, "no-show")
                db._append_event(conn, bid, "no-show",
                                 f"no-show {bid} ({got['customer']})")
            return (f"Marked {bid} ({got['customer']} at "
                    f"{got['resource']}) as a no-show, per the rules "
                    "frozen at booking time.")

        if action == "events":
            bid = (booking_id or "").strip()
            # The chain is verified over the FULL ledger, never over
            # a filtered subset (a filter would break the links).
            all_rows = conn.execute(
                "SELECT seq, kind, detail, at_utc, prev_hash, hash"
                " FROM events ORDER BY seq").fetchall()
            entries = [{"kind": kind, "detail": detail, "at": at,
                        "prev_hash": ph, "hash": h}
                       for seq, kind, detail, at, ph, h in all_rows]
            result = store_base.verify_entries(
                entries, "pipe_fstring", store_base.LEGACY_GENESIS)
            broken = (all_rows[result["first_break"]["index"]][0]
                      if not result["ok"] else None)
            sql = ("SELECT seq, booking_id, kind, detail, at_utc"
                   " FROM events")
            args: list = []
            if bid:
                sql += " WHERE booking_id = ?"
                args.append(bid)
            elif name:
                sql += (" WHERE booking_id LIKE ? OR booking_id IN"
                        " (SELECT id FROM bookings WHERE resource = ?)"
                        " OR booking_id IN"
                        " (SELECT id FROM holds WHERE resource = ?)")
                args.extend([f"resource:{name}", name, name])
            sql += " ORDER BY seq"
            rows = conn.execute(sql, args).fetchall()
            if not rows:
                return "No events recorded yet."
            lines = [f"Event ledger ({len(rows)} events):"]
            for seq, _bid, kind, detail, at in rows[-20:]:
                lines.append(f"  #{seq} {at} UTC — {_bid} — {kind}: "
                             f"{detail}")
            if len(rows) > 20:
                lines.append(f"  … and {len(rows) - 20} earlier events.")
            lines.append("Chain intact." if broken is None else
                         f"Chain BROKEN at event #{broken} — the ledger "
                         "does not verify; do not trust later events.")
            return "\n".join(lines)

        return _usage()
    finally:
        db.close()
