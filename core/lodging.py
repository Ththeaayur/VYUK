"""Lodging / property management for the owner's own small lodging — own code.

Parent-ruled (licence, 2026-11-12): murdawkmedia/openstays
(MIT, "MIT License / Copyright (c) 2026 Murdawk Media"; the LICENSE
file from the default branch nostr-main was read in full 2026-11-11)
is INSPIRATION ONLY. No code, schema, SQL, text or package is adopted
from it; this module is written from scratch in stdlib Python over the
tree's own SQLite-store shape (core/bookings.py, core/cost_ledger.py)
and the tree's own config DATA_DIR. Ideas are not copyrighted, so no
MIT duty is triggered by a from-scratch rebuild. The "OpenStays" name
is NOT used here: the tool is the generic word "lodging", and no
package, fork, service or hosted offering is named after the original.

What this is: the owner is the SUPPLIER — a cabin, a campsite, a
small lodge, a yurt. The owner defines the property (timezone,
currency, tax, turnover nights, deposit and cancellation policy),
its units (rooms/sites, sleeps, nightly rate, minimum stay), seasonal
rates that override the unit default, and the owner's guests stay in
them. That is the lodging / property-management half openstays adds
on top of a booking engine — and the booking-engine half is NOT
rebuilt here: core/bookings.py (R17-1) already is the generic booking
engine, with the same SQLite-trigger double-booking guarantee. What
bookings cannot express is a NIGHT: it books hourly time slots
against a weekly schedule with a capacity and buffer minutes, and it
has no property, no per-night rate, no season, no minimum stay, no
turnover night, no deposit, no tax, no stay lifecycle (reserved ->
checked_in -> checked_out / cancelled / no_show), no guest record and
no housekeeping. Forcing nightly stays through the hourly engine
would distort both engines' invariants, so lodging keeps its own
SQLite store in the same shape (triggers in the same transaction,
single-writer serialisation, hash-chained events, frozen rules) and
does NOT reuse bookings' store or its tables.

The guarantee, in the tree's own store: a BEFORE INSERT trigger and
a BEFORE UPDATE trigger on the stays table abort the write when an
overlapping stay in a blocking status (reserved / checked_in /
checked_out) already holds the unit — the overlap test is
NEW.check_in < existing.blocked_until AND existing.check_in <
NEW.check_out, where blocked_until is check_out + the property's
turnover nights, frozen at booking time. The check runs inside the
same transaction as the insert, and SQLite serialises writers, so no
two calls can interleave between the check and the write. That is
airtight for this app's shape: one desktop process owns the store.
The memory-facts store was not used for stays on purpose, because
check-then-write over JSONL facts is racy without a lock (the
bookings precedent). Every write is re-read before it is claimed.

Money is integer cents everywhere (openstays' binding rule); tax is a
single rounding on the aggregate nightly base (never per-night
rounding); dates are property-local ISO 'YYYY-MM-DD'; a stay is the
half-open range [check_in, check_out). No money moves: the deposit
and the balance are amounts DUE, recorded on the frozen folio —
there is no payment capture, no public booking page, no hosted
anything, no multi-user accounts, no channel manager / OTA / iCal
sync, no groups, no currency conversion (the currency is a label on
the property). A repeated `book` call creates a second stay — there
is no idempotency key on stays, so the caller must not retry blindly;
the trigger still refuses any second stay that overlaps the first.
"""

import json
import os
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DATA_DIR
from . import store_base

DEFAULT_DB_FILENAME = "lodging.sqlite3"

_ACTIONS = ("property", "unit", "rate", "availability", "book", "cancel",
            "checkin", "checkout", "no-show", "guest", "housekeeping",
            "day", "list", "events")

# Housekeeping states, openstays' set. The transition table is the
# rule: a unit only moves along an allowed edge, so a dirty unit can
# never be checked in and a ready unit is never "cleaned" twice.
_HOUSEKEEPING_STATES = ("ready", "dirty", "cleaning", "inspection",
                        "do_not_disturb", "out_of_service")
_HOUSEKEEPING_NEXT = {
    "ready": ("dirty", "cleaning", "inspection", "do_not_disturb",
              "out_of_service"),
    "dirty": ("cleaning", "inspection", "out_of_service"),
    "cleaning": ("ready", "inspection", "dirty", "out_of_service"),
    "inspection": ("ready", "dirty", "cleaning", "out_of_service"),
    "do_not_disturb": ("dirty", "out_of_service"),
    "out_of_service": ("dirty",),
}

_BLOCKING_STATUSES = ("reserved", "checked_in", "checked_out")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_NIGHTS = 120
_MAX_STAYS_LIST = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS properties (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    currency TEXT NOT NULL DEFAULT 'USD',
    tax_rate_bps INTEGER NOT NULL DEFAULT 0,
    tax_label TEXT NOT NULL DEFAULT 'tax',
    check_in_time TEXT NOT NULL DEFAULT '15:00',
    check_out_time TEXT NOT NULL DEFAULT '11:00',
    turnover_nights INTEGER NOT NULL DEFAULT 0,
    deposit_policy TEXT NOT NULL DEFAULT 'full',
    cancel_policy TEXT NOT NULL DEFAULT '0:0',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY,
    property_id INTEGER NOT NULL REFERENCES properties(id),
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'room',
    sleeps INTEGER NOT NULL DEFAULT 2,
    nightly_cents INTEGER NOT NULL DEFAULT 0,
    min_nights INTEGER NOT NULL DEFAULT 1,
    accessible INTEGER NOT NULL DEFAULT 0,
    pets TEXT NOT NULL DEFAULT 'no',
    created_at TEXT NOT NULL,
    UNIQUE(property_id, name)
);
CREATE TABLE IF NOT EXISTS rates (
    id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL REFERENCES units(id),
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    cents INTEGER NOT NULL,
    min_nights INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS guests (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(name, contact)
);
CREATE TABLE IF NOT EXISTS stays (
    id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL REFERENCES units(id),
    guest_id INTEGER NOT NULL REFERENCES guests(id),
    check_in TEXT NOT NULL,
    check_out TEXT NOT NULL,
    blocked_until TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'reserved',
    folio_json TEXT NOT NULL DEFAULT '{}',
    rules_json TEXT NOT NULL DEFAULT '{}',
    refund_percent INTEGER,
    refund_cents INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS housekeeping (
    unit_id INTEGER PRIMARY KEY REFERENCES units(id),
    state TEXT NOT NULL DEFAULT 'ready',
    note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    body_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS stays_no_double_insert
BEFORE INSERT ON stays
WHEN NEW.status IN ('reserved', 'checked_in', 'checked_out')
BEGIN
    SELECT RAISE(ABORT, 'double-booking: unit already held for those nights')
    WHERE EXISTS (
        SELECT 1 FROM stays s
        WHERE s.unit_id = NEW.unit_id
          AND s.status IN ('reserved', 'checked_in', 'checked_out')
          AND NEW.check_in < s.blocked_until
          AND s.check_in < NEW.check_out
    );
END;
CREATE TRIGGER IF NOT EXISTS stays_no_double_update
BEFORE UPDATE ON stays
WHEN NEW.status IN ('reserved', 'checked_in', 'checked_out')
  AND (NEW.check_in <> OLD.check_in
       OR NEW.check_out <> OLD.check_out
       OR NEW.blocked_until <> OLD.blocked_until
       OR OLD.status NOT IN ('reserved', 'checked_in', 'checked_out'))
BEGIN
    SELECT RAISE(ABORT, 'double-booking: unit already held for those nights')
    WHERE EXISTS (
        SELECT 1 FROM stays s
        WHERE s.unit_id = NEW.unit_id
          AND s.id <> NEW.id
          AND s.status IN ('reserved', 'checked_in', 'checked_out')
          AND NEW.check_in < s.blocked_until
          AND s.check_in < NEW.check_out
    );
END;
"""


def _utcnow() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _valid_date(value, name: str):
    """Strict YYYY-MM-DD, or (None, error string)."""
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return None, (f"{name} has to be a date like 2026-12-24 — "
                      f"got {value!r}.")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date(), None
    except ValueError:
        return None, (f"{name} is not a real calendar date — "
                      f"got {value!r}.")


def _night_list(check_in: date, check_out: date):
    nights = []
    day = check_in
    while day < check_out:
        nights.append(day)
        day += timedelta(days=1)
    return nights


def _today_in(timezone: str) -> date:
    try:
        return datetime.now(ZoneInfo(timezone)).date()
    except Exception:
        return datetime.now().date()


def _whole(value, name: str, minimum: int = 0):
    """Whole number >= minimum — the warehouse_stock refusal shape,
    not a truncation. Bools and floats are refused."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None, (f"{name} needs to be a whole number, {minimum} or "
                      f"more — got {value!r}.")
    if value < minimum:
        return None, (f"{name} needs to be a whole number, {minimum} or "
                      f"more — got {value}.")
    return value, None


def _cents(value):
    """Integer cents, 0 or more — never a float, never negative."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None, (f"cents needs to be a whole number of cents, 0 or "
                      f"more — got {value!r}.")
    if value < 0:
        return None, f"cents needs to be 0 or more — got {value}."
    return value, None


def _money(cents: int, currency: str) -> str:
    return f"{currency} {cents // 100}.{cents % 100:02d}"


def _parse_deposit(raw):
    """Deposit policy, one string: 'full', 'percent:50', 'first_night'
    or 'flat:20000' (flat is cents). Returns (kind, amount|None)."""
    if raw is None:
        return ("full", None), None
    if not isinstance(raw, str):
        return None, "deposit has to be a string like 'full', 'percent:50', 'first_night' or 'flat:20000'."
    text = raw.strip().lower()
    if text == "full":
        return ("full", None), None
    if text == "first_night":
        return ("first_night", None), None
    if text.startswith("percent:"):
        try:
            pct = int(text[len("percent:"):])
        except ValueError:
            return None, f"deposit {raw!r} is not a percent — use 'percent:50'."
        if not 0 <= pct <= 100:
            return None, f"deposit percent has to be 0-100 — got {pct}."
        return ("percent", pct), None
    if text.startswith("flat:"):
        try:
            amount = int(text[len("flat:"):])
        except ValueError:
            return None, f"deposit {raw!r} is not cents — use 'flat:20000'."
        if amount < 0:
            return None, f"deposit flat amount has to be 0 or more — got {amount}."
        return ("flat", amount), None
    return None, ("deposit has to be 'full', 'percent:50', 'first_night' "
                  f"or 'flat:20000' — got {raw!r}.")


def _parse_cancel(raw):
    """Cancellation policy, one string: 'days:refund%;...' — e.g.
    '14:100;7:50;0:0' means 100% back 14+ days out, 50% at 7+ days,
    nothing inside 7 days. Returns a list of (days, pct) sorted by
    days descending, or (None, error)."""
    if raw is None:
        return [(0, 0)], None
    if not isinstance(raw, str):
        return None, "cancel_policy has to be a string like '14:100;7:50;0:0'."
    entries = []
    for part in raw.strip().split(";"):
        part = part.strip()
        if not part:
            continue
        try:
            days_s, pct_s = part.split(":", 1)
            days, pct = int(days_s), int(pct_s)
        except ValueError:
            return None, (f"cancel_policy {raw!r} is not 'days:percent' "
                          f"pairs — use '14:100;7:50;0:0'.")
        if days < 0 or not 0 <= pct <= 100:
            return None, (f"cancel_policy {raw!r} needs days >= 0 and "
                          f"percent 0-100 — got {days}:{pct}.")
        entries.append((days, pct))
    if not entries:
        return None, f"cancel_policy {raw!r} has no entries."
    entries.sort(key=lambda item: item[0], reverse=True)
    return entries, None


def _refund_for(days_before: int, policy):
    """First (days, pct) whose days threshold the stay still meets —
    the policy is sorted days-descending, so the first match is the
    most generous one that applies."""
    for days, pct in policy:
        if days_before >= days:
            return pct
    return 0

class _LodgingDB:
    """The lodging store: one SQLite file, triggers as the
    double-booking guarantee. Follows core/bookings.py."""

    def __init__(self, db_path=None):
        if db_path is None:
            db_path = DATA_DIR / DEFAULT_DB_FILENAME
        self.db_path = Path(db_path)
        self._conn = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = store_base.connect(
                str(self.db_path), row_factory=sqlite3.Row,
                pragmas=("PRAGMA foreign_keys = ON",))
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

    # -- lookups ------------------------------------------------------

    def _property(self, name):
        return self._connect().execute(
            "SELECT * FROM properties WHERE name = ?", (name,)).fetchone()

    def _unit(self, property_id, name):
        return self._connect().execute(
            "SELECT * FROM units WHERE property_id = ? AND name = ?",
            (property_id, name)).fetchone()

    def _unit_by_name(self, name):
        """A unit name on its own — refused when two properties share
        it, so a bare unit name can never silently pick the wrong
        property's room."""
        rows = self._connect().execute(
            "SELECT * FROM units WHERE name = ?", (name,)).fetchall()
        return rows

    def _stay(self, stay_id):
        return self._connect().execute(
            "SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()

    def _guest(self, name, contact):
        return self._connect().execute(
            "SELECT * FROM guests WHERE name = ? AND contact = ?",
            (name, contact or "")).fetchone()

    def _housekeeping_state(self, unit_id):
        row = self._connect().execute(
            "SELECT state FROM housekeeping WHERE unit_id = ?",
            (unit_id,)).fetchone()
        return row["state"] if row else "ready"

    def _rate_for_night(self, unit_id, night: date):
        """The rate row covering one night — the latest-starting
        covering row wins; when none covers it, the unit default
        applies (min_nights 1, so it never raises the minimum)."""
        row = self._connect().execute(
            "SELECT * FROM rates WHERE unit_id = ? AND start_date <= ?"
            " AND end_date > ? ORDER BY start_date DESC LIMIT 1",
            (unit_id, night.isoformat(), night.isoformat())).fetchone()
        if row:
            return row["cents"], row["min_nights"]
        unit = self._connect().execute(
            "SELECT nightly_cents, min_nights FROM units WHERE id = ?",
            (unit_id,)).fetchone()
        return unit["nightly_cents"], unit["min_nights"]

    def _quote(self, unit, check_in: date, check_out: date):
        """Frozen price breakdown for a stay: per-night amounts, one
        tax rounding on the aggregate base, deposit due, balance due.
        Returns (folio dict, error string)."""
        conn = self._connect()
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ?",
            (unit["property_id"],)).fetchone()
        nights = _night_list(check_in, check_out)
        if len(nights) > _MAX_NIGHTS:
            return None, (f"a stay is at most {_MAX_NIGHTS} nights — "
                          f"got {len(nights)}.")
        per_night = {}
        min_needed = unit["min_nights"]
        for night in nights:
            cents, rate_min = self._rate_for_night(unit["id"], night)
            per_night[night.isoformat()] = cents
            min_needed = max(min_needed, rate_min)
        if len(nights) < min_needed:
            return None, (f"{unit['name']!r} needs at least {min_needed} "
                          f"nights — got {len(nights)}.")
        subtotal = sum(per_night.values())
        # Single rounding on the aggregate base, never per night.
        tax = (subtotal * prop["tax_rate_bps"] + 5000) // 10000
        total = subtotal + tax
        deposit, err = _parse_deposit(prop["deposit_policy"])
        if err:
            return None, err
        kind, amount = deposit
        if kind == "full":
            deposit_due = total
        elif kind == "percent":
            deposit_due = total * amount // 100
        elif kind == "first_night":
            first = per_night[nights[0].isoformat()]
            deposit_due = first + (first * prop["tax_rate_bps"]
                                   + 5000) // 10000
        else:  # flat
            deposit_due = min(amount, total)
        folio = {
            "nights": per_night,
            "nightly_subtotal_cents": subtotal,
            "tax_cents": tax,
            "tax_rate_bps": prop["tax_rate_bps"],
            "tax_label": prop["tax_label"],
            "total_cents": total,
            "deposit_due_cents": deposit_due,
            "balance_due_cents": total - deposit_due,
            "currency": prop["currency"],
            "deposit_policy": prop["deposit_policy"],
            "cancel_policy": prop["cancel_policy"],
        }
        return folio, None

    # -- property / unit / rate ---------------------------------------

    def property_cmd(self, name, timezone, currency, tax_rate_bps,
                     turnover_nights, deposit, cancel_policy):
        conn = self._connect()
        if not name:
            rows = conn.execute(
                "SELECT * FROM properties ORDER BY name").fetchall()
            if not rows:
                return "No properties yet — property='Pine Cabins' to add one."
            bits = []
            for row in rows:
                units = conn.execute(
                    "SELECT COUNT(*) AS n FROM units WHERE property_id = ?",
                    (row["id"],)).fetchone()["n"]
                bits.append(f"{row['name']} ({row['currency']}, "
                            f"{row['timezone']}, {units} unit(s))")
            return "Properties: " + "; ".join(bits)
        name = str(name).strip()
        if not name:
            return "Give the property a name — e.g. property='Pine Cabins'."
        existing = self._property(name)
        if existing:
            return (f"{name}: {existing['currency']}, "
                    f"{existing['timezone']}, tax "
                    f"{existing['tax_rate_bps'] / 100:.2f}% "
                    f"({existing['tax_label']}), turnover "
                    f"{existing['turnover_nights']} night(s), deposit "
                    f"{existing['deposit_policy']}, cancel "
                    f"{existing['cancel_policy']}.")
        tz = (str(timezone).strip() if timezone else "UTC") or "UTC"
        try:
            ZoneInfo(tz)
        except Exception:
            return f"timezone {tz!r} is not a known IANA timezone."
        cur = (str(currency).strip().upper() if currency else "USD") or "USD"
        tax, err = _whole(tax_rate_bps, "tax_rate_bps", 0) \
            if tax_rate_bps is not None else (0, None)
        if err:
            return err
        if tax > 10000:
            return f"tax_rate_bps has to be 0-10000 — got {tax}."
        turnover, err = _whole(turnover_nights, "turnover_nights", 0) \
            if turnover_nights is not None else (0, None)
        if err:
            return err
        if turnover > 14:
            return f"turnover_nights has to be 0-14 — got {turnover}."
        # The handler passes "" for an omitted string — an empty
        # deposit/cancel policy means "the default", not an error.
        deposit = deposit or None
        cancel_policy = cancel_policy or None
        dep, err = _parse_deposit(deposit)
        if err:
            return err
        cancel, err = _parse_cancel(cancel_policy)
        if err:
            return err
        cancel_text = ";".join(f"{d}:{p}" for d, p in cancel)
        conn.execute(
            "INSERT INTO properties (name, timezone, currency,"
            " tax_rate_bps, turnover_nights, deposit_policy,"
            " cancel_policy, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, tz, cur, tax, turnover,
             deposit if deposit is not None else "full",
             cancel_text, _utcnow()))
        conn.commit()
        self._log_event("property_added",
                        {"property": name, "timezone": tz,
                         "currency": cur})
        conn.commit()
        again = self._property(name)
        if again is None:
            return (f"{name} was not saved — the write did not stick, "
                    "nothing was claimed.")
        return (f"Property {name} added ({cur}, {tz}, tax "
                f"{tax / 100:.2f}%, turnover {turnover} night(s), "
                f"deposit {again['deposit_policy']}, cancel "
                f"{again['cancel_policy']}).")

    def unit_cmd(self, property_name, name, type_, sleeps, cents,
                 min_nights):
        conn = self._connect()
        if not name:
            if property_name:
                prop = self._property(str(property_name).strip())
                if prop is None:
                    return f"No property named {property_name!r}."
                rows = conn.execute(
                    "SELECT * FROM units WHERE property_id = ?"
                    " ORDER BY name", (prop["id"],)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM units ORDER BY name").fetchall()
            if not rows:
                return "No units yet — unit='Cabin 1' with property='Pine Cabins' to add one."
            bits = []
            for row in rows:
                prop = conn.execute(
                    "SELECT name, currency FROM properties WHERE id = ?",
                    (row["property_id"],)).fetchone()
                bits.append(f"{row['name']} ({prop['name']}, "
                            f"{row['type']}, sleeps {row['sleeps']}, "
                            f"{_money(row['nightly_cents'], prop['currency'])} / night, "
                            f"min {row['min_nights']} night(s), "
                            f"{self._housekeeping_state(row['id'])})")
            return "Units: " + "; ".join(bits)
        if not property_name:
            return ("A unit needs its property — e.g. property='Pine "
                    "Cabins', unit='Cabin 1'.")
        prop = self._property(str(property_name).strip())
        if prop is None:
            return f"No property named {property_name!r}."
        name = str(name).strip()
        if not name:
            return "Give the unit a name — e.g. unit='Cabin 1'."
        existing = self._unit(prop["id"], name)
        if existing:
            return (f"{name} ({prop['name']}, {existing['type']}, sleeps "
                    f"{existing['sleeps']}, "
                    f"{_money(existing['nightly_cents'], prop['currency'])} / night, "
                    f"min {existing['min_nights']} night(s), "
                    f"{self._housekeeping_state(existing['id'])}).")
        type_text = (str(type_).strip().lower() if type_ else "room") or "room"
        sleeps_n, err = _whole(sleeps, "sleeps", 1) \
            if sleeps is not None else (2, None)
        if err:
            return err
        nightly, err = _cents(cents) if cents is not None else (0, None)
        if err:
            return err
        min_n, err = _whole(min_nights, "min_nights", 1) \
            if min_nights is not None else (1, None)
        if err:
            return err
        conn.execute(
            "INSERT INTO units (property_id, name, type, sleeps,"
            " nightly_cents, min_nights, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (prop["id"], name, type_text, sleeps_n, nightly, min_n,
             _utcnow()))
        conn.commit()
        again = self._unit(prop["id"], name)
        if again is None:
            return (f"{name} was not saved — the write did not stick, "
                    "nothing was claimed.")
        conn.execute(
            "INSERT OR IGNORE INTO housekeeping (unit_id, state,"
            " updated_at) VALUES (?, 'ready', ?)",
            (again["id"], _utcnow()))
        conn.commit()
        self._log_event("unit_added",
                        {"property": prop["name"], "unit": name,
                         "type": type_text})
        conn.commit()
        return (f"Unit {name} added to {prop['name']} ({type_text}, "
                f"sleeps {sleeps_n}, "
                f"{_money(nightly, prop['currency'])} / night, min "
                f"{min_n} night(s), housekeeping ready).")

    def rate_cmd(self, property_name, unit_name, cents, check_in,
                 check_out, min_nights):
        conn = self._connect()
        if not unit_name:
            return "A rate needs its unit — e.g. unit='Cabin 1'."
        rows = self._unit_by_name(str(unit_name).strip())
        if not rows:
            return f"No unit named {unit_name!r}."
        if len(rows) > 1 and not property_name:
            return (f"More than one unit is named {unit_name!r} — say "
                    "which property it belongs to.")
        unit = rows[0]
        if property_name:
            prop = self._property(str(property_name).strip())
            if prop is None:
                return f"No property named {property_name!r}."
            match = self._unit(prop["id"], str(unit_name).strip())
            if match is None:
                return (f"No unit named {unit_name!r} in "
                        f"{property_name!r}.")
            unit = match
        if cents is None:
            found = conn.execute(
                "SELECT * FROM rates WHERE unit_id = ?"
                " ORDER BY start_date", (unit["id"],)).fetchall()
            if not found:
                prop = conn.execute(
                    "SELECT currency FROM properties WHERE id = ?",
                    (unit["property_id"],)).fetchone()
                return (f"No seasonal rates for {unit['name']} — the "
                        f"default is "
                        f"{_money(unit['nightly_cents'], prop['currency'])} / night, "
                        f"min {unit['min_nights']} night(s).")
            prop = conn.execute(
                "SELECT currency FROM properties WHERE id = ?",
                (unit["property_id"],)).fetchone()
            bits = [f"{row['start_date']} to {row['end_date']}: "
                    f"{_money(row['cents'], prop['currency'])} / night, "
                    f"min {row['min_nights']} night(s)"
                    for row in found]
            return f"Rates for {unit['name']}: " + "; ".join(bits)
        amount, err = _cents(cents)
        if err:
            return err
        start, err = _valid_date(check_in, "check_in")
        if err:
            return err
        end, err = _valid_date(check_out, "check_out")
        if err:
            return err
        if end <= start:
            return "The rate's end has to be after its start."
        min_n, err = _whole(min_nights, "min_nights", 1) \
            if min_nights is not None else (1, None)
        if err:
            return err
        conn.execute(
            "INSERT INTO rates (unit_id, start_date, end_date, cents,"
            " min_nights, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (unit["id"], start.isoformat(), end.isoformat(), amount,
             min_n, _utcnow()))
        conn.commit()
        prop = conn.execute(
            "SELECT currency FROM properties WHERE id = ?",
            (unit["property_id"],)).fetchone()
        self._log_event("rate_added",
                        {"unit": unit["name"],
                         "start": start.isoformat(),
                         "end": end.isoformat(), "cents": amount})
        conn.commit()
        return (f"Rate set for {unit['name']}: {start.isoformat()} to "
                f"{end.isoformat()} at "
                f"{_money(amount, prop['currency'])} / night, min "
                f"{min_n} night(s).")

    # -- availability / stays -----------------------------------------

    def _resolve_unit(self, property_name, unit_name):
        """(unit row, error string) — a bare unit name is refused
        when two properties share it."""
        if not unit_name:
            return None, "A unit name is needed — e.g. unit='Cabin 1'."
        rows = self._unit_by_name(str(unit_name).strip())
        if not rows:
            return None, f"No unit named {unit_name!r}."
        if property_name:
            prop = self._property(str(property_name).strip())
            if prop is None:
                return None, f"No property named {property_name!r}."
            match = self._unit(prop["id"], str(unit_name).strip())
            if match is None:
                return None, (f"No unit named {unit_name!r} in "
                              f"{property_name!r}.")
            return match, None
        if len(rows) > 1:
            return None, (f"More than one unit is named {unit_name!r} — "
                          "say which property it belongs to.")
        return rows[0], None

    def _blocking_stay(self, unit_id, check_in: date, check_out: date):
        """The stay that blocks a [check_in, check_out) range, if any
        — the same overlap test the trigger enforces, so the message
        names the blocker before the write is even attempted."""
        conn = self._connect()
        return conn.execute(
            "SELECT * FROM stays WHERE unit_id = ?"
            " AND status IN ('reserved', 'checked_in', 'checked_out')"
            " AND ? < blocked_until AND check_in < ?"
            " ORDER BY check_in LIMIT 1",
            (unit_id, check_in.isoformat(),
             check_out.isoformat())).fetchone()

    def availability_cmd(self, property_name, unit_name, check_in,
                         check_out):
        start, err = _valid_date(check_in, "check_in")
        if err:
            return err
        end, err = _valid_date(check_out, "check_out")
        if err:
            return err
        if end <= start:
            return "The end has to be after the start."
        if len(_night_list(start, end)) > _MAX_NIGHTS:
            return (f"a stay is at most {_MAX_NIGHTS} nights — "
                    f"got {len(_night_list(start, end))}.")
        conn = self._connect()
        units = []
        if unit_name:
            unit, err = self._resolve_unit(property_name, unit_name)
            if err:
                return err
            units = [unit]
        elif property_name:
            prop = self._property(str(property_name).strip())
            if prop is None:
                return f"No property named {property_name!r}."
            units = conn.execute(
                "SELECT * FROM units WHERE property_id = ? ORDER BY name",
                (prop["id"],)).fetchall()
            if not units:
                return f"{property_name} has no units yet."
        else:
            return ("Availability needs a unit or a property — e.g. "
                    "unit='Cabin 1', check_in='2026-12-24', "
                    "check_out='2026-12-27'.")
        lines = []
        for unit in units:
            prop = conn.execute(
                "SELECT currency FROM properties WHERE id = ?",
                (unit["property_id"],)).fetchone()
            blocker = self._blocking_stay(unit["id"], start, end)
            if blocker:
                lines.append(
                    f"{unit['name']}: not free — held by stay "
                    f"#{blocker['id']} ({blocker['check_in']} to "
                    f"{blocker['check_out']}, {blocker['status']})")
                continue
            folio, err = self._quote(unit, start, end)
            if err:
                lines.append(f"{unit['name']}: {err}")
                continue
            cur = folio["currency"]
            lines.append(
                f"{unit['name']}: free — "
                f"{_money(folio['nightly_subtotal_cents'], cur)} for "
                f"{len(folio['nights'])} night(s) + "
                f"{folio['tax_label']} "
                f"{_money(folio['tax_cents'], cur)} = "
                f"{_money(folio['total_cents'], cur)} total, "
                f"{_money(folio['deposit_due_cents'], cur)} deposit due, "
                f"{_money(folio['balance_due_cents'], cur)} balance")
        return "\n".join(lines)

    def book_cmd(self, property_name, unit_name, guest_name, contact,
                 check_in, check_out):
        if not guest_name or not str(guest_name).strip():
            return "A booking needs a guest name — e.g. guest='Maya Rao'."
        unit, err = self._resolve_unit(property_name, unit_name)
        if err:
            return err
        start, err = _valid_date(check_in, "check_in")
        if err:
            return err
        end, err = _valid_date(check_out, "check_out")
        if err:
            return err
        if end <= start:
            return "The end has to be after the start."
        folio, err = self._quote(unit, start, end)
        if err:
            return err
        conn = self._connect()
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ?",
            (unit["property_id"],)).fetchone()
        blocker = self._blocking_stay(unit["id"], start, end)
        if blocker:
            return (f"{unit['name']} is already held for those nights — "
                    f"stay #{blocker['id']} ({blocker['check_in']} to "
                    f"{blocker['check_out']}, {blocker['status']}).")
        guest_name = str(guest_name).strip()
        contact_text = str(contact).strip() if contact else ""
        guest = self._guest(guest_name, contact_text)
        if guest is None:
            conn.execute(
                "INSERT INTO guests (name, contact, created_at)"
                " VALUES (?, ?, ?)",
                (guest_name, contact_text, _utcnow()))
            conn.commit()
            guest = self._guest(guest_name, contact_text)
        blocked_until = (end + timedelta(
            days=prop["turnover_nights"])).isoformat()
        rules = {
            "turnover_nights": prop["turnover_nights"],
            "min_nights": max(unit["min_nights"],
                              max((self._rate_for_night(
                                  unit["id"], night)[1])
                                  for night in _night_list(start, end))),
            "deposit_policy": prop["deposit_policy"],
            "cancel_policy": prop["cancel_policy"],
            "tax_rate_bps": prop["tax_rate_bps"],
            "currency": prop["currency"],
        }
        try:
            cursor = conn.execute(
                "INSERT INTO stays (unit_id, guest_id, check_in,"
                " check_out, blocked_until, status, folio_json,"
                " rules_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'reserved', ?, ?, ?, ?)",
                (unit["id"], guest["id"], start.isoformat(),
                 end.isoformat(), blocked_until,
                 json.dumps(folio, sort_keys=True),
                 json.dumps(rules, sort_keys=True),
                 _utcnow(), _utcnow()))
            stay_id = cursor.lastrowid
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            return (f"{unit['name']} is already held for those nights — "
                    f"the booking was refused ({exc}).")
        self._log_event("stay_booked",
                        {"stay_id": stay_id, "unit": unit["name"],
                         "guest": guest_name,
                         "check_in": start.isoformat(),
                         "check_out": end.isoformat(),
                         "total_cents": folio["total_cents"]})
        conn.commit()
        again = self._stay(stay_id)
        if again is None or again["status"] != "reserved":
            return (f"Stay #{stay_id} was not saved as reserved — the "
                    "write did not stick, nothing was claimed.")
        cur = folio["currency"]
        return (f"Stay #{stay_id} booked: {unit['name']} for "
                f"{guest_name}, {start.isoformat()} to "
                f"{end.isoformat()} ({len(folio['nights'])} night(s)), "
                f"{_money(folio['total_cents'], cur)} total, "
                f"{_money(folio['deposit_due_cents'], cur)} deposit due, "
                f"{_money(folio['balance_due_cents'], cur)} balance. "
                f"Turnover holds the unit until {blocked_until}.")

    def cancel_cmd(self, stay_id):
        stay, err = self._resolve_stay(stay_id)
        if err:
            return err
        if stay["status"] != "reserved":
            return (f"Stay #{stay['id']} is {stay['status']} — only a "
                    "reserved stay can be cancelled.")
        conn = self._connect()
        unit = conn.execute("SELECT * FROM units WHERE id = ?",
                            (stay["unit_id"],)).fetchone()
        prop = conn.execute("SELECT * FROM properties WHERE id = ?",
                            (unit["property_id"],)).fetchone()
        policy, err = _parse_cancel(prop["cancel_policy"])
        if err:
            return err
        today = _today_in(prop["timezone"])
        check_in = datetime.strptime(stay["check_in"], "%Y-%m-%d").date()
        days_before = (check_in - today).days
        pct = _refund_for(days_before, policy)
        folio = json.loads(stay["folio_json"])
        refund = folio["total_cents"] * pct // 100
        conn.execute(
            "UPDATE stays SET status = 'cancelled',"
            " refund_percent = ?, refund_cents = ?, updated_at = ?"
            " WHERE id = ?",
            (pct, refund, _utcnow(), stay["id"]))
        conn.commit()
        self._log_event("stay_cancelled",
                        {"stay_id": stay["id"],
                         "refund_percent": pct, "refund_cents": refund})
        conn.commit()
        again = self._stay(stay["id"])
        if again is None or again["status"] != "cancelled":
            return (f"Stay #{stay['id']} was not cancelled — the write "
                    "did not stick, nothing was claimed.")
        cur = folio["currency"]
        return (f"Stay #{stay['id']} cancelled ({unit['name']}, "
                f"{stay['check_in']} to {stay['check_out']}). "
                f"{days_before} day(s) before check-in under "
                f"'{prop['cancel_policy']}': {pct}% back — "
                f"{_money(refund, cur)} of "
                f"{_money(folio['total_cents'], cur)}.")

    def _resolve_stay(self, stay_id):
        value, err = _whole(stay_id, "stay_id", 1) \
            if stay_id is not None else (None, "A stay id is needed — e.g. stay_id=3.")
        if err:
            return None, err
        stay = self._stay(value)
        if stay is None:
            return None, f"No stay #{value}."
        return stay, None

    def checkin_cmd(self, stay_id):
        stay, err = self._resolve_stay(stay_id)
        if err:
            return err
        if stay["status"] != "reserved":
            return (f"Stay #{stay['id']} is {stay['status']} — only a "
                    "reserved stay can be checked in.")
        conn = self._connect()
        state = self._housekeeping_state(stay["unit_id"])
        if state != "ready":
            return (f"Stay #{stay['id']} cannot check in — its unit is "
                    f"{state}, not ready. Clean it first.")
        conn.execute(
            "UPDATE stays SET status = 'checked_in', updated_at = ?"
            " WHERE id = ?", (_utcnow(), stay["id"]))
        conn.commit()
        self._log_event("stay_checked_in", {"stay_id": stay["id"]})
        conn.commit()
        again = self._stay(stay["id"])
        if again is None or again["status"] != "checked_in":
            return (f"Stay #{stay['id']} was not checked in — the write "
                    "did not stick, nothing was claimed.")
        unit = conn.execute("SELECT name FROM units WHERE id = ?",
                            (stay["unit_id"],)).fetchone()
        return (f"Stay #{stay['id']} checked in ({unit['name']}, "
                f"{stay['check_in']} to {stay['check_out']}).")

    def checkout_cmd(self, stay_id):
        stay, err = self._resolve_stay(stay_id)
        if err:
            return err
        if stay["status"] != "checked_in":
            return (f"Stay #{stay['id']} is {stay['status']} — only a "
                    "checked-in stay can be checked out.")
        conn = self._connect()
        conn.execute(
            "UPDATE stays SET status = 'checked_out', updated_at = ?"
            " WHERE id = ?", (_utcnow(), stay["id"]))
        conn.execute(
            "INSERT INTO housekeeping (unit_id, state, updated_at)"
            " VALUES (?, 'dirty', ?)"
            " ON CONFLICT(unit_id) DO UPDATE SET state = 'dirty',"
            " updated_at = excluded.updated_at",
            (stay["unit_id"], _utcnow()))
        conn.commit()
        self._log_event("stay_checked_out", {"stay_id": stay["id"]})
        conn.commit()
        again = self._stay(stay["id"])
        if again is None or again["status"] != "checked_out":
            return (f"Stay #{stay['id']} was not checked out — the "
                    "write did not stick, nothing was claimed.")
        unit = conn.execute("SELECT name FROM units WHERE id = ?",
                            (stay["unit_id"],)).fetchone()
        return (f"Stay #{stay['id']} checked out ({unit['name']}). The "
                "unit is now dirty — housekeeping needs to turn it over "
                "before the next guest.")

    def no_show_cmd(self, stay_id):
        stay, err = self._resolve_stay(stay_id)
        if err:
            return err
        if stay["status"] != "reserved":
            return (f"Stay #{stay['id']} is {stay['status']} — only a "
                    "reserved stay can be marked a no-show.")
        conn = self._connect()
        conn.execute(
            "UPDATE stays SET status = 'no_show', updated_at = ?"
            " WHERE id = ?", (_utcnow(), stay["id"]))
        conn.commit()
        self._log_event("stay_no_show", {"stay_id": stay["id"]})
        conn.commit()
        again = self._stay(stay["id"])
        if again is None or again["status"] != "no_show":
            return (f"Stay #{stay['id']} was not marked a no-show — the "
                    "write did not stick, nothing was claimed.")
        unit = conn.execute("SELECT name FROM units WHERE id = ?",
                            (stay["unit_id"],)).fetchone()
        return (f"Stay #{stay['id']} marked a no-show ({unit['name']}, "
                f"{stay['check_in']} to {stay['check_out']}). The unit "
                "is free again; its housekeeping state is unchanged.")

    # -- guests / housekeeping / day / list / events -------------------

    def guest_cmd(self, guest_name, contact):
        if not guest_name or not str(guest_name).strip():
            return "A guest name is needed — e.g. guest='Maya Rao'."
        conn = self._connect()
        guest_name = str(guest_name).strip()
        contact_text = str(contact).strip() if contact else ""
        if contact_text:
            guest = self._guest(guest_name, contact_text)
            guests = [guest] if guest else []
        else:
            guests = conn.execute(
                "SELECT * FROM guests WHERE name = ? ORDER BY id",
                (guest_name,)).fetchall()
        if not guests:
            return f"No guest named {guest_name!r}."
        lines = []
        for guest in guests:
            stays = conn.execute(
                "SELECT s.*, u.name AS unit_name FROM stays s"
                " JOIN units u ON u.id = s.unit_id"
                " WHERE s.guest_id = ? ORDER BY s.check_in",
                (guest["id"],)).fetchall()
            contact_bit = (f", {guest['contact']}"
                           if guest["contact"] else "")
            if not stays:
                lines.append(f"{guest['name']}{contact_bit}: no stays.")
                continue
            stay_bits = [f"#{row['id']} {row['unit_name']} "
                         f"{row['check_in']} to {row['check_out']} "
                         f"({row['status']})" for row in stays]
            lines.append(f"{guest['name']}{contact_bit}: "
                         + "; ".join(stay_bits))
        return "\n".join(lines)

    def housekeeping_cmd(self, property_name, unit_name, state, note):
        unit, err = self._resolve_unit(property_name, unit_name)
        if err:
            return err
        conn = self._connect()
        current = self._housekeeping_state(unit["id"])
        if not state:
            return (f"{unit['name']} housekeeping: {current}. Allowed "
                    f"next: {', '.join(_HOUSEKEEPING_NEXT[current])}.")
        target = str(state).strip().lower().replace(" ", "_")
        if target not in _HOUSEKEEPING_STATES:
            return (f"Housekeeping state {state!r} is not one of: "
                    f"{', '.join(_HOUSEKEEPING_STATES)}.")
        if target == current:
            return f"{unit['name']} housekeeping is already {current}."
        if target not in _HOUSEKEEPING_NEXT[current]:
            return (f"{unit['name']} cannot go from {current} to "
                    f"{target} — allowed next: "
                    f"{', '.join(_HOUSEKEEPING_NEXT[current])}.")
        conn.execute(
            "INSERT INTO housekeeping (unit_id, state, note, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(unit_id) DO UPDATE SET state = excluded.state,"
            " note = excluded.note, updated_at = excluded.updated_at",
            (unit["id"], target, str(note).strip() if note else "",
             _utcnow()))
        conn.commit()
        self._log_event("housekeeping_changed",
                        {"unit": unit["name"], "from": current,
                         "to": target})
        conn.commit()
        again = self._housekeeping_state(unit["id"])
        if again != target:
            return (f"{unit['name']} housekeeping was not set to "
                    f"{target} — the write did not stick, nothing was "
                    "claimed.")
        return f"{unit['name']} housekeeping: {current} -> {target}."

    def day_cmd(self, property_name, day):
        conn = self._connect()
        if not property_name:
            return "The day view needs a property — e.g. property='Pine Cabins'."
        prop = self._property(str(property_name).strip())
        if prop is None:
            return f"No property named {property_name!r}."
        if day:
            target, err = _valid_date(day, "date")
            if err:
                return err
        else:
            target = _today_in(prop["timezone"])
        target_s = target.isoformat()
        units = conn.execute(
            "SELECT * FROM units WHERE property_id = ?",
            (prop["id"],)).fetchall()
        unit_ids = [row["id"] for row in units]
        if not unit_ids:
            return f"{prop['name']} has no units yet."
        placeholders = ",".join("?" for _ in unit_ids)
        stays = conn.execute(
            f"SELECT s.*, u.name AS unit_name FROM stays s"
            f" JOIN units u ON u.id = s.unit_id"
            f" WHERE s.unit_id IN ({placeholders})"
            f" AND s.status IN ('reserved', 'checked_in', 'checked_out')"
            f" AND s.check_in <= ? AND s.check_out > ?"
            f" ORDER BY s.check_in",
            (*unit_ids, target_s, target_s)).fetchall()
        arrivals = [row for row in stays
                    if row["check_in"] == target_s
                    and row["status"] == "reserved"]
        departures = [row for row in stays
                      if row["check_out"] == target_s
                      and row["status"] == "checked_in"]
        in_house = [row for row in stays
                    if row["status"] == "checked_in"]
        take = 0
        for row in stays:
            folio = json.loads(row["folio_json"])
            take += folio.get("nights", {}).get(target_s, 0)
        total_units = len(units)
        occupied = len({row["unit_id"] for row in stays
                        if row["status"] in ("reserved", "checked_in")})
        bits = [f"{prop['name']} — {target_s} "
                f"({prop['timezone']}): {len(arrivals)} arrival(s), "
                f"{len(departures)} departure(s), {len(in_house)} "
                f"in-house, occupancy {occupied}/{total_units}, "
                f"expected take "
                f"{_money(take, prop['currency'])}"]
        for row in arrivals:
            bits.append(f"  arrives: #{row['id']} {row['unit_name']} "
                        f"({row['check_in']} to {row['check_out']})")
        for row in departures:
            bits.append(f"  departs: #{row['id']} {row['unit_name']} "
                        f"({row['check_in']} to {row['check_out']})")
        for row in in_house:
            if row["id"] not in {r["id"] for r in departures}:
                bits.append(f"  in-house: #{row['id']} "
                            f"{row['unit_name']} ({row['check_in']} to "
                            f"{row['check_out']})")
        return "\n".join(bits)

    def list_cmd(self, property_name, unit_name, status, day, limit,
                 detail):
        conn = self._connect()
        clauses = []
        params = []
        if property_name:
            prop = self._property(str(property_name).strip())
            if prop is None:
                return f"No property named {property_name!r}."
            clauses.append("u.property_id = ?")
            params.append(prop["id"])
        if unit_name:
            unit, err = self._resolve_unit(property_name, unit_name)
            if err:
                return err
            clauses.append("s.unit_id = ?")
            params.append(unit["id"])
        if status:
            text = str(status).strip().lower()
            if text not in ("reserved", "checked_in", "checked_out",
                            "cancelled", "no_show"):
                return (f"status {status!r} is not one of: reserved, "
                        "checked_in, checked_out, cancelled, no_show.")
            clauses.append("s.status = ?")
            params.append(text)
        if day:
            target, err = _valid_date(day, "date")
            if err:
                return err
            clauses.append("s.check_in <= ? AND s.check_out > ?")
            params.extend([target.isoformat(), target.isoformat()])
        limit_n, err = _whole(limit, "limit", 1) \
            if limit is not None else (20, None)
        if err:
            return err
        if limit_n > _MAX_STAYS_LIST:
            limit_n = _MAX_STAYS_LIST
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT s.*, u.name AS unit_name, g.name AS guest_name"
            f" FROM stays s JOIN units u ON u.id = s.unit_id"
            f" JOIN guests g ON g.id = s.guest_id {where}"
            f" ORDER BY s.check_in DESC LIMIT ?",
            (*params, limit_n)).fetchall()
        if not rows:
            return "No stays match."
        lines = []
        for row in rows:
            folio = json.loads(row["folio_json"])
            line = (f"#{row['id']} {row['unit_name']} — "
                    f"{row['guest_name']}, {row['check_in']} to "
                    f"{row['check_out']} ({row['status']}), "
                    f"{_money(folio.get('total_cents', 0), folio.get('currency', 'USD'))} total")
            if detail:
                line += (f" | folio: {folio.get('nightly_subtotal_cents', 0)}c "
                         f"nights + {folio.get('tax_cents', 0)}c "
                         f"{folio.get('tax_label', 'tax')} = "
                         f"{folio.get('total_cents', 0)}c, deposit "
                         f"{folio.get('deposit_due_cents', 0)}c, balance "
                         f"{folio.get('balance_due_cents', 0)}c")
            lines.append(line)
        return "\n".join(lines)

    def events_cmd(self, limit):
        conn = self._connect()
        limit_n, err = _whole(limit, "limit", 1) \
            if limit is not None else (20, None)
        if err:
            return err
        if limit_n > _MAX_STAYS_LIST:
            limit_n = _MAX_STAYS_LIST
        rows = conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?",
            (limit_n,)).fetchall()
        if not rows:
            return "No lodging events yet."
        lines = []
        for row in reversed(rows):
            body = json.loads(row["body_json"])
            summary = ", ".join(f"{key}={value}"
                                for key, value in body.items())
            lines.append(f"#{row['id']} {row['ts']} {row['kind']} "
                         f"[{row['hash'][:8]}] {summary}")
        # The hash chain is verified over the FULL ledger, not the
        # window shown: one broken link fails the whole check.
        chain_ok, chain_err = self.verify_chain()
        tail = ("chain OK" if chain_ok
                else f"CHAIN BROKEN: {chain_err}")
        return "\n".join(lines) + f"\n({tail})"

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


def lodging(action: str = "", property: str = "", unit: str = "",
            guest: str = "", contact: str = "", check_in: str = "",
            check_out: str = "", stay_id=None, date: str = "",
            state: str = "", cents=None, min_nights=None, sleeps=None,
            type: str = "", timezone: str = "", currency: str = "",
            tax_rate_bps=None, turnover_nights=None, deposit: str = "",
            cancel_policy: str = "", limit=None, detail=None,
            _db_path=None) -> str:
    """The owner's own lodging: properties, units, seasonal rates,
    nightly availability with a quote, stays, guests, housekeeping
    and the day view. The booking-engine half is core/bookings.py —
    it is NOT rebuilt here."""
    action = str(action or "").strip().lower()
    if action not in _ACTIONS:
        return ("lodging needs an action: 'property', 'unit', 'rate', "
                "'availability', 'book', 'cancel', 'checkin', "
                "'checkout', 'no-show', 'guest', 'housekeeping', "
                "'day', 'list' or 'events'.")
    db = _LodgingDB(_db_path)
    try:
        if action == "property":
            return db.property_cmd(
                property, timezone, currency, tax_rate_bps,
                turnover_nights, deposit, cancel_policy)
        if action == "unit":
            return db.unit_cmd(property, unit, type, sleeps, cents,
                               min_nights)
        if action == "rate":
            return db.rate_cmd(property, unit, cents, check_in,
                               check_out, min_nights)
        if action == "availability":
            return db.availability_cmd(property, unit, check_in,
                                       check_out)
        if action == "book":
            return db.book_cmd(property, unit, guest, contact,
                               check_in, check_out)
        if action == "cancel":
            return db.cancel_cmd(stay_id)
        if action == "checkin":
            return db.checkin_cmd(stay_id)
        if action == "checkout":
            return db.checkout_cmd(stay_id)
        if action == "no-show":
            return db.no_show_cmd(stay_id)
        if action == "guest":
            return db.guest_cmd(guest, contact)
        if action == "housekeeping":
            return db.housekeeping_cmd(property, unit, state, "")
        if action == "day":
            return db.day_cmd(property, date)
        if action == "list":
            return db.list_cmd(property, unit, state, date, limit,
                               detail)
        if action == "events":
            return db.events_cmd(limit)
    finally:
        db.close()
    return f"Unknown lodging action {action!r}."
